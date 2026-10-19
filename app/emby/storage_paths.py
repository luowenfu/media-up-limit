"""Emby 播放记录 JSON 存储路径。"""

import hashlib
import os
import re

from core.paths import EMBY_EVENTS_DIR  # noqa: E402


def safe_filename(instance_name: str) -> str:
    safe = re.sub(r'[^\w\-]', '_', (instance_name or '').strip())[:50] or 'instance'
    digest = hashlib.sha256((instance_name or '').encode('utf-8')).hexdigest()[:12]
    return f'{safe}_{digest}.json'


def playback_record_store_path(instance_name: str) -> str:
    return os.path.join(EMBY_EVENTS_DIR, safe_filename(instance_name))


def legacy_playback_record_store_path(instance_name: str) -> str:
    """旧并行方案使用的 *_2.json 路径（仅迁移用）。"""
    base = safe_filename(instance_name)
    stem, ext = os.path.splitext(base)
    return os.path.join(EMBY_EVENTS_DIR, f'{stem}_2{ext}')
