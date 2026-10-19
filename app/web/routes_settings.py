"""全局设置、用户偏好、系统日志与系统信息接口"""

import os
import time
from flask import Blueprint
from flask import jsonify
from flask import request
import core.config_manager as config_manager
import logging

from core.paths import APP_LOG_PATH, CONFIG_PATH, EMBY_DB_PATH, QB_DB_PATH
from web import state
from web.auth import get_session_username
from web.common import GENERIC_API_ERROR

START_TIME = time.time()
logger = logging.getLogger(__name__)
bp = Blueprint('routes_settings', __name__)

@bp.route('/api/config/global', methods=['GET'])
def api_config_global_get():
    try:
        global_cfg = config_manager.mask_global_for_api(
            config_manager.get_global_config(state.monitor.config),
            state.monitor.config,
        )
        return jsonify({'success': True, 'data': global_cfg})
    except Exception as e:
        logger.error(f"API GET /config/global 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/config/global', methods=['PUT'])
def api_config_global_update():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求无效'}), 400
        
        old_port = state.monitor.config.get('global', {}).get('web_port', 8765)
        validated, full_config = config_manager.update_global(
            data, base_config=state.monitor.config
        )
        if not state.monitor.apply_config(full_config, refresh_status=False):
            return jsonify({'success': False, 'error': '保存失败'}), 500
        if state.emby_monitor and not state.emby_monitor.apply_config(full_config):
            return jsonify({'success': False, 'error': 'Emby 配置应用失败'}), 500

        port_changed = validated.get('web_port') != old_port
        msg = '保存成功，需重启生效' if port_changed else '保存成功'

        return jsonify({
            'success': True,
            'message': msg,
            'data': config_manager.mask_global_for_api(validated, full_config),
            'port_changed': port_changed
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"API PUT /config/global 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/user/prefs/devices', methods=['GET'])
def api_user_device_prefs_get():
    try:
        username = get_session_username()
        if not username:
            return jsonify({'success': False, 'error': '未登录', 'auth_required': True}), 401
        import core.user_prefs_store as user_prefs_store
        return jsonify({
            'success': True,
            'data': user_prefs_store.get_device_prefs(username),
        })
    except Exception as e:
        logger.error(f"API GET /user/prefs/devices 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/user/prefs/devices', methods=['PUT'])
def api_user_device_prefs_update():
    try:
        username = get_session_username()
        if not username:
            return jsonify({'success': False, 'error': '未登录', 'auth_required': True}), 401
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({'success': False, 'error': '请求无效'}), 400
        import core.user_prefs_store as user_prefs_store
        saved = user_prefs_store.update_device_prefs(username, data)
        return jsonify({'success': True, 'data': saved})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"API PUT /user/prefs/devices 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/system/info')
def api_system_info():
    """运行时长与数据文件大小，供全局设置的「系统」「数据」页使用"""
    try:
        files = []
        for label, path in (
            ('traffic.db', QB_DB_PATH),
            ('emby_traffic.db', EMBY_DB_PATH),
            ('config.yaml', CONFIG_PATH),
            ('app.log', APP_LOG_PATH),
        ):
            size = os.path.getsize(path) if os.path.exists(path) else 0
            files.append({'name': label, 'size': size})
        return jsonify({
            'success': True,
            'uptime_seconds': int(time.time() - START_TIME),
            'files': files,
        })
    except Exception as e:
        logger.error(f"API GET /system/info 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500