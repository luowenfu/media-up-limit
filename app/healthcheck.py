"""容器健康检查：访问登录页，端口取自配置文件（默认 8765）"""

import sys
import urllib.request

import yaml

from core.paths import CONFIG_PATH


def _port() -> int:
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return int(((yaml.safe_load(f) or {}).get('global') or {}).get('web_port', 8765))
    except Exception:
        return 8765


if __name__ == '__main__':
    try:
        ok = urllib.request.urlopen(f'http://127.0.0.1:{_port()}/login', timeout=4).status == 200
    except Exception:
        ok = False
    sys.exit(0 if ok else 1)
