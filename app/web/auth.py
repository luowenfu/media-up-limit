"""Web 登录会话管理"""

import os
import logging
from datetime import timedelta
from urllib.parse import urlencode
from flask import session, request, jsonify, redirect

logger = logging.getLogger(__name__)

from core.paths import WEB_SECRET_PATH as SECRET_PATH  # noqa: E402
from web.login_guard import resolve_client_ip  # noqa: E402

LOGIN_EXEMPT_PREFIXES = ('/static/',)
LOGIN_EXEMPT_EXACT = ('/login', '/favicon.ico', '/api/auth/login', '/api/auth/check')
_SETUP_WARNING = (
    '尚未设置 Web 账号密码。设置完成前，第一个打开页面的人就能设置密码，'
    '请不要把未完成设置的服务暴露到公网'
)


def get_secret_key() -> str:
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, 'r', encoding='utf-8') as f:
            key = f.read().strip()
            if key:
                return key
    key = os.urandom(32).hex()
    os.makedirs(os.path.dirname(SECRET_PATH), exist_ok=True)
    with open(SECRET_PATH, 'w', encoding='utf-8') as f:
        f.write(key)
    return key


def client_ip() -> str:
    return resolve_client_ip(
        request.remote_addr or '',
        request.headers.get('X-Forwarded-For', ''),
        request.headers.get('X-Real-IP', ''),
    )


def _pw_ver_matches() -> bool:
    from core.secrets_store import get_pw_ver
    current = get_pw_ver()
    stored = session.get('pw_ver', 0)
    try:
        return int(stored) == int(current)
    except (TypeError, ValueError):
        return False


def sync_session_after_password_change() -> None:
    """当前会话跟上新密码版本，其他设备因版本不一致需要重新登录。"""
    from core.secrets_store import get_pw_ver, get_web_username
    if not session.get('authenticated'):
        return
    session['pw_ver'] = get_pw_ver()
    session.pop('must_change_pw', None)
    session['username'] = get_web_username()


def init_auth(app):
    """注册认证钩子"""
    app.secret_key = get_secret_key()
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    if os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes'):
        app.config['SESSION_COOKIE_SECURE'] = True
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

    from core.secrets_store import needs_setup
    if needs_setup():
        logger.warning(_SETUP_WARNING)

    @app.before_request
    def require_login():
        path = request.path
        if path.startswith(LOGIN_EXEMPT_PREFIXES) or path == '/favicon.ico':
            return None

        if needs_setup():
            if path in ('/setup', '/api/auth/setup'):
                return None
            if path.startswith('/api/'):
                return jsonify({
                    'error': '请先设置账号密码',
                    'setup_required': True,
                }), 403
            return redirect('/setup')

        if session.get('authenticated') and not _pw_ver_matches():
            logout_user()

        if path in LOGIN_EXEMPT_EXACT:
            return None
        if session.get('authenticated'):
            return None
        if path.startswith('/api/'):
            return jsonify({'success': False, 'error': '未登录', 'auth_required': True}), 401
        next_url = request.full_path if request.query_string else path
        if next_url.endswith('?') and not request.query_string:
            next_url = path
        return redirect('/login?' + urlencode({'next': next_url}))


def login_user(username: str, remember: bool = False):
    from core.secrets_store import get_pw_ver
    session['authenticated'] = True
    session['username'] = username
    session['pw_ver'] = get_pw_ver()
    session.pop('must_change_pw', None)
    session.permanent = bool(remember)


def logout_user():
    session.clear()


def verify_credentials(username: str, password: str) -> bool:
    from core.secrets_store import get_web_username, verify_web_password
    expected = get_web_username()
    return bool(expected) and username == expected and verify_web_password(password)


def get_session_username() -> str:
    from core.secrets_store import get_web_username
    return str(session.get('username') or get_web_username() or '').strip()
