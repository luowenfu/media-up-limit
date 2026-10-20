"""Emby 设备配置接口：增删改、连通 / API / Lucky 测试、孤儿数据检查"""

from flask import Blueprint
from flask import jsonify
from flask import request
import core.config_manager as config_manager
import emby.records.store as playback_record_store
import emby.traffic.db as emby_traffic_db
import logging
import threading

from emby.client import EmbyClient
from web import state
from web.common import emby_required, fresh_active_emby_instance_names, no_cache_json, reload_all_config, require_orphan_data_policy
from web.common import GENERIC_API_ERROR

logger = logging.getLogger(__name__)
bp = Blueprint('routes_emby_config', __name__)

def _reload_emby_monitor_after_save(defer: bool = False):
    """保存 Emby 设备后仅重载 Emby 监控，避免阻塞 HTTP"""

    def _run():
        if not state.emby_monitor:
            return
        try:
            state.emby_monitor.reload_config()
            state.emby_monitor.wake_all()
        except Exception as e:
            logger.error(f'Emby 配置重载失败: {e}', exc_info=True)

    if defer:
        threading.Thread(
            target=_run, name='reload-emby-after-save', daemon=True,
        ).start()
    else:
        _run()


def _apply_emby_add_data_policy(target_name: str, data_policy: str, active_names: list):
    if not emby_traffic_db.is_orphaned_instance(target_name, active_names):
        return
    require_orphan_data_policy(True, data_policy, add_mode=True)
    if data_policy == 'fresh':
        emby_traffic_db.delete_instance_data(target_name)
        playback_record_store.delete_instance_records(target_name)


def _apply_emby_rename_data_policy(old_name: str, new_name: str,
                                   data_policy: str, active_names: list):
    if old_name == new_name:
        return
    if not emby_traffic_db.is_orphaned_instance(
            new_name, active_names, renaming_from=old_name):
        emby_traffic_db.rename_instance_data(old_name, new_name)
        playback_record_store.rename_instance_records(old_name, new_name)
        return
    require_orphan_data_policy(True, data_policy, add_mode=False)
    if data_policy == 'restore_orphan':
        emby_traffic_db.delete_instance_data(old_name)
        playback_record_store.delete_instance_records(old_name)
    elif data_policy == 'keep_current':
        emby_traffic_db.delete_instance_data(new_name)
        playback_record_store.delete_instance_records(new_name)
        emby_traffic_db.rename_instance_data(old_name, new_name)
        playback_record_store.rename_instance_records(old_name, new_name)


@bp.route('/api/emby/config/instances', methods=['GET'])
def api_emby_config_instances_list():
    try:
        instances = [
            config_manager.mask_emby_instance_for_api(i)
            for i in config_manager.get_emby_instances(
                state.emby_monitor.config if state.emby_monitor else None)
        ]
        return jsonify({'success': True, 'data': instances})
    except Exception as e:
        logger.error(f'API GET /emby/config/instances 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/config/instances/<name>', methods=['GET'])
def api_emby_config_instance_get(name):
    try:
        inst = config_manager.get_emby_instance(
            name, state.emby_monitor.config if state.emby_monitor else None)
        if not inst:
            return jsonify({'success': False, 'error': '设备不存在'}), 404
        return jsonify({
            'success': True,
            'data': config_manager.mask_emby_instance_for_api(inst),
        })
    except Exception as e:
        logger.error(f'API GET /emby/config/instances/{name} 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/config/instances/orphan-check', methods=['GET'])
def api_emby_config_instance_orphan_check():
    err = emby_required()
    if err:
        return err
    try:
        name = str(request.args.get('name', '')).strip()
        if not name:
            return jsonify({'success': False, 'error': '参数缺失'}), 400
        renaming_from = str(request.args.get('renaming_from', '')).strip() or None
        active_names = fresh_active_emby_instance_names()
        has_orphan = emby_traffic_db.is_orphaned_instance(
            name, active_names, renaming_from=renaming_from)
        return no_cache_json({
            'success': True,
            'has_orphaned_data': has_orphan,
            'name': name,
        })
    except Exception as e:
        logger.error(f'API GET /emby/config/instances/orphan-check 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/config/instances/orphan-data', methods=['GET'])
def api_emby_config_instance_orphan_data():
    err = emby_required()
    if err:
        return err
    try:
        active_names = fresh_active_emby_instance_names()
        orphans = emby_traffic_db.get_orphaned_instances(active_names)
        return no_cache_json({'success': True, 'data': orphans})
    except Exception as e:
        logger.error(f'API GET /emby/config/instances/orphan-data 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/config/instances/orphan-data/<name>', methods=['DELETE'])
def api_emby_config_instance_orphan_data_delete(name):
    err = emby_required()
    if err:
        return err
    try:
        active_names = fresh_active_emby_instance_names()
        if name in active_names:
            return jsonify({'success': False, 'error': '该名称仍被设备使用'}), 400
        if not emby_traffic_db.is_orphaned_instance(name, active_names):
            return jsonify({'success': False, 'error': '孤儿数据不存在'}), 404
        emby_traffic_db.delete_instance_data(name)
        playback_record_store.delete_instance_records(name)
        logger.info(f'已删除 Emby 孤儿数据: {name}')
        return jsonify({'success': True, 'message': '删除成功'})
    except Exception as e:
        logger.error(
            f'API DELETE /emby/config/instances/orphan-data/{name} 错误: {e}',
            exc_info=True,
        )
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/config/instances/test', methods=['POST'])
def api_emby_config_instances_test():
    try:
        data = request.get_json() or {}
        test_type = data.get('test_type', 'connectivity')
        validated = config_manager.validate_emby_instance_for_test(
            data, test_type=test_type)

        if test_type in ('lucky', 'lucky_rules', 'lucky_connect'):
            from emby.lucky.api import (
                LuckyClient,
                build_rule_label,
                choose_selected_candidates,
            )
            import core.secrets_store as secrets_store
            token = validated.get('lucky_open_token', '')
            lc = LuckyClient(
                validated.get('lucky_base_url', ''),
                open_token=token,
                verify_ssl=bool(validated.get('lucky_verify_ssl', False)),
            )
            if test_type == 'lucky_rules':
                candidates, err = lc.list_proxy_candidates()
                if err:
                    return jsonify({'success': False, 'error': err}), 400
                saved = secrets_store.get_lucky_rules(validated.get('name', ''))
                selected = choose_selected_candidates(
                    candidates,
                    saved,
                    emby_host=validated.get('host', ''),
                    emby_port=int(validated.get('port') or 8096),
                )
                matched = selected[0] if len(selected) == 1 else None
                return jsonify({
                    'success': True,
                    'data': {
                        'candidates': candidates,
                        'selected': selected,
                        'matched': matched,
                        'matched_label': build_rule_label(matched) if matched else '',
                    },
                })
            rules = validated.get('lucky_rules') or []
            if test_type == 'lucky' and rules:
                ok_count = 0
                errors = []
                ip_total = 0
                for rule in rules:
                    one = lc.test_access_detail(rule['rule_key'], rule['sub_key'])
                    label = rule.get('label') or rule['rule_key']
                    if one.get('ok'):
                        ok_count += 1
                        ip_total += int(one.get('ip_total') or 0)
                    else:
                        errors.append(f'{label}：{one.get("error") or "读取失败"}')
                if ok_count == 0:
                    result = {'ok': False, 'error': '；'.join(errors) or '流量接口读取失败'}
                else:
                    message = f'{ok_count} 条反代规则流量接口正常'
                    if errors:
                        message += '；' + '；'.join(errors)
                    result = {
                        'ok': True,
                        'ip_total': ip_total,
                        'message': message,
                    }
            else:
                result = lc.test_connection()
            return jsonify({
                'success': result.get('ok', False),
                'data': result,
                'error': result.get('error'),
            })

        client = EmbyClient(validated)
        if test_type == 'api':
            result = client.test_api()
        else:
            result = client.test_connectivity()
        return jsonify({
            'success': result.get('ok', False),
            'data': result,
            'error': result.get('error'),
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f'API POST /emby/config/instances/test 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/config/instances', methods=['POST'])
def api_emby_config_instances_add():
    err = emby_required()
    if err:
        return err
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求无效'}), 400
        attempt_sync, reachable, data_policy = config_manager.pop_instance_save_flags(data)
        target_name = str(data.get('name', '')).strip()
        active_names = fresh_active_emby_instance_names()
        _apply_emby_add_data_policy(target_name, data_policy, active_names)
        validated = config_manager.add_emby_instance(data)
        reload_all_config()
        if state.emby_monitor:
            state.emby_monitor.reload_config()
            state.emby_monitor.wake_all()
        policy_note = ''
        if data_policy == 'restore':
            policy_note = '（恢复历史数据）'
        elif data_policy == 'fresh':
            policy_note = '（清空后新建）'
        logger.info(f'Emby 设备已添加: {validated["name"]}{policy_note}')
        return jsonify({
            'success': True,
            'message': '添加成功',
            'data': config_manager.mask_emby_instance_for_api(validated),
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f'API POST /emby/config/instances 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/config/instances/<name>', methods=['PUT'])
def api_emby_config_instances_update(name):
    err = emby_required()
    if err:
        return err
    try:
        raw = request.get_json()
        if not raw:
            return jsonify({'success': False, 'error': '请求无效'}), 400
        attempt_sync, reachable, data_policy = config_manager.pop_instance_save_flags(raw)
        clear_traffic_data = bool(raw.pop('clear_traffic_data', False))
        api_key_in_request = str(raw.get('api_key', '') or '').strip()
        existing = config_manager.get_emby_instance(
            name, config_manager.load_config())
        data = config_manager.resolve_emby_credentials(raw, existing)
        new_name = str(data.get('name', '')).strip() or name
        active_names = fresh_active_emby_instance_names()
        _apply_emby_rename_data_policy(name, new_name, data_policy, active_names)
        validated = config_manager.update_emby_instance(name, data)
        if clear_traffic_data and state.emby_monitor:
            state.emby_monitor.reset_traffic_stats(validated['name'])
        basics_only = config_manager.emby_instance_only_basics_changed(
            existing, validated, api_key_in_request=api_key_in_request)
        if basics_only:
            _reload_emby_monitor_after_save(defer=True)
        else:
            reload_all_config()
            if state.emby_monitor:
                state.emby_monitor.wake_all()
        policy_note = ''
        if name != validated['name'] and data_policy == 'restore_orphan':
            policy_note = '（改名并恢复历史数据）'
        elif name != validated['name'] and data_policy == 'keep_current':
            policy_note = '（改名并保持现有数据）'
        logger.info(f'Emby 设备配置已更新: {validated["name"]}{policy_note}')
        return jsonify({
            'success': True,
            'message': '保存成功',
            'data': config_manager.mask_emby_instance_for_api(validated),
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f'API PUT /emby/config/instances/{name} 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/config/instances/<name>', methods=['DELETE'])
def api_emby_config_instances_delete(name):
    err = emby_required()
    if err:
        return err
    try:
        keep_data = request.args.get('keep_data', '0') in ('1', 'true', 'True')
        config_manager.delete_emby_instance(name)
        keep_note = '（保留历史数据）' if keep_data else ''
        if keep_data:
            emby_traffic_db.mark_instance_orphan_deleted(name)
        else:
            try:
                emby_traffic_db.delete_instance_data(name)
                playback_record_store.delete_instance_records(name)
            except Exception as data_err:
                logger.error(
                    f'Emby 设备 {name} 已从配置移除，但历史数据清理失败: {data_err}',
                    exc_info=True,
                )
        reload_all_config()
        if state.emby_monitor:
            state.emby_monitor.reload_config()
        logger.info(f'Emby 设备已删除: {name}{keep_note}')
        return jsonify({'success': True, 'message': '删除成功'})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f'API DELETE /emby/config/instances/{name} 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500
