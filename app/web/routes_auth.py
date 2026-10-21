"""登录、退出与页面入口"""

import os

from flask import Blueprint
from flask import current_app
from flask import jsonify
from flask import redirect
from flask import render_template
from flask import request
from flask import send_from_directory
from flask import session
import logging

from core.secrets_store import DEFAULT_WEB_PASS
from core.secrets_store import get_web_username
from core.secrets_store import is_default_password
from core.secrets_store import set_web_credentials
from core.secrets_store import verify_web_password
from web.auth import client_ip
from web.auth import login_user
from web.auth import logout_user
from web.auth import sync_session_after_password_change
from web.auth import verify_credentials
from web.common import GENERIC_API_ERROR
from web.login_guard import lock_message
from web.login_guard import record_failure
from web.login_guard import record_success
from web.login_guard import retry_after

logger = logging.getLogger(__name__)
bp = Blueprint('routes_auth', __name__)


def _locked_response(ip: str):
    wait = retry_after(ip)
    if wait <= 0:
        return None
    logger.warning('认证尝试被锁定，IP %s，剩余 %s 秒', ip, wait)
    return jsonify({
        'error': lock_message(wait),
        'retry_after': wait,
    }), 429


def _text(data: dict, *keys: str) -> str:
    for key in keys:
        if key in data and data.get(key) is not None:
            return str(data.get(key))
    return ''


@bp.route('/login')
def login_page():
    if session.get('authenticated'):
        if session.get('must_change_pw'):
            return redirect('/change-password')
        return redirect('/')
    return render_template('login.html')


@bp.route('/change-password')
def change_password_page():
    return render_template(
        'change_password.html',
        must_change=bool(session.get('must_change_pw')),
    )


@bp.route('/')
def index():
    return render_template('index.html')


@bp.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(current_app.static_folder, 'img'), 'favicon.ico')


@bp.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    try:
        ip = client_ip()
        locked = _locked_response(ip)
        if locked:
            return locked
        data = request.get_json() or {}
        username = str(data.get('username', '')).strip()
        password = data.get('password', '')
        if not verify_credentials(username, password):
            record_failure(ip)
            logger.warning('登录失败，IP %s', ip)
            return jsonify({'success': False, 'error': '账号或密码错误'}), 401
        record_success(ip)
        must_change = is_default_password()
        remember = bool(data.get('remember'))
        login_user(username, remember=remember, must_change_pw=must_change)
        return jsonify({
            'success': True,
            'username': username,
            'must_change_password': must_change,
        })
    except Exception as e:
        logger.error(f"API /auth/login 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/auth/change-password', methods=['POST'])
def api_auth_change_password():
    try:
        if not session.get('authenticated'):
            return jsonify({'success': False, 'error': '未登录', 'auth_required': True}), 401
        ip = client_ip()
        locked = _locked_response(ip)
        if locked:
            return locked
        data = request.get_json() or {}
        if not isinstance(data, dict):
            return jsonify({'success': False, 'error': '请求无效'}), 400
        old = _text(data, 'old_password', 'old')
        new = _text(data, 'new_password', 'new')
        confirm = _text(data, 'confirm_password', 'confirm')
        if not old or not new or not confirm:
            return jsonify({'success': False, 'error': '请填写完整'}), 400
        if new != confirm:
            return jsonify({'success': False, 'error': '两次输入的新密码不一致'}), 400
        if len(new) < 6:
            return jsonify({'success': False, 'error': '密码至少 6 位'}), 400
        if new == DEFAULT_WEB_PASS:
            return jsonify({'success': False, 'error': '不能使用默认密码'}), 400
        if not verify_web_password(old):
            record_failure(ip)
            logger.warning('修改密码失败，原密码错误，IP %s', ip)
            return jsonify({'success': False, 'error': '原密码错误'}), 401
        username = get_web_username()
        set_web_credentials(username, new)
        record_success(ip)
        sync_session_after_password_change()
        return jsonify({'success': True, 'username': username})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"API /auth/change-password 错误: {e}", exc_info=True)
        return jsonify({'success': False, 'error': GENERIC_API_ERROR}), 500


@bp.route('/api/auth/logout', methods=['POST'])
def api_auth_logout():
    logout_user()
    return jsonify({'success': True})


@bp.route('/api/auth/check')
def api_auth_check():
    return jsonify({
        'success': True,
        'authenticated': bool(session.get('authenticated')),
        'username': session.get('username', ''),
        'must_change_password': bool(
            session.get('authenticated') and session.get('must_change_pw')
        ),
    })
