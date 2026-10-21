"""选片流量结算：跟随 Lucky 选片入账状态，连接结束或离开选片时写入 SQLite。"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from emby.client import EmbyClient
from emby.lucky.verdict import (
    browse_persist_key_for_session,
    browse_persist_key_variants_for_session,
    legacy_browse_persist_key_for_session,
    persist_key_for_session,
    sid_from_browse_persist_key,
)
from emby.traffic.filter import (
    _session_device_group_key,
    _session_last_activity_epoch,
    filter_superseded_wan_sessions,
    is_wan_remote_session,
    parse_endpoint_ip,
)

logger = logging.getLogger(__name__)

BROWSE_CONN_END_GRACE_SECONDS = 5
ORPHAN_BUCKET_MIN_AGE_SECONDS = 30
DEFAULT_MIN_BROWSE_UPLOAD_BYTES = 1024 * 1024
OFFLINE_TIMEOUT_SECONDS = 5 * 60
_BROWSE_KEY_PREFIX = 'browse:sid:'
SETTLER_VERSION = 'emby-primary-v5'

_lock = threading.RLock()
_active_browse_keys: Dict[str, Set[str]] = {}
_browse_key_gap_since: Dict[str, Dict[str, float]] = {}
_browse_key_meta: Dict[str, Dict[str, dict]] = {}
_last_session_modes: Dict[str, Dict[str, str]] = {}
_last_session_users: Dict[str, Dict[str, str]] = {}
_last_session_profiles: Dict[str, Dict[str, dict]] = {}
_last_device_profiles: Dict[str, Dict[tuple, dict]] = {}
_orphan_bucket_since: Dict[str, Dict[str, float]] = {}
_offline_since: Dict[str, Optional[float]] = {}
_next_segment_id: Dict[str, int] = {}
_tick_logged: Set[str] = set()
_tick_min_upload_bytes: int = DEFAULT_MIN_BROWSE_UPLOAD_BYTES


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + '0000Z'


def _remember_key_meta(instance_name: str, key: str, meta: dict) -> None:
    name = (instance_name or '').strip()
    key = str(key or '').strip()
    user_name = str((meta or {}).get('user_name') or '').strip()
    if not name or not key or not user_name:
        return
    with _lock:
        bucket = _browse_key_meta.setdefault(name, {})
        prev = bucket.get(key) or {}

        def keep(field: str) -> str:
            return str(meta.get(field) or prev.get(field) or '')

        bucket[key] = {
            'user_name': user_name,
            'user_id': keep('user_id'),
            'viewing_title': keep('viewing_title'),
            'series_name': keep('series_name'),
            'episode_label': keep('episode_label'),
            'episode_title': keep('episode_title'),
            'device_name': keep('device_name'),
            'client': keep('client'),
            'client_ip': keep('client_ip'),
            'production_year': (
                meta.get('production_year')
                if meta.get('production_year') is not None
                else prev.get('production_year')
            ),
            'emby_session_id': str(
                meta.get('emby_session_id')
                or meta.get('session_id')
                or meta.get('id')
                or prev.get('emby_session_id')
                or '',
            ),
        }


def _pop_key_meta(instance_name: str, key: str) -> None:
    name = (instance_name or '').strip()
    key = str(key or '').strip()
    if not name or not key:
        return
    with _lock:
        bucket = _browse_key_meta.get(name)
        if bucket:
            bucket.pop(key, None)
            if not bucket:
                _browse_key_meta.pop(name, None)


def _meta_for_browse_key(instance_name: str, key: str) -> dict:
    name = (instance_name or '').strip()
    key = str(key or '').strip()
    if not name or not key:
        return {}
    with _lock:
        return dict((_browse_key_meta.get(name) or {}).get(key) or {})


def _cache_meta_from_analysis(instance_name: str, analysis: dict) -> None:
    name = (instance_name or '').strip()
    if not name:
        return
    import emby.traffic.playback as emby_playback_traffic

    for row in (analysis or {}).get('rows') or []:
        if str(row.get('billing_state') or '') != 'browse_credited':
            continue
        key = str(row.get('browse_persist_key') or '').strip()
        if not key:
            continue
        sid = _sid_from_browse_key(key)
        meta = _meta_from_lucky_row(row)
        if meta.get('user_name'):
            _remember_key_meta(name, key, meta)
            if sid:
                emby_playback_traffic.remember_browse_session_meta(name, sid, meta)


def _prepare_session(session: dict) -> dict:
    if session.get('NowPlayingItem') or session.get('PlayState') or session.get('NowViewingItem'):
        return EmbyClient.normalize_session(session)
    return session


def _session_sid(session: dict) -> str:
    return str(
        session.get('emby_session_id')
        or session.get('session_id')
        or session.get('id')
        or session.get('Id')
        or '',
    ).strip()


def _sid_from_browse_key(key: str) -> str:
    return sid_from_browse_persist_key(key)


def _has_browse_bytes(instance_name: str, sid: str, meta: Optional[dict] = None) -> bool:
    import emby.traffic.playback as emby_playback_traffic
    name = (instance_name or '').strip()
    sid = str(sid or '').strip()
    if not name or not sid:
        return False
    if emby_playback_traffic.peek_browse_upload_bytes_for_sid(name, sid, meta) > 0:
        return True
    bucket = emby_playback_traffic.peek_browse_upload_bucket(name)
    for key in _all_browse_keys_for_sid(name, sid, meta):
        if int(bucket.get(key) or 0) > 0:
            return True
    return False


def _clear_browse_pending_key(instance_name: str, sid: str, meta: Optional[dict] = None) -> None:
    name = (instance_name or '').strip()
    sid = str(sid or '').strip()
    if not name or not sid:
        return
    keys = _all_browse_keys_for_sid(name, sid, meta)
    with _lock:
        gaps = _browse_key_gap_since.get(name)
        orphan = _orphan_bucket_since.get(name)
        for key in keys:
            if gaps and key:
                gaps.pop(key, None)
            if orphan and key:
                orphan.pop(key, None)


def _sync_superseded_browse_settlement(
    instance_name: str,
    sessions: list,
) -> None:
    """被取代的僵尸会话：立即结算其选片累计并清理绑定。"""
    import emby.traffic.playback as emby_playback_traffic

    name = (instance_name or '').strip()
    if not name:
        return
    wan = [
        s for s in (sessions or [])
        if isinstance(s, dict) and is_wan_remote_session(_prepare_session(s))
    ]
    prepared = [_prepare_session(s) for s in wan]
    _, superseded = filter_superseded_wan_sessions(prepared)
    for item in superseded or []:
        session = item.get('session') if isinstance(item, dict) else None
        if not isinstance(session, dict):
            continue
        sid = _session_sid(session)
        if not sid or not _has_browse_bytes(name, sid, _meta_from_session(session)):
            continue
        pkey = persist_key_for_session(session)
        if pkey:
            emby_playback_traffic.clear_lucky_bindings_for_persist_key(name, pkey)
        meta = _meta_from_session(session)
        _clear_browse_pending_key(name, sid)
        emby_playback_traffic.pop_browse_session_meta(name, sid)
        _settle_browse_session(name, sid, meta, settle_reason='account_superseded')


def _meta_for_prev_user_switch(
    instance_name: str,
    sid: str,
    prev_uid: str,
    prev_profile: Optional[dict] = None,
) -> dict:
    """账户切换时解析被切出用户的选片元数据。"""
    import emby.traffic.playback as emby_playback_traffic

    name = (instance_name or '').strip()
    sid = str(sid or '').strip()
    prev_uid = str(prev_uid or '').strip()
    if not name or not sid or not prev_uid:
        return {}

    old_key = browse_persist_key_for_session({
        'user_id': prev_uid,
        'id': sid,
        'session_id': sid,
        'emby_session_id': sid,
    })
    meta = _meta_for_browse_key(name, old_key)
    if meta.get('user_name'):
        meta['user_id'] = prev_uid
        return meta

    profile = dict(prev_profile or {})
    prev_name = str(profile.get('user_name') or '').strip()
    if prev_name:
        meta = dict(profile)
        meta['user_id'] = prev_uid
        meta.setdefault('emby_session_id', sid)
        meta.setdefault('session_id', sid)
        meta.setdefault('id', sid)
        return meta

    stored = emby_playback_traffic.get_browse_session_meta(name, sid)
    if str(stored.get('user_id') or '').strip() == prev_uid and stored.get('user_name'):
        return stored

    with _lock:
        for key, cached in (_browse_key_meta.get(name) or {}).items():
            if _sid_from_browse_key(key) != sid:
                continue
            if key != old_key and not key.startswith(f'browse:{prev_uid}:'):
                continue
            if cached.get('user_name'):
                merged = dict(cached)
                merged['user_id'] = prev_uid
                return merged
    return {}


def _sync_user_switch_settlement(
    instance_name: str,
    sessions: list,
) -> None:
    """同 SessionId 上 UserId 变化时切开选片账务。"""
    import emby.traffic.playback as emby_playback_traffic

    name = (instance_name or '').strip()
    if not name:
        return
    by_sid = _sessions_by_sid(sessions)
    with _lock:
        prev_users = dict(_last_session_users.get(name) or {})
        prev_profiles = dict(_last_session_profiles.get(name) or {})
    for sid, session in by_sid.items():
        uid = str(session.get('user_id') or '').strip()
        if not uid:
            continue
        prev_uid = str(prev_users.get(sid) or '').strip()
        if not prev_uid or prev_uid == uid:
            continue
        prev_profile = dict(prev_profiles.get(sid) or {})
        meta = _meta_for_prev_user_switch(
            name, sid, prev_uid, prev_profile,
        )
        if not _has_browse_bytes(name, sid, meta):
            continue
        old_pkey = persist_key_for_session({
            'user_id': prev_uid,
            'user_name': meta.get('user_name') or prev_profile.get('user_name') or '',
            'client': session.get('client') or meta.get('client') or '',
            'id': sid,
        })
        if old_pkey:
            emby_playback_traffic.clear_lucky_bindings_for_persist_key(name, old_pkey)
        _clear_browse_pending_key(name, sid, meta)
        emby_playback_traffic.pop_browse_session_meta(name, sid)
        _settle_browse_session(
            name, sid, meta, settle_reason='user_switch',
            restrict_user_id=prev_uid,
        )


def _settle_outgoing_device_user(
    instance_name: str,
    old_sid: str,
    prev_uid: str,
    prev_profile: dict,
) -> None:
    """同设备换账户：立即结算被切出用户的选片累计。"""
    import emby.traffic.playback as emby_playback_traffic

    name = (instance_name or '').strip()
    old_sid = str(old_sid or '').strip()
    prev_uid = str(prev_uid or '').strip()
    if not name or not old_sid or not prev_uid:
        return
    meta = _meta_for_prev_user_switch(
        name, old_sid, prev_uid, prev_profile,
    )
    if not meta.get('user_name') and prev_profile.get('user_name'):
        meta = dict(prev_profile)
        meta['user_id'] = prev_uid
        meta.setdefault('emby_session_id', old_sid)
        meta.setdefault('session_id', old_sid)
        meta.setdefault('id', old_sid)
    if not _has_browse_bytes(name, old_sid, meta):
        return
    old_pkey = persist_key_for_session({
        'user_id': prev_uid,
        'user_name': meta.get('user_name') or prev_profile.get('user_name') or '',
        'client': meta.get('client') or prev_profile.get('client') or '',
        'id': old_sid,
    })
    if old_pkey:
        emby_playback_traffic.clear_lucky_bindings_for_persist_key(name, old_pkey)
    _clear_browse_pending_key(name, old_sid, meta)
    emby_playback_traffic.pop_browse_session_meta(name, old_sid)
    _settle_browse_session(
        name,
        old_sid,
        meta,
        settle_reason='user_switch',
        restrict_user_id=prev_uid,
    )


def _sync_device_user_switch_settlement(
    instance_name: str,
    sessions: list,
) -> None:
    """同设备（IP+Client+Device）换账户时立即结算旧用户选片（含新 SessionId 场景）。"""
    name = (instance_name or '').strip()
    if not name:
        return
    current = _device_profiles_from_sessions(
        sessions,
        instance_name=name,
        settle_same_tick_switch=True,
    )
    with _lock:
        prev = dict(_last_device_profiles.get(name) or {})
    for gkey, cur in current.items():
        prev_prof = dict(prev.get(gkey) or {})
        prev_uid = str(prev_prof.get('user_id') or '').strip()
        cur_uid = str(cur.get('user_id') or '').strip()
        if not prev_uid or not cur_uid or prev_uid == cur_uid:
            continue
        old_sid = str(prev_prof.get('sid') or '').strip()
        if not old_sid:
            continue
        _settle_outgoing_device_user(name, old_sid, prev_uid, prev_prof)


def _redirect_or_settle_browse_playback_start(
    instance_name: str,
    sid: str,
    session: dict,
    sessions: list,
    analysis: Optional[dict],
    *,
    prev_mode: str = '',
    now_mono: Optional[float] = None,
) -> None:
    """开播时结算真选片；连播切集空窗则把选片桶转回播放桶。"""
    import emby.browse.continuous as emby_continuous_playback
    import emby.traffic.playback as emby_playback_traffic

    name = (instance_name or '').strip()
    now_mono = time.monotonic() if now_mono is None else float(now_mono)
    meta = _meta_from_session(session)
    settle_browse = emby_continuous_playback.should_settle_browse_on_playback_start(
        name, session, prev_mode, now_mono=now_mono,
    )
    if not settle_browse:
        moved = emby_playback_traffic.transfer_browse_bytes_to_play_for_session(
            name, session,
        )
        if moved > 0:
            _clear_browse_pending_key(name, sid, meta)
        return
    # 开播结算前：把开播前误计入选片桶的推流突发移回播放键累加器，
    # 使选片记录只结算真实选片流量，突发归到播放段。
    emby_playback_traffic.settle_preplay_burst_to_play(name, session)
    if not _has_browse_bytes(name, sid, meta):
        return
    if meta.get('user_name'):
        emby_playback_traffic.remember_browse_session_meta(name, sid, meta)
    _clear_browse_pending_key(name, sid)
    _settle_browse_session(
        name, sid, meta, settle_reason='playback_started',
    )


def _sync_emby_browse_transitions(
    instance_name: str,
    sessions: list,
    *,
    now_mono: float,
    analysis: Optional[dict] = None,
) -> None:
    """Emby 会话边沿：开播立即结选片、断会话立即结案。"""
    name = (instance_name or '').strip()
    if not name:
        return
    by_sid = _sessions_by_sid(sessions)
    with _lock:
        prev_modes = dict(_last_session_modes.get(name) or {})
    current_sids = set(by_sid.keys())
    prev_sids = set(prev_modes.keys())

    for sid in prev_sids - current_sids:
        if not _has_browse_bytes(name, sid):
            continue
        meta = _resolve_meta(name, sid, sessions, analysis or {})
        _clear_browse_pending_key(name, sid, meta)
        _settle_browse_session(name, sid, meta, settle_reason='disconnect')

    for sid, session in by_sid.items():
        mode = str(session.get('session_mode') or '').strip()
        prev_mode = str(prev_modes.get(sid) or '').strip()
        if mode in ('playing', 'paused') and prev_mode in ('viewing', 'connected'):
            if not _has_browse_bytes(name, sid, _meta_from_session(session)):
                continue
            _redirect_or_settle_browse_playback_start(
                name, sid, session, sessions, analysis,
                prev_mode=prev_mode, now_mono=now_mono,
            )

    with _lock:
        _last_session_modes[name] = {
            sid: str(sess.get('session_mode') or '').strip()
            for sid, sess in by_sid.items()
        }
        _last_session_users[name] = {
            sid: str(sess.get('user_id') or '').strip()
            for sid, sess in by_sid.items()
            if str(sess.get('user_id') or '').strip()
        }
        _last_session_profiles[name] = {
            sid: {
                'user_id': str(sess.get('user_id') or '').strip(),
                'user_name': str(sess.get('user_name') or '').strip(),
                'client': str(sess.get('client') or '').strip(),
                'device_name': str(sess.get('device_name') or '').strip(),
                'client_ip': parse_endpoint_ip(
                    str(sess.get('remote_endpoint') or '').strip(),
                ),
            }
            for sid, sess in by_sid.items()
            if str(sess.get('user_id') or '').strip()
        }
        _last_device_profiles[name] = _device_profiles_from_sessions(sessions)


def _browse_keys_for_sid(sid: str, meta: Optional[dict] = None) -> List[str]:
    sid = str(sid or '').strip()
    if not sid:
        return []
    meta = dict(meta or {})
    meta.setdefault('id', sid)
    meta.setdefault('session_id', sid)
    meta.setdefault('emby_session_id', sid)
    keys = browse_persist_key_variants_for_session(meta)
    if keys:
        return keys
    legacy = legacy_browse_persist_key_for_session(meta)
    return [legacy] if legacy else [f'{_BROWSE_KEY_PREFIX}{sid}']


def _all_browse_keys_for_sid(
    instance_name: str,
    sid: str,
    meta: Optional[dict] = None,
) -> List[str]:
    """会话 sid 下所有可能有累计字节的选片键（含历史 user_id 键）。"""
    import emby.traffic.playback as emby_playback_traffic

    sid = str(sid or '').strip()
    if not sid:
        return []
    keys: List[str] = []
    seen: Set[str] = set()
    for key in _browse_keys_for_sid(sid, meta):
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    bucket = emby_playback_traffic.peek_browse_upload_bucket(instance_name)
    for key, amount in bucket.items():
        if int(amount or 0) <= 0 or key in seen:
            continue
        if _sid_from_browse_key(key) == sid:
            seen.add(key)
            keys.append(key)
    return keys


def _browse_keys_for_user_sid(
    instance_name: str,
    sid: str,
    user_id: str,
    meta: Optional[dict] = None,
) -> List[str]:
    """指定用户 + 会话 sid 的选片累计键（账户切换结算时避免误取新用户桶）。"""
    import emby.traffic.playback as emby_playback_traffic

    sid = str(sid or '').strip()
    user_id = str(user_id or '').strip()
    if not sid:
        return []
    subject = dict(meta or {})
    if user_id:
        subject['user_id'] = user_id
    subject.setdefault('id', sid)
    subject.setdefault('session_id', sid)
    subject.setdefault('emby_session_id', sid)
    keys: List[str] = []
    seen: Set[str] = set()
    for key in _browse_keys_for_sid(sid, subject):
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    if not user_id:
        return keys
    bucket = emby_playback_traffic.peek_browse_upload_bucket(instance_name)
    user_prefix = f'browse:{user_id}:'
    for key, amount in bucket.items():
        if int(amount or 0) <= 0 or key in seen:
            continue
        if not key.startswith(user_prefix):
            continue
        if _sid_from_browse_key(key) == sid:
            seen.add(key)
            keys.append(key)
    return keys


def _profile_from_prepared_session(prepared: dict) -> dict:
    sid = _session_sid(prepared)
    return {
        **_meta_from_session(prepared),
        'sid': sid,
        'user_id': str(prepared.get('user_id') or '').strip(),
        'user_name': str(prepared.get('user_name') or '').strip(),
        'client': str(prepared.get('client') or '').strip(),
        'device_name': str(prepared.get('device_name') or '').strip(),
        'client_ip': parse_endpoint_ip(
            str(prepared.get('remote_endpoint') or '').strip(),
        ),
        '_activity_epoch': _session_last_activity_epoch(prepared) or 0.0,
    }


def _device_profiles_from_sessions(
    sessions: list,
    *,
    instance_name: str = '',
    settle_same_tick_switch: bool = False,
) -> Dict[tuple, dict]:
    """按 IP+Client+DeviceName 汇总当前外网设备上的活跃用户。"""
    grouped: Dict[tuple, List[dict]] = {}
    for raw in sessions or []:
        if not isinstance(raw, dict):
            continue
        prepared = _prepare_session(raw)
        if not is_wan_remote_session(prepared):
            continue
        gkey = _session_device_group_key(prepared)
        if not gkey[0]:
            continue
        sid = _session_sid(prepared)
        uid = str(prepared.get('user_id') or '').strip()
        if not sid or not uid:
            continue
        grouped.setdefault(gkey, []).append(_profile_from_prepared_session(prepared))

    profiles: Dict[tuple, dict] = {}
    name = (instance_name or '').strip()
    for gkey, members in grouped.items():
        if len(members) == 1:
            prof = dict(members[0])
            prof.pop('_activity_epoch', None)
            profiles[gkey] = prof
            continue
        user_ids = {
            str(m.get('user_id') or '').strip()
            for m in members
        }
        user_ids.discard('')
        ranked = sorted(
            members,
            key=lambda m: float(m.get('_activity_epoch') or 0.0),
            reverse=True,
        )
        if len(user_ids) < 2:
            prof = dict(ranked[0])
            prof.pop('_activity_epoch', None)
            profiles[gkey] = prof
            continue
        primary = dict(ranked[0])
        primary.pop('_activity_epoch', None)
        profiles[gkey] = primary
        if settle_same_tick_switch and name:
            primary_uid = str(primary.get('user_id') or '').strip()
            for member in ranked[1:]:
                uid = str(member.get('user_id') or '').strip()
                if not uid or uid == primary_uid:
                    continue
                old_sid = str(member.get('sid') or '').strip()
                if not old_sid:
                    continue
                prev_prof = dict(member)
                prev_prof.pop('_activity_epoch', None)
                _settle_outgoing_device_user(name, old_sid, uid, prev_prof)
    return profiles


def _sessions_by_sid(sessions: list) -> Dict[str, dict]:
    by_sid: Dict[str, dict] = {}
    for raw in sessions or []:
        if not isinstance(raw, dict):
            continue
        prepared = _prepare_session(raw)
        sid = _session_sid(prepared)
        if sid:
            by_sid[sid] = prepared
    return by_sid


def _browse_media_from_session(session: Optional[dict]) -> dict:
    if not session or not isinstance(session, dict):
        return {}
    prepared = _prepare_session(session)
    mode = str(prepared.get('session_mode') or '').strip()
    if mode == 'viewing' or str(prepared.get('viewing_title') or '').strip():
        series = str(prepared.get('viewing_series_name') or '').strip()
        label = str(prepared.get('viewing_episode_label') or '').strip()
        main = str(prepared.get('viewing_title') or '').strip()
    else:
        series = str(prepared.get('series_name') or '').strip()
        label = str(prepared.get('episode_label') or '').strip()
        main = str(
            prepared.get('episode_title') or prepared.get('title') or '',
        ).strip()
    endpoint = str(prepared.get('remote_endpoint') or '').strip()
    return {
        'series_name': series,
        'episode_label': label,
        'episode_title': main,
        'viewing_title': main or series,
        'production_year': prepared.get('production_year'),
    }


def _meta_from_session(session: Optional[dict]) -> dict:
    if not session or not isinstance(session, dict):
        return {}
    prepared = _prepare_session(session)
    sid = _session_sid(prepared)
    media = _browse_media_from_session(prepared)
    endpoint = str(prepared.get('remote_endpoint') or '').strip()
    meta = {
        'user_name': str(prepared.get('user_name') or '').strip(),
        'user_id': str(prepared.get('user_id') or '').strip(),
        'device_name': str(prepared.get('device_name') or '').strip(),
        'client': str(prepared.get('client') or '').strip(),
        'client_ip': parse_endpoint_ip(endpoint),
        **media,
    }
    if sid:
        meta['emby_session_id'] = sid
        meta['session_id'] = sid
        meta['id'] = sid
    return meta


def _meta_from_lucky_row(row: dict) -> dict:
    emby_user = str(row.get('emby_user') or '').strip()
    user_name = emby_user.split('·', 1)[0].strip() if emby_user else ''
    user_id = ''
    key = str(row.get('browse_persist_key') or '').strip()
    if key.startswith('browse:') and not key.startswith('browse:sid:'):
        parts = key.split(':')
        if len(parts) >= 3:
            user_id = str(parts[1] or '').strip()
    return {
        'user_name': user_name,
        'user_id': user_id,
        'viewing_title': str(row.get('media_label') or '').strip(),
        'client': str(row.get('client') or '').strip(),
        'device_name': str(row.get('device_name') or '').strip(),
        'client_ip': str(row.get('client_ip') or '').strip(),
    }


def _resolve_meta(instance_name: str, sid: str, sessions: list, analysis: dict) -> dict:
    import emby.traffic.playback as emby_playback_traffic

    name = (instance_name or '').strip()
    sid = str(sid or '').strip()
    by_sid = {}
    for raw in sessions or []:
        if not isinstance(raw, dict):
            continue
        prepared = _prepare_session(raw)
        psid = _session_sid(prepared)
        if psid:
            by_sid[psid] = prepared
    if sid in by_sid:
        meta = _meta_from_session(by_sid[sid])
        stored = emby_playback_traffic.get_browse_session_meta(name, sid)
        live_uid = str(meta.get('user_id') or '').strip()
        stored_uid = str(stored.get('user_id') or '').strip()
        if live_uid and stored_uid and live_uid != stored_uid:
            emby_playback_traffic.pop_browse_session_meta(name, sid)
        elif stored_uid and not live_uid:
            meta['user_id'] = stored_uid
        if meta.get('user_name'):
            emby_playback_traffic.remember_browse_session_meta(name, sid, meta)
            return meta
    stored = emby_playback_traffic.get_browse_session_meta(name, sid)
    if stored.get('user_name'):
        return stored
    for key in _all_browse_keys_for_sid(name, sid):
        cached = _meta_for_browse_key(name, key)
        if cached.get('user_name'):
            return cached
    for row in (analysis or {}).get('rows') or []:
        key = str(row.get('browse_persist_key') or '').strip()
        if _sid_from_browse_key(key) != sid:
            continue
        meta = _meta_from_lucky_row(row)
        if meta.get('user_name'):
            emby_playback_traffic.remember_browse_session_meta(name, sid, meta)
            return meta
    return {}


def _alloc_browse_segment_id(instance_name: str) -> int:
    name = (instance_name or '').strip()
    with _lock:
        if name not in _next_segment_id:
            import emby.traffic.db as emby_traffic_db
            emby_traffic_db._ensure_emby_schema()
            conn = emby_traffic_db.get_conn()
            try:
                c = conn.cursor()
                c.execute(
                    'SELECT COALESCE(MAX(segment_id), 0) FROM emby_browse_upload_facts'
                    ' WHERE instance_name = ?',
                    (name,),
                )
                row = c.fetchone()
                _next_segment_id[name] = int(row[0] or 0) + 1
            finally:
                conn.close()
        seg_id = _next_segment_id[name]
        _next_segment_id[name] = seg_id + 1
        return seg_id


def _settle_browse_session(
    instance_name: str,
    sid: str,
    meta: dict,
    *,
    settle_reason: str,
    restrict_user_id: str = '',
) -> None:
    name = (instance_name or '').strip()
    if not name or not sid:
        return
    import emby.traffic.playback as emby_playback_traffic
    import emby.traffic.db as emby_traffic_db

    restrict_uid = str(
        restrict_user_id or (
            meta.get('user_id') if settle_reason == 'user_switch' else ''
        ) or '',
    ).strip()
    if restrict_uid:
        key_list = _browse_keys_for_user_sid(name, sid, restrict_uid, meta)
    else:
        key_list = _all_browse_keys_for_sid(name, sid, meta)

    upload_int = 0
    used_keys: List[str] = []
    for key in key_list:
        part = emby_playback_traffic.take_accumulated_browse_upload_by_key(name, key)
        if part is not None and int(part) > 0:
            upload_int += int(part)
            used_keys.append(key)

    if upload_int <= 0:
        peek = emby_playback_traffic.peek_browse_upload_bytes_for_sid(name, sid, meta)
        logger.debug(
            f'[Browse:{name}] 选片结算跳过 sid={sid} reason={settle_reason} '
            f'bytes=0 peek={peek} user={meta.get("user_name") or "?"}',
        )
        return

    user_name = str(meta.get('user_name') or '').strip()
    if not user_name:
        logger.debug(
            f'[Browse:{name}] 选片结算跳过 sid={sid} reason={settle_reason} '
            f'bytes={upload_int} user=空 keys={used_keys}',
        )
        return

    min_bytes = max(0, int(_tick_min_upload_bytes))
    if upload_int < min_bytes:
        emby_playback_traffic.pop_browse_session_meta(name, sid)
        for key in used_keys:
            _pop_key_meta(name, key)
        logger.debug(
            f'[Browse:{name}] 选片未达入账阈值 sid={sid} reason={settle_reason} '
            f'bytes={upload_int} min={min_bytes} user={user_name}',
        )
        return

    stopped_at = emby_traffic_db._now().strftime('%Y-%m-%d %H:%M:%S')
    ok = emby_traffic_db.save_browse_upload_fact(
        name,
        _alloc_browse_segment_id(name),
        user_name,
        meta.get('user_id') or restrict_uid or '',
        stopped_at,
        upload_int,
        meta.get('viewing_title') or meta.get('episode_title') or '',
        settle_reason,
        series_name=meta.get('series_name') or '',
        episode_label=meta.get('episode_label') or '',
        episode_title=meta.get('episode_title') or '',
        device_name=meta.get('device_name') or '',
        client=meta.get('client') or '',
        client_ip=meta.get('client_ip') or '',
        production_year=meta.get('production_year'),
        min_upload_bytes=min_bytes,
    )
    if ok:
        emby_playback_traffic.pop_browse_session_meta(name, sid)
        for key in used_keys:
            _pop_key_meta(name, key)
        logger.info(
            f'[Browse:{name}] 选片入库 sid={sid} bytes={upload_int} '
            f'reason={settle_reason} keys={used_keys} user={user_name}',
        )
    else:
        logger.warning(
            f'[Browse:{name}] 选片入库失败 sid={sid} bytes={upload_int} '
            f'reason={settle_reason} keys={used_keys}',
        )


def _current_browse_credit_keys(analysis: dict) -> Set[str]:
    keys: Set[str] = set()
    for row in (analysis or {}).get('rows') or []:
        if str(row.get('billing_state') or '') != 'browse_credited':
            continue
        key = str(row.get('browse_persist_key') or '').strip()
        if key:
            keys.add(key)
    return keys


def sync_browse_credit_from_analysis(
    instance_name: str,
    analysis: dict,
    sessions: list,
    *,
    now_mono: Optional[float] = None,
) -> None:
    """Lucky 连接从「选片入账」消失时，按会话 sid 结算累计桶。"""
    name = (instance_name or '').strip()
    if not name:
        return
    now_mono = time.monotonic() if now_mono is None else float(now_mono)
    _cache_meta_from_analysis(name, analysis)
    current = _current_browse_credit_keys(analysis)
    by_sid = _sessions_by_sid(sessions)
    with _lock:
        prev = set(_active_browse_keys.get(name) or set())
        dropped = prev - current
        gaps = _browse_key_gap_since.setdefault(name, {})

        for key in current:
            gaps.pop(key, None)
            orphan = _orphan_bucket_since.setdefault(name, {})
            orphan.pop(key, None)

        for key in dropped:
            sid = _sid_from_browse_key(key)
            if not sid:
                gaps.pop(key, None)
                continue
            session = by_sid.get(sid)
            if session is None:
                gaps.pop(key, None)
                meta = _resolve_meta(name, sid, sessions, analysis)
                _settle_browse_session(name, sid, meta, settle_reason='disconnect')
                continue
            mode = str(session.get('session_mode') or '').strip()
            if mode in ('playing', 'paused'):
                gaps.pop(key, None)
                _redirect_or_settle_browse_playback_start(
                    name, sid, session, sessions, analysis,
                    now_mono=now_mono,
                )
                continue
            if key not in gaps:
                gaps[key] = now_mono
                logger.info(
                    f'[Browse:{name}] 选片连接结束待结算 key={key} sid={sid}',
                )

        for key, since in list(gaps.items()):
            sid = _sid_from_browse_key(key)
            if not sid:
                gaps.pop(key, None)
                continue
            session = by_sid.get(sid)
            if session is None:
                gaps.pop(key, None)
                meta = _resolve_meta(name, sid, sessions, analysis)
                _settle_browse_session(name, sid, meta, settle_reason='disconnect')
                continue
            mode = str(session.get('session_mode') or '').strip()
            if mode in ('playing', 'paused'):
                gaps.pop(key, None)
                _redirect_or_settle_browse_playback_start(
                    name, sid, session, sessions, analysis,
                    now_mono=now_mono,
                )
                continue
            if now_mono - float(since) < BROWSE_CONN_END_GRACE_SECONDS:
                continue
            gaps.pop(key, None)
            meta = _resolve_meta(name, sid, sessions, analysis)
            _settle_browse_session(name, sid, meta, settle_reason='browse_conn_end')

        _active_browse_keys[name] = current


def _settle_orphan_buckets(
    instance_name: str,
    sessions: list,
    analysis: dict,
    *,
    now_mono: float,
) -> None:
    """兜底：累计桶仍有字节、且已脱管超过阈值时入库。"""
    import emby.traffic.playback as emby_playback_traffic

    name = (instance_name or '').strip()
    if not name:
        return
    active = _current_browse_credit_keys(analysis)
    bucket = emby_playback_traffic.peek_browse_upload_bucket(name)
    with _lock:
        gaps = dict(_browse_key_gap_since.get(name) or {})
        orphan_since = _orphan_bucket_since.setdefault(name, {})

    for key, amount in bucket.items():
        if int(amount or 0) <= 0 or key in active:
            continue
        if key in gaps:
            continue
        sid = _sid_from_browse_key(key)
        if not sid:
            continue
        first_seen = orphan_since.get(key)
        if first_seen is None:
            orphan_since[key] = now_mono
            continue
        if now_mono - float(first_seen) < ORPHAN_BUCKET_MIN_AGE_SECONDS:
            continue
        meta = _resolve_meta(name, sid, sessions, analysis)
        if not meta.get('user_name'):
            continue
        orphan_since.pop(key, None)
        _settle_browse_session(name, sid, meta, settle_reason='orphan_bucket')


def _handle_offline(instance_name: str, now_mono: float) -> None:
    bucket = _offline_since
    if bucket.get(instance_name) is None:
        bucket[instance_name] = now_mono
    elapsed = now_mono - float(bucket[instance_name])
    if elapsed < OFFLINE_TIMEOUT_SECONDS:
        return
    import emby.traffic.playback as emby_playback_traffic

    name = (instance_name or '').strip()
    browse_bucket = emby_playback_traffic.peek_browse_upload_bucket(name)
    for key, amount in list(browse_bucket.items()):
        if int(amount or 0) <= 0:
            continue
        sid = _sid_from_browse_key(key)
        if not sid:
            continue
        meta = emby_playback_traffic.get_browse_session_meta(name, sid)
        if not meta.get('user_name'):
            continue
        _settle_browse_session(name, sid, meta, settle_reason='timeout_offline')
    with _lock:
        _active_browse_keys.pop(name, None)
        _browse_key_gap_since.pop(name, None)
    bucket[instance_name] = now_mono


def tick(
    instance_name: str,
    sessions: list,
    *,
    api_online: bool = True,
    credit_enabled: bool = True,
    analysis: Optional[dict] = None,
    min_upload_bytes: Optional[int] = None,
) -> None:
    """每个采集 tick：同步 Lucky 选片入账状态并兜底结算。"""
    global _tick_min_upload_bytes
    if min_upload_bytes is not None:
        _tick_min_upload_bytes = max(0, int(min_upload_bytes))
    name = (instance_name or '').strip()
    if not name or not credit_enabled:
        return

    now_mono = time.monotonic()
    with _lock:
        if name not in _tick_logged:
            _tick_logged.add(name)
            logger.info(
                f'[Browse:{name}] 选片结算器已加载 ({SETTLER_VERSION})',
            )
        if not api_online:
            _handle_offline(name, now_mono)
            return
        _offline_since[name] = None

    if isinstance(analysis, dict):
        _cache_meta_from_analysis(name, analysis)

    _sync_user_switch_settlement(name, sessions)
    _sync_device_user_switch_settlement(name, sessions)
    _sync_superseded_browse_settlement(name, sessions)
    _sync_emby_browse_transitions(
        name, sessions, now_mono=now_mono, analysis=analysis,
    )
    if isinstance(analysis, dict):
        sync_browse_credit_from_analysis(
            name, analysis, sessions, now_mono=now_mono,
        )
        _settle_orphan_buckets(
            name, sessions, analysis, now_mono=now_mono,
        )

    import emby.browse.continuous as emby_continuous_playback
    emby_continuous_playback.tick(name, sessions, now_mono=now_mono)


def flush_instance(instance_name: str, *, settle_reason: str = 'instance_reset') -> None:
    name = (instance_name or '').strip()
    if not name:
        return
    import emby.traffic.playback as emby_playback_traffic

    browse_bucket = emby_playback_traffic.peek_browse_upload_bucket(name)
    for key, amount in list(browse_bucket.items()):
        if int(amount or 0) <= 0:
            continue
        sid = _sid_from_browse_key(key)
        if not sid:
            continue
        meta = _meta_for_browse_key(name, key)
        if not meta.get('user_name'):
            meta = emby_playback_traffic.get_browse_session_meta(name, sid)
        if not meta.get('user_name'):
            continue
        _settle_browse_session(name, sid, meta, settle_reason=settle_reason)


def purge_user(
    instance_name: str,
    user_name: str,
    *,
    user_ids: list = None,
) -> None:
    """Emby 用户删除后，清理选片结算器内该用户的跟踪状态。"""
    from emby.lucky.verdict import persist_key_belongs_to_user

    name = (instance_name or '').strip()
    user_fold = str(user_name or '').strip().casefold()
    if not name or not user_fold:
        return
    uid_set = {
        str(uid or '').strip()
        for uid in (user_ids or [])
        if str(uid or '').strip()
    }

    def _meta_matches(meta: dict) -> bool:
        if not isinstance(meta, dict):
            return False
        meta_name = str(meta.get('user_name') or '').strip().casefold()
        meta_uid = str(meta.get('user_id') or '').strip()
        return meta_name == user_fold or (meta_uid and meta_uid in uid_set)

    with _lock:
        meta_map = _browse_key_meta.get(name) or {}
        for key, meta in list(meta_map.items()):
            if _meta_matches(meta):
                meta_map.pop(key, None)
                (_active_browse_keys.get(name) or set()).discard(key)
                (_browse_key_gap_since.get(name) or {}).pop(key, None)
                (_orphan_bucket_since.get(name) or {}).pop(key, None)
        for sid, uid in list((_last_session_users.get(name) or {}).items()):
            if uid in uid_set:
                (_last_session_users.get(name) or {}).pop(sid, None)
        for sid, profile in list((_last_session_profiles.get(name) or {}).items()):
            if _meta_matches(profile):
                (_last_session_profiles.get(name) or {}).pop(sid, None)
        for dev_key, profile in list((_last_device_profiles.get(name) or {}).items()):
            if _meta_matches(profile):
                (_last_device_profiles.get(name) or {}).pop(dev_key, None)


def clear_instance(instance_name: str, *, flush: bool = True) -> None:
    name = (instance_name or '').strip()
    if not name:
        return
    if flush:
        flush_instance(name, settle_reason='instance_reset')
    with _lock:
        _active_browse_keys.pop(name, None)
        _browse_key_gap_since.pop(name, None)
        _browse_key_meta.pop(name, None)
        _last_session_modes.pop(name, None)
        _last_session_users.pop(name, None)
        _last_session_profiles.pop(name, None)
        _last_device_profiles.pop(name, None)
        _orphan_bucket_since.pop(name, None)
        _offline_since.pop(name, None)
        _next_segment_id.pop(name, None)
    import emby.browse.continuous as emby_continuous_playback
    emby_continuous_playback.clear_instance(name)
