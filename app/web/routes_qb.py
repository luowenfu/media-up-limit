"""qB 状态、统计、限速控制与事件接口"""

from flask import Blueprint
from flask import jsonify
from flask import request
from urllib.parse import urlparse
import core.config_manager as config_manager
import logging
import qb.traffic_db as traffic_db

from core.cycle import iter_cycle_periods
from core.log_reader import get_system_logs
from web import state
from web.common import GENERIC_API_ERROR

logger = logging.getLogger(__name__)
bp = Blueprint('routes_qb', __name__)

@bp.route('/api/status')
def api_status():
    try:
        status = state.monitor.get_status_summary()
        return jsonify({
            'success': True,
            'data': status,
            'timestamp': traffic_db.now_local().isoformat()
        })
    except Exception as e:
        logger.error(f"API /status 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/status/live')
def api_status_live():
    try:
        status = state.monitor.get_live_status_summary()
        return jsonify({
            'success': True,
            'data': status,
            'timestamp': traffic_db.now_local().isoformat()
        })
    except Exception as e:
        logger.error(f"API /status/live 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/stats/<instance_name>/<period>')
def api_stats(instance_name, period):
    try:
        direction = request.args.get('direction', 'upload')
        if direction not in ('upload', 'download'):
            return jsonify({'success': False, 'error': '无效的方向参数'}), 400

        if period == 'hourly':
            hours = min(int(request.args.get('hours', 24)), 366)
            start = request.args.get('start')
            end = request.args.get('end')
            data = traffic_db.get_hourly_stats(
                instance_name, hours, direction, start=start, end=end,
            )
        elif period == 'daily':
            days = min(int(request.args.get('days', 31)), 366)
            start = request.args.get('start')
            end = request.args.get('end')
            data = traffic_db.get_daily_stats(
                instance_name, days, direction, start=start, end=end,
            )
        elif period == 'weekly':
            weeks = min(int(request.args.get('weeks', 16)), 53)
            start = request.args.get('start')
            end = request.args.get('end')
            data = traffic_db.get_weekly_stats(
                instance_name, weeks, direction, start=start, end=end,
            )
        elif period == 'monthly':
            months = min(int(request.args.get('months', 12)), 60)
            start = request.args.get('start')
            end = request.args.get('end')
            data = traffic_db.get_monthly_stats(instance_name, months, direction, start=start, end=end)
        elif period == 'yearly':
            years = min(int(request.args.get('years', 10)), 24)
            start_year_raw = request.args.get('start_year')
            end_year_raw = request.args.get('end_year')
            try:
                start_year = int(start_year_raw) if start_year_raw else None
                end_year = int(end_year_raw) if end_year_raw else None
            except (ValueError, TypeError):
                start_year = end_year = None
            data = traffic_db.get_yearly_stats(
                instance_name, years, direction,
                start_year=start_year, end_year=end_year,
            )
        elif period == 'cycle':
            inst = config_manager.get_instance(instance_name, state.monitor.config)
            if not inst:
                return jsonify({'success': False, 'error': '设备不存在'}), 404
            cycle_cfg = inst.get('cycle', {})
            tz_name = config_manager.get_global_config(state.monitor.config).get(
                'timezone', 'Asia/Shanghai'
            )
            from zoneinfo import ZoneInfo
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo('Asia/Shanghai')
            count = min(int(request.args.get('cycles', 12)), 50)
            periods = iter_cycle_periods(cycle_cfg, tz, count=count)
            data = traffic_db.get_cycle_stats(instance_name, periods, direction)
        else:
            return jsonify({'success': False, 'error': '无效的period参数'}), 400
        
        return jsonify({
            'success': True,
            'period': period,
            'direction': direction,
            'data': data
        })
    except Exception as e:
        logger.error(f"API /stats 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


def _tracker_host(tracker: str) -> str:
    raw = (tracker or '').strip()
    if not raw:
        return ''
    if '://' not in raw:
        raw = 'http://' + raw
    return (urlparse(raw).hostname or '').strip().lower()


@bp.route('/api/qb/torrent-ranks')
def api_qb_torrent_ranks():
    """当前种子与站点的累计上传，不跟随图表时段。"""
    try:
        instance_name = (request.args.get('instance') or '').strip()
        if not instance_name:
            return jsonify({'success': False, 'error': '缺少 instance 参数'}), 400
        with state.monitor._config_lock:
            client = state.monitor.clients.get(instance_name)
        if not client:
            return jsonify({'success': False, 'error': '设备不存在'}), 404
        rows = client.list_torrent_uploads() or []
        torrents = sorted(
            (row for row in rows if row['uploaded'] > 0),
            key=lambda row: row['uploaded'],
            reverse=True,
        )[:10]
        sites = {}
        for row in rows:
            if row['uploaded'] <= 0:
                continue
            host = _tracker_host(row['tracker'])
            if not host:
                continue
            sites[host] = sites.get(host, 0) + row['uploaded']
        site_rows = sorted(sites.items(), key=lambda item: item[1], reverse=True)[:10]
        return jsonify({
            'success': True,
            'data': {
                'torrents': [[row['name'], row['uploaded']] for row in torrents],
                'sites': [[name, uploaded] for name, uploaded in site_rows],
            },
        })
    except Exception as e:
        logger.error(f'API /qb/torrent-ranks 错误: {e}', exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/events')
def api_events():
    try:
        instance_name = request.args.get('instance')
        limit = min(int(request.args.get('limit', 2000)), 2000)
        events = traffic_db.get_device_events(instance_name, limit)
        return jsonify({'success': True, 'data': events})
    except Exception as e:
        logger.error(f"API /events 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/system-logs')
def api_system_logs():
    try:
        level = request.args.get('level')
        instance = (request.args.get('instance') or '').strip() or None
        service = (request.args.get('service') or '').strip().lower() or None
        limit = int(request.args.get('limit', 200))
        offset = int(request.args.get('offset', 0) or 0)
        before_time = request.args.get('before_time')
        before_logger = request.args.get('before_logger')
        before_message = request.args.get('before_message')
        logs, has_more = get_system_logs(
            limit=limit,
            offset=offset,
            level=level,
            instance=instance,
            service=service,
            before_time=before_time,
            before_logger=before_logger,
            before_message=before_message,
        )
        return jsonify({'success': True, 'data': logs, 'has_more': has_more})
    except Exception as e:
        logger.error(f"API /system-logs 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/control/limit', methods=['POST'])
def api_control_limit():
    try:
        data = request.get_json()
        instance_name = data.get('instance_name')

        if not instance_name:
            return jsonify({'success': False, 'error': '参数缺失'}), 400

        limit_kbps = config_manager._validate_upload_limit_kbps(
            data.get('limit_kbps', 0), '限速')

        success = state.monitor.manual_set_limit(instance_name, limit_kbps)
        
        if success:
            return jsonify({
                'success': True,
                'message': '设置成功' if limit_kbps > 0 else '解除成功'
            })
        return jsonify({'success': False, 'error': '设置失败'}), 500
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"API /control/limit 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/control/speed-limits-mode', methods=['POST'])
def api_control_speed_limits_mode():
    """切换 qB 全局/备用限速模式"""
    try:
        data = request.get_json() or {}
        instance_name = data.get('instance_name')
        if not instance_name:
            return jsonify({'success': False, 'error': '参数缺失'}), 400
        if 'use_alt' not in data:
            return jsonify({'success': False, 'error': '缺少 use_alt 参数'}), 400
        use_alt = bool(data.get('use_alt'))
        with state.monitor._config_lock:
            if instance_name not in state.monitor.clients:
                return jsonify({'success': False, 'error': '设备不存在'}), 404
        success = state.monitor.manual_set_speed_limits_mode(instance_name, use_alt)
        if success:
            mode_label = '备用' if use_alt else '全局'
            return jsonify({
                'success': True,
                'message': f'已切换为{mode_label}限速模式',
            })
        return jsonify({'success': False, 'error': '切换失败'}), 500
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"API /control/speed-limits-mode 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/control/reset', methods=['POST'])
def api_control_reset():
    """解除限速，不清空流量统计"""
    try:
        data = request.get_json() or {}
        instance_name = data.get('instance_name')
        if not instance_name:
            return jsonify({'success': False, 'error': '参数缺失'}), 400
        with state.monitor._config_lock:
            if instance_name not in state.monitor.clients:
                return jsonify({'success': False, 'error': '设备不存在'}), 404
            client = state.monitor.clients[instance_name]
            if not client.allow_manual_unlimit:
                return jsonify({
                    'success': False,
                    'error': '该设备已禁用程序手动解除限速'
                }), 400
        if not state.monitor.manual_reset(instance_name):
            return jsonify({
                'success': False,
                'error': '解除限速失败，请检查 qBittorrent 连接状态',
            }), 500
        return jsonify({
            'success': True,
            'message': '解除成功',
        })
    except Exception as e:
        logger.error(f"API /control/reset 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/control/reset-stats', methods=['POST'])
def api_control_reset_stats():
    """清空流量统计，重新统计"""
    try:
        data = request.get_json()
        instance_name = data.get('instance_name')
        if not instance_name:
            return jsonify({'success': False, 'error': '参数缺失'}), 400
        state.monitor.reset_traffic_stats(instance_name)
        return jsonify({
            'success': True,
            'message': '清空成功'
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"API /control/reset-stats 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/instances')
def api_instances():
    try:
        names = state.monitor.get_client_names()
        return jsonify({'success': True, 'data': names})
    except Exception as e:
        logger.error(f"API /instances 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/config')
def api_config():
    try:
        global_cfg = config_manager.mask_global_for_api(
            config_manager.get_global_config(state.monitor.config),
            state.monitor.config,
        )
        collect = state.monitor.collect_interval
        refresh = state.monitor.refresh_interval
        global_cfg['collect_interval'] = collect
        global_cfg['refresh_interval'] = refresh
        return jsonify({
            'success': True,
            'global': global_cfg,
            'collect_interval': collect,
            'refresh_interval': refresh,
            'dual_tier': True,
        })
    except Exception as e:
        logger.error(f"API /config 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500
