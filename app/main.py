import os
import sys
import signal
import logging
from logging.handlers import RotatingFileHandler

from core.log_reader import resolve_app_log_path

_LOG_LEVEL_NAME = os.environ.get('APP_LOG_LEVEL', 'INFO').upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)

logging.basicConfig(
    level=_LOG_LEVEL,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)

file_handler = RotatingFileHandler(
    str(resolve_app_log_path(for_write=True)), encoding='utf-8',
    maxBytes=10 * 1024 * 1024, backupCount=3
)
file_handler.setLevel(_LOG_LEVEL)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
logging.getLogger().addHandler(file_handler)

logging.getLogger('urllib3').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import qb.traffic_db as traffic_db
import emby.traffic.db as emby_traffic_db
import emby.records.store as playback_record_store
from qb.scheduler import TrafficMonitor
from emby.scheduler import EmbyMonitor
from web.app import init_web_server, run_web_server
import core.config_manager as config_manager

_runtime = {}


def _graceful_shutdown(signum=None, frame=None):
    if _runtime.get('shutting_down'):
        return
    _runtime['shutting_down'] = True
    sig_name = signal.Signals(signum).name if signum else 'EXIT'
    logger.info('收到 %s 信号，正在优雅关闭服务...', sig_name)

    monitor = _runtime.get('monitor')
    emby_monitor = _runtime.get('emby_monitor')
    if monitor:
        try:
            monitor.stop()
        except Exception as e:
            logger.warning('关闭 qB 监控失败: %s', e)
    if emby_monitor:
        try:
            emby_monitor.stop()
        except Exception as e:
            logger.warning('关闭 Emby 监控失败: %s', e)

    try:
        playback_record_store.flush_all_pending()
    except Exception as e:
        logger.warning('刷新播放记录缓存失败: %s', e)

    logger.info('服务已关闭')
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    logger.info("=" * 60)
    logger.info("qB-达量限速管理 qb-up-limit 启动中...")
    logger.info("=" * 60)

    config = config_manager.ensure_config()
    runtime_config = config_manager.enrich_config(config)

    global_cfg = config_manager.get_global_config(config)
    retention_years = global_cfg.get('data_retention_years', 5)
    traffic_db.set_retention_years(retention_years)
    emby_traffic_db.set_retention_years(retention_years)
    traffic_db.init_db()
    emby_traffic_db.init_db()
    logger.info(
        "数据库初始化完成 (qB: %s, Emby: %s)",
        traffic_db.DB_PATH,
        emby_traffic_db.DB_PATH,
    )

    monitor = TrafficMonitor(runtime_config, config_path=config_manager.CONFIG_PATH)
    monitor.start()

    emby_monitor = EmbyMonitor(runtime_config, config_path=config_manager.CONFIG_PATH)
    emby_monitor.start()

    _runtime['monitor'] = monitor
    _runtime['emby_monitor'] = emby_monitor

    init_web_server(monitor, emby_monitor)

    web_port = config.get('global', {}).get('web_port', 8765)
    logger.info(f"Web 管理界面启动: http://0.0.0.0:{web_port}")
    logger.info(f"配置文件路径: {config_manager.CONFIG_PATH}")
    run_web_server(port=web_port)


if __name__ == '__main__':
    main()
