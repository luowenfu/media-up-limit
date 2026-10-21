"""连播切集空窗判别：区分短暂 connected 与真选片。"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 近期在播记忆（用于判断 connected 是否紧跟播放）
CONTINUOUS_PLAYBACK_MEMORY_SECONDS = 30
# 连播切集空窗内部固定窗口（秒，非用户可调）：仅短于此秒数的 connected（且紧跟在播、
# 播放段仍 open）视为切集空窗，超过则按真选片处理，避免误吞较长的选片流量。
# 比历史默认 3 秒略宽以覆盖较慢网络；开播/切集的突发误计由 tag-and-settle 机制
# （与时长无关）兜底，故此窗口无需再拉长。
EPISODE_SWITCH_GAP_MAX_SECONDS = 6

_lock = threading.RLock()
_last_playing_mono: Dict[str, Dict[str, float]] = {}
_connected_since_mono: Dict[str, Dict[str, float]] = {}
# 进入 playing 时缓存上一段 connected 时长（供同轮结算读取）
_connected_age_at_play: Dict[str, Dict[str, float]] = {}


def _session_sid(session: dict) -> str:
    if not isinstance(session, dict):
        return ''
    return str(
        session.get('emby_session_id')
        or session.get('session_id')
        or session.get('id')
        or '',
    ).strip()


def session_has_viewing_item(session: Optional[dict]) -> bool:
    if not session or not isinstance(session, dict):
        return False
    if str(session.get('viewing_item_id') or '').strip():
        return True
    return bool(str(session.get('viewing_title') or '').strip())


def _playing_modes(session: dict) -> bool:
    mode = str(session.get('session_mode') or '').strip()
    return mode in ('playing', 'paused')


def tick(instance_name: str, sessions: list, *, now_mono: Optional[float] = None) -> None:
    """每轮 Sessions 轮询更新连播上下文（只读 API 状态，不 hold）。"""
    name = (instance_name or '').strip()
    if not name:
        return
    now = time.monotonic() if now_mono is None else float(now_mono)
    seen: set = set()
    with _lock:
        playing_map = _last_playing_mono.setdefault(name, {})
        connected_map = _connected_since_mono.setdefault(name, {})
        age_at_play = _connected_age_at_play.setdefault(name, {})
        for raw in sessions or []:
            if not isinstance(raw, dict):
                continue
            session = raw
            if raw.get('NowPlayingItem') or raw.get('PlayState'):
                from emby.client import EmbyClient
                session = EmbyClient.normalize_session(raw)
            sid = _session_sid(session)
            if not sid:
                continue
            seen.add(sid)
            mode = str(session.get('session_mode') or '').strip()
            has_media = bool(
                str(session.get('item_id') or '').strip()
                or str(session.get('title') or '').strip()
            )
            if _playing_modes(session) and (has_media or mode == 'paused'):
                if sid in connected_map:
                    age_at_play[sid] = max(
                        0.0, now - float(connected_map[sid]),
                    )
                    connected_map.pop(sid, None)
                playing_map[sid] = now
            elif mode == 'connected':
                age_at_play.pop(sid, None)
                connected_map.setdefault(sid, now)
            elif mode == 'viewing':
                playing_map.pop(sid, None)
                connected_map.pop(sid, None)
                age_at_play.pop(sid, None)
            else:
                connected_map.pop(sid, None)
                age_at_play.pop(sid, None)
        for sid in list(playing_map.keys()):
            if sid not in seen:
                playing_map.pop(sid, None)
        for sid in list(connected_map.keys()):
            if sid not in seen:
                connected_map.pop(sid, None)
        for sid in list(age_at_play.keys()):
            if sid not in seen:
                age_at_play.pop(sid, None)


def recently_was_playing(
    instance_name: str,
    sid: str,
    *,
    now_mono: Optional[float] = None,
) -> bool:
    name = (instance_name or '').strip()
    sid = str(sid or '').strip()
    if not name or not sid:
        return False
    now = time.monotonic() if now_mono is None else float(now_mono)
    with _lock:
        last = (_last_playing_mono.get(name) or {}).get(sid)
    if last is None:
        return False
    return (now - float(last)) <= CONTINUOUS_PLAYBACK_MEMORY_SECONDS


def connected_duration_seconds(
    instance_name: str,
    sid: str,
    *,
    now_mono: Optional[float] = None,
) -> float:
    """当前 connected 时长；若本轮已进入 playing 则读缓存的上一段时长。"""
    name = (instance_name or '').strip()
    sid = str(sid or '').strip()
    if not name or not sid:
        return 0.0
    now = time.monotonic() if now_mono is None else float(now_mono)
    with _lock:
        since = (_connected_since_mono.get(name) or {}).get(sid)
        if since is not None:
            return max(0.0, now - float(since))
        cached = (_connected_age_at_play.get(name) or {}).get(sid)
    if cached is not None:
        return max(0.0, float(cached))
    return 0.0


def is_episode_switch_gap(
    instance_name: str,
    session: dict,
    *,
    now_mono: Optional[float] = None,
) -> bool:
    """连播切集空窗：connected、无选片媒资、紧跟在播、仍在高速率推流缓冲且播放段 open。"""
    if not isinstance(session, dict):
        return False
    mode = str(session.get('session_mode') or '').strip()
    if mode not in ('connected', 'playing', 'paused'):
        return False
    if session_has_viewing_item(session):
        return False
    sid = _session_sid(session)
    if not sid:
        return False
    now = time.monotonic() if now_mono is None else float(now_mono)
    if not recently_was_playing(instance_name, sid, now_mono=now):
        return False
    conn_age = connected_duration_seconds(instance_name, sid, now_mono=now)
    if conn_age >= EPISODE_SWITCH_GAP_MAX_SECONDS:
        return False
    try:
        import emby.records.store as playback_record_store
        open_sids = playback_record_store.open_playback_session_ids(
            instance_name,
        )
        if sid not in open_sids:
            return False
    except Exception:
        logger.debug(
            '读取未结案播放段失败: %s',
            instance_name,
            exc_info=True,
        )
        return False
    return True


def should_suppress_connected_browse_credit(
    instance_name: str,
    session: Optional[dict],
) -> bool:
    """connected 是否应抑制选片入账（仅切集空窗，不含停播后真选片）。"""
    if not session or not isinstance(session, dict):
        return False
    mode = str(session.get('session_mode') or '').strip()
    if mode != 'connected':
        return False
    return is_episode_switch_gap(instance_name, session)


def should_settle_browse_on_playback_start(
    instance_name: str,
    session: dict,
    prev_mode: str = '',
    *,
    now_mono: Optional[float] = None,
) -> bool:
    """viewing/真选片 → playing 可结算；连播切集短暂 connected 空窗不结算。"""
    if not isinstance(session, dict):
        return False
    if session_has_viewing_item(session):
        return True
    prev = str(prev_mode or '').strip()
    if prev == 'viewing':
        return True
    if prev in ('playing', 'paused'):
        return False
    now = time.monotonic() if now_mono is None else float(now_mono)
    if is_episode_switch_gap(instance_name, session, now_mono=now):
        return False
    return True


def should_route_browse_delta_to_play(
    instance_name: str,
    session: Optional[dict],
) -> bool:
    """切集空窗或正在推流时，增量应进播放桶而非选片桶。"""
    if not session or not isinstance(session, dict):
        return False
    from emby.client import EmbyClient
    if EmbyClient.is_active_playback_stream(session):
        return True
    return is_episode_switch_gap(instance_name, session)


def clear_instance(instance_name: str) -> None:
    name = (instance_name or '').strip()
    if not name:
        return
    with _lock:
        _last_playing_mono.pop(name, None)
        _connected_since_mono.pop(name, None)
        _connected_age_at_play.pop(name, None)
