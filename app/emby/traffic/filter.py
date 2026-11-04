"""Emby 外网流量估算：Docker 总量无法按 IP 拆分，需结合播放会话客户端 IP 与码率分配。"""

from __future__ import annotations

import ipaddress
import logging
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Emby 偶发上报占位码率（如 1000001）；转码分摊时改用保守默认值。
SUSPICIOUS_PLACEHOLDER_BPS = 1_500_000
DEFAULT_STREAM_BPS = 8_000_000

_TRANSCODE_KINDS = frozenset({
    'full_transcode', 'audio_transcode', 'video_transcode',
})


def parse_endpoint_ip(remote_endpoint: str) -> str:
    ep = (remote_endpoint or '').strip()
    if not ep:
        return ''
    if ep.startswith('['):
        end = ep.find(']')
        if end > 0:
            return ep[1:end]
    if ep.count('.') == 3 and ':' in ep:
        return ep.rsplit(':', 1)[0]
    if ':' in ep and '.' not in ep:
        return ep
    return ep


def canon_client_ip(value: str) -> str:
    """收成可比对的 IP。``::ffff:1.2.3.4`` 收成 ``1.2.3.4``。"""
    raw = str(value or '').strip()
    if not raw:
        return ''
    if raw.startswith('['):
        ip = parse_endpoint_ip(raw)
    elif raw.count(':') == 1 and raw.count('.') == 3:
        ip = raw.rsplit(':', 1)[0]
    else:
        ip = raw
    if not ip:
        return ''
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    mapped = getattr(addr, 'ipv4_mapped', None)
    if mapped is not None:
        return str(mapped)
    return str(addr)


def is_lan_ip(ip_str: str) -> bool:
    if not ip_str:
        return True
    try:
        addr = ipaddress.ip_address(ip_str.strip('[]'))
    except ValueError:
        return False
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
    )


def is_wan_endpoint(remote_endpoint: str) -> bool:
    ip = parse_endpoint_ip(remote_endpoint)
    if not ip:
        return False
    return not is_lan_ip(ip)


def is_active_playback_session(session: dict) -> bool:
    if not isinstance(session, dict):
        return False
    # 仅把“真实播放中且非暂停”的会话纳入流量分摊，避免伪活跃会话稀释占比。
    if not bool(session.get('is_playing')):
        return False
    if bool(session.get('is_paused')):
        return False
    return True


def is_wan_playback_session(session: dict) -> bool:
    if not is_active_playback_session(session):
        return False
    return is_wan_endpoint(session.get('remote_endpoint') or '')


def is_wan_remote_session(session: dict) -> bool:
    """外网在线会话（含选片/暂停），用于 Lucky 连接匹配池。"""
    if not isinstance(session, dict):
        return False
    return is_wan_endpoint(session.get('remote_endpoint') or '')


_SUPERSEDE_STALE_GAP_SECONDS = 120.0
_SUPERSEDE_PROTECTED_MODES = frozenset({'playing', 'paused', 'viewing'})


def _session_last_activity_epoch(session: dict) -> Optional[float]:
    raw = str((session or {}).get('last_activity_date') or '').strip()
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


def _session_device_group_key(session: dict) -> Tuple[str, str, str]:
    ip = parse_endpoint_ip(session.get('remote_endpoint') or '')
    client = str(session.get('client') or '').strip().casefold()
    device = str(session.get('device_name') or '').strip().casefold()
    return ip, client, device


def filter_superseded_wan_sessions(
    sessions: list,
    *,
    stale_gap_seconds: float = _SUPERSEDE_STALE_GAP_SECONDS,
) -> Tuple[List[dict], List[dict]]:
    """同 IP+Client+DeviceName 下账户切换后，剔除活动明显落后的旧用户会话。

    返回 (保留列表, 被剔除列表)；后者含 reason 字段供调试展示。
    """
    wan = [
        s for s in (sessions or [])
        if isinstance(s, dict) and is_wan_remote_session(s)
    ]
    if not wan:
        return [], []

    gap = max(30.0, float(stale_gap_seconds or _SUPERSEDE_STALE_GAP_SECONDS))
    groups: Dict[Tuple[str, str, str], List[dict]] = {}
    for session in wan:
        key = _session_device_group_key(session)
        if not key[0]:
            groups.setdefault(('', '', ''), []).append(session)
            continue
        groups.setdefault(key, []).append(session)

    kept: List[dict] = []
    superseded: List[dict] = []

    for members in groups.values():
        if len(members) < 2:
            kept.extend(members)
            continue

        user_ids = {
            str(s.get('user_id') or '').strip()
            for s in members
        }
        user_ids.discard('')
        if len(user_ids) < 2:
            kept.extend(members)
            continue

        ranked = sorted(
            members,
            key=lambda s: _session_last_activity_epoch(s) or 0.0,
            reverse=True,
        )
        newest_epoch = _session_last_activity_epoch(ranked[0]) or 0.0

        for session in members:
            mode = str(session.get('session_mode') or '').strip()
            if mode in _SUPERSEDE_PROTECTED_MODES:
                kept.append(session)
                continue
            epoch = _session_last_activity_epoch(session) or 0.0
            if newest_epoch > 0 and (newest_epoch - epoch) >= gap:
                superseded.append({
                    'session': session,
                    'user_name': str(
                        session.get('user_name')
                        or session.get('UserName')
                        or '',
                    ).strip(),
                    'user_id': str(
                        session.get('user_id')
                        or session.get('UserId')
                        or '',
                    ).strip(),
                    'session_id': str(
                        session.get('id')
                        or session.get('session_id')
                        or session.get('emby_session_id')
                        or '',
                    ).strip(),
                    'ip': parse_endpoint_ip(session.get('remote_endpoint') or ''),
                    'client': str(session.get('client') or '').strip(),
                    'device_name': str(session.get('device_name') or '').strip(),
                    'reason': '检测到账户切换，已排除旧会话',
                    'activity_lag_seconds': int(max(0.0, newest_epoch - epoch)),
                })
            else:
                kept.append(session)

    return kept, superseded


def _is_transcode_session(session: dict) -> bool:
    if not session:
        return False
    if (session.get('play_method') or '').strip() == 'Transcode':
        return True
    return (session.get('transcode_kind') or '').strip() in _TRANSCODE_KINDS


def resolve_transcode_kind(session: dict) -> str:
    """与 emby_client.derive_transcode_kind 口径一致。"""
    kind = (session.get('transcode_kind') or '').strip()
    if kind:
        return kind
    play_method = (session.get('play_method') or '').strip()
    if play_method == 'DirectPlay':
        return 'direct_play'
    if play_method == 'DirectStream':
        return 'direct_stream'
    if play_method != 'Transcode':
        return ''
    is_video_direct = session.get('is_video_direct')
    is_audio_direct = session.get('is_audio_direct')
    if is_video_direct is None:
        is_video_direct = False
    if is_audio_direct is None:
        is_audio_direct = False
    video_direct = bool(is_video_direct)
    audio_direct = bool(is_audio_direct)
    if not video_direct and audio_direct:
        return 'video_transcode'
    if video_direct and not audio_direct:
        return 'audio_transcode'
    if not video_direct and not audio_direct:
        return 'full_transcode'
    return ''


def session_stream_bps(session: dict) -> int:
    """有效码率：优先 video+audio 分量，过滤占位低码率，转码缺省 8Mbps。"""
    video = int(session.get('video_bitrate') or 0)
    audio = int(session.get('audio_bitrate') or 0)
    component_sum = video + audio
    if component_sum > 0:
        return component_sum

    bps = int(session.get('bitrate') or 0)
    if bps > SUSPICIOUS_PLACEHOLDER_BPS:
        return bps
    if bps > 0 and _is_transcode_session(session):
        return DEFAULT_STREAM_BPS
    if bps > 0:
        return bps
    if _is_transcode_session(session):
        return DEFAULT_STREAM_BPS
    if session.get('is_playing') or session.get('item_id'):
        return DEFAULT_STREAM_BPS
    return 0


def session_container_egress_bps(session: dict) -> int:
    """估算容器→该会话客户端的出口码率（LAN / 外网同一公式）。

    按 transcode_kind + 音视频分量自动区分，无需为每种组合单独调系数：
    - direct_play：DirectPlay，容器几乎无串流出口
    - direct_stream / 各类转码 / 直播串流：容器向客户端送出 video+audio 输出
      （仅音频转码时仍包含直传视频 + 转码音频；仅视频转码时仍含直传音频）
    """
    if not is_active_playback_session(session):
        return 0
    kind = resolve_transcode_kind(session)
    if kind == 'direct_play':
        return 0
    video = max(0, int(session.get('video_bitrate') or 0))
    audio = max(0, int(session.get('audio_bitrate') or 0))
    component_sum = video + audio
    if component_sum > 0:
        return component_sum
    return max(0, int(session_stream_bps(session) or 0))


def session_docker_share_bps(session: dict) -> int:
    """Docker 出口分摊权重 = 容器→该会话的出口码率（LAN / WAN 统一）。"""
    return session_container_egress_bps(session)


def _active_playback_sessions(sessions: Iterable[dict]) -> List[dict]:
    return [
        s for s in (sessions or [])
        if is_active_playback_session(s)
    ]


def allocate_wan_deltas(delta_up: int, delta_dl: int,
                        sessions: Iterable[dict]) -> Tuple[int, int]:
    """
    将 Docker 容器增量按外网会话码率占比分配。
    无播放、仅局域网播放、或无法识别外网会话时返回 (0, 0)。
    """
    delta_up = max(0, int(delta_up or 0))
    delta_dl = max(0, int(delta_dl or 0))
    if delta_up == 0 and delta_dl == 0:
        return 0, 0

    playing = _active_playback_sessions(sessions)
    if not playing:
        return 0, 0

    wan = [s for s in playing if is_wan_playback_session(s)]
    if not wan:
        return 0, 0

    lan = [s for s in playing if not is_wan_playback_session(s)]
    if not lan:
        return delta_up, delta_dl

    wan_bps = sum(session_docker_share_bps(s) for s in wan)
    total_bps = sum(session_docker_share_bps(s) for s in playing)
    if total_bps <= 0:
        ratio = len(wan) / len(playing)
    else:
        ratio = wan_bps / total_bps
    ratio = max(0.0, min(1.0, ratio))
    return int(delta_up * ratio), int(delta_dl * ratio)


# M3 全转码局域网会话容器实际出口常高于 Emby 报告码率，低估 LAN 会导致 WAN 池偏高。
_M3_LAN_FULL_TRANSCODE_OVERHEAD = 1.45


def m3_session_docker_share_bps(session: dict) -> int:
    """M3 分摊权重：全转码 LAN 会话按经验 overhead 加权。"""
    base = session_docker_share_bps(session)
    if base <= 0:
        return 0
    kind = resolve_transcode_kind(session)
    if kind == 'full_transcode' and not is_wan_playback_session(session):
        return int(base * _M3_LAN_FULL_TRANSCODE_OVERHEAD)
    return base


def allocate_m3_wan_deltas(delta_up: int, delta_dl: int,
                           sessions: Iterable[dict]) -> Tuple[int, int]:
    """M3 专用 WAN 池比例切分（含 LAN 全转码 overhead）。"""
    delta_up = max(0, int(delta_up or 0))
    delta_dl = max(0, int(delta_dl or 0))
    if delta_up == 0 and delta_dl == 0:
        return 0, 0

    playing = _active_playback_sessions(sessions)
    if not playing:
        return 0, 0

    wan = [s for s in playing if is_wan_playback_session(s)]
    if not wan:
        return 0, 0

    lan = [s for s in playing if not is_wan_playback_session(s)]
    if not lan:
        return delta_up, delta_dl

    wan_bps = sum(m3_session_docker_share_bps(s) for s in wan)
    total_bps = sum(m3_session_docker_share_bps(s) for s in playing)
    if total_bps <= 0:
        ratio = len(wan) / len(playing)
    else:
        ratio = wan_bps / total_bps
    ratio = max(0.0, min(1.0, ratio))
    return int(delta_up * ratio), int(delta_dl * ratio)


def m3_lan_baseline_bytes(lan_sessions: Iterable[dict], tick_seconds: float) -> int:
    """本 tick LAN 会话按码率估算的容器出口基线（与 raw 增量解耦）。"""
    elapsed = max(0.5, min(120.0, float(tick_seconds or 1.0)))
    lan_bps = sum(m3_session_docker_share_bps(s) for s in (lan_sessions or []))
    if lan_bps <= 0:
        return 0
    return max(0, int(lan_bps * elapsed / 8))


def scale_m3_wan_pool_bytes(delta_up: int, raw_up: int, scale: float) -> int:
    """M3 稳态 WAN 池补偿；结果不超过 Docker 本 tick 上行。"""
    pool = max(0, int(delta_up or 0))
    raw = max(0, int(raw_up or 0))
    try:
        factor = float(scale)
    except (TypeError, ValueError):
        factor = 1.0
    if factor == 1.0 or pool <= 0:
        return pool
    scaled = int(pool * factor)
    if raw > 0:
        return min(raw, max(0, scaled))
    return max(0, scaled)


def _parse_playback_started_epoch(session: dict) -> Optional[float]:
    raw = str((session or {}).get('playback_started_at') or '').strip()
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


def _wan_session_in_burst_window(session: dict, *, now_epoch: float,
                                 burst_window_seconds: float) -> bool:
    window = max(1.0, float(burst_window_seconds or 8.0))
    started = _parse_playback_started_epoch(session)
    if started is not None:
        return (float(now_epoch) - float(started)) <= window
    played = max(0, int((session or {}).get('played_seconds') or 0))
    if played > 0:
        return float(played) <= window
    # 尚无时间戳的新外网会话：视为突发窗口内
    return True


def m3_allocate_wan_pool(
    delta_up: int,
    sessions: Iterable[dict],
    *,
    scale: float = 1.0,
    burst_window_seconds: float = 8.0,
    now_epoch: Optional[float] = None,
    tick_seconds: float = 1.0,
    ratio_only: bool = False,
) -> int:
    """M3 WAN 池：稳态按 egress 比例；突发窗口内按「raw − LAN 码率基线」补 WAN 突发。

    注意：不能用 raw*lan/total 作 LAN 份额——它与比例池数学等价，无法识别 WAN 突发。
    """
    raw = max(0, int(delta_up or 0))
    if raw <= 0:
        return 0
    playing = _active_playback_sessions(sessions)
    wan = [s for s in playing if is_wan_playback_session(s)]
    if not wan:
        return 0
    lan = [s for s in playing if not is_wan_playback_session(s)]
    ratio_pool, _ = allocate_m3_wan_deltas(raw, 0, playing)
    if not lan:
        pool = raw
    else:
        pool = max(0, int(ratio_pool or 0))
        if not ratio_only:
            now = float(
                now_epoch if now_epoch is not None
                else datetime.now(timezone.utc).timestamp(),
            )
            if any(
                _wan_session_in_burst_window(
                    s, now_epoch=now, burst_window_seconds=burst_window_seconds,
                )
                for s in wan
            ):
                lan_baseline = min(raw, m3_lan_baseline_bytes(lan, tick_seconds))
                if lan_baseline >= 0:
                    burst_wan = max(pool, raw - lan_baseline)
                    pool = min(raw, max(0, int(burst_wan)))
    return scale_m3_wan_pool_bytes(pool, raw, scale)


def apply_wan_traffic_filter(delta_up: int, delta_dl: int,
                             sessions: Iterable[dict],
                             enabled: bool) -> Tuple[int, int]:
    if not enabled:
        return max(0, int(delta_up or 0)), max(0, int(delta_dl or 0))
    return allocate_wan_deltas(delta_up, delta_dl, sessions)


def playback_accumulator_key(record: dict) -> str:
    """外网播放流量累加键：优先 user+client+item_id，其次集数标签，最后剧名标题。"""
    user = (record.get('user_name') or record.get('UserName') or '').strip().casefold()
    client = (record.get('client') or record.get('Client') or '').strip().casefold()
    sid = str(
        record.get('emby_session_id')
        or record.get('session_id')
        or record.get('id')
        or record.get('SessionId')
        or '',
    ).strip()
    item_id = str(record.get('item_id') or record.get('ItemId') or '').strip()
    series = (record.get('series_name') or '').casefold()
    episode_label = (record.get('episode_label') or '').strip().casefold()
    title = (
        record.get('item_title') or record.get('title')
        or record.get('episode_title') or ''
    ).casefold()

    if sid and user and client:
        return f'{user}|{client}|sid:{sid}'
    if sid and user:
        return f'{user}|sid:{sid}'
    if sid:
        return f'sid:{sid}'
    if user and client and item_id:
        return f'{user}|{client}|{item_id}'
    if user and client and episode_label:
        return f'{user}|{client}|{series}|{episode_label}'
    if user and client and (series or title):
        return f'{user}|{client}|{series}|{title}'
    return f'{user}|{series}|{title}'


def legacy_playback_accumulator_key(record: dict) -> str:
    """旧版累加键，用于停止事件与历史累计桶匹配。"""
    user = (record.get('user_name') or record.get('UserName') or '').strip().casefold()
    series = (record.get('series_name') or '').casefold()
    title = (
        record.get('item_title') or record.get('title')
        or record.get('episode_title') or ''
    ).casefold()
    return f'{user}|{series}|{title}'
