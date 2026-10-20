"""登录、退出与页面入口"""

import os

from flask import Blueprint
from flask import current_app
from flask import jsonify
from flask import render_template
from flask import request
from flask import send_from_directory
from flask import session
import logging

from web.auth import login_user
from web.auth import logout_user
from web.auth import verify_credentials
from web.common import GENERIC_API_ERROR

logger = logging.getLogger(__name__)
bp = Blueprint('routes_auth', __name__)

@bp.route('/login')
def login_page():
    from flask import redirect
    if session.get('authenticated'):
        return redirect('/')
    return render_template('login.html')


@bp.route('/')
def index():
    return render_template('index.html')


@bp.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(current_app.static_folder, 'img'), 'favicon.ico')


@bp.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    try:
        data = request.get_json() or {}
        username = str(data.get('username', '')).strip()
        password = data.get('password', '')
        if not verify_credentials(username, password):
            return jsonify({'success': False, 'error': '账号或密码错误'}), 401
        remember = bool(data.get('remember'))
        login_user(username, remember=remember)
        return jsonify({'success': True, 'username': username})
    except Exception as e:
        logger.error(f"API /auth/login 错误: {e}", exc_info=True)
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
        'username': session.get('username', '')
    })
