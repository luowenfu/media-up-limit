"""Web 用户界面偏好（按登录账号存储）"""

import logging
import threading

from core.secrets_store import _read_json, _write_json

logger = logging.getLogger(__name__)

_lock = threading.RLock()

from core.paths import USER_PREFS_PATH  # noqa: E402

DEVICE_VIEW_MODES = frozenset({'qb', 'emby', 'merge'})
DEVICE_PREF_KEYS = (
    'device_view_mode',
    'merge_qb_selection',
    'merge_emby_selection',
    'merge_qb_order',
    'merge_emby_order',
)


def _load_all() -> dict:
    return _read_json(USER_PREFS_PATH, {})


def _save_all(data: dict) -> None:
    _write_json(USER_PREFS_PATH, data)


def _normalize_string_list(value) -> list:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        name = str(item or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def validate_device_prefs(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('请求无效')

    result = {}
    if 'device_view_mode' in data:
        mode = str(data.get('device_view_mode') or '').strip().lower()
        if mode not in DEVICE_VIEW_MODES:
            raise ValueError('device_view_mode 无效')
        result['device_view_mode'] = mode

    list_fields = {
        'merge_qb_selection': 'merge_qb_selection',
        'merge_emby_selection': 'merge_emby_selection',
        'merge_qb_order': 'merge_qb_order',
        'merge_emby_order': 'merge_emby_order',
    }
    for src_key, dst_key in list_fields.items():
        if src_key in data:
            result[dst_key] = _normalize_string_list(data.get(src_key))

    return result


def get_device_prefs(username: str) -> dict:
    user = str(username or '').strip()
    if not user:
        return {}
    with _lock:
        all_prefs = _load_all()
        stored = all_prefs.get(user, {}).get('devices', {})
        if not isinstance(stored, dict):
            return {}
        return {key: stored[key] for key in DEVICE_PREF_KEYS if key in stored}


def update_device_prefs(username: str, partial: dict) -> dict:
    user = str(username or '').strip()
    if not user:
        raise ValueError('未登录')

    validated = validate_device_prefs(partial)
    if not validated:
        return get_device_prefs(user)

    with _lock:
        all_prefs = _load_all()
        user_prefs = all_prefs.setdefault(user, {})
        devices = user_prefs.setdefault('devices', {})
        devices.update(validated)
        _save_all(all_prefs)
        return get_device_prefs(user)
