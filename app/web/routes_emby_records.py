"""Emby 播放与选片记录、播放用户接口"""

from flask import Blueprint
from flask import jsonify
from flask import request
import core.config_manager as config_manager
import emby.records.store as playback_record_store
import emby.traffic.db as emby_traffic_db
import emby.user_sync as emby_user_sync
import logging
import time

from core.cycle import iter_cycle_periods
from core.syslog_localize import localize_system_log_entry
from emby.client import EmbyClient
from emby.client import is_playback_browse_server_log_message
from emby.client import parse_emby_log_line
from web import state
from web.common import emby_required, get_emby_client
from web.common import GENERIC_API_ERROR

logger = logging.getLogger(__name__)
bp = Blueprint('routes_emby_records', __name__)

@bp.route('/api/emby/playback-records')
def api_emby_playback_records():
    err = emby_required()
    if err:
        return err
    try:
        import emby.records.store as playback_record_store
        instance_name = request.args.get('instance')
        limit = min(int(request.args.get('limit', 200)), playback_record_store.MAX_STORED_RECORDS)
        if instance_name:
            inst = config_manager.get_emby_instance(instance_name, state.emby_monitor.config)
            if not inst:
                return jsonify({'success': False, 'error': '设备不存在'}), 404
            records = playback_record_store.list_records(instance_name, limit=limit)
        else:
            records = playback_record_store.list_records(limit=limit)
        return jsonify({'success': True, 'data': records})
    except Exception as e:
        logger.error(f'API /emby/playback-records 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/browse-records')
def api_emby_browse_records():
    err = emby_required()
    if err:
        return err
    try:
        instance_name = request.args.get('instance')
        if not instance_name:
            return jsonify({'success': False, 'error': '请选择设备'}), 400
        if not config_manager.get_emby_instance(instance_name, state.emby_monitor.config):
            return jsonify({'success': False, 'error': '设备不存在'}), 404
        limit = min(int(request.args.get('limit', 200)), 500)
        global_cfg = config_manager.get_global_config(state.emby_monitor.config)
        min_bytes = config_manager.emby_browse_upload_min_bytes(global_cfg)
        min_mb = config_manager.clamp_emby_browse_upload_min_mb(
            global_cfg.get('emby_browse_upload_min_mb', 1.0),
        )
        records = emby_traffic_db.list_browse_upload_records(
            instance_name, limit=limit, min_upload_bytes=min_bytes,
        )
        return jsonify({
            'success': True,
            'data': records,
            'browse_upload_min_mb': min_mb,
            'browse_upload_min_bytes': min_bytes,
        })
    except Exception as e:
        logger.error(f'API /emby/browse-records 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/activity-log')
def api_emby_activity_log():
    err = emby_required()
    if err:
        return err
    try:
        instance_name = request.args.get('instance')
        if not instance_name:
            return jsonify({'success': False, 'error': '请选择设备'}), 400
        inst = config_manager.get_emby_instance(instance_name, state.emby_monitor.config)
        if not inst:
            return jsonify({'success': False, 'error': '设备不存在'}), 404

        limit = min(int(request.args.get('limit', 200)), 500)
        client = EmbyClient(inst)
        sessions = client.get_sessions() or []
        item_cache = {}
        item_fetch_state = {'count': 0}
        deadline = time.monotonic() + 20.0
        events = []
        for entry in client.get_activity_log(limit=limit):
            enrichment = EmbyClient.enrich_activity_entry(
                client, entry, item_cache, sessions,
                max_item_fetches=48,
                item_fetch_state=item_fetch_state,
                deadline_mono=deadline,
            )
            events.append(EmbyClient.normalize_activity_entry(
                entry, instance_name, enrichment,
            ))
        return jsonify({'success': True, 'data': events})
    except Exception as e:
        logger.error(f'API /emby/activity-log 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/playback-users')
def api_emby_playback_users():
    err = emby_required()
    if err:
        return err
    try:
        instance_name = request.args.get('instance')
        if not instance_name:
            return jsonify({'success': False, 'error': '缺少 instance 参数'}), 400
        if not config_manager.get_emby_instance(instance_name, state.emby_monitor.config):
            return jsonify({'success': False, 'error': '设备不存在'}), 404
        client, err_resp = get_emby_client(instance_name)
        if err_resp:
            return err_resp
        emby_user_sync.sync_deleted_users(instance_name, client, force=True)
        users, source = emby_user_sync.list_instance_user_names(client, instance_name)
        return jsonify({'success': True, 'data': users, 'source': source})
    except Exception as e:
        logger.error(f'API /emby/playback-users 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/playback-stats/<instance_name>/<period>')
def api_emby_playback_stats(instance_name, period):
    err = emby_required()
    if err:
        return err
    try:
        user_name = (request.args.get('user') or '').strip()
        if not user_name:
            return jsonify({'success': False, 'error': '缺少 user 参数'}), 400
        inst = config_manager.get_emby_instance(instance_name, state.emby_monitor.config)
        if not inst:
            return jsonify({'success': False, 'error': '设备不存在'}), 404
        all_users = user_name == emby_traffic_db.PLAYBACK_ALL_USERS_TOKEN
        import emby.browse.stats as browse_stats
        credit_browse = browse_stats.resolve_instance_credit_browse(inst)
        browse_kw = {'credit_browse': credit_browse}
        if period == 'hourly':
            hours = min(int(request.args.get('hours', 24)), 366)
            if all_users:
                data = browse_stats.get_combined_playback_upload_hourly_stats_all_users(
                    instance_name, hours=hours,
                    start=request.args.get('start'), end=request.args.get('end'),
                    **browse_kw,
                )
            else:
                data = browse_stats.get_combined_playback_upload_hourly_stats(
                    instance_name, user_name, hours=hours,
                    start=request.args.get('start'), end=request.args.get('end'),
                    **browse_kw,
                )
        elif period == 'daily':
            days = min(int(request.args.get('days', 31)), 366)
            if all_users:
                data = browse_stats.get_combined_playback_upload_daily_stats_all_users(
                    instance_name, days=days,
                    start=request.args.get('start'), end=request.args.get('end'),
                    **browse_kw,
                )
            else:
                data = browse_stats.get_combined_playback_upload_daily_stats(
                    instance_name, user_name, days=days,
                    start=request.args.get('start'), end=request.args.get('end'),
                    **browse_kw,
                )
        elif period == 'monthly':
            months = min(int(request.args.get('months', 12)), 60)
            if all_users:
                data = browse_stats.get_combined_playback_upload_monthly_stats_all_users(
                    instance_name, months=months,
                    start=request.args.get('start'), end=request.args.get('end'),
                    **browse_kw,
                )
            else:
                data = browse_stats.get_combined_playback_upload_monthly_stats(
                    instance_name, user_name, months=months,
                    start=request.args.get('start'), end=request.args.get('end'),
                    **browse_kw,
                )
        elif period == 'weekly':
            weeks = min(int(request.args.get('weeks', 12)), 104)
            if all_users:
                data = browse_stats.get_combined_playback_upload_weekly_stats_all_users(
                    instance_name, weeks=weeks,
                    start=request.args.get('start'), end=request.args.get('end'),
                    **browse_kw,
                )
            else:
                data = browse_stats.get_combined_playback_upload_weekly_stats(
                    instance_name, user_name, weeks=weeks,
                    start=request.args.get('start'), end=request.args.get('end'),
                    **browse_kw,
                )
        elif period == 'yearly':
            years = min(int(request.args.get('years', 5)), 20)
            start_year = request.args.get('start_year')
            end_year = request.args.get('end_year')
            if all_users:
                data = browse_stats.get_combined_playback_upload_yearly_stats_all_users(
                    instance_name, years=years,
                    start=request.args.get('start'), end=request.args.get('end'),
                    start_year=int(start_year) if start_year else None,
                    end_year=int(end_year) if end_year else None,
                    **browse_kw,
                )
            else:
                data = browse_stats.get_combined_playback_upload_yearly_stats(
                    instance_name, user_name, years=years,
                    start=request.args.get('start'), end=request.args.get('end'),
                    start_year=int(start_year) if start_year else None,
                    end_year=int(end_year) if end_year else None,
                    **browse_kw,
                )
        elif period == 'cycle':
            from core.config_manager import DEFAULT_CYCLE, get_global_config
            from core.cycle import iter_cycle_periods
            from zoneinfo import ZoneInfo
            tz_name = get_global_config(state.emby_monitor.config).get(
                'timezone', 'Asia/Shanghai',
            )
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo('Asia/Shanghai')
            count = min(int(request.args.get('cycles', 12)), 50)
            periods = iter_cycle_periods(DEFAULT_CYCLE, tz, count=count)
            if all_users:
                data = browse_stats.get_combined_playback_upload_cycle_stats_all_users(
                    instance_name, periods, **browse_kw,
                )
            else:
                data = browse_stats.get_combined_playback_upload_cycle_stats(
                    instance_name, user_name, periods, **browse_kw,
                )
        else:
            return jsonify({'success': False, 'error': '播放用户统计暂不支持该周期'}), 400
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        logger.error(f'API /emby/playback-stats 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/system-logs/files')
def api_emby_system_log_files():
    err = emby_required()
    if err:
        return err
    try:
        instance_name = request.args.get('instance')
        if not instance_name:
            return jsonify({'success': False, 'error': '请选择设备'}), 400
        inst = config_manager.get_emby_instance(instance_name, state.emby_monitor.config)
        if not inst:
            return jsonify({'success': False, 'error': '设备不存在'}), 404

        client = EmbyClient(inst)
        files = []
        for item in client.list_server_logs():
            name = item.get('Name') or item.get('Id') or ''
            if not name:
                continue
            files.append({
                'name': name,
                'modified': item.get('DateModified') or item.get('DateCreated') or '',
                'size': item.get('Size') or 0,
            })
        return jsonify({'success': True, 'data': files})
    except Exception as e:
        logger.error(f'API /emby/system-logs/files 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/emby/system-logs')
def api_emby_system_logs():
    err = emby_required()
    if err:
        return err
    try:
        instance_name = request.args.get('instance')
        log_name = (request.args.get('log_name') or '').strip()
        level_filter = (request.args.get('level') or '').strip().upper()
        limit = min(int(request.args.get('limit', 500)), 1000)
        if not instance_name:
            return jsonify({'success': False, 'error': '请选择设备'}), 400
        inst = config_manager.get_emby_instance(instance_name, state.emby_monitor.config)
        if not inst:
            return jsonify({'success': False, 'error': '设备不存在'}), 404

        client = EmbyClient(inst)
        log_items = client.list_server_logs()
        target_names = []
        if log_name:
            target_names = [log_name]
        else:
            for item in log_items[:5]:
                name = item.get('Name') or item.get('Id') or ''
                if name:
                    target_names.append(name)

        lines = []
        for name in target_names:
            for line in client.get_server_log_lines(name, limit=limit):
                parsed = parse_emby_log_line(line)
                message = parsed.get('message') or parsed.get('raw') or line
                if is_playback_browse_server_log_message(message):
                    continue
                if level_filter and parsed.get('level') != level_filter:
                    continue
                lines.append(localize_system_log_entry({
                    'log_name': name,
                    'time': parsed.get('time') or '',
                    'level': parsed.get('level') or 'INFO',
                    'logger': parsed.get('logger') or '',
                    'message': parsed.get('message') or parsed.get('raw') or '',
                    'line': parsed.get('raw') or line,
                }))
        return jsonify({'success': True, 'data': lines[:limit]})
    except Exception as e:
        logger.error(f'API /emby/system-logs 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500
