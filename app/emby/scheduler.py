"""Emby 流量与状态采集调度"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Set
from zoneinfo import ZoneInfo

import core.config_manager as config_manager
import emby.traffic.db as emby_traffic_db
import emby.records.store as playback_record_store
import core.secrets_store as secrets_store
import qb.traffic_db as traffic_db
import emby.traffic.playback as emby_playback_traffic
import emby.traffic.tick_audit as emby_traffic_tick_audit
import emby.user_sync as emby_user_sync
from emby.client import EmbyClient
from emby.quota.enforcer import QuotaEnforcer
from emby.quota.usage import quota_summary, refresh_quota_summary, set_summary_ttl
from emby.lucky.api import (
    LuckyClient,
    calc_conn_traffic_deltas,
    calc_ip_traffic_deltas,
    extract_wan_ip_cumulative_traffic,
    iter_wan_conn_statistics,
    sum_positive,
)
from emby.traffic.filter import (
    canon_client_ip,
    is_lan_ip,
    is_wan_playback_session,
)
from qb.scheduler import clamp_interval, ticks_per_full_collect

logger = logging.getLogger(__name__)


def _sort_sessions_newest_first(sessions) -> list:
    """当前播放会话：最新开始的在前。没有开始时间的放最后。"""
    dated = []
    undated = []
    for session in sessions or []:
        if not isinstance(session, dict):
            continue
        if str(session.get('playback_started_at') or ''):
            dated.append(session)
        else:
            undated.append(session)
    dated.sort(key=lambda s: str(s.get('id') or ''))
    undated.sort(key=lambda s: str(s.get('id') or ''))
    dated.sort(key=lambda s: str(s.get('playback_started_at') or ''), reverse=True)
    return dated + undated


def _active_playback_sessions(sessions: list) -> list:
    return [
        s for s in (sessions or [])
        if isinstance(s, dict) and bool(s.get('is_playing')) and not bool(s.get('is_paused'))
    ]


def _has_confirmed_wan_playback(sessions: list) -> bool:
    return any(
        is_wan_playback_session(s)
        for s in _active_playback_sessions(sessions)
    )


def _debug_mode_from_sessions(sessions: list) -> tuple:
    active = _active_playback_sessions(sessions)
    if not active:
        return 'M0', '无播放 M0', 0, 0
    wan = [s for s in active if is_wan_playback_session(s)]
    lan = [s for s in active if not is_wan_playback_session(s)]
    wan_count = len(wan)
    lan_count = len(lan)
    if wan_count <= 0 and lan_count > 0:
        return 'M1', '仅局域网 M1', lan_count, wan_count
    if wan_count > 0 and lan_count <= 0:
        return 'M2', '仅外网 M2', lan_count, wan_count
    return 'M3', '局域网+外网 M3', lan_count, wan_count


def _build_debug_traffic_metrics(total_upload_bytes: int, sessions: list,
                                 wan_upload_bytes: int,
                                 lan_upload_bytes: int = 0,
                                 program_remainder_bytes: int = 0,
                                 mode_switch_pending_bytes: int = 0,
                                 mode_switch_replay_bytes: int = 0,
                                 mode_switch_replay_alloc_bytes: int = 0,
                                 mode_switch_replay_total_bytes: int = 0,
                                 mode_switch_replay_alloc_total_bytes: int = 0,
                                 wan_alloc_backlog_bytes: int = 0,
                                 wan_alloc_backlog_applied_bytes: int = 0,
                                 m1_wan_capture_bytes: int = 0,
                                 wan_only_enabled: bool = True) -> dict:
    total_up = max(0, int(total_upload_bytes or 0))
    wan_up = max(0, min(total_up, int(wan_upload_bytes or 0)))
    lan_up = max(0, min(total_up, int(lan_upload_bytes or 0)))
    mode_code, mode_label, lan_count, wan_count = _debug_mode_from_sessions(sessions)
    if wan_up + lan_up > total_up:
        overflow = wan_up + lan_up - total_up
        lan_up = max(0, lan_up - overflow)
        if wan_up + lan_up > total_up:
            wan_up = max(0, total_up - lan_up)

    remainder_max = max(0, total_up - wan_up - lan_up)
    remainder_in = max(0, int(program_remainder_bytes or 0))
    remainder = min(remainder_max, remainder_in) if remainder_in > 0 else remainder_max

    if mode_code == 'M0':
        wan_up = 0
        lan_up = 0
        pending = max(0, int(mode_switch_pending_bytes or 0))
        if pending > 0 and pending >= total_up:
            remainder = 0
        else:
            remainder = max(0, total_up - pending)
    elif mode_code == 'M1':
        wan_up = 0
        if wan_only_enabled:
            lan_up = total_up
            remainder = 0
        else:
            if lan_up <= 0:
                lan_up = total_up
            lan_up = min(total_up, lan_up)
            remainder = max(0, total_up - lan_up)
    elif mode_code == 'M2':
        lan_up = 0
        if wan_up <= 0:
            wan_up = total_up
        wan_up = min(total_up, wan_up)
        remainder = max(0, total_up - wan_up)
    else:
        assigned = wan_up + lan_up + remainder
        if assigned < total_up:
            lan_up += (total_up - assigned)

    return {
        'mode_code': mode_code,
        'mode_label': mode_label,
        'lan_session_count': lan_count,
        'wan_session_count': wan_count,
        'total_upload_bytes': total_up,
        'wan_upload_bytes': max(0, int(wan_up)),
        'lan_upload_bytes': max(0, int(lan_up)),
        'program_remainder_bytes': max(0, int(remainder)),
        'mode_switch_pending_bytes': max(0, int(mode_switch_pending_bytes or 0)),
        'mode_switch_replay_bytes': max(0, int(mode_switch_replay_bytes or 0)),
        'mode_switch_replay_alloc_bytes': max(0, int(mode_switch_replay_alloc_bytes or 0)),
        'mode_switch_replay_total_bytes': max(0, int(mode_switch_replay_total_bytes or 0)),
        'mode_switch_replay_alloc_total_bytes': max(
            0, int(mode_switch_replay_alloc_total_bytes or 0),
        ),
        'wan_alloc_backlog_bytes': max(0, int(wan_alloc_backlog_bytes or 0)),
        'wan_alloc_backlog_applied_bytes': max(
            0, int(wan_alloc_backlog_applied_bytes or 0),
        ),
        'm1_wan_capture_bytes': max(0, int(m1_wan_capture_bytes or 0)),
    }


def _online_since_from_prev(prev: dict, was_online: bool = None) -> str:
    if was_online is None:
        was_online = prev.get('is_online')
    if was_online:
        cached = prev.get('online_since')
        if cached:
            return cached
    return emby_traffic_db._now().strftime('%Y-%m-%d %H:%M:%S')


class EmbyInstanceWorker:
    def __init__(self, monitor: 'EmbyMonitor', name: str):
        self.monitor = monitor
        self.name = name
        self._thread: threading.Thread = None
        self._running = False
        self._wake = threading.Event()
        self._was_online = False
        self._light_ticks = 0
        self._last_sessions = []
        self._verify_cumulative = {}
        self._last_tick_audit = {}
        self._lucky_ip_baselines: Dict[str, Dict[str, int]] = {}
        self._lucky_conn_baselines: Dict[str, Dict[str, int]] = {}
        self._lucky_conn_rows_last: list = []
        self._lucky_conn_deltas_last: Dict[str, int] = {}
        self._lucky_ip_traffic_last: Dict[str, Dict[str, int]] = {}
        self._lucky_ip_deltas_last: Dict[str, int] = {}
        self._lucky_total_out = 0
        self._lucky_total_in = 0
        self._lucky_analysis_last: Optional[dict] = None
        self._wan_client_sessions_last: list = []
        self._live_upload_hydrated = False
        self._recovery_allowed_persist_keys: Optional[Set[str]] = None
        self._pending_lucky_snapshot: Optional[dict] = None
        self._upload_miss_ticks: Dict[str, int] = {}
        self._upload_anomaly_ids: Set[str] = set()
        self._quota_enforcer = QuotaEnforcer(name)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name=f'emby-collector-{self.name}', daemon=True,
        )
        self._thread.start()

    def stop(self, wait: bool = True):
        self._running = False
        self._wake.set()
        if self._thread:
            if wait:
                self._thread.join(timeout=15)
            self._thread = None

    def wake(self):
        self._wake.set()

    def _sleep_interval(self, tick_elapsed_seconds: float = 0.0):
        interval = clamp_interval(self.monitor.refresh_interval)
        wait_seconds = max(0.0, float(interval) - max(0.0, float(tick_elapsed_seconds or 0.0)))
        self._wake.wait(timeout=wait_seconds)
        self._wake.clear()

    def _ticks_per_full(self) -> int:
        return ticks_per_full_collect(
            self.monitor.collect_interval, self.monitor.refresh_interval)

    def _should_run_full_tick(self) -> bool:
        return self._light_ticks <= 0 or self._light_ticks >= self._ticks_per_full()

    def _get_client(self) -> Optional[EmbyClient]:
        with self.monitor._config_lock:
            return self.monitor.clients.get(self.name)

    def _lucky_client(self, client: EmbyClient) -> Optional[LuckyClient]:
        token = secrets_store.get_lucky_open_token(self.name)
        if not token or not client.lucky_base_url:
            return None
        return LuckyClient(
            client.lucky_base_url,
            open_token=token,
            verify_ssl=client.lucky_verify_ssl,
        )

    def _lucky_wan_totals(self, res_list: list) -> tuple:
        total_out = total_in = 0
        for item in res_list or []:
            if not isinstance(item, dict):
                continue
            ip = str(item.get('IP') or '').strip()
            if not ip or is_lan_ip(ip):
                continue
            total_out += max(0, int(item.get('TrafficOut') or 0))
            total_in += max(0, int(item.get('TrafficIn') or 0))
        return total_out, total_in

    def _fetch_lucky_rule_details(self, client: EmbyClient) -> tuple:
        """拉取已选的每条反代。

        返回 ([(rule, data), ...], 是否至少成功一条, 是否每条都成功)。
        """
        lc = self._lucky_client(client)
        rules = list(getattr(client, 'lucky_rules', None) or [])
        if not lc or not rules:
            return [], False, False
        loaded = []
        failed = False
        for rule in rules:
            label = str(rule.get('label') or rule.get('rule_key') or '').strip()
            data, err = lc.fetch_access_detail(
                rule.get('rule_key'),
                rule.get('sub_key'),
            )
            if err or not data:
                failed = True
                logger.warning(
                    f'[Emby:{self.name}] Lucky 反代读取失败'
                    f'{(" " + label) if label else ""}: {err or "空数据"}',
                )
                continue
            loaded.append((rule, data))
        any_ok = bool(loaded)
        return loaded, any_ok, any_ok and not failed

    def _fetch_sessions(self, client: EmbyClient) -> tuple:
        """返回 (sessions, ok)；拉取失败时 ok=False，调用方应保留分摊状态。"""
        try:
            return client.get_all_client_sessions() or [], True
        except Exception as e:
            logger.debug(f'[Emby:{self.name}] 获取会话失败: {e}')
            return None, False

    def _probe_api_online(self, client: EmbyClient) -> bool:
        return client.is_reachable()

    def _proxy_seen_ips(self, lucky_conn_debug, lucky_ip_traffic) -> set:
        """已选反代里出现过的客户端地址：打开的连接，或 IP 流量列表。"""
        seen = set()
        debug = lucky_conn_debug or {}
        for group in debug.get('groups') or []:
            if not isinstance(group, dict):
                continue
            ip = canon_client_ip(group.get('ip') or '')
            if ip:
                seen.add(ip)
        for ip in lucky_ip_traffic or {}:
            canon = canon_client_ip(ip)
            if canon:
                seen.add(canon)
        return seen

    def _apply_upload_anomalies(
        self, sessions, lucky_conn_debug, *, fresh: bool, lucky_ip_traffic=None,
    ) -> None:
        """外网在播、已选反代里没有这个地址，连续两次样本仍是 0 则标为异常。

        fresh 仅在本次每条已选反代都读成功时为真。残缺样本不新增、也不清除标记。
        """
        sessions = sessions or []
        if not fresh:
            for session in sessions:
                if not isinstance(session, dict):
                    continue
                sid = str(session.get('id') or '').strip()
                if sid and sid in self._upload_anomaly_ids:
                    session['upload_anomaly'] = True
            return
        seen = self._proxy_seen_ips(lucky_conn_debug, lucky_ip_traffic)
        active = set()
        turned_on = []
        turned_off = []
        for session in sessions:
            if not isinstance(session, dict) or not session.get('is_remote'):
                continue
            if not EmbyClient.is_current_playback_session(session):
                continue
            sid = str(session.get('id') or '').strip()
            if not sid:
                continue
            active.add(sid)
            if 'estimated_upload_bytes_live' not in session:
                continue
            upload = max(0, int(session.get('estimated_upload_bytes_live') or 0))
            ip = canon_client_ip(session.get('remote_endpoint') or '')
            unmatched = upload <= 0 and bool(ip) and ip not in seen
            if unmatched:
                ticks = self._upload_miss_ticks.get(sid, 0) + 1
                self._upload_miss_ticks[sid] = ticks
                if ticks >= 2:
                    session['upload_anomaly'] = True
                    if sid not in self._upload_anomaly_ids:
                        self._upload_anomaly_ids.add(sid)
                        turned_on.append(sid)
            else:
                self._upload_miss_ticks.pop(sid, None)
                if sid in self._upload_anomaly_ids:
                    self._upload_anomaly_ids.discard(sid)
                    turned_off.append(sid)
        for sid in list(self._upload_miss_ticks):
            if sid not in active:
                self._upload_miss_ticks.pop(sid, None)
        if turned_on or turned_off:
            try:
                playback_record_store.sync_upload_anomaly(
                    self.name, turned_on, turned_off,
                )
            except Exception as e:
                logger.debug(f'[Emby:{self.name}] 上传异常标记写入失败: {e}')

    def _loop(self):
        while self._running and self.monitor._running:
            tick_started = time.monotonic()
            try:
                if self._should_run_full_tick():
                    self._tick(full=True)
                    self._light_ticks = 0
                else:
                    self._tick(full=False)
                self._light_ticks += 1
            except Exception as e:
                logger.error(f'[Emby:{self.name}] 采集循环异常: {e}', exc_info=True)
            tick_elapsed = time.monotonic() - tick_started
            self._sleep_interval(tick_elapsed)

    def _sync_playback_sessions(self, client: EmbyClient, *, api_online: bool) -> list:
        """拉取并同步播放会话；API 返回空列表表示无播放（如 Web 返回键退出）。"""
        if not api_online:
            return []
        fetched_sessions, sessions_fetch_ok = self._fetch_sessions(client)
        if not sessions_fetch_ok:
            logger.debug(
                f'[Emby:{self.name}] 会话拉取失败，保留 WAN 分摊状态',
            )
            return list(self._last_sessions or [])
        fetched_sessions = fetched_sessions or []
        estimate_upload_enabled = bool(
            getattr(client, 'upload_tracking_enabled', False),
        )
        traffic_collect_mode = str(
            getattr(client, 'traffic_collect_mode', '') or '',
        ).strip().lower()
        sessions = [
            {
                **s,
                'estimate_upload_enabled': estimate_upload_enabled,
                'traffic_collect_mode': traffic_collect_mode,
            }
            if isinstance(s, dict) else s
            for s in fetched_sessions
        ]
        store_snapshot = None
        with self.monitor._config_lock:
            inst_cfg = config_manager.get_emby_instance(
                self.name, self.monitor.config,
            )
        if inst_cfg:
            credit_browse = bool(
                inst_cfg.get('lucky_credit_browse_traffic', False),
            ) and traffic_collect_mode == 'lucky'
            try:
                _, store_snapshot = playback_record_store.tick_from_sessions(
                    self.name, sessions, api_online=api_online,
                    return_store=True,
                )
            except Exception as e:
                logger.debug(
                    f'[Emby:{self.name}] 播放段记录更新失败: {e}',
                )
        sessions = playback_record_store.enrich_sessions_playback_started_at(
            self.name, sessions, store=store_snapshot,
        )
        try:
            emby_playback_traffic.purge_stopped_wan_live_upload_state(
                self.name, sessions,
            )
        except Exception as e:
            logger.debug(
                f'[Emby:{self.name}] 停止会话分摊状态清理失败: {e}',
            )
        if not fetched_sessions:
            self._verify_cumulative = {}
            self._last_tick_audit = {}
        self._wan_client_sessions_last = [
            dict(s) for s in sessions
            if isinstance(s, dict) and s.get('is_remote')
        ]
        from emby.client import EmbyClient
        open_sessions = [
            s for s in sessions
            if isinstance(s, dict) and EmbyClient.is_current_playback_session(s)
        ]
        self._last_sessions = open_sessions
        try:
            self._quota_enforcer.on_tick(client, inst_cfg, open_sessions)
        except Exception as e:
            logger.error(f'[Emby:{self.name}] 达量停播处理异常: {e}', exc_info=True)
        return open_sessions

    def _tick(self, full: bool):
        client = self._get_client()
        if not client:
            return

        collect_mode = str(getattr(client, 'traffic_collect_mode', '') or '').strip().lower()
        if collect_mode != 'lucky':
            collect_mode = ''

        was_online = self._was_online
        api_online = self._probe_api_online(client)
        recovering = not was_online and api_online
        is_recovery_tick = (
            full
            and recovering
            and collect_mode == 'lucky'
            and api_online
            and emby_traffic_db.has_snapshot_baseline(self.name)
        )
        self._recovery_allowed_persist_keys = None
        self._pending_lucky_snapshot = None
        if is_recovery_tick:
            fetched, fetch_ok = self._fetch_sessions(client)
            prep_sessions = []
            if fetch_ok and fetched is not None:
                prep_sessions = [
                    {
                        **s,
                        'estimate_upload_enabled': bool(
                            getattr(client, 'upload_tracking_enabled', False),
                        ),
                        'traffic_collect_mode': collect_mode,
                    }
                    if isinstance(s, dict) else s
                    for s in (fetched or [])
                ]
            try:
                self._recovery_allowed_persist_keys = (
                    playback_record_store.run_recovery_scan(
                        self.name, prep_sessions,
                    )
                )
            except Exception as e:
                logger.warning(
                    f'[Emby:{self.name}] 程序重启播放段扫描失败: {e}',
                )
                self._recovery_allowed_persist_keys = set()
            try:
                playback_record_store.begin_post_recovery_playback_window(self.name)
            except Exception:
                logger.warning(
                    '[Emby:%s] 开启重启后续传窗口失败',
                    self.name,
                    exc_info=True,
                )

        sessions = self._sync_playback_sessions(client, api_online=api_online)

        if is_recovery_tick:
            try:
                playback_record_store.end_post_recovery_playback_window(self.name)
            except Exception:
                logger.warning(
                    '[Emby:%s] 结束重启后续传窗口失败',
                    self.name,
                    exc_info=True,
                )

        credit_browse = (
            collect_mode == 'lucky'
            and bool(getattr(client, 'lucky_credit_browse_traffic', False))
        )
        if not self._live_upload_hydrated and api_online:
            try:
                emby_playback_traffic.hydrate_live_upload_state(
                    self.name,
                    list(self._wan_client_sessions_last or sessions or []),
                    credit_browse=credit_browse,
                )
            except Exception as e:
                logger.debug(
                    f'[Emby:{self.name}] 会话流量续传恢复失败: {e}',
                )
            self._live_upload_hydrated = True

        lucky_available = False
        lucky_sample_ok = False
        lucky_ip_deltas: Dict[str, int] = {}
        lucky_ip_traffic: Dict[str, Dict[str, int]] = {}
        is_online = api_online
        is_backfill = False
        backfill_up = backfill_dl = 0
        raw_up = raw_dl = 0
        live_raw_up = live_raw_dl = 0
        delta_up = delta_dl = 0
        live_delta_up = live_delta_dl = 0
        allocation_debug = {
            'total_upload_bytes': 0,
            'wan_upload_bytes': 0,
            'lan_upload_bytes': 0,
            'wan_pool_bytes': 0,
            'assigned_bytes': 0,
            'remainder_bytes': 0,
            'program_remainder_bytes': 0,
            'target_session_count': 0,
        }

        if collect_mode == 'lucky':
            loaded_rules = []
            rules_complete = False
            if full or self._was_online:
                loaded_rules, lucky_available, rules_complete = (
                    self._fetch_lucky_rule_details(client)
                )
            if loaded_rules:
                lucky_sample_ok = rules_complete
                is_online = api_online or lucky_available
                ip_baselines = emby_traffic_db.load_lucky_ip_baselines(self.name)
                conn_baselines = emby_traffic_db.load_lucky_conn_baselines(self.name)
                out_deltas: Dict[str, int] = {}
                in_deltas: Dict[str, int] = {}
                conn_out_deltas: Dict[str, int] = {}
                conn_rows = []
                lucky_ip_traffic = {}
                total_out = total_in = 0
                for rule, detail in loaded_rules:
                    res_list = detail.get('resList') or []
                    prefix = f"{rule.get('rule_key')}|{rule.get('sub_key')}|"
                    rule_out, rule_in, ip_baselines = calc_ip_traffic_deltas(
                        res_list,
                        ip_baselines,
                        wan_only=True,
                        key_prefix=prefix,
                    )
                    rule_conn_out, _, conn_baselines = calc_conn_traffic_deltas(
                        res_list,
                        conn_baselines,
                        wan_only=True,
                        key_prefix=prefix,
                    )
                    for ip, amount in rule_out.items():
                        out_deltas[ip] = out_deltas.get(ip, 0) + amount
                    for ip, amount in rule_in.items():
                        in_deltas[ip] = in_deltas.get(ip, 0) + amount
                    for addr, amount in rule_conn_out.items():
                        conn_out_deltas[addr] = conn_out_deltas.get(addr, 0) + amount
                    conn_rows.extend(iter_wan_conn_statistics(res_list, wan_only=True))
                    for ip, traffic in extract_wan_ip_cumulative_traffic(res_list).items():
                        slot = lucky_ip_traffic.setdefault(ip, {'out': 0, 'in': 0})
                        slot['out'] += int(traffic.get('out') or 0)
                        slot['in'] += int(traffic.get('in') or 0)
                    rule_out_total, rule_in_total = self._lucky_wan_totals(res_list)
                    total_out += rule_out_total
                    total_in += rule_in_total
                emby_traffic_db.save_lucky_ip_baselines(self.name, ip_baselines)
                self._lucky_ip_baselines = ip_baselines
                emby_traffic_db.save_lucky_conn_baselines(self.name, conn_baselines)
                self._lucky_conn_baselines = conn_baselines
                self._lucky_conn_rows_last = conn_rows
                self._lucky_conn_deltas_last = dict(conn_out_deltas)
                self._lucky_ip_traffic_last = lucky_ip_traffic
                self._lucky_ip_deltas_last = dict(out_deltas)
                lucky_ip_deltas = out_deltas
                raw_up = sum_positive(out_deltas)
                raw_dl = sum_positive(in_deltas)
                live_raw_up = raw_up
                live_raw_dl = raw_dl
                live_delta_up = raw_up
                live_delta_dl = raw_dl
                last_tx, last_rx = emby_traffic_db.get_instance_last_totals(self.name)
                is_backfill = (
                    full
                    and recovering
                    and emby_traffic_db.has_snapshot_baseline(self.name)
                )
                self._pending_lucky_snapshot = {
                    'last_tx': last_tx,
                    'last_rx': last_rx,
                    'raw_up': raw_up,
                    'raw_dl': raw_dl,
                    'is_backfill': is_backfill,
                }
                if is_backfill and raw_up > 0:
                    logger.info(
                        f'[Emby:{self.name}] Lucky 离线恢复待分摊上行='
                        f'{raw_up / 1024 / 1024:.2f}MB'
                    )
                self._lucky_total_out = total_out
                self._lucky_total_in = total_in
            else:
                if api_online:
                    is_online = api_online
                lucky_ip_traffic = dict(self._lucky_ip_traffic_last or {})
                lucky_ip_deltas = dict(self._lucky_ip_deltas_last or {})

        mode_sessions = sessions
        wan_alloc_sessions = (
            list(getattr(self, '_wan_client_sessions_last', None) or [])
            if credit_browse else mode_sessions
        )

        if not is_online and self._was_online:
            logger.warning(f'[Emby:{self.name}] 连接中断，进入离线探测模式')

        emby_traffic_db.update_instance_status(
            self.name,
            is_online=is_online,
            api_online=api_online,
        )

        mode_code, _, _, _ = _debug_mode_from_sessions(mode_sessions)
        wan_only_enabled = bool(getattr(client, 'wan_traffic_only', True))
        allocation_tick_seconds = max(1, int(self.monitor.refresh_interval or 1))
        if collect_mode == 'lucky':
            conn_deltas = dict(getattr(self, '_lucky_conn_deltas_last', None) or {})
            conn_rows = list(getattr(self, '_lucky_conn_rows_last', None) or [])
            alloc_deltas = conn_deltas if conn_deltas else lucky_ip_deltas
            if credit_browse:
                try:
                    import emby.browse.continuous as emby_continuous_playback
                    emby_continuous_playback.tick(
                        self.name,
                        list(self._wan_client_sessions_last or []),
                    )
                except Exception as e:
                    logger.debug(
                        f'[Emby:{self.name}] 连播上下文更新失败: {e}',
                    )
            # 边界顺序保障：playback_record_store.tick_from_sessions（上文，
            # 会在换集/结案时先把累加器结转进旧分段）必须在下方 Lucky 分摊
            # 之前执行，这样换集 tick 的新增量只会计入新分段，旧分段已完整
            # 结转，边界误差被限制在至多一个 tick 内。
            allowed_keys = (
                self._recovery_allowed_persist_keys
                if is_recovery_tick else None
            )
            if sessions is not None and live_delta_up > 0 and alloc_deltas:
                try:
                    if conn_deltas and conn_rows:
                        part = emby_playback_traffic.accumulate_wan_upload_by_conn(
                            self.name,
                            wan_alloc_sessions,
                            conn_deltas,
                            conn_rows,
                            tick_seconds=allocation_tick_seconds,
                            credit_browse=credit_browse,
                            ip_deltas=lucky_ip_deltas,
                            allowed_persist_keys=allowed_keys,
                        )
                    else:
                        part = emby_playback_traffic.accumulate_wan_upload_by_ip(
                            self.name,
                            mode_sessions,
                            lucky_ip_deltas,
                            tick_seconds=allocation_tick_seconds,
                            allowed_persist_keys=allowed_keys,
                        )
                    if isinstance(part, dict):
                        allocation_debug.update(part)
                except Exception as e:
                    logger.debug(
                        f'[Emby:{self.name}] Lucky 上行分摊失败: {e}',
                    )
            if credit_browse:
                lucky_analysis = None
                try:
                    lucky_analysis = emby_playback_traffic.get_lucky_conn_debug_snapshot(
                        self.name,
                        wan_alloc_sessions,
                        conn_rows,
                        alloc_deltas or {},
                        credit_browse=True,
                    )
                except Exception as e:
                    logger.debug(
                        f'[Emby:{self.name}] Lucky 选片结算快照失败: {e}',
                    )
                if isinstance(lucky_analysis, dict):
                    self._lucky_analysis_last = lucky_analysis
                elif self._lucky_analysis_last is not None:
                    lucky_analysis = self._lucky_analysis_last
                try:
                    import emby.browse.settler as browse_upload_settler
                    browse_upload_settler.tick(
                        self.name,
                        list(self._wan_client_sessions_last or []),
                        api_online=api_online,
                        credit_enabled=True,
                        analysis=lucky_analysis,
                        min_upload_bytes=config_manager.instance_browse_upload_min_bytes({
                            'browse_upload_min_mb': getattr(
                                client, 'browse_upload_min_mb', None,
                            ),
                        }),
                    )
                except Exception as e:
                    logger.warning(
                        f'[Emby:{self.name}] 选片流量结算失败: {e}',
                        exc_info=True,
                    )
        debug_total_up = live_raw_up
        replay_upload_up = 0
        replay_alloc_up = 0
        alloc_input_up = 0
        effective_alloc_up = 0
        filter_wan_pool_up = max(0, int(live_delta_up or 0))
        wan_backlog_applied_bytes = 0
        wan_backlog_before = 0
        if collect_mode == 'lucky':
            alloc_wan_up = allocation_debug.get('wan_upload_bytes')
            if alloc_wan_up is None:
                alloc_wan_up = allocation_debug.get('wan_pool_bytes')
            live_delta_up = max(0, int(alloc_wan_up or live_delta_up or 0))
            filter_wan_pool_up = live_delta_up
            effective_alloc_up = live_delta_up
            alloc_input_up = live_delta_up
        else:
            live_delta_up = 0
            live_delta_dl = 0
        pending = self._pending_lucky_snapshot
        if collect_mode == 'lucky' and pending:
            write_up = max(0, int(effective_alloc_up or 0))
            try:
                _, _, backfill_up, backfill_dl = emby_traffic_db.save_snapshot(
                    self.name,
                    int(pending['last_tx']) + int(pending['raw_up']),
                    int(pending['last_rx']) + int(pending['raw_dl']),
                    record_up=write_up,
                    record_down=int(pending['raw_dl']),
                    is_backfill=bool(pending.get('is_backfill')),
                )
                if pending.get('is_backfill') and write_up > 0:
                    logger.info(
                        f'[Emby:{self.name}] Lucky 离线恢复入账上行='
                        f'{write_up / 1024 / 1024:.2f}MB'
                    )
            except Exception as e:
                logger.debug(
                    f'[Emby:{self.name}] Lucky 快照写入失败: {e}',
                )
            self._pending_lucky_snapshot = None
        debug_wan_up = (
            allocation_debug.get('wan_upload_bytes')
            if allocation_debug.get('wan_upload_bytes') is not None
            else allocation_debug.get('wan_pool_bytes') or 0
        )
        debug_lan_up = (
            allocation_debug.get('lan_upload_bytes')
            if allocation_debug.get('lan_upload_bytes') is not None
            else allocation_debug.get('lan_pool_bytes') or 0
        )
        if wan_only_enabled and debug_total_up > 0 and int(debug_lan_up or 0) <= 0:
            if mode_code == 'M1':
                debug_lan_up = int(debug_total_up)
            else:
                # wan_pool_only 路径仅分摊 WAN 池；M3 调试补齐 LAN 残余便于观测。
                debug_lan_up = max(
                    0, int(debug_total_up) - max(0, int(debug_wan_up or 0)),
                )
        debug_traffic_metrics = _build_debug_traffic_metrics(
            debug_total_up,
            mode_sessions,
            debug_wan_up,
            debug_lan_up,
            allocation_debug.get('program_remainder_bytes')
            if allocation_debug.get('program_remainder_bytes') is not None
            else allocation_debug.get('remainder_bytes') or 0,
            mode_switch_pending_bytes=0,
            mode_switch_replay_bytes=0,
            mode_switch_replay_alloc_bytes=0,
            mode_switch_replay_total_bytes=0,
            mode_switch_replay_alloc_total_bytes=0,
            wan_alloc_backlog_bytes=0,
            wan_alloc_backlog_applied_bytes=0,
            m1_wan_capture_bytes=0,
            wan_only_enabled=wan_only_enabled,
        )
        lucky_conn_debug: dict = {}
        try:
            annotate_kwargs = {}
            sessions = emby_playback_traffic.annotate_live_sessions_upload(
                self.name, sessions, **annotate_kwargs,
            )
        except Exception as e:
            logger.debug(
                f'[Emby:{self.name}] 实时会话上行调试字段附加失败: {e}',
            )
        if collect_mode == 'lucky':
            try:
                verdict_sessions = list(
                    getattr(self, '_wan_client_sessions_last', None) or [],
                )
                if not verdict_sessions:
                    verdict_sessions = list(mode_sessions or [])
                lucky_conn_debug = emby_playback_traffic.get_lucky_conn_debug_snapshot(
                    self.name,
                    verdict_sessions,
                    list(getattr(self, '_lucky_conn_rows_last', None) or []),
                    dict(getattr(self, '_lucky_conn_deltas_last', None) or {}),
                    credit_browse=credit_browse,
                )
            except Exception as e:
                logger.debug(
                    f'[Emby:{self.name}] Lucky 连接调试快照失败: {e}',
                )
        self._apply_upload_anomalies(
            sessions,
            lucky_conn_debug,
            fresh=lucky_sample_ok,
            lucky_ip_traffic=lucky_ip_traffic,
        )
        tick_wan_assigned = max(
            0,
            int(allocation_debug.get('wan_upload_bytes')
                or allocation_debug.get('wan_pool_bytes') or 0),
        )
        self._last_tick_audit = emby_traffic_tick_audit.build_tick_audit(
            mode_code=mode_code,
            live_raw_up=live_raw_up,
            live_delta_up=filter_wan_pool_up,
            alloc_input_up=alloc_input_up,
            effective_alloc_up=effective_alloc_up,
            allocation_debug=allocation_debug,
            wan_backlog_before=wan_backlog_before,
            wan_backlog_after=0,
            wan_backlog_applied=wan_backlog_applied_bytes,
            replay_alloc_up=replay_alloc_up,
            m1_capture_bytes=0,
            mode_switch_pending_bytes=0,
            debug_total_up=debug_total_up,
            debug_wan_up=int(debug_traffic_metrics.get('wan_upload_bytes') or 0),
            debug_lan_up=int(debug_traffic_metrics.get('lan_upload_bytes') or 0),
            debug_remainder_up=int(debug_traffic_metrics.get('program_remainder_bytes') or 0),
            sessions=sessions,
            wan_only_enabled=wan_only_enabled,
            m3_wan_pool_scale=1.0,
            tick_seconds=allocation_tick_seconds,
        )
        if mode_code in ('M2', 'M3') and _has_confirmed_wan_playback(mode_sessions):
            self._verify_cumulative = emby_traffic_tick_audit.merge_cumulative(
                self._verify_cumulative,
                self._last_tick_audit,
                mode_code=mode_code,
                tick_wan_assigned=tick_wan_assigned,
            )
        elif mode_code == 'M0' and not _active_playback_sessions(mode_sessions):
            self._verify_cumulative = {}
        self.monitor.update_live_cache(
            self.name,
            is_online=is_online,
            api_online=api_online,
            lucky_available=lucky_available if collect_mode == 'lucky' else False,
            traffic_collect_mode=collect_mode,
            delta_up=live_delta_up,
            delta_dl=live_delta_dl,
            sessions=_sort_sessions_newest_first(sessions),
            debug_metrics=debug_traffic_metrics,
            traffic_tick_audit={
                'tick': self._last_tick_audit,
                'cumulative': dict(self._verify_cumulative or {}),
            },
            full=full,
            lucky_ip_traffic=lucky_ip_traffic if collect_mode == 'lucky' else None,
            lucky_ip_tick_deltas=lucky_ip_deltas if collect_mode == 'lucky' else None,
            lucky_conn_debug=lucky_conn_debug if collect_mode == 'lucky' else None,
        )
        if full:
            try:
                playback_record_store.accrue_playing_upload(self.name)
            except Exception as e:
                logger.debug(
                    f'[Emby:{self.name}] 播放上行按采集入账失败: {e}',
                )
            try:
                emby_playback_traffic.sync_live_upload_persistence(
                    self.name,
                    list(self._wan_client_sessions_last or sessions or []),
                    credit_browse=credit_browse,
                )
            except Exception as e:
                logger.debug(
                    f'[Emby:{self.name}] 会话流量持久化失败: {e}',
                )
            if api_online:
                try:
                    emby_user_sync.sync_deleted_users(self.name, client)
                except Exception as e:
                    logger.debug(
                        f'[Emby:{self.name}] 用户同步失败: {e}',
                    )
        if not sessions:
            try:
                emby_playback_traffic.clear_instance_live_upload_state(self.name)
            except Exception as e:
                logger.debug(
                    f'[Emby:{self.name}] 无会话时分摊状态清理失败: {e}',
                )
        if collect_mode == 'lucky':
            try:
                refresh_quota_summary(
                    self.name, getattr(client, 'user_quota', {}) or {},
                    credit_browse=credit_browse,
                )
            except Exception as e:
                logger.debug(f'[Emby:{self.name}] 达量摘要预热失败: {e}')
        self._was_online = is_online
        self._recovery_allowed_persist_keys = None


class EmbyMonitor:
    def __init__(self, config: dict, config_path: str = None):
        self.config_path = config_path or config_manager.CONFIG_PATH
        self.config = config
        self.clients: Dict[str, EmbyClient] = {}
        self._workers: Dict[str, EmbyInstanceWorker] = {}
        self._running = False
        self._config_lock = threading.Lock()
        self._live_cache: Dict[str, dict] = {}
        self._live_cache_lock = threading.Lock()
        self._collect_generation: Dict[str, int] = {}
        self._state_generation: Dict[str, int] = {}
        self._live_status_traffic_cache: Dict[str, dict] = {}
        self._live_status_traffic_cache_at = 0.0
        self._live_status_traffic_cache_lock = threading.Lock()
        self._live_status_traffic_cache_ttl = 1.0
        self._apply_global_config()
        self._init_clients()

    def _apply_global_config(self):
        self.global_cfg = config_manager.get_global_config(self.config)
        try:
            refresh = int(self.global_cfg.get('refresh_interval', 1))
        except (TypeError, ValueError):
            refresh = 1
        refresh = max(
            config_manager.REFRESH_INTERVAL_MIN,
            min(config_manager.REFRESH_INTERVAL_MAX, refresh),
        )
        self.refresh_interval = refresh
        self.collect_interval = config_manager.collect_interval_for_refresh(refresh)
        self._live_status_traffic_cache_ttl = float(self.collect_interval)
        set_summary_ttl(self.collect_interval)
        self.preplay_burst_mbps = config_manager.clamp_emby_preplay_burst_mbps(
            self.global_cfg.get('emby_preplay_burst_mbps', 1.5),
        )
        self.preplay_burst_window_seconds = (
            config_manager.clamp_emby_preplay_burst_window_seconds(
                self.global_cfg.get('emby_preplay_burst_window_seconds', 3),
            )
        )
        try:
            import emby.traffic.playback as emby_playback_traffic
            emby_playback_traffic.set_browse_stream_burst_bps(
                config_manager.emby_preplay_burst_bps(self.global_cfg),
            )
            emby_playback_traffic.set_browse_stream_burst_window_seconds(
                self.preplay_burst_window_seconds,
            )
        except Exception as e:
            logger.debug(f'推流突发识别阈值配置同步失败: {e}')
        tz_name = self.global_cfg.get('timezone', 'Asia/Shanghai')
        try:
            self.timezone = ZoneInfo(tz_name)
        except Exception:
            self.timezone = ZoneInfo('Asia/Shanghai')
        traffic_db.set_timezone(self.timezone)

    def _now(self) -> datetime:
        return datetime.now(self.timezone)

    def _init_clients(self):
        if not self.global_cfg.get('emby_enabled', False):
            return
        for inst_cfg in self.config.get('emby_instances', []):
            name = inst_cfg['name']
            self.clients[name] = EmbyClient(inst_cfg)
            logger.info(
                f'初始化 Emby 实例: {name} ({inst_cfg.get("host")}:'
                f'{inst_cfg.get("port", 8096)})'
            )

    def apply_config(self, new_config: dict) -> bool:
        try:
            with self._config_lock:
                new_config = config_manager.enrich_config(new_config or {})
                self.config = new_config
                self._apply_global_config()
                enabled = bool(self.global_cfg.get('emby_enabled', False))
                new_instances = {
                    i['name']: i for i in (new_config.get('emby_instances') or [])
                } if enabled else {}
                for name in list(self.clients.keys()):
                    if name not in new_instances:
                        del self.clients[name]
                for name, inst_cfg in new_instances.items():
                    if name in self.clients:
                        self.clients[name].update_config(inst_cfg)
                    else:
                        self.clients[name] = EmbyClient(inst_cfg)
            self._sync_workers()
            return True
        except Exception as e:
            logger.error(f'Emby 配置应用失败: {e}', exc_info=True)
            return False

    def reload_config(self):
        try:
            new_config = config_manager.load_runtime_config(self.config_path)
            return self.apply_config(new_config)
        except Exception as e:
            logger.error(f'Emby 配置热重载失败: {e}', exc_info=True)
            return False

    def _sync_workers(self):
        enabled = bool(self.global_cfg.get('emby_enabled', False))
        with self._config_lock:
            names = set(self.clients.keys()) if enabled else set()
        for name in list(self._workers.keys()):
            if name not in names:
                self._workers[name].stop()
                del self._workers[name]
        if not enabled:
            return
        for name in names:
            if name not in self._workers:
                worker = EmbyInstanceWorker(self, name)
                self._workers[name] = worker
                if self._running:
                    worker.start()
            elif self._running:
                self._workers[name].wake()

    def start(self):
        self._running = True
        self._sync_workers()
        logger.info(
            f'Emby 监控已启动（数据采集 {clamp_interval(self.collect_interval)}s，'
            f'轻量探测 {clamp_interval(self.refresh_interval)}s）'
        )

    def stop(self):
        self._running = False
        for worker in self._workers.values():
            worker.stop()
        self._workers.clear()

    def _bump_collect_generation(self, name: str) -> int:
        with self._live_cache_lock:
            val = self._collect_generation.get(name, 0) + 1
            self._collect_generation[name] = val
            return val

    def _bump_state_generation(self, name: str) -> int:
        with self._live_cache_lock:
            val = self._state_generation.get(name, 0) + 1
            self._state_generation[name] = val
            return val

    def update_live_cache(self, name: str, is_online: bool, api_online: bool,
                          delta_up: int, delta_dl: int,
                          sessions: list, debug_metrics: dict, full: bool,
                          traffic_tick_audit: dict = None,
                          lucky_available: bool = False,
                          traffic_collect_mode: str = '',
                          lucky_ip_traffic: dict = None,
                          lucky_ip_tick_deltas: dict = None,
                          lucky_conn_debug: dict = None):
        with self._live_cache_lock:
            prev = self._live_cache.get(name, {})
            prev_api_online = prev.get('api_online', False)
            offline_since = None
            online_since = None
            if api_online:
                online_since = _online_since_from_prev(prev, prev_api_online)
            else:
                if prev_api_online:
                    offline_since = emby_traffic_db._now().strftime('%Y-%m-%d %H:%M:%S')
                else:
                    offline_since = prev.get('offline_since')
                    if not offline_since:
                        offline_since = emby_traffic_db._now().strftime('%Y-%m-%d %H:%M:%S')
            entry = {
                'name': name,
                'is_online': is_online,
                'api_online': api_online,
                'lucky_available': lucky_available,
                'traffic_collect_mode': traffic_collect_mode or '',
                'online_since': online_since,
                'offline_since': offline_since,
                'recent_delta_bytes': delta_up,
                'recent_delta_download_bytes': delta_dl,
                'session_count': len(sessions),
                'sessions': sessions,
                'debug_traffic_metrics': dict(debug_metrics or {}),
                'traffic_tick_audit': dict(traffic_tick_audit or {}),
                'collect_generation': self._collect_generation.get(name, 0),
                'state_generation': self._state_generation.get(name, 0),
            }
            if lucky_ip_traffic is not None:
                entry['lucky_ip_traffic'] = dict(lucky_ip_traffic or {})
            elif prev.get('lucky_ip_traffic'):
                entry['lucky_ip_traffic'] = dict(prev.get('lucky_ip_traffic') or {})
            if lucky_ip_tick_deltas is not None:
                entry['lucky_ip_tick_deltas'] = {
                    str(k): max(0, int(v or 0))
                    for k, v in (lucky_ip_tick_deltas or {}).items()
                    if str(k).strip()
                }
            elif prev.get('lucky_ip_tick_deltas'):
                entry['lucky_ip_tick_deltas'] = dict(prev.get('lucky_ip_tick_deltas') or {})
            if lucky_conn_debug is not None:
                entry['lucky_conn_debug'] = dict(lucky_conn_debug or {})
            elif prev.get('lucky_conn_debug'):
                prev_debug = prev.get('lucky_conn_debug')
                if isinstance(prev_debug, dict):
                    entry['lucky_conn_debug'] = dict(prev_debug)
                elif isinstance(prev_debug, list):
                    entry['lucky_conn_debug'] = {
                        'version': 2,
                        'groups': [],
                        'rows': list(prev_debug),
                        'emby_without_lucky': [],
                        'total_connections': len(prev_debug or []),
                    }
            self._live_cache[name] = entry
        if full:
            self._bump_collect_generation(name)
        self._bump_state_generation(name)

    def _get_live_status_traffic_batch(self, clients: dict) -> tuple:
        now_mono = time.monotonic()
        names = list(clients.keys())
        now = self._now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_key = (
            today_start.isoformat(),
            yesterday_start.isoformat(),
            month_start.isoformat(),
        )
        with self._live_status_traffic_cache_lock:
            if (
                names
                and self._live_status_traffic_cache
                and (now_mono - self._live_status_traffic_cache_at)
                < self._live_status_traffic_cache_ttl
                and self._live_status_traffic_cache.get('_period_key') == period_key
                and set(
                    n for n in self._live_status_traffic_cache.keys()
                    if not str(n).startswith('_')
                ) >= set(names)
            ):
                return (
                    {
                        name: dict(self._live_status_traffic_cache.get(name) or {})
                        for name in names
                    },
                    dict(self._live_status_traffic_cache.get('_status_map') or {}),
                    dict(self._live_status_traffic_cache.get('_data_starts') or {}),
                )

            credit_browse_map = {
                name: (
                    client.traffic_collect_mode == 'lucky'
                    and bool(client.lucky_credit_browse_traffic)
                )
                for name, client in clients.items()
            }
            snap = emby_traffic_db.get_live_status_snapshot(
                names, today_start, yesterday_start, month_start,
            )
            traffic_batch = {}
            for name in names:
                credit = bool(credit_browse_map.get(name))
                today_up = int(snap['playback_today'].get(name) or 0)
                yesterday_base = int(snap['playback_yesterday'].get(name) or 0)
                month_up = int(snap['playback_month'].get(name) or 0)
                device_up = int(snap['playback_total'].get(name) or 0)
                if credit:
                    today_up += int(snap['browse_today'].get(name) or 0)
                    yesterday_base += int(snap['browse_yesterday'].get(name) or 0)
                    month_up += int(snap['browse_month'].get(name) or 0)
                    device_up += int(snap['browse_total'].get(name) or 0)
                today_dl = int(snap['download_today'].get(name) or 0)
                yesterday_base_dl = int(snap['download_yesterday'].get(name) or 0)
                traffic_batch[name] = {
                    'today_upload': today_up,
                    'today_download': today_dl,
                    'yesterday_upload': max(0, yesterday_base - today_up),
                    'yesterday_download': max(0, yesterday_base_dl - today_dl),
                    'month_upload': month_up,
                    'month_download': int(snap['download_month'].get(name) or 0),
                    'device_upload': device_up,
                    'device_download': int(snap['download_total'].get(name) or 0),
                }
            status_map = dict(snap.get('status_map') or {})
            data_starts = dict(snap.get('data_starts') or {})
            cached = dict(traffic_batch)
            cached['_period_key'] = period_key
            cached['_status_map'] = status_map
            cached['_data_starts'] = data_starts
            self._live_status_traffic_cache = cached
            self._live_status_traffic_cache_at = now_mono
            return traffic_batch, status_map, data_starts

    def get_live_status_summary(self) -> list:
        with self._live_cache_lock:
            cache = {k: dict(v) for k, v in self._live_cache.items()}
        result = []
        with self._config_lock:
            clients = dict(self.clients)
        traffic_batch, status_map, data_starts = self._get_live_status_traffic_batch(
            clients,
        )
        for name, client in clients.items():
            live = cache.get(name, {})
            status = status_map.get(name, {})
            traffic = traffic_batch.get(name, {})
            credit_browse = (
                client.traffic_collect_mode == 'lucky'
                and bool(client.lucky_credit_browse_traffic)
            )
            today_up = int(traffic.get('today_upload') or 0)
            today_dl = int(traffic.get('today_download') or 0)
            yesterday_up = int(traffic.get('yesterday_upload') or 0)
            yesterday_dl = int(traffic.get('yesterday_download') or 0)
            month_up = int(traffic.get('month_upload') or 0)
            month_dl = int(traffic.get('month_download') or 0)
            device_up = int(traffic.get('device_upload') or 0)
            device_dl = int(traffic.get('device_download') or 0)

            api_online = live.get('api_online', status.get('api_online', 0) == 1)
            raw_data_start = data_starts.get(name)
            data_start_time = (
                traffic_db._format_datetime_seconds(raw_data_start)
                if raw_data_start else None
            )
            offline_since = None
            online_since = None
            if api_online:
                raw_online = live.get('online_since')
                if raw_online:
                    online_since = traffic_db._format_datetime_seconds(raw_online)
            else:
                raw_offline = live.get('offline_since') or status.get('last_update')
                if raw_offline:
                    offline_since = traffic_db._format_datetime_seconds(raw_offline)

            result.append({
                **live,
                'name': name,
                'host': client.host,
                'port': client.port,
                'use_https': client.use_https,
                'container_name': '',
                'container_id': '',
                'display_priority': client.display_priority,
                'wan_traffic_only': client.wan_traffic_only,
                'traffic_collect_mode': client.traffic_collect_mode,
                'lucky_rule_label': client.lucky_rule_label,
                'lucky_credit_browse_traffic': client.lucky_credit_browse_traffic,
                'user_quota_status': quota_summary(
                    name, client.user_quota,
                    credit_browse=credit_browse,
                ) if client.traffic_collect_mode == 'lucky' else {'enabled': False},
                'is_online': live.get('is_online', status.get('is_online', 0) == 1),
                'api_online': api_online,
                'offline_since': offline_since,
                'online_since': online_since,
                'data_start_time': data_start_time,
                'lucky_available': live.get('lucky_available', False),
                'traffic_collect_mode': live.get(
                    'traffic_collect_mode', client.traffic_collect_mode),
                'monthly_uploaded_bytes': month_up,
                'monthly_downloaded_bytes': month_dl,
                'today_uploaded_bytes': today_up,
                'today_downloaded_bytes': today_dl,
                'yesterday_uploaded_bytes': yesterday_up,
                'yesterday_downloaded_bytes': yesterday_dl,
                'device_uploaded_bytes': device_up,
                'device_downloaded_bytes': device_dl,
                'recent_delta_bytes': live.get('recent_delta_bytes', 0),
                'recent_delta_download_bytes': live.get(
                    'recent_delta_download_bytes', 0),
                'session_count': live.get('session_count', 0),
                'sessions': live.get('sessions') or [],
                'lucky_ip_traffic': live.get('lucky_ip_traffic') or {},
                'lucky_ip_tick_deltas': live.get('lucky_ip_tick_deltas') or {},
                'traffic_tick_audit': live.get('traffic_tick_audit') or {},
                'collect_interval': self.collect_interval,
                'refresh_interval': self.refresh_interval,
                'last_update': status.get('last_update'),
                'collect_generation': live.get(
                    'collect_generation', self._collect_generation.get(name, 0)),
                'state_generation': live.get(
                    'state_generation', self._state_generation.get(name, 0)),
            })
        result.sort(key=lambda x: (x.get('display_priority', 500), x.get('name', '')))
        return result

    def get_cached_sessions(self, instance_name: str = None) -> list:
        """已缓存的播放会话，不现场请求 Emby。"""
        with self._live_cache_lock:
            cache = {k: dict(v) for k, v in self._live_cache.items()}
        rows = []
        for name, live in cache.items():
            if instance_name and name != instance_name:
                continue
            for session in live.get('sessions') or []:
                if isinstance(session, dict):
                    rows.append({'instance_name': name, **session})
        rows.sort(key=lambda r: (r.get('instance_name', ''), r.get('user_name', '')))
        return rows

    def get_status_summary(self) -> list:
        return self.get_live_status_summary()

    def get_traffic_verify_summary(self, instance_name: str = None) -> list:
        with self._live_cache_lock:
            cache = {k: dict(v) for k, v in self._live_cache.items()}
        rows = []
        for name, live in cache.items():
            if instance_name and name != instance_name:
                continue
            audit = live.get('traffic_tick_audit') or {}
            tick = audit.get('tick') or {}
            cumulative = audit.get('cumulative') or {}
            metrics = live.get('debug_traffic_metrics') or {}
            rows.append({
                'instance_name': name,
                'mode_code': metrics.get('mode_code') or '',
                'mode_label': metrics.get('mode_label') or '',
                'tick_passed': bool(tick.get('passed')),
                'tick_failed_count': int(tick.get('failed_count') or 0),
                'tick_checks': tick.get('checks') or [],
                'tick_failed_checks': tick.get('failed_checks') or [],
                'tick_inputs': tick.get('inputs') or {},
                'tick_outputs': tick.get('outputs') or {},
                'cumulative': cumulative,
                'debug_traffic_metrics': metrics,
                'recent_delta_bytes': int(live.get('recent_delta_bytes') or 0),
            })
        rows.sort(key=lambda r: r.get('instance_name') or '')
        return rows

    def reset_traffic_verify(self, instance_name: str):
        worker = self._workers.get(instance_name)
        if worker:
            worker._verify_cumulative = {}
            worker._last_tick_audit = {}
            worker.wake()

    def reset_traffic_stats(self, instance_name: str):
        if not instance_name:
            raise ValueError('参数缺失')
        with self._config_lock:
            if not config_manager.get_emby_instance(instance_name, self.config):
                raise ValueError('设备不存在')
        emby_traffic_db.reset_instance_traffic(instance_name)
        emby_traffic_db.clear_lucky_ip_baselines(instance_name)
        try:
            emby_playback_traffic.clear_persisted_live_upload_state(instance_name)
        except Exception:
            logger.warning(
                '清除 Emby 播放上行状态失败: %s',
                instance_name,
                exc_info=True,
            )
        try:
            import emby.browse.settler as browse_upload_settler
            browse_upload_settler.clear_instance(instance_name)
        except Exception:
            logger.warning(
                '清除 Emby 选片状态失败: %s',
                instance_name,
                exc_info=True,
            )
        worker = self._workers.get(instance_name)
        if worker:
            worker._live_upload_hydrated = False
            worker._lucky_ip_baselines = {}
            worker._lucky_conn_baselines = {}
            worker._lucky_conn_rows_last = []
            worker._lucky_conn_deltas_last = {}
            worker._lucky_ip_traffic_last = {}
            worker._lucky_ip_deltas_last = {}
            worker._wan_client_sessions_last = []
            worker._lucky_total_out = 0
            worker._lucky_total_in = 0
            worker.wake()
        self._bump_collect_generation(instance_name)
        self._bump_state_generation(instance_name)
        logger.info(f'Emby 流量统计重置完成: {instance_name}')

    def wake_all(self):
        for worker in self._workers.values():
            worker.wake()
