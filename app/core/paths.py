"""数据目录与文件路径，全程序唯一出处。

容器内数据卷固定挂在 /data。本地调试可用环境变量 DATA_DIR 指向别处。
"""

import os

DATA_DIR = os.environ.get('DATA_DIR', '/data').strip() or '/data'


def data_path(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)


CONFIG_PATH = data_path('config.yaml')
APP_LOG_PATH = os.environ.get('APP_LOG_PATH', '').strip() or data_path('app.log')

DATA_KEY_PATH = data_path('.data_key')
WEB_AUTH_PATH = data_path('.web_auth')
WEB_SECRET_PATH = data_path('.web_secret')
QB_SECRETS_PATH = data_path('.qb_secrets')
EMBY_SECRETS_PATH = data_path('.emby_secrets')
LUCKY_SECRETS_PATH = data_path('.lucky_secrets')
USER_PREFS_PATH = data_path('.user_prefs')

QB_DB_PATH = data_path('traffic.db')
EMBY_DB_PATH = data_path('emby_traffic.db')
EMBY_EVENTS_DIR = data_path('emby_events')
