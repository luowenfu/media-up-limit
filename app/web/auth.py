"""Web 登录会话管理"""

import os
import logging
import threading
from datetime import timedelta
from urllib.parse import urlencode
from flask import session, request, jsonify, redirect

logger = logging.getLogger(__name__)

from core.paths import WEB_SECRET_PATH as SECRET_PATH  # noqa: E402
from web.login_guard import resolve_client_ip  # noqa: E402

LOGIN_EXEMPT_PREFIXES = ('/static/',)
LOGIN_EXEMPT_EXACT = ('/login', '/favicon.ico', '/api/auth/login', '/api/auth/check')
PASSWORD_CHANGE_EXEMPT = (
    '/change-password',
    '/api/auth/change-password',
    '/api/auth/logout',
    '/api/auth/check',
)

_default_pw_by_user = {}
_default_pw_lock = threading.Lock()


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
    )


def _pw_ver_matches() -> bool:
    from core.secrets_store import get_pw_ver
    current = get_pw_ver()
    stored = session.get('pw_ver', 0)
    try:
        return int(stored) == int(current)
    except (TypeError, ValueError):
        return False


def _cached_default_password(username: str) -> bool:
    from core.secrets_store import is_default_password, password_state_generation
    generation = password_state_generation()
    key = username or ''
    with _default_pw_lock:
        cached = _default_pw_by_user.get(key)
        if cached and cached[0] == generation:
            return cached[1]
    result = is_default_password()
    with _default_pw_lock:
        _default_pw_by_user[key] = (generation, result)
    return result


def session_must_change_password() -> bool:
    """已登录但仍在使用默认密码时必须先改密。结果按用户名缓存。"""
    if not session.get('authenticated'):
        return False
    if 'must_change_pw' in session:
        return bool(session.get('must_change_pw'))
    username = str(session.get('username') or '')
    must = _cached_default_password(username)
    session['must_change_pw'] = must
    return must


def sync_session_after_password_change() -> None:
    """当前会话跟上新密码版本，其他设备因版本不一致需要重新登录。"""
    from core.secrets_store import get_pw_ver, get_web_username
    with _default_pw_lock:
        _default_pw_by_user.clear()
    if not session.get('authenticated'):
        return
    session['pw_ver'] = get_pw_ver()
    session['must_change_pw'] = False
    session['username'] = get_web_username()


def init_auth(app):
    """注册认证钩子"""
    app.secret_key = get_secret_key()
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    if os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes'):
        app.config['SESSION_COOKIE_SECURE'] = True
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

    @app.before_request
    def require_login():
        path = request.path
        if path.startswith(LOGIN_EXEMPT_PREFIXES) or path == '/favicon.ico':
            return None

        if session.get('authenticated') and not _pw_ver_matches():
            logout_user()

        authed = bool(session.get('authenticated'))
        must_change = session_must_change_password() if authed else False

        if path in LOGIN_EXEMPT_EXACT:
            return None

        if authed and must_change:
            if path in PASSWORD_CHANGE_EXEMPT or path.startswith('/static/'):
                return None
            if path.startswith('/api/'):
                return jsonify({
                    'error': '请先修改默认密码',
                    'password_change_required': True,
                }), 403
            return redirect('/change-password')

        if authed:
            return None
        if path.startswith('/api/'):
            return jsonify({'success': False, 'error': '未登录', 'auth_required': True}), 401
        next_url = request.full_path if request.query_string else path
        if next_url.endswith('?') and not request.query_string:
            next_url = path
        return redirect('/login?' + urlencode({'next': next_url}))


def login_user(username: str, remember: bool = False, must_change_pw: bool = False):
    from core.secrets_store import get_pw_ver
    session['authenticated'] = True
    session['username'] = username
    session['pw_ver'] = get_pw_ver()
    session['must_change_pw'] = bool(must_change_pw)
    session.permanent = bool(remember)


def logout_user():
    session.clear()


def verify_credentials(username: str, password: str) -> bool:
    from core.secrets_store import get_web_username, verify_web_password
    return username == get_web_username() and verify_web_password(password)


def get_session_username() -> str:
    from core.secrets_store import get_web_username
    return str(session.get('username') or get_web_username() or '').strip()
