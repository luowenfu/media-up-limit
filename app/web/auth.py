"""Web 登录会话管理"""

import os
import logging
from datetime import timedelta
from urllib.parse import urlencode
from flask import session, request, jsonify, redirect

logger = logging.getLogger(__name__)

from core.paths import WEB_SECRET_PATH as SECRET_PATH  # noqa: E402
LOGIN_EXEMPT_PREFIXES = ('/static/',)
LOGIN_EXEMPT_EXACT = ('/login', '/api/auth/login', '/api/auth/check')


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
        if path in LOGIN_EXEMPT_EXACT:
            return None
        for prefix in LOGIN_EXEMPT_PREFIXES:
            if path.startswith(prefix):
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
    session['authenticated'] = True
    session['username'] = username
    session.permanent = remember


def logout_user():
    session.clear()


def verify_credentials(username: str, password: str) -> bool:
    from core.secrets_store import get_web_username, verify_web_password
    return username == get_web_username() and verify_web_password(password)


def get_session_username() -> str:
    from core.secrets_store import get_web_username
    return str(session.get('username') or get_web_username() or '').strip()
