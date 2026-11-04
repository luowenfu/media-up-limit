"""Lucky 连接 + Emby 会话统一裁决（调试展示与入账绑定共用）。"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from emby.lucky.api import parse_accept_time_epoch
from emby.traffic.filter import (
    filter_superseded_wan_sessions,
    is_wan_remote_session,
    parse_endpoint_ip,
    playback_accumulator_key,
)

_ACCEPT_TIME_MATCH_SECONDS = 120
_STALE_CONN_BEFORE_SEGMENT_SECONDS = 30 * 60
_CONTROL_MAX_BYTES = 8 * 1024
_BROWSE_MIN_BYTES = 64 * 1024
_STREAM_TICK_BYTES = 200 * 1024
_STREAM_CUMULATIVE_BYTES = 512 * 1024
_SATELLITE_ACCEPT_SECONDS = 2.0
_STICKY_BINDING_BONUS = 120.0
_AMBIGUOUS_SCORE_GAP = 50.0
_WAVE_ALIGN_BONUS = 40.0
_WAVE_ASSIGN_MIN_SCORE = 80.0
_DUAL_PLAYING_MAX_DELTA = 90.0
_DUAL_PLAYING_TIME_PENALTY = 100.0
_DEFAULT_TICK_SECONDS = 2.0
_ACTIVITY_FRESHNESS_WINDOW = 90.0
_ACTIVITY_FRESHNESS_BONUS = 30.0
_ACTIVITY_FRESHNESS_PEER_BONUS = 15.0
_ACTIVITY_FRESHNESS_PEER_GAP = 60.0
_BROWSE_LEGACY_PREFIX = 'browse:sid:'
_BROWSE_RECENT_ACTIVITY_SECONDS = 600.0
_BROWSE_CONFIDENCE_HIGH_AGE = 120.0
_BROWSE_CONFIDENCE_MEDIUM_AGE = 300.0

_CONN_ROLE_LABELS = {
    'control': '保活',
    'browse': '选片',
    'stream_pending': '推流候选',
    'stream_primary': '主推流',
    'stream_secondary': '副推流',
}
_EMBY_MODE_LABELS = {
    'playing': '正在播',
    'paused': '已暂停',
    'viewing': '选片',
    'connected': '在线',
    'orphan': '无会话',
}
_BILLING_LABELS = {
    'credited': '已入账',
    'browse_credited': '选片入账',
    'excluded': '不入账',
    'pending': '待确认',
    'orphan': '孤立',
}
_CONFIDENCE_LABELS = {
    'high': '高',
    'medium': '中',
    'low': '低',
}


def _parse_iso_epoch_seconds(value: str) -> Optional[float]:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def persist_key_for_session(session: dict) -> str:
    key = playback_accumulator_key(session)
    if key:
        return key
    sid = str(
        session.get('emby_session_id')
        or session.get('session_id')
        or session.get('id')
        or '',
    ).strip()
    if sid:
        return f'sid:{sid}'
    return ''


def browse_persist_key_for_session(session: dict) -> str:
    uid = str(
        session.get('user_id') or session.get('UserId') or '',
    ).strip()
    sid = str(
        session.get('emby_session_id')
        or session.get('session_id')
        or session.get('id')
        or '',
    ).strip()
    if uid and sid:
        return f'browse:{uid}:{sid}'
    if sid:
        return f'{_BROWSE_LEGACY_PREFIX}{sid}'
    return ''


def legacy_browse_persist_key_for_session(session: dict) -> str:
    sid = str(
        session.get('emby_session_id')
        or session.get('session_id')
        or session.get('id')
        or '',
    ).strip()
    return f'{_BROWSE_LEGACY_PREFIX}{sid}' if sid else ''


def browse_persist_key_variants_for_session(session: dict) -> List[str]:
    keys: List[str] = []
    primary = browse_persist_key_for_session(session)
    if primary:
        keys.append(primary)
    legacy = legacy_browse_persist_key_for_session(session)
    if legacy and legacy not in keys:
        keys.append(legacy)
    return keys


def sid_from_browse_persist_key(key: str) -> str:
    text = str(key or '').strip()
    if text.startswith(_BROWSE_LEGACY_PREFIX):
        return text[len(_BROWSE_LEGACY_PREFIX):]
    if text.startswith('browse:'):
        parts = text.split(':')
        if len(parts) >= 3:
            return str(parts[-1] or '').strip()
    return ''


def user_from_persist_key(pkey: str) -> str:
    text = str(pkey or '').strip()
    if not text or text.startswith('browse:') or text.startswith('sid:'):
        return ''
    if '|' in text:
        return text.split('|', 1)[0].strip().casefold()
    return ''


def persist_key_belongs_to_user(
    persist_key: str,
    user_fold: str,
    user_ids: set,
) -> bool:
    text = str(persist_key or '').strip()
    fold = str(user_fold or '').strip().casefold()
    if not text or not fold:
        return False
    if text.startswith('browse:'):
        parts = text.split(':')
        if len(parts) >= 3:
            return str(parts[1] or '').strip() in (user_ids or set())
        return False
    bound = user_from_persist_key(text)
    if bound and bound == fold:
        return True
    if text.startswith(f'{fold}|'):
        return True
    return False


def _session_has_viewing_item(session: Optional[dict]) -> bool:
    if not session or not isinstance(session, dict):
        return False
    if str(session.get('viewing_item_id') or '').strip():
        return True
    return bool(str(session.get('viewing_title') or '').strip())


def should_browse_credit_billing(
    session: Optional[dict],
    emby_mode: str,
    traffic_role: str,
    *,
    credit_browse: bool,
    instance_name: str = '',
) -> bool:
    """Lucky 选片流量计入开启时，判定连接是否应按选片入账。"""
    if not credit_browse or traffic_role == 'control':
        return False
    mode = str(emby_mode or '').strip()
    if mode == 'viewing':
        return True
    if _session_has_viewing_item(session):
        return True
    if mode == 'connected' and traffic_role == 'browse':
        if instance_name:
            import emby.browse.continuous as emby_continuous_playback
            if emby_continuous_playback.should_suppress_connected_browse_credit(
                instance_name, session,
            ):
                return False
        return True
    return False


def is_wan_browse_session(session: dict, *, credit_browse: bool = True) -> bool:
    """外网选片会话（供 Lucky 分摊池纳入）。"""
    if not credit_browse or not isinstance(session, dict):
        return False
    if not is_wan_remote_session(session):
        return False
    mode = str(session.get('session_mode') or '').strip()
    if mode == 'viewing':
        return True
    return _session_has_viewing_item(session)


def _session_started_epoch(session: dict) -> Optional[float]:
    for field in ('playback_started_at', 'started_at'):
        epoch = _parse_iso_epoch_seconds(session.get(field) or '')
        if epoch is not None:
            return epoch
    return None


def _session_active_epoch(session: dict) -> Optional[float]:
    return _parse_iso_epoch_seconds(
        (session or {}).get('last_activity_date') or '',
    )


def session_activity_epoch(session: dict) -> Optional[float]:
    mode = str((session or {}).get('session_mode') or '').strip()
    if mode in ('playing', 'paused'):
        started = _session_started_epoch(session)
        if started is not None:
            return started
    activity = _session_active_epoch(session)
    if activity is not None:
        return activity
    return _session_started_epoch(session)


def _session_activity_age_seconds(
    session: dict,
    now_epoch: Optional[float] = None,
) -> Optional[float]:
    epoch = _session_active_epoch(session)
    if epoch is None:
        return None
    now = (
        float(now_epoch)
        if now_epoch is not None
        else datetime.now(timezone.utc).timestamp()
    )
    return max(0.0, now - epoch)


def _is_browse_like_session(session: dict) -> bool:
    mode = str((session or {}).get('session_mode') or '').strip()
    if mode in ('viewing', 'connected'):
        return True
    return _session_has_viewing_item(session)


def conn_accept_epoch(conn: dict) -> Optional[float]:
    epoch = conn.get('accept_epoch')
    if epoch is not None:
        try:
            return float(epoch)
        except (TypeError, ValueError):
            pass
    return parse_accept_time_epoch(conn.get('accept_time') or '')


def _time_match_score(
    accept_at: Optional[float],
    session: dict,
    *,
    traffic_role: str = '',
    delta_out: int = 0,
    now_epoch: Optional[float] = None,
) -> Tuple[float, float, List[str]]:
    if accept_at is None:
        return 0.0, -1.0, []
    now = (
        float(now_epoch)
        if now_epoch is not None
        else datetime.now(timezone.utc).timestamp()
    )
    mode = str((session or {}).get('session_mode') or '').strip()
    play_epoch = _session_started_epoch(session)
    active_epoch = _session_active_epoch(session)
    activity_age = _session_activity_age_seconds(session, now)
    browse_like = _is_browse_like_session(session)
    ongoing_browse = (
        str(traffic_role or '').strip() == 'browse'
        and max(0, int(delta_out or 0)) > 0
    )
    recent_activity = (
        activity_age is not None
        and activity_age <= _BROWSE_RECENT_ACTIVITY_SECONDS
    )
    details: List[str] = []
    score = 0.0
    best_delta = -1.0

    def _apply_delta(delta: float, cap: float, weight: float, label: str) -> None:
        nonlocal score, best_delta
        if delta < 0:
            return
        if best_delta < 0 or delta < best_delta:
            best_delta = delta
        part = max(0.0, weight - min(delta, cap))
        if part > 0:
            score += part
            details.append(f'{label} {int(delta)}s +{int(part):.0f}')

    anchor_epoch = play_epoch or active_epoch
    if (
        mode != 'paused'
        and anchor_epoch is not None
        and accept_at < anchor_epoch - _STALE_CONN_BEFORE_SEGMENT_SECONDS
        and not (browse_like and (recent_activity or ongoing_browse))
    ):
        return -1.0, best_delta, ['建连早于会话过久']

    if mode in ('playing', 'paused'):
        if play_epoch is not None:
            _apply_delta(abs(accept_at - play_epoch), 120.0, 200.0, '播放开始')
        if active_epoch is not None:
            _apply_delta(abs(accept_at - active_epoch), 300.0, 50.0, '最后活动')
    elif mode == 'viewing':
        if active_epoch is not None:
            _apply_delta(abs(accept_at - active_epoch), 180.0, 150.0, '选片活动')
    elif active_epoch is not None:
        _apply_delta(abs(accept_at - active_epoch), 300.0, 80.0, '在线活动')

    if browse_like and activity_age is not None and (recent_activity or ongoing_browse):
        recency_delta = activity_age
        recency_score = max(0.0, 150.0 - min(recency_delta, 180.0))
        if recency_score > score:
            score = recency_score
            details.append(
                f'选片近期活动 {int(recency_delta)}s +{int(recency_score):.0f}',
            )
        if best_delta < 0 or recency_delta < best_delta:
            best_delta = recency_delta

    if (
        mode == 'playing'
        and max(0, int(delta_out or 0)) >= _STREAM_TICK_BYTES
        and activity_age is not None
        and activity_age <= _ACTIVITY_FRESHNESS_WINDOW
    ):
        active_bonus = max(
            0.0,
            100.0 - min(activity_age, _ACTIVITY_FRESHNESS_WINDOW),
        )
        if active_bonus > 0:
            score += active_bonus
            details.append(f'播放活跃 +{int(active_bonus):.0f}')
            if best_delta < 0 or activity_age < best_delta:
                best_delta = activity_age

    if best_delta > _ACCEPT_TIME_MATCH_SECONDS and score <= 0:
        return max(0.0, 200.0 - best_delta), best_delta, details

    return score, best_delta, details


def _mode_consistency_score(traffic_role: str, emby_mode: str) -> Tuple[float, List[str]]:
    if emby_mode == 'orphan':
        return 0.0, []
    if traffic_role == 'stream_pending' and emby_mode == 'playing':
        return 300.0, ['模式一致(推流↔播放) +300']
    if traffic_role == 'browse' and emby_mode in ('viewing', 'connected'):
        return 250.0, ['模式一致(浏览↔选片/在线) +250']
    if traffic_role == 'browse' and emby_mode == 'paused':
        return 180.0, ['模式一致(浏览↔暂停) +180']
    if traffic_role == 'control':
        return 50.0, ['保活连接 +50']
    if traffic_role == 'browse' and emby_mode == 'playing':
        return 80.0, ['播放中副流量 +80']
    if traffic_role == 'stream_pending' and emby_mode == 'viewing':
        return -200.0, ['推流流量不应匹配选片 -200']
    return 0.0, []


def _traffic_scale_score(
    traffic_out: int,
    delta_out: int,
    traffic_role: str,
    emby_mode: str,
) -> Tuple[float, List[str]]:
    if traffic_role != 'stream_pending' or emby_mode != 'playing':
        return 0.0, []
    if traffic_out <= 0:
        return 0.0, []
    part = min(150.0, math.log10(max(traffic_out, 1)) * 30.0)
    if delta_out > 0:
        part += min(40.0, math.log10(max(delta_out, 1)) * 12.0)
    return part, [f'流量规模 +{int(part):.0f}']


def _bitrate_plausibility_score(
    delta_out: int,
    session: dict,
    *,
    tick_seconds: float = _DEFAULT_TICK_SECONDS,
) -> Tuple[float, List[str]]:
    if delta_out <= 0:
        return 0.0, []
    bitrate = int(session.get('bitrate') or 0)
    if bitrate <= 0:
        return 0.0, []
    expected = (bitrate / 8.0) * max(0.5, tick_seconds)
    if expected <= 0:
        return 0.0, []
    ratio = delta_out / expected
    if 0.15 <= ratio <= 6.0:
        return 40.0, ['码率合理 +40']
    if ratio > 6.0:
        return 15.0, ['码率偏高 +15']
    return 0.0, []


def _sticky_binding_score(
    remote_addr: str,
    session: dict,
    bindings: Dict[str, str],
    match_hints: Optional[Dict[str, str]] = None,
) -> Tuple[float, List[str]]:
    pkey = persist_key_for_session(session)
    if not pkey:
        return 0.0, []
    addr = str(remote_addr or '').strip()
    sess_user = str(session.get('user_name') or '').strip().casefold()
    for label, store in (
        ('入账绑定', bindings or {}),
        ('匹配记忆', match_hints or {}),
    ):
        bound = str(store.get(addr) or '').strip()
        if not bound or bound != pkey:
            continue
        bound_user = user_from_persist_key(bound)
        if bound_user and sess_user and bound_user != sess_user:
            continue
        return _STICKY_BINDING_BONUS, [
            f'{label}粘性 +{int(_STICKY_BINDING_BONUS):.0f}',
        ]
    return 0.0, []


def _activity_freshness_bonus(
    session: dict,
    peer_sessions: Optional[List[dict]] = None,
) -> Tuple[float, List[str]]:
    epoch = session_activity_epoch(session)
    if epoch is None:
        return 0.0, []
    now = datetime.now(timezone.utc).timestamp()
    age = now - epoch
    details: List[str] = []
    bonus = 0.0
    if age <= _ACTIVITY_FRESHNESS_WINDOW:
        bonus += _ACTIVITY_FRESHNESS_BONUS
        details.append(f'近期活动 +{int(_ACTIVITY_FRESHNESS_BONUS):.0f}')

    sess_uid = str(session.get('user_id') or '').strip()
    if peer_sessions and sess_uid:
        peer_epochs: List[float] = []
        for peer in peer_sessions:
            if peer is session:
                continue
            peer_uid = str(peer.get('user_id') or '').strip()
            if not peer_uid or peer_uid == sess_uid:
                continue
            peer_epoch = session_activity_epoch(peer)
            if peer_epoch is not None:
                peer_epochs.append(peer_epoch)
        if peer_epochs and epoch >= max(peer_epochs) + _ACTIVITY_FRESHNESS_PEER_GAP:
            bonus += _ACTIVITY_FRESHNESS_PEER_BONUS
            details.append(
                f'同设备最新用户 +{int(_ACTIVITY_FRESHNESS_PEER_BONUS):.0f}',
            )
    return bonus, details


def _dual_playing_time_penalty(
    time_delta: float,
    traffic_role: str,
    emby_mode: str,
    playing_session_count: int,
) -> Tuple[float, List[str]]:
    if playing_session_count < 2:
        return 0.0, []
    if traffic_role != 'stream_pending' or emby_mode != 'playing':
        return 0.0, []
    if time_delta < 0 or time_delta <= _DUAL_PLAYING_MAX_DELTA:
        return 0.0, []
    return -_DUAL_PLAYING_TIME_PENALTY, [
        f'同 IP 多路播放时差 {int(time_delta)}s -{int(_DUAL_PLAYING_TIME_PENALTY):.0f}',
    ]


def score_conn_for_session(
    conn: dict,
    session: dict,
    *,
    traffic_role: str = '',
    delta_out: int = 0,
    bindings: Optional[Dict[str, str]] = None,
) -> float:
    total, _, _ = score_conn_for_session_detail(
        conn,
        session,
        traffic_role=traffic_role,
        delta_out=delta_out,
        bindings=bindings,
    )
    return total


def score_conn_for_session_detail(
    conn: dict,
    session: dict,
    *,
    traffic_role: str = '',
    delta_out: int = 0,
    bindings: Optional[Dict[str, str]] = None,
    match_hints: Optional[Dict[str, str]] = None,
    wave_accept_epoch: Optional[float] = None,
    playing_session_count: int = 0,
    peer_sessions: Optional[List[dict]] = None,
) -> Tuple[float, float, List[str]]:
    accept_at = conn_accept_epoch(conn)
    role = traffic_role or _traffic_role(conn, delta_out, is_satellite=False)
    emby_mode = str((session or {}).get('session_mode') or 'connected').strip()
    traffic_out = max(0, int(conn.get('traffic_out') or 0))

    time_score, time_delta, time_details = _time_match_score(
        accept_at, session, traffic_role=role, delta_out=delta_out,
    )
    if wave_accept_epoch is not None and accept_at is not None:
        wave_score, wave_td, wave_details = _time_match_score(
            wave_accept_epoch, session, traffic_role=role, delta_out=delta_out,
        )
        if wave_score > time_score:
            time_score = wave_score + _WAVE_ALIGN_BONUS
            time_delta = wave_td
            time_details = [f'波次时刻对齐 +{_WAVE_ALIGN_BONUS:.0f}'] + wave_details
        elif abs(wave_accept_epoch - accept_at) <= _SATELLITE_ACCEPT_SECONDS:
            bonus = min(_WAVE_ALIGN_BONUS, 25.0)
            time_score += bonus
            time_details.append(f'波次内建连 +{int(bonus):.0f}')
            if wave_td >= 0 and (time_delta < 0 or wave_td < time_delta):
                time_delta = wave_td

    sticky_score, sticky_details = _sticky_binding_score(
        str(conn.get('remote_addr') or '').strip(),
        session,
        bindings or {},
        match_hints,
    )
    if time_score < 0:
        if sticky_score > 0:
            details = list(time_details) + sticky_details + ['粘性绑定覆盖时差拒绝']
            return sticky_score, time_delta, details
        return time_score, time_delta, time_details

    details = list(time_details)
    total = time_score

    mode_score, mode_details = _mode_consistency_score(role, emby_mode)
    total += mode_score
    details.extend(mode_details)

    scale_score, scale_details = _traffic_scale_score(
        traffic_out, delta_out, role, emby_mode,
    )
    total += scale_score
    details.extend(scale_details)

    bitrate_score, bitrate_details = _bitrate_plausibility_score(delta_out, session)
    total += bitrate_score
    details.extend(bitrate_details)

    freshness_score, freshness_details = _activity_freshness_bonus(
        session, peer_sessions,
    )
    total += freshness_score
    details.extend(freshness_details)

    total += sticky_score
    details.extend(sticky_details)

    dual_penalty, dual_details = _dual_playing_time_penalty(
        time_delta, role, emby_mode, playing_session_count,
    )
    total += dual_penalty
    details.extend(dual_details)

    return total, time_delta, details


def _session_media_label(session: dict) -> str:
    mode = str((session or {}).get('session_mode') or '').strip()
    if mode == 'viewing':
        series = str(session.get('viewing_series_name') or '').strip()
        label = str(session.get('viewing_episode_label') or '').strip()
        title = str(session.get('viewing_title') or '').strip()
    else:
        series = str(session.get('series_name') or '').strip()
        label = str(session.get('episode_label') or '').strip()
        title = str(
            session.get('episode_title') or session.get('title') or '',
        ).strip()
    if series and label:
        return f'{series} {label}'
    if title:
        return title
    return ''


def _session_device_hint(session: dict) -> str:
    client = str(session.get('client') or '').strip()
    device = str(session.get('device_name') or '').strip()
    if client and device and client.casefold() != device.casefold():
        return f'{client} · {device}'
    return client or device or ''


def _session_summary_label(session: dict) -> str:
    user = str(session.get('user_name') or '').strip()
    mode = str(session.get('session_mode') or 'connected').strip()
    mode_label = _EMBY_MODE_LABELS.get(mode, mode)
    device_hint = _session_device_hint(session)
    media = _session_media_label(session)
    parts = [p for p in (user, mode_label) if p]
    head = ' · '.join(parts) if parts else mode_label
    if media:
        head = f'{head} · {media}'
    if device_hint:
        head = f'{head} · {device_hint}'
    return head


def _satellite_addrs(conns: List[dict]) -> set:
    """同秒建连、流量悬殊的保活伴生端口。"""
    satellites: set = set()
    items = []
    for conn in conns:
        addr = str(conn.get('remote_addr') or '').strip()
        if not addr:
            continue
        epoch = conn_accept_epoch(conn) or 0.0
        items.append((
            epoch,
            addr,
            max(0, int(conn.get('traffic_out') or 0)),
        ))
    items.sort(key=lambda x: (x[0], x[1]))
    for i, (ea, addr_a, out_a) in enumerate(items):
        for eb, addr_b, out_b in items[i + 1:]:
            if eb - ea > _SATELLITE_ACCEPT_SECONDS:
                break
            if out_a < _CONTROL_MAX_BYTES and out_b >= _BROWSE_MIN_BYTES:
                satellites.add(addr_a)
            elif out_b < _CONTROL_MAX_BYTES and out_a >= _BROWSE_MIN_BYTES:
                satellites.add(addr_b)
    return satellites


def _cluster_conn_waves(conns: List[dict]) -> Dict[str, dict]:
    """按 AcceptTime 聚为波次，并标记波内主推流候选。"""
    items: List[dict] = []
    for conn in conns:
        addr = str(conn.get('remote_addr') or '').strip()
        if not addr:
            continue
        items.append({
            'addr': addr,
            'accept_epoch': conn_accept_epoch(conn) or 0.0,
            'traffic_out': max(0, int(conn.get('traffic_out') or 0)),
        })
    items.sort(key=lambda x: (x['accept_epoch'], x['addr']))

    waves: List[List[dict]] = []
    current: List[dict] = []
    wave_start = 0.0
    for item in items:
        if not current or item['accept_epoch'] - wave_start <= _SATELLITE_ACCEPT_SECONDS:
            if not current:
                wave_start = item['accept_epoch']
            current.append(item)
            continue
        waves.append(current)
        current = [item]
        wave_start = item['accept_epoch']
    if current:
        waves.append(current)

    result: Dict[str, dict] = {}
    for wave_id, wave_items in enumerate(waves, start=1):
        primary = max(wave_items, key=lambda x: x['traffic_out'])
        wave_start_epoch = wave_items[0]['accept_epoch'] if wave_items else 0.0
        for item in wave_items:
            result[item['addr']] = {
                'wave_id': wave_id,
                'wave_primary_addr': primary['addr'],
                'wave_accept_epoch': wave_start_epoch,
            }
    return result


def _traffic_role(conn: dict, delta_out: int, *, is_satellite: bool) -> str:
    if is_satellite:
        return 'control'
    total = max(0, int(conn.get('traffic_out') or 0))
    delta = max(0, int(delta_out or 0))
    if total < _CONTROL_MAX_BYTES and delta < 4096:
        return 'control'
    if delta >= _STREAM_TICK_BYTES or (
        total >= _STREAM_CUMULATIVE_BYTES and delta > 0
    ):
        return 'stream_pending'
    if delta > 0 or total >= _BROWSE_MIN_BYTES:
        return 'browse'
    return 'control'


def _draft_conn_stub(draft: dict) -> dict:
    return {
        'remote_addr': draft['remote_addr'],
        'traffic_out': draft['traffic_out'],
        'accept_epoch': draft['accept_epoch'],
        'accept_time': draft['accept_time'],
    }


def _wave_accept_epoch_for(drafts: List[dict]) -> Optional[float]:
    epochs = [
        float(d.get('accept_epoch') or 0)
        for d in drafts
        if float(d.get('accept_epoch') or 0) > 0
    ]
    if not epochs:
        return None
    return min(epochs)


def _playing_session_count(sessions: List[dict]) -> int:
    return sum(
        1 for s in (sessions or [])
        if str((s or {}).get('session_mode') or '') == 'playing'
    )


def _assign_sessions_to_drafts(
    drafts: List[dict],
    sessions: List[dict],
    bindings: Dict[str, str],
    match_hints: Optional[Dict[str, str]] = None,
) -> None:
    hints = dict(match_hints or {})
    playing_count = _playing_session_count(sessions)
    peer_sessions = list(sessions or [])

    def score_draft(
        draft: dict,
        session: dict,
        *,
        wave_epoch: Optional[float] = None,
    ) -> Tuple[float, float, List[str]]:
        return score_conn_for_session_detail(
            _draft_conn_stub(draft),
            session,
            traffic_role=draft['traffic_role'],
            delta_out=draft['delta_out'],
            bindings=bindings,
            match_hints=hints,
            wave_accept_epoch=wave_epoch,
            playing_session_count=playing_count,
            peer_sessions=peer_sessions,
        )

    if not sessions:
        for draft in drafts:
            draft['best_session'] = None
            draft['best_score'] = -1.0
            draft['time_delta'] = -1.0
            draft['score_details'] = []
            draft['ambiguous'] = False
            draft['session_match_key'] = ''
        return

    if len(sessions) == 1:
        session = sessions[0]
        match_key = persist_key_for_session(session)
        for draft in drafts:
            score, td, details = score_draft(draft, session)
            if score < 0:
                details = list(details) + ['同 IP 唯一外网会话强制匹配']
                score = max(score, 80.0)
            draft['best_session'] = session
            draft['best_score'] = score
            draft['time_delta'] = td
            draft['score_details'] = details
            draft['ambiguous'] = False
            draft['session_match_key'] = match_key
        return

    assigned: Dict[str, dict] = {}
    session_primary_taken: set = set()

    stream_drafts = sorted(
        [d for d in drafts if d['traffic_role'] == 'stream_pending'],
        key=lambda d: (-d['traffic_out'], -d['delta_out'], -d['accept_epoch']),
    )
    for draft in stream_drafts:
        addr = draft['remote_addr']
        if addr in assigned:
            continue
        wave_epoch = _wave_accept_epoch_for(
            [d for d in drafts if int(d.get('wave_id') or 0) == int(draft.get('wave_id') or 0)]
            if draft.get('wave_id') else [draft],
        )
        best_session = None
        best_score = -1e9
        best_td = -1.0
        best_details: List[str] = []
        for session in sessions:
            if str(session.get('session_mode') or '') != 'playing':
                continue
            pkey = persist_key_for_session(session)
            if pkey and pkey in session_primary_taken:
                continue
            score, td, details = score_draft(
                draft, session, wave_epoch=wave_epoch,
            )
            if score > best_score:
                best_score = score
                best_session = session
                best_td = td
                best_details = details
        if best_session is not None and best_score >= 0:
            assigned[addr] = best_session
            draft['best_session'] = best_session
            draft['best_score'] = best_score
            draft['time_delta'] = best_td
            draft['score_details'] = best_details
            draft['ambiguous'] = False
            draft['session_match_key'] = persist_key_for_session(best_session)
            pkey = persist_key_for_session(best_session)
            if pkey:
                session_primary_taken.add(pkey)

    wave_session: Dict[int, dict] = {}
    for draft in drafts:
        addr = draft['remote_addr']
        if addr not in assigned:
            continue
        wave_id = int(draft.get('wave_id') or 0)
        if wave_id > 0 and draft['traffic_role'] == 'stream_pending':
            wave_session[wave_id] = assigned[addr]

    wave_groups: Dict[int, List[dict]] = {}
    for draft in drafts:
        wid = int(draft.get('wave_id') or 0)
        if wid > 0:
            wave_groups.setdefault(wid, []).append(draft)

    for wave_id, wave_drafts in wave_groups.items():
        if wave_id in wave_session:
            continue
        pending = [
            d for d in wave_drafts
            if d['remote_addr'] not in assigned
        ]
        if not pending:
            continue
        wave_epoch = _wave_accept_epoch_for(wave_drafts)
        primary_draft = max(pending, key=lambda d: d['traffic_out'])
        best_session = None
        best_score = -1e9
        for session in sessions:
            score, _, _ = score_draft(
                primary_draft, session, wave_epoch=wave_epoch,
            )
            if score > best_score:
                best_score = score
                best_session = session
        if best_session is None or best_score < _WAVE_ASSIGN_MIN_SCORE:
            continue
        for draft in pending:
            addr = draft['remote_addr']
            score, td, details = score_draft(
                draft, best_session, wave_epoch=wave_epoch,
            )
            details = list(details) + ['波次整体对齐会话']
            assigned[addr] = best_session
            draft['best_session'] = best_session
            draft['best_score'] = score
            draft['time_delta'] = td
            draft['score_details'] = details
            draft['ambiguous'] = False
            draft['session_match_key'] = persist_key_for_session(best_session)
        wave_session[wave_id] = best_session

    for draft in drafts:
        addr = draft['remote_addr']
        if addr in assigned:
            continue
        wave_id = int(draft.get('wave_id') or 0)
        follow_session = wave_session.get(wave_id)
        if follow_session and draft['traffic_role'] in ('control', 'browse'):
            wave_epoch = _wave_accept_epoch_for(
                wave_groups.get(wave_id) or [draft],
            )
            score, td, details = score_draft(
                draft, follow_session, wave_epoch=wave_epoch,
            )
            tag = '伴生保活跟随波次' if draft['traffic_role'] == 'control' else '波次内浏览跟随'
            details = list(details) + [tag]
            assigned[addr] = follow_session
            draft['best_session'] = follow_session
            draft['best_score'] = score
            draft['time_delta'] = td
            draft['score_details'] = details
            draft['ambiguous'] = False
            draft['session_match_key'] = persist_key_for_session(follow_session)
            continue

    for draft in drafts:
        addr = draft['remote_addr']
        if addr in assigned:
            if not draft.get('session_match_key'):
                draft['session_match_key'] = persist_key_for_session(
                    draft.get('best_session') or {},
                )
            draft['ambiguous'] = bool(draft.get('ambiguous'))
            continue
        wave_id = int(draft.get('wave_id') or 0)
        wave_epoch = _wave_accept_epoch_for(wave_groups.get(wave_id) or [draft])
        ranked: List[Tuple[float, dict, float, List[str]]] = []
        for session in sessions:
            score, td, details = score_draft(
                draft, session, wave_epoch=wave_epoch,
            )
            ranked.append((score, session, td, details))
        ranked.sort(key=lambda x: -x[0])
        if not ranked or ranked[0][0] < 0:
            draft['best_session'] = None
            draft['best_score'] = -1.0
            draft['time_delta'] = -1.0
            draft['score_details'] = ranked[0][3] if ranked else []
            draft['ambiguous'] = False
            draft['session_match_key'] = ''
            continue
        best_score, best_session, best_td, best_details = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else -1e9
        draft['best_session'] = best_session
        draft['best_score'] = best_score
        draft['time_delta'] = best_td
        draft['score_details'] = best_details
        draft['ambiguous'] = (
            len(ranked) > 1
            and second_score >= 0
            and (best_score - second_score) < _AMBIGUOUS_SCORE_GAP
        )
        draft['session_match_key'] = persist_key_for_session(best_session)
        assigned[addr] = best_session


def _wave_align_in_details(score_details: Optional[List[str]]) -> bool:
    needles = (
        '波次整体对齐',
        '伴生保活跟随波次',
        '波次内浏览跟随',
        '波次时刻对齐',
        '波次内建连',
    )
    for item in score_details or []:
        text = str(item or '')
        if any(needle in text for needle in needles):
            return True
    return False


def _effective_timing_delta(
    emby_mode: str,
    time_delta: float,
    activity_age_seconds: Optional[float],
) -> float:
    """播放中优先用会话近期活动刻画「当前仍有效」的时差。"""
    if emby_mode != 'playing':
        if time_delta >= 0:
            return time_delta
        if activity_age_seconds is not None and activity_age_seconds >= 0:
            return activity_age_seconds
        return time_delta
    age = activity_age_seconds
    if time_delta < 0:
        return age if age is not None and age >= 0 else time_delta
    if age is not None and age >= 0:
        # 选片后沿用旧连接开播：建连早于 playback_started_at，但播放仍活跃
        if time_delta > 120 and age <= _ACTIVITY_FRESHNESS_WINDOW:
            return age
        return min(time_delta, age)
    return time_delta


def _confidence_for(
    conn_role: str,
    emby_mode: str,
    time_delta: float,
    *,
    has_emby: bool,
    primary_conflict: bool,
    ambiguous: bool,
    match_score: float,
    activity_age_seconds: Optional[float] = None,
    traffic_role: str = '',
    delta_out: int = 0,
    billing_state: str = '',
    score_details: Optional[List[str]] = None,
    sticky_matched: bool = False,
) -> str:
    if not has_emby or emby_mode == 'orphan':
        return 'low'
    if primary_conflict or ambiguous:
        return 'low'

    delta = max(0, int(delta_out or 0))
    wave_aligned = _wave_align_in_details(score_details)
    timing = _effective_timing_delta(
        emby_mode, time_delta, activity_age_seconds,
    )

    browse_like = emby_mode in ('viewing', 'connected', 'paused')
    ongoing_browse = (
        str(traffic_role or conn_role or '').strip() == 'browse'
        and delta > 0
    )
    if conn_role in ('control', 'browse') and browse_like:
        age = activity_age_seconds
        if age is None and timing >= 0:
            age = timing
        if age is not None and age >= 0:
            if ongoing_browse or age <= _BROWSE_CONFIDENCE_HIGH_AGE:
                return 'high'
            if age <= _BROWSE_CONFIDENCE_MEDIUM_AGE:
                return 'medium'
            return 'low'

    if conn_role == 'stream_primary' and emby_mode == 'playing':
        if billing_state == 'credited' and delta > 0:
            return 'high'
        if sticky_matched or wave_aligned:
            return 'high' if timing >= 0 and timing <= 120 else 'medium'
        if match_score >= 350 and timing >= 0 and timing <= 120:
            return 'high'
        if delta >= _STREAM_TICK_BYTES:
            return 'high'
        if match_score >= 250:
            return 'high' if timing >= 0 and timing <= 180 else 'medium'
        if timing >= 0 and timing <= 30:
            return 'high'
        if timing >= 0 and timing <= 120:
            return 'medium'
        return 'low'

    if conn_role == 'stream_pending' and emby_mode == 'playing':
        if delta >= _STREAM_TICK_BYTES and match_score >= 200:
            return 'high'
        return 'medium'

    if conn_role in ('control', 'browse') and emby_mode == 'playing':
        if sticky_matched or wave_aligned:
            return 'high' if timing >= 0 and timing <= 120 else 'medium'
        if delta > 0 or (
            activity_age_seconds is not None
            and activity_age_seconds <= _ACTIVITY_FRESHNESS_WINDOW
        ):
            return 'medium'
        return 'low'

    if conn_role in ('control', 'browse') and emby_mode in (
        'viewing', 'connected', 'paused',
    ):
        if timing >= 0 and timing <= 60:
            return 'high'
        return 'medium'
    if conn_role == 'stream_secondary':
        return 'medium'
    if conn_role == 'stream_pending':
        return 'medium'
    return 'low'


def _build_reasons(
    conn_role: str,
    emby_mode: str,
    billing_state: str,
    time_delta: float,
    *,
    primary_conflict: bool,
    ambiguous: bool,
    score_details: List[str],
    wave_id: int,
) -> List[str]:
    reasons: List[str] = []
    if wave_id > 0:
        reasons.append(f'建连波次 #{wave_id}')
    if conn_role == 'control':
        reasons.append('流量极低，判定为保活/控制连接')
    elif conn_role == 'browse':
        reasons.append('有流量，会话处于选片/在线（非播放推流）')
    elif conn_role == 'stream_pending':
        reasons.append('流量达标，待分配主推流位')
    elif conn_role == 'stream_primary':
        reasons.append('同 IP 下本 tick 主推流连接')
    elif conn_role == 'stream_secondary':
        reasons.append('有流量但非该会话主推流位')

    if emby_mode == 'viewing':
        reasons.append('Emby 会话处于选片状态')
    elif emby_mode == 'paused':
        reasons.append('Emby 会话已暂停')
    elif emby_mode == 'orphan':
        reasons.append('该 IP 无 Emby 外网会话')
    elif emby_mode == 'playing':
        reasons.append('Emby 会话正在播放')

    if billing_state == 'credited':
        reasons.append('计入播放段流量')
    elif billing_state == 'browse_credited':
        reasons.append('选片流量计入用户')
    elif billing_state == 'excluded' and emby_mode == 'viewing':
        reasons.append('选片流量不计入播放')
    elif billing_state == 'pending':
        reasons.append('推流尚未确认或未分配主推流位')

    if time_delta >= 0:
        if time_delta <= 30:
            reasons.append(f'建连与活动时刻差 {int(time_delta)}s')
        elif time_delta > 120:
            reasons.append(f'建连与活动时刻差较大（{int(time_delta)}s）')

    if primary_conflict:
        reasons.append('与同 IP 其他播放会话争夺主推流位')
    if ambiguous:
        reasons.append('多会话匹配分差过小，置信度降低')

    for item in score_details or []:
        if item not in reasons:
            reasons.append(item)
    return reasons


def _analyze_ip_group(
    ip: str,
    conns: List[dict],
    deltas: Dict[str, int],
    matching_sessions: List[dict],
    *,
    bindings: Dict[str, str],
    match_hints: Dict[str, str],
    upload_bucket: Dict[str, int],
    browse_upload_bucket: Optional[Dict[str, int]] = None,
    credit_browse: bool = False,
    instance_name: str = '',
) -> Tuple[str, List[dict]]:
    satellites = _satellite_addrs(conns)
    wave_map = _cluster_conn_waves(conns)

    drafts: List[dict] = []
    for conn in conns:
        addr = str(conn.get('remote_addr') or '').strip()
        if not addr:
            continue
        delta_out = max(0, int(deltas.get(addr) or 0))
        wave_info = wave_map.get(addr) or {}
        traffic_role = _traffic_role(
            conn, delta_out, is_satellite=(addr in satellites),
        )
        drafts.append({
            'conn': conn,
            'remote_addr': addr,
            'port': int(conn.get('port') or 0),
            'accept_time': str(conn.get('accept_time') or '').strip(),
            'accept_epoch': conn_accept_epoch(conn) or 0.0,
            'traffic_out': max(0, int(conn.get('traffic_out') or 0)),
            'delta_out': delta_out,
            'traffic_role': traffic_role,
            'wave_id': int(wave_info.get('wave_id') or 0),
            'wave_primary_addr': str(wave_info.get('wave_primary_addr') or '').strip(),
        })

    _assign_sessions_to_drafts(
        drafts, matching_sessions, bindings, match_hints,
    )

    for draft in drafts:
        best_session = draft.get('best_session')
        emby_mode = 'orphan'
        emby_user = ''
        media_label = ''
        persist_key = ''
        if best_session and draft.get('best_score', -1) >= 0:
            emby_mode = str(best_session.get('session_mode') or 'connected')
            emby_user = str(best_session.get('user_name') or '').strip()
            media_label = _session_media_label(best_session)
            persist_key = persist_key_for_session(best_session)

        if emby_mode in ('viewing', 'connected') and draft['traffic_role'] != 'control':
            draft['traffic_role'] = 'browse'
        draft['emby_mode'] = emby_mode if best_session else 'orphan'
        draft['emby_user'] = emby_user
        draft['media_label'] = media_label
        draft['persist_key'] = persist_key
        draft['best_session'] = best_session

    stream_rank = [
        d for d in drafts
        if d['traffic_role'] in ('stream_pending', 'browse')
        and d['emby_mode'] == 'playing'
        and d.get('best_score', -1) >= 0
        and d.get('best_session')
    ]
    stream_rank.sort(
        key=lambda d: (-d['delta_out'], -d['traffic_out'], -d['accept_epoch']),
    )
    assigned_sessions: set = set()
    primary_by_addr: Dict[str, str] = {}
    for draft in stream_rank:
        session = draft.get('best_session')
        if not session:
            continue
        pkey = persist_key_for_session(session)
        if not pkey or pkey in assigned_sessions:
            continue
        assigned_sessions.add(pkey)
        primary_by_addr[draft['remote_addr']] = pkey

    rows: List[dict] = []
    for draft in drafts:
        addr = draft['remote_addr']
        traffic_role = draft['traffic_role']
        emby_mode = draft['emby_mode']
        primary_conflict = False
        persist_key = ''

        if addr in primary_by_addr:
            conn_role = 'stream_primary'
            billing_state = 'credited'
            persist_key = primary_by_addr[addr]
        elif emby_mode == 'orphan':
            conn_role = 'control' if traffic_role == 'control' else 'browse'
            billing_state = 'orphan'
        elif should_browse_credit_billing(
            draft.get('best_session'),
            emby_mode,
            traffic_role,
            credit_browse=credit_browse,
            instance_name=instance_name,
        ):
            conn_role = 'control' if traffic_role == 'control' else 'browse'
            billing_state = 'browse_credited'
        elif emby_mode == 'paused':
            conn_role = 'control' if traffic_role == 'control' else 'browse'
            billing_state = 'excluded'
            persist_key = draft['persist_key']
        elif emby_mode == 'connected':
            conn_role = 'control' if traffic_role == 'control' else 'browse'
            billing_state = 'excluded'
        elif emby_mode == 'playing':
            if traffic_role == 'control':
                conn_role = 'control'
                billing_state = 'excluded'
            elif traffic_role == 'stream_pending':
                conn_role = 'stream_secondary'
                billing_state = 'pending'
                persist_key = draft['persist_key']
                primary_conflict = (
                    bool(persist_key) and persist_key not in assigned_sessions
                )
            else:
                conn_role = 'browse'
                billing_state = 'excluded'
        else:
            conn_role = traffic_role
            billing_state = 'excluded'
            persist_key = draft['persist_key']

        bound_key = str(bindings.get(addr) or '').strip()
        accumulator_bytes = 0
        billing_persist_key = persist_key if billing_state == 'credited' else ''
        browse_persist_key = ''
        if billing_state == 'browse_credited' and draft.get('best_session'):
            browse_persist_key = browse_persist_key_for_session(draft['best_session'])
        if billing_state == 'credited' and billing_persist_key:
            accumulator_bytes = max(
                0, int(upload_bucket.get(billing_persist_key) or 0),
            )
        elif billing_state == 'browse_credited' and browse_persist_key:
            bucket = browse_upload_bucket if browse_upload_bucket is not None else {}
            accumulator_bytes = 0
            session = draft.get('best_session') or {}
            for key in browse_persist_key_variants_for_session(session):
                accumulator_bytes += max(0, int(bucket.get(key) or 0))
        elif bound_key:
            accumulator_bytes = max(0, int(upload_bucket.get(bound_key) or 0))

        ambiguous = bool(draft.get('ambiguous'))
        match_score = float(draft.get('best_score') or -1)
        time_delta = float(draft.get('time_delta', -1))
        best_session = draft.get('best_session') or {}
        activity_age = _session_activity_age_seconds(best_session)
        score_details = list(draft.get('score_details') or [])
        session_match_key = str(draft.get('session_match_key') or '').strip()
        sticky_matched = bool(
            session_match_key
            and str(match_hints.get(addr) or '').strip() == session_match_key
        )
        confidence = _confidence_for(
            conn_role,
            emby_mode,
            time_delta,
            has_emby=bool(draft.get('best_session')),
            primary_conflict=primary_conflict,
            ambiguous=ambiguous,
            match_score=match_score,
            activity_age_seconds=activity_age,
            traffic_role=str(draft.get('traffic_role') or ''),
            delta_out=int(draft.get('delta_out') or 0),
            billing_state=billing_state,
            score_details=score_details,
            sticky_matched=sticky_matched,
        )
        reasons = _build_reasons(
            conn_role,
            emby_mode,
            billing_state,
            time_delta,
            primary_conflict=primary_conflict,
            ambiguous=ambiguous,
            score_details=score_details,
            wave_id=int(draft.get('wave_id') or 0),
        )

        emby_label = draft['emby_user']
        if emby_mode != 'orphan' and emby_label:
            emby_label = (
                f"{emby_label} · {_EMBY_MODE_LABELS.get(emby_mode, emby_mode)}"
            )
            device_hint = ''
            session = draft.get('best_session')
            if session:
                device_hint = _session_device_hint(session)
            if draft['media_label'] and emby_mode in ('playing', 'paused', 'viewing'):
                emby_label = f"{emby_label} · {draft['media_label']}"
            if device_hint:
                emby_label = f'{emby_label} · {device_hint}'
        elif emby_mode == 'orphan':
            emby_label = _EMBY_MODE_LABELS['orphan']

        display_persist = billing_persist_key or bound_key
        rows.append({
            'remote_addr': addr,
            'ip': ip,
            'port': draft['port'],
            'accept_time': draft['accept_time'],
            'accept_epoch': draft['accept_epoch'],
            'traffic_out': draft['traffic_out'],
            'delta_out': draft['delta_out'],
            'wave_id': int(draft.get('wave_id') or 0),
            'conn_role': conn_role,
            'conn_role_label': _CONN_ROLE_LABELS.get(conn_role, conn_role),
            'emby_user': draft['emby_user'],
            'client': str(best_session.get('client') or '').strip(),
            'device_name': str(best_session.get('device_name') or '').strip(),
            'client_ip': parse_endpoint_ip(
                str(best_session.get('remote_endpoint') or '').strip(),
            ) or ip,
            'emby_mode': emby_mode,
            'emby_mode_label': _EMBY_MODE_LABELS.get(emby_mode, emby_mode),
            'emby_label': emby_label,
            'media_label': draft['media_label'],
            'billing_state': billing_state,
            'billing_label': _BILLING_LABELS.get(billing_state, billing_state),
            'confidence': confidence,
            'confidence_label': _CONFIDENCE_LABELS.get(confidence, confidence),
            'reasons': reasons,
            'score_details': score_details,
            'match_score': int(match_score) if match_score >= 0 else None,
            'ambiguous': ambiguous,
            'session_match_key': session_match_key,
            'sticky_hint': bool(
                session_match_key
                and str(match_hints.get(addr) or '').strip() == session_match_key
            ),
            'persist_key': display_persist,
            'billing_persist_key': billing_persist_key,
            'browse_persist_key': browse_persist_key,
            'accumulator_bytes': accumulator_bytes,
            'time_match_seconds': (
                int(time_delta)
                if time_delta >= 0 else None
            ),
            'is_stream': conn_role in (
                'stream_primary', 'stream_secondary', 'stream_pending',
            ),
            'user_name': draft['emby_user'],
            'emby_hint': emby_label if billing_state != 'credited' else '',
        })

    rows.sort(
        key=lambda r: (-float(r.get('accept_epoch') or 0), str(r.get('remote_addr') or '')),
    )
    session_summary = _lucky_group_summary(rows)
    return session_summary, rows


def _lucky_group_summary(rows: List[dict]) -> str:
    if not rows:
        return '无 Lucky 连接'
    users: List[str] = []
    for row in rows:
        user = str(row.get('emby_user') or '').strip()
        if user and user not in users:
            users.append(user)
    if users:
        return '、'.join(users)
    if all(str(row.get('emby_mode') or '') == 'orphan' for row in rows):
        return f'{len(rows)} 条未匹配'
    return f'{len(rows)} 条连接'


def analyze_lucky_connections(
    sessions: list,
    conn_rows: List[dict],
    conn_deltas: Dict[str, int],
    *,
    bindings: Optional[Dict[str, str]] = None,
    match_hints: Optional[Dict[str, str]] = None,
    upload_bucket: Optional[Dict[str, int]] = None,
    browse_upload_bucket: Optional[Dict[str, int]] = None,
    credit_browse: bool = False,
    instance_name: str = '',
) -> dict:
    """Lucky 全部外网连接裁决（按 IP 分组）。"""
    deltas = {
        str(k).strip(): max(0, int(v or 0))
        for k, v in (conn_deltas or {}).items()
        if str(k).strip()
    }
    matching_remote = [
        s for s in (sessions or [])
        if isinstance(s, dict) and is_wan_remote_session(s)
    ]
    matching_remote, _superseded_meta = filter_superseded_wan_sessions(
        matching_remote,
    )
    bindings = dict(bindings or {})
    hints = dict(match_hints or {})
    upload_bucket = dict(upload_bucket or {})
    browse_bucket = dict(browse_upload_bucket or {})

    by_ip: Dict[str, List[dict]] = {}
    for conn in conn_rows or []:
        if not isinstance(conn, dict):
            continue
        addr = str(conn.get('remote_addr') or '').strip()
        if not addr:
            continue
        ip = str(conn.get('ip') or parse_endpoint_ip(addr) or '').strip()
        if not ip:
            continue
        by_ip.setdefault(ip, []).append(conn)

    groups: List[dict] = []
    all_rows: List[dict] = []
    for ip in sorted(by_ip.keys()):
        ip_sessions = [
            s for s in matching_remote
            if parse_endpoint_ip(s.get('remote_endpoint') or '') == ip
        ]
        summary, rows = _analyze_ip_group(
            ip,
            by_ip[ip],
            deltas,
            ip_sessions,
            bindings=bindings,
            match_hints=hints,
            upload_bucket=upload_bucket,
            browse_upload_bucket=browse_bucket,
            credit_browse=credit_browse,
            instance_name=instance_name,
        )
        groups.append({
            'ip': ip,
            'session_summary': summary,
            'session_count': len(ip_sessions),
            'rows': rows,
        })
        all_rows.extend(rows)

    emby_without_lucky: List[dict] = []
    lucky_ips = set(by_ip.keys())

    for session in matching_remote:
        ep = parse_endpoint_ip(session.get('remote_endpoint') or '')
        if ep and ep in lucky_ips:
            continue
        emby_without_lucky.append({
            'ip': ep or str(session.get('remote_endpoint') or '').strip(),
            'emby_label': _session_summary_label(session),
            'session_mode': str(session.get('session_mode') or ''),
        })

    return {
        'version': 2,
        'groups': groups,
        'rows': all_rows,
        'emby_without_lucky': emby_without_lucky,
        'total_connections': len(all_rows),
    }


def binding_targets_from_analysis(analysis: dict) -> Dict[str, str]:
    """从裁决结果提取应写入绑定表的 RemoteAddr→persist_key。

    低置信 / 模糊（多会话评分接近）的行不写粘性绑定，避免用一次不确定的
    猜测把连接长期钉死在错误会话上，导致后续增量持续漂移。
    """
    result: Dict[str, str] = {}
    for group in (analysis or {}).get('groups') or []:
        for row in group.get('rows') or []:
            if row.get('ambiguous'):
                continue
            if str(row.get('confidence') or '') == 'low':
                continue
            billing = str(row.get('billing_state') or '')
            addr = str(row.get('remote_addr') or '').strip()
            if billing == 'credited':
                pkey = str(row.get('billing_persist_key') or '').strip()
            elif billing == 'browse_credited':
                pkey = str(row.get('browse_persist_key') or '').strip()
            else:
                continue
            if addr and pkey:
                result[addr] = pkey
    return result


def match_hints_from_analysis(analysis: dict) -> Dict[str, str]:
    """从裁决结果提取跨 tick 匹配记忆（含非入账连接）。"""
    result: Dict[str, str] = {}
    for group in (analysis or {}).get('groups') or []:
        for row in group.get('rows') or []:
            if row.get('ambiguous'):
                continue
            if str(row.get('confidence') or '') == 'low':
                continue
            if str(row.get('emby_mode') or '') == 'orphan':
                continue
            addr = str(row.get('remote_addr') or '').strip()
            pkey = str(row.get('session_match_key') or '').strip()
            if not pkey:
                pkey = str(row.get('billing_persist_key') or '').strip()
            if addr and pkey:
                result[addr] = pkey
    return result
