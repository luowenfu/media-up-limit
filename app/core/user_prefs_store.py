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
    'nav_collapsed',
    'chart_block_orders',
    'card_block_orders',
)
NAV_GROUPS = ('qb', 'emby')
CHART_KINDS = ('qb', 'emby')
CHART_BLOCKS = ('trend', 'rank')
CARD_BLOCKS = ('main', 'side')
BLOCK_ORDER_KEYS = {'chart_block_orders': CHART_BLOCKS, 'card_block_orders': CARD_BLOCKS}


def _load_all() -> dict:
    return _read_json(USER_PREFS_PATH, {})


def _save_all(data: dict) -> None:
    _write_json(USER_PREFS_PATH, data)


def _normalize_chart_order(value, blocks=CHART_BLOCKS) -> list:
    picked = [item for item in _normalize_string_list(value) if item in blocks]
    for item in blocks:
        if item not in picked:
            picked.append(item)
    return picked


def _normalize_chart_orders(value, blocks=CHART_BLOCKS) -> dict:
    """按设备名保存。结构：{qb: {设备名: [trend, rank]}, emby: {...}}；设备卡片同结构，块为 main/side"""
    if not isinstance(value, dict):
        return {}
    result = {}
    for kind in CHART_KINDS:
        block = value.get(kind)
        if not isinstance(block, dict):
            continue
        orders = {}
        for name, order in block.items():
            device = str(name or '').strip()
            if not device:
                continue
            orders[device] = _normalize_chart_order(order, blocks)
        if orders:
            result[kind] = orders
    return result


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

    if 'nav_collapsed' in data:
        result['nav_collapsed'] = [g for g in _normalize_string_list(data.get('nav_collapsed')) if g in NAV_GROUPS]

    for key, blocks in BLOCK_ORDER_KEYS.items():
        if key in data:
            result[key] = _normalize_chart_orders(data.get(key), blocks)

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
        result = {key: stored[key] for key in DEVICE_PREF_KEYS if key in stored}
        for key, blocks in BLOCK_ORDER_KEYS.items():
            if key in result:
                result[key] = _normalize_chart_orders(result[key], blocks)
        return result


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
        pending = {key: validated.pop(key, None) for key in BLOCK_ORDER_KEYS}
        devices.update(validated)
        for key, orders in pending.items():
            if not orders:
                continue
            stored_orders = devices.get(key)
            if not isinstance(stored_orders, dict):
                stored_orders = {}
            for kind, by_device in orders.items():
                bucket = stored_orders.get(kind)
                if not isinstance(bucket, dict):
                    bucket = {}
                bucket.update(by_device)
                stored_orders[kind] = bucket
            devices[key] = stored_orders
        _save_all(all_prefs)
        return get_device_prefs(user)
