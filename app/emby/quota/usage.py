"""用户本周期上行用量：已结算 + 还没结算的实时累加器。"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Dict, Iterable, Optional

import emby.browse.stats as browse_stats
import emby.traffic.db as emby_db
import emby.traffic.playback as emby_playback_traffic
from core.cycle import TYPE_LABELS, iter_cycle_periods
from emby.lucky.verdict import persist_key_belongs_to_user, user_from_persist_key
import qb.traffic_db as traffic_db

logger = logging.getLogger(__name__)

# Emby 流量按十进制单位显示（1 GB = 1000 MB），额度同口径
GB = 1000 ** 3
_SUMMARY_TTL_SECONDS = 10.0


def listed_limit_gb(quota: dict, user_name: str) -> Optional[float]:
    """用户独立额度。没单独列出返回 None，列出则返回额度，0 表示不限。"""
    fold = str(user_name or '').strip().casefold()
    if not fold:
        return None
    for row in (quota or {}).get('users') or []:
        if str(row.get('user_name') or '').strip().casefold() == fold:
            return max(0.0, float(row.get('limit_gb') or 0))
    return None


def user_limit_gb(quota: dict, user_name: str) -> float:
    """按每人额度时该用户适用的额度（GB）。用户独立额度优先，否则用每人额度。0 表示不限。"""
    own = listed_limit_gb(quota, user_name)
    if own is not None:
        return own
    return max(0.0, float((quota or {}).get('default_limit_gb') or 0))


def is_total_mode(quota: dict) -> bool:
    return (quota or {}).get('mode') == 'total'


def has_any_limit(quota: dict) -> bool:
    """当前规则下是否设了有效额度。"""
    quota = quota or {}
    if is_total_mode(quota):
        if float(quota.get('total_limit_gb') or 0) > 0:
            return True
        return any(float(u.get('limit_gb') or 0) > 0 for u in quota.get('users') or [])
    if float(quota.get('default_limit_gb') or 0) > 0:
        return True
    return any(float(u.get('limit_gb') or 0) > 0 for u in quota.get('users') or [])


def _live_bytes(bucket: dict, user_fold: str, user_ids: set) -> int:
    total = 0
    for key, amount in (bucket or {}).items():
        if persist_key_belongs_to_user(key, user_fold, user_ids):
            total += max(0, int(amount or 0))
    return total


def cycle_used_bytes(
    instance_name: str,
    user_name: str,
    cycle: dict,
    *,
    credit_browse: bool,
    user_ids: Optional[Iterable[str]] = None,
    tz=None,
) -> int:
    """该用户在当前周期内的上行字节数（播放，选片按设备设置计入）。"""
    user = str(user_name or '').strip()
    if not user:
        return 0
    periods = iter_cycle_periods(cycle, tz or traffic_db.get_config_timezone(), count=1)
    rows = browse_stats.get_combined_playback_upload_cycle_stats(
        instance_name, user, periods, credit_browse=credit_browse,
    )
    settled = sum(max(0, int(row.get('total_bytes') or 0)) for row in rows)

    fold = user.casefold()
    ids = {str(x).strip() for x in (user_ids or []) if str(x).strip()}
    live = _live_bytes(emby_playback_traffic.peek_upload_bucket(instance_name), fold, ids)
    if credit_browse:
        live += _live_bytes(
            emby_playback_traffic.peek_browse_upload_bucket(instance_name), fold, ids,
        )
    return settled + live


def cycle_total_bytes(
    instance_name: str,
    cycle: dict,
    *,
    credit_browse: bool,
    tz=None,
) -> int:
    """当前周期内所有用户合计的上行字节数（已结算 + 实时累加器，选片按设备设置计入）。"""
    settled = cycle_used_by_user(instance_name, cycle, credit_browse=credit_browse, tz=tz,
                                 include_live=False)
    total = sum(settled.values())
    total += sum(max(0, int(v or 0)) for v in emby_playback_traffic.peek_upload_bucket(instance_name).values())
    if credit_browse:
        total += sum(
            max(0, int(v or 0))
            for v in emby_playback_traffic.peek_browse_upload_bucket(instance_name).values()
        )
    return total


def _sum_by_user(cursor, sql: str, params: tuple, into: Dict[str, int],
                 names: Optional[Dict[str, str]] = None) -> None:
    cursor.execute(sql, params)
    for row in cursor.fetchall():
        shown = str(row['user_name'] or '').strip()
        fold = shown.casefold()
        if not fold:
            continue
        into[fold] = into.get(fold, 0) + max(0, int(row['total'] or 0))
        if names is not None and fold not in names:
            names[fold] = shown


def cycle_used_by_user(
    instance_name: str,
    cycle: dict,
    *,
    credit_browse: bool,
    tz=None,
    include_live: bool = True,
    names: Optional[Dict[str, str]] = None,
) -> Dict[str, int]:
    """当前周期内每个用户的上行字节数（小写用户名为键）。

    含已结算数据和播放实时累加器（include_live=False 时只含已结算），选片的实时部分不含。
    """
    period = iter_cycle_periods(cycle, tz or traffic_db.get_config_timezone(), count=1)[0]
    start_s = period['cycle_start'].strftime('%Y-%m-%d %H:%M:%S')
    end_s = period['cycle_end'].strftime('%Y-%m-%d %H:%M:%S')
    used: Dict[str, int] = {}
    emby_db._ensure_emby_schema()
    with emby_db._lock:
        conn = emby_db.get_conn()
        try:
            c = conn.cursor()
            _sum_by_user(c, '''
                SELECT user_name, SUM(uploaded_bytes) AS total
                FROM emby_playback_upload_hourly
                WHERE instance_name = ? AND hour_start >= ? AND hour_start < ?
                GROUP BY user_name
            ''', (instance_name, start_s, end_s), used, names)
            if credit_browse:
                _sum_by_user(c, '''
                    SELECT user_name, SUM(estimated_upload_bytes) AS total
                    FROM emby_browse_upload_facts
                    WHERE instance_name = ? AND stopped_at >= ? AND stopped_at < ?
                    GROUP BY user_name
                ''', (instance_name, start_s, end_s), used, names)
        finally:
            conn.close()
    if include_live:
        for key, amount in emby_playback_traffic.peek_upload_bucket(instance_name).items():
            fold = user_from_persist_key(key)
            if fold:
                used[fold] = used.get(fold, 0) + max(0, int(amount or 0))
                if names is not None:
                    names.setdefault(fold, fold)
    return used


def _quota_via(quota: dict, fold: str) -> str:
    """own 独立额度，total 总额度，all 每人额度。"""
    if listed_limit_gb(quota, fold) is not None:
        return 'own'
    if is_total_mode(quota):
        return 'total'
    return 'all'


def _prefer_config_names(quota: dict, names: Dict[str, str]) -> None:
    """名单上的名字优先用额度里配置的写法。"""
    for row in (quota or {}).get('users') or []:
        shown = str(row.get('user_name') or '').strip()
        fold = shown.casefold()
        if fold and fold in names:
            names[fold] = shown


def _quota_user(
    names: Dict[str, str],
    fold: str,
    limit_gb: float,
    used_bytes: int,
    via: str,
    personal_bytes: Optional[int] = None,
) -> dict:
    """used_bytes 是拿来和额度比的量。personal_bytes 是这个人自己的用量，缺省就用 used_bytes。"""
    meter = max(0, int(used_bytes))
    personal = meter if personal_bytes is None else max(0, int(personal_bytes))
    limit = round(float(limit_gb), 2)
    meter_gb = round(meter / GB, 2)
    remain = None if limit <= 0 else round(max(0.0, limit - meter_gb), 2)
    return {
        'name': names.get(fold) or fold,
        'limit_gb': limit,
        'used_gb': round(personal / GB, 2),
        'meter_gb': meter_gb,
        'remain_gb': remain,
        'via': via,
    }


def _remember_name(names: Dict[str, str], shown: str) -> None:
    text = str(shown or '').strip()
    fold = text.casefold()
    if fold:
        names.setdefault(fold, text)


_summary_lock = threading.Lock()
_summary_cache: Dict[str, dict] = {}


def quota_summary(
    instance_name: str,
    quota: dict,
    *,
    credit_browse: bool,
    clock=time.monotonic,
) -> dict:
    """设备卡片用：当前周期、达量用户数和名单。每 10 秒才重新查一次。"""
    quota = quota or {}
    if not quota.get('enabled'):
        return {'enabled': False}
    signature = json.dumps([quota, credit_browse], sort_keys=True, default=str)
    now = clock()
    with _summary_lock:
        cached = _summary_cache.get(instance_name)
        if cached and cached['signature'] == signature and now - cached['at'] < _SUMMARY_TTL_SECONDS:
            return cached['value']
    cycle = quota.get('cycle') or {}
    value = {
        'enabled': True,
        'dry_run': bool(quota.get('dry_run', True)),
        'type_label': TYPE_LABELS.get(str(cycle.get('type') or 'month'), '按月'),
        'range_label': '',
        'mode': 'total' if is_total_mode(quota) else 'user',
        'over_count': 0,
        'total_reached': False,
        'overs': [],
        'lookup': [],
    }
    try:
        period = iter_cycle_periods(cycle, traffic_db.get_config_timezone(), count=1)[0]
        value['range_label'] = period['period']
        names: Dict[str, str] = {}
        used = cycle_used_by_user(
            instance_name, cycle, credit_browse=credit_browse, names=names,
        )
        _prefer_config_names(quota, names)
        overs = []
        if is_total_mode(quota):
            limit_gb = float(quota.get('total_limit_gb') or 0)
            total = 0
            if limit_gb > 0:
                total = cycle_total_bytes(instance_name, cycle, credit_browse=credit_browse)
                value['total_reached'] = total >= limit_gb * GB
            value['has_own_limits'] = bool(quota.get('users'))
            if value['has_own_limits']:
                for fold, amount in used.items():
                    own = listed_limit_gb(quota, fold)
                    if own is None:
                        if value['total_reached']:
                            overs.append(_quota_user(names, fold, limit_gb, total, _quota_via(quota, fold)))
                    elif own > 0 and amount >= own * GB:
                        overs.append(_quota_user(names, fold, own, amount, _quota_via(quota, fold)))
            elif value['total_reached']:
                for fold in used:
                    overs.append(_quota_user(names, fold, limit_gb, total, _quota_via(quota, fold)))
        else:
            for fold, amount in used.items():
                limit_gb = user_limit_gb(quota, fold)
                if limit_gb > 0 and amount >= limit_gb * GB:
                    overs.append(_quota_user(names, fold, limit_gb, amount, _quota_via(quota, fold)))
        overs.sort(key=lambda row: (-row['used_gb'], str(row['name']).casefold()))
        value['overs'] = overs
        # 没配独立额度时卡片写「全部用户」，人数仍按原来的 0
        if not (is_total_mode(quota) and value['total_reached'] and not value.get('has_own_limits')):
            value['over_count'] = len(overs)
        for shown in emby_db.list_distinct_user_names(instance_name):
            _remember_name(names, shown)
        for row in (quota.get('users') or []):
            _remember_name(names, row.get('user_name'))
        _prefer_config_names(quota, names)
        pool_limit = float(quota.get('total_limit_gb') or 0) if is_total_mode(quota) else 0.0
        pool_bytes = total if is_total_mode(quota) else 0
        lookup = []
        for fold in names:
            personal = used.get(fold, 0)
            via = _quota_via(quota, fold)
            if is_total_mode(quota):
                own = listed_limit_gb(quota, fold)
                if own is None and pool_limit > 0:
                    lookup.append(_quota_user(
                        names, fold, pool_limit, pool_bytes, via, personal,
                    ))
                    continue
                limit_gb = own or 0.0
                lookup.append(_quota_user(names, fold, limit_gb, personal, via))
            else:
                lookup.append(_quota_user(
                    names, fold, user_limit_gb(quota, fold), personal, via,
                ))
        lookup.sort(key=lambda row: str(row['name']).casefold())
        value['lookup'] = lookup
    except Exception:
        logger.warning(f'[Emby:{instance_name}] 达量用户数统计失败', exc_info=True)
    with _summary_lock:
        _summary_cache[instance_name] = {'signature': signature, 'at': now, 'value': value}
    return value
