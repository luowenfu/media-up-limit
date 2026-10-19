"""Web 层公共工具"""

from flask import jsonify
import core.config_manager as config_manager
import logging

from web import state

logger = logging.getLogger(__name__)
GENERIC_API_ERROR = '服务器内部错误，请稍后重试'

def reload_all_config(skip_qb_ops: bool = False):
    try:
        state.monitor.reload_config(refresh_status=False, skip_qb_ops=skip_qb_ops)
    except Exception as e:
        logger.error(f"qB 配置重载失败: {e}", exc_info=True)
    if state.emby_monitor:
        try:
            state.emby_monitor.reload_config()
        except Exception as e:
            logger.error(f"Emby 配置重载失败: {e}", exc_info=True)


def fresh_active_instance_names() -> list:
    """从配置文件实时读取当前设备名称，避免内存配置滞后"""
    return config_manager.get_active_instance_names(config_manager.load_config())


def fresh_active_emby_instance_names() -> list:
    return config_manager.get_active_emby_instance_names(config_manager.load_config())


def no_cache_json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    return response


def require_orphan_data_policy(has_orphan: bool, data_policy: str, add_mode: bool = True):
    if not has_orphan:
        return
    valid = ('restore', 'fresh') if add_mode else ('restore_orphan', 'keep_current')
    if data_policy not in valid:
        if add_mode:
            raise ValueError('检测到该名称存在历史数据，请选择恢复设备或新建设备')
        raise ValueError('检测到该名称存在历史数据，请选择恢复为旧数据或保持现有数据')


def emby_feature_enabled() -> bool:
    if state.emby_monitor is None:
        return False
    return bool(
        config_manager.get_global_config(state.emby_monitor.config).get('emby_enabled', False)
    )


def emby_required():
    if state.emby_monitor is None:
        return jsonify({'success': False, 'error': 'Emby 模块未初始化'}), 503
    if not emby_feature_enabled():
        return jsonify({'success': False, 'error': 'Emby 功能未开启'}), 403
    return None


def get_emby_client(instance_name: str):
    if not state.emby_monitor:
        return None, (jsonify({'success': False, 'error': 'Emby 模块未初始化'}), 500)
    with state.emby_monitor._config_lock:
        client = state.emby_monitor.clients.get(instance_name)
    if not client:
        return None, (jsonify({'success': False, 'error': '设备不存在'}), 404)
    return client, None
