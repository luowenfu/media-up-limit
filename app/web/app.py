"""Flask 应用工厂：注册蓝图、认证钩子，并启动 Waitress。"""

import logging
import os
import time

from flask import Flask, g, request
from flask_cors import CORS

from web import state
from web.auth import init_auth
from web.routes_auth import bp as auth_bp
from web.routes_emby import bp as emby_bp
from web.routes_emby_config import bp as emby_config_bp
from web.routes_emby_records import bp as emby_records_bp
from web.routes_qb import bp as qb_bp
from web.routes_qb_config import bp as qb_config_bp
from web.routes_settings import bp as settings_bp

logger = logging.getLogger(__name__)

app = Flask(__name__)

_cors_origins = os.environ.get('CORS_ORIGINS', '').strip()
if _cors_origins:
    CORS(
        app,
        supports_credentials=True,
        origins=[item.strip() for item in _cors_origins.split(',') if item.strip()],
    )

for _bp in (auth_bp, qb_bp, qb_config_bp, settings_bp, emby_bp, emby_records_bp, emby_config_bp):
    app.register_blueprint(_bp)


SLOW_REQUEST_SECONDS = 0.5


@app.before_request
def mark_request_start():
    g.request_started = time.perf_counter()


@app.after_request
def add_no_cache_headers(response):
    if request.path.startswith('/static/img/'):
        response.headers['Cache-Control'] = 'public, max-age=86400'
        response.headers.pop('Pragma', None)
        response.headers.pop('Expires', None)
    elif request.path.startswith('/static/') or request.path == '/favicon.ico':
        # 每次带 ETag 问一下，没改动回 304，不重新下载
        response.headers['Cache-Control'] = 'no-cache'
        response.headers.pop('Pragma', None)
        response.headers.pop('Expires', None)
    return response


@app.after_request
def log_slow_request(response):
    started = g.get('request_started')
    if started is not None and not request.path.startswith('/static/'):
        elapsed = time.perf_counter() - started
        if elapsed >= SLOW_REQUEST_SECONDS:
            logger.warning('慢请求 %s %s 用时 %.2f 秒', request.method, request.path, elapsed)
    return response


def init_web_server(traffic_monitor, emby_traffic_monitor=None):
    state.monitor = traffic_monitor
    state.emby_monitor = emby_traffic_monitor
    init_auth(app)


def run_web_server(host='0.0.0.0', port=8765):
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    try:
        from waitress import serve
        logger.info('Web 服务: Waitress (生产模式)')
        serve(app, host=host, port=port, threads=16, channel_timeout=120)
    except ImportError:
        logger.warning('未安装 waitress，使用 Flask 内置服务器（不建议生产环境）')
        app.run(host=host, port=port, debug=False, threaded=True)
