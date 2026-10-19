"""qB 设备配置接口：增删改、连通与限速测试、孤儿数据检查"""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from flask import Blueprint
from flask import jsonify
from flask import request
import core.config_manager as config_manager
import logging
import qb.traffic_db as traffic_db
import threading

from qb.monitor import estimate_test_timeout
from qb.monitor import run_connection_test
from qb.monitor import run_speed_limit_test
from web import state
from web.common import fresh_active_instance_names, no_cache_json, reload_all_config, require_orphan_data_policy
from web.common import GENERIC_API_ERROR

logger = logging.getLogger(__name__)
bp = Blueprint('routes_qb_config', __name__)

@bp.route('/api/config/instances', methods=['GET'])
def api_config_instances_list():
    try:
        instances = config_manager.get_instances(state.monitor.config)
        masked = [config_manager.mask_instance_for_api(i) for i in instances]
        return jsonify({'success': True, 'data': masked})
    except Exception as e:
        logger.error(f"API GET /config/instances 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/config/instances/<name>', methods=['GET'])
def api_config_instance_get(name):
    try:
        inst = config_manager.get_instance(name, state.monitor.config)
        if not inst:
            return jsonify({'success': False, 'error': '设备不存在'}), 404
        return jsonify({
            'success': True,
            'data': config_manager.mask_instance_for_api(inst)
        })
    except Exception as e:
        logger.error(f"API GET /config/instances/{name} 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


def _defer_instance_sync(instance_name: str) -> None:
    """后台同步，避免保存 API 被长连接重试阻塞"""
    def _run():
        try:
            state.monitor.sync_instance_after_save(
                instance_name,
                connection_changed=True,
                force_attempt=True,
            )
        except Exception as e:
            logger.error(
                f"后台同步设备 {instance_name} 失败: {e}", exc_info=True)

    threading.Thread(
        target=_run, name=f'sync-after-save-{instance_name}', daemon=True,
    ).start()


def _apply_config_after_save(skip_qb_ops: bool = False,
                             sync_instance: str = None,
                             defer_reload: bool = False) -> None:
    """保存后重载内存配置；纯本地项可延后 reload，避免 HTTP 被采集线程阻塞"""
    def _reload():
        reload_all_config(skip_qb_ops=skip_qb_ops)

    if defer_reload:
        threading.Thread(
            target=_reload, name='reload-after-save', daemon=True,
        ).start()
    else:
        _reload()

    if not sync_instance:
        return

    def _run():
        try:
            state.monitor.sync_instance_after_save(
                sync_instance,
                connection_changed=True,
                force_attempt=True,
            )
        except Exception as e:
            logger.error(
                f"后台同步设备 {sync_instance} 失败: {e}", exc_info=True)

    threading.Thread(
        target=_run, name=f'sync-after-save-{sync_instance}', daemon=True,
    ).start()


def _run_instance_test(validated: dict, test_type: str) -> dict:
    """在独立线程中执行测试，避免连接挂起导致请求永不返回"""
    if test_type == 'connect':
        runner = run_connection_test
    elif test_type == 'limit':
        runner = run_speed_limit_test
    else:
        raise ValueError('无效的 test_type，应为 connect 或 limit')

    timeout_sec = estimate_test_timeout(validated, test_type)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runner, validated)
        try:
            return future.result(timeout=timeout_sec)
        except FuturesTimeoutError:
            return {
                'success': False,
                'error': '测试超时',
                'steps': [],
            }


def _apply_add_data_policy(target_name: str, data_policy: str, active_names: list):
    if not traffic_db.is_orphaned_instance(target_name, active_names):
        return
    require_orphan_data_policy(True, data_policy, add_mode=True)
    if data_policy == 'fresh':
        traffic_db.delete_instance_data(target_name)


def _apply_rename_data_policy(old_name: str, new_name: str,
                              data_policy: str, active_names: list):
    if old_name == new_name:
        return
    if not traffic_db.is_orphaned_instance(
            new_name, active_names, renaming_from=old_name):
        traffic_db.rename_instance_data(old_name, new_name)
        return
    require_orphan_data_policy(True, data_policy, add_mode=False)
    if data_policy == 'restore_orphan':
        traffic_db.delete_instance_data(old_name)
    elif data_policy == 'keep_current':
        traffic_db.delete_instance_data(new_name)
        traffic_db.rename_instance_data(old_name, new_name)


@bp.route('/api/config/instances/orphan-check', methods=['GET'])
def api_config_instance_orphan_check():
    try:
        name = str(request.args.get('name', '')).strip()
        if not name:
            return jsonify({'success': False, 'error': '参数缺失'}), 400
        renaming_from = str(request.args.get('renaming_from', '')).strip() or None
        active_names = fresh_active_instance_names()
        has_orphan = traffic_db.is_orphaned_instance(
            name, active_names, renaming_from=renaming_from)
        return no_cache_json({
            'success': True,
            'has_orphaned_data': has_orphan,
            'name': name,
        })
    except Exception as e:
        logger.error(f"API GET /config/instances/orphan-check 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/config/instances/orphan-data', methods=['GET'])
def api_config_instance_orphan_data():
    try:
        active_names = fresh_active_instance_names()
        orphans = traffic_db.get_orphaned_instances(active_names)
        return no_cache_json({'success': True, 'data': orphans})
    except Exception as e:
        logger.error(f"API GET /config/instances/orphan-data 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/config/instances/orphan-data/<name>', methods=['DELETE'])
def api_config_instance_orphan_data_delete(name):
    try:
        active_names = fresh_active_instance_names()
        if name in active_names:
            return jsonify({'success': False, 'error': '该名称仍被设备使用'}), 400
        if not traffic_db.is_orphaned_instance(name, active_names):
            return jsonify({'success': False, 'error': '孤儿数据不存在'}), 404
        traffic_db.delete_instance_data(name)
        logger.info(f"已删除孤儿数据: {name}")
        return jsonify({'success': True, 'message': '删除成功'})
    except Exception as e:
        logger.error(
            f"API DELETE /config/instances/orphan-data/{name} 错误: {e}",
            exc_info=True,
        )
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/config/instances/test', methods=['POST'])
def api_config_instance_test():
    """测试实例连通性或限速读写能力（test_type: connect | limit）"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求无效'}), 400

        test_type = str(data.pop('test_type', 'connect')).strip().lower()
        original_name = str(data.pop('_original_name', '') or '').strip()
        existing = (
            config_manager.get_instance(original_name, state.monitor.config)
            if original_name else None
        )
        data = config_manager.resolve_instance_credentials_for_test_with_existing(
            data, existing
        )
        if not data.get('name') and original_name:
            data['name'] = original_name

        user = str(data.get('username', '')).strip()
        pwd = data.get('password', '') or ''
        if user and not pwd:
            return jsonify({
                'success': False,
                'error': '请填写密码',
            }), 400

        validated = config_manager.validate_instance_for_test(data)
        result = _run_instance_test(validated, test_type)
        payload = {'success': result['success'], 'steps': result.get('steps', [])}
        if result.get('error'):
            payload['error'] = result['error']
        return jsonify(payload)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"API POST /config/instances/test 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/config/instances', methods=['POST'])
def api_config_instance_add():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求无效'}), 400
        
        attempt_sync, reachable, data_policy = config_manager.pop_instance_save_flags(data)
        target_name = str(data.get('name', '')).strip()
        active_names = fresh_active_instance_names()
        _apply_add_data_policy(target_name, data_policy, active_names)

        validated = config_manager.add_instance(data)
        skip_qb_ops = reachable is False or not attempt_sync
        sync_name = (
            validated['name']
            if attempt_sync and reachable is not False else None
        )
        _apply_config_after_save(skip_qb_ops=skip_qb_ops, sync_instance=sync_name)
        synced = sync_name is not None

        policy_note = ''
        if data_policy == 'restore':
            policy_note = '（恢复历史数据）'
        elif data_policy == 'fresh':
            policy_note = '（清空后新建）'
        traffic_db.add_device_event(
            validated['name'],
            'instance_added',
            None,
            f'通过 Web 界面添加设备{policy_note}',
        )
        logger.info(f"设备已添加: {validated['name']}{policy_note}")

        return jsonify({
            'success': True,
            'message': '添加成功',
            'synced': synced,
            'data': config_manager.mask_instance_for_api(validated)
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"API POST /config/instances 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/config/instances/<name>', methods=['PUT'])
def api_config_instance_update(name):
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求无效'}), 400
        
        attempt_sync, reachable, data_policy = config_manager.pop_instance_save_flags(data)
        
        existing = config_manager.get_instance(name, state.monitor.config)
        data = config_manager.resolve_instance_credentials(data, existing)

        if config_manager.instance_cycle_settings_changed(existing, data):
            if reachable is False or not attempt_sync:
                raise ValueError('设备不在线，待设备上线后才能修改规则')
            if not traffic_db.is_instance_online(name):
                raise ValueError('设备不在线，待设备上线后才能修改规则')

        new_name = str(data.get('name', '')).strip() or name
        active_names = fresh_active_instance_names()
        _apply_rename_data_policy(name, new_name, data_policy, active_names)

        validated = config_manager.update_instance(name, data)
        skip_qb_ops = (
            reachable is False
            or not attempt_sync
            or config_manager.instance_only_basics_changed(existing, validated)
        )
        sync_name = None
        if (attempt_sync and reachable is not False
                and not config_manager.instance_only_basics_changed(existing, validated)):
            sync_name = validated['name']
        if skip_qb_ops and not sync_name:
            state.monitor.apply_saved_instance_config(name, validated)
        if (reachable is False
                and config_manager.instance_connection_changed(existing, validated, name)):
            state.monitor.mark_instance_unreachable_save(validated['name'])
        _apply_config_after_save(
            skip_qb_ops=skip_qb_ops,
            sync_instance=sync_name,
            defer_reload=skip_qb_ops and not sync_name,
        )
        synced = sync_name is not None

        policy_note = ''
        if name != validated['name'] and data_policy == 'restore_orphan':
            policy_note = '（改名并恢复历史数据）'
        elif name != validated['name'] and data_policy == 'keep_current':
            policy_note = '（改名并保持现有数据）'
        traffic_db.add_device_event(
            validated['name'],
            'instance_updated',
            None,
            f'通过 Web 界面更新设备配置{policy_note}',
        )
        logger.info(f"设备配置已更新: {validated['name']}{policy_note}")

        return jsonify({
            'success': True,
            'message': '更新成功',
            'synced': synced,
            'data': config_manager.mask_instance_for_api(validated)
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"API PUT /config/instances/{name} 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/config/instances/<name>', methods=['DELETE'])
def api_config_instance_delete(name):
    try:
        keep_data = request.args.get('keep_data', '0') in ('1', 'true', 'True')
        config_manager.delete_instance(name)
        keep_note = '（保留历史数据）' if keep_data else ''
        if keep_data:
            traffic_db.mark_instance_orphan_deleted(name)
            traffic_db.add_device_event(
                name,
                'instance_deleted',
                None,
                f'通过 Web 界面删除设备{keep_note}',
            )
        else:
            try:
                traffic_db.delete_instance_data(name)
            except Exception as data_err:
                logger.error(
                    f"设备 {name} 已从配置移除，但历史数据清理失败: {data_err}",
                    exc_info=True,
                )
        state.monitor.reload_config(refresh_status=False)

        logger.info(f"设备已删除: {name}{keep_note}")

        return jsonify({
            'success': True,
            'message': '删除成功'
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"API DELETE /config/instances/{name} 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500
