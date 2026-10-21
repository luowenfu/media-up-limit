import logging
import os
import re
from pathlib import Path
from typing import Iterator, List, Optional

from core.syslog_localize import localize_system_log_entry

from core.paths import APP_LOG_PATH

logger = logging.getLogger(__name__)

_LOG_LINE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) '
    r'\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\] '
    r'(\S+): (.*)$'
)


def resolve_app_log_path(*, for_write: bool = False) -> Path:
    """app.log 路径，固定在数据卷里（可用 APP_LOG_PATH 覆盖）。"""
    path = Path(APP_LOG_PATH)
    if for_write:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.debug('创建日志目录失败: %s', path.parent, exc_info=True)
    return path


def _log_files() -> list:
    base = resolve_app_log_path(for_write=False)
    if not base.exists():
        return []

    files = [base]
    for idx in range(1, 4):
        rotated = Path(f'{base}.{idx}')
        if rotated.exists():
            files.append(rotated)
    return files


def _iter_valid_log_lines(path: Path) -> Iterator[str]:
    """逐行正向读取，仅产出符合格式的日志行（跳过 traceback 等续行）。"""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            for raw in handle:
                line = raw.rstrip('\r\n')
                if _LOG_LINE_RE.match(line):
                    yield line
    except OSError:
        return


def _iter_valid_log_lines_reverse(path: Path) -> Iterator[str]:
    """从文件尾部反向逐行读取，仅产出符合格式的日志行。"""
    try:
        with open(path, 'rb') as handle:
            handle.seek(0, os.SEEK_END)
            file_size = handle.tell()
            if file_size == 0:
                return
            pos = file_size
            remainder = b''
            while pos > 0:
                read_len = min(65536, pos)
                pos -= read_len
                handle.seek(pos)
                remainder = handle.read(read_len) + remainder
                parts = remainder.split(b'\n')
                remainder = parts[0]
                for part in reversed(parts[1:]):
                    if not part:
                        continue
                    line = part.decode('utf-8', errors='replace').rstrip('\r')
                    if _LOG_LINE_RE.match(line):
                        yield line
            if remainder:
                line = remainder.decode('utf-8', errors='replace').rstrip('\r')
                if line and _LOG_LINE_RE.match(line):
                    yield line
    except OSError:
        return


def _entry_matches_instance(entry: dict, instance: str) -> bool:
    if not instance:
        return True
    msg = entry.get('message') or ''
    if f'[{instance}]' in msg:
        return True
    if f'[Emby:{instance}]' in msg:
        return True
    for pattern in (
        f': {instance}',
        f'：{instance}',
        f'实例: {instance}',
        f'实例 {instance}',
    ):
        if pattern in msg:
            return True
    return False


def _is_playback_or_browse_app_log(entry: dict) -> bool:
    msg = entry.get('message') or ''
    if '[Playback:' in msg or '[Browse:' in msg:
        return True
    return False


def _is_emby_device_log(entry: dict) -> bool:
    if _is_playback_or_browse_app_log(entry):
        return False
    msg = entry.get('message') or ''
    logger_name = (entry.get('logger') or '').lower()
    if '[Emby:' in msg:
        return True
    if 'emby' in logger_name:
        return True
    if msg.startswith('Emby '):
        return True
    for marker in ('Emby 设备', 'Emby 实例', '初始化 Emby', 'Emby 流量', 'Emby 容器'):
        if marker in msg:
            return True
    return False


_QB_DEVICE_TAG_RE = re.compile(r'^\[[^:\]]+\]')


def _is_qb_device_log(entry: dict) -> bool:
    if _is_emby_device_log(entry):
        return False
    msg = entry.get('message') or ''
    logger_name = (entry.get('logger') or '').lower()
    if _QB_DEVICE_TAG_RE.match(msg):
        return True
    if '初始化qB实例' in msg or '初始化 qB' in msg:
        return True
    if logger_name in ('scheduler', 'qb_monitor', 'speed_limiter') and '[' in msg:
        return True
    for marker in ('设备已添加', '设备配置已更新', '设备已删除'):
        if marker in msg and 'Emby' not in msg:
            return True
    return False


def _entry_matches_service(entry: dict, service: str) -> bool:
    if not service:
        return True
    is_emby = _is_emby_device_log(entry)
    is_qb = _is_qb_device_log(entry)
    if service == 'emby':
        return is_emby
    if service == 'qb':
        return is_qb
    if service == 'system':
        return not is_emby and not is_qb
    return True


def _entry_passes_filters(
    entry: dict,
    *,
    level: Optional[str],
    service: Optional[str],
    instance: Optional[str],
) -> bool:
    if (entry.get('logger') or '') == 'waitress.queue':
        return False
    if level and entry['level'] != level:
        return False
    if service and not _entry_matches_service(entry, service):
        return False
    if instance and not _entry_matches_instance(entry, instance):
        return False
    if _is_playback_or_browse_app_log(entry) and service != 'emby':
        return False
    return True


def get_system_logs(limit: int = 300, level: str = None, instance: str = None,
                    service: str = None,
                    before_time: str = None, before_logger: str = None,
                    before_message: str = None, offset: int = 0) -> tuple:
    """按新到旧返回。offset 跳过最新的若干条，供「加载更多」继续往下接。"""
    limit = max(1, min(int(limit), 500))
    try:
        offset = max(0, min(int(offset or 0), 50000))
    except (TypeError, ValueError):
        offset = 0
    level = (level or '').strip().upper() or None
    instance = (instance or '').strip() or None
    service = (service or '').strip().lower() or None
    cursor = None
    if (before_time or '').strip():
        cursor = (
            before_time.strip(),
            (before_logger or '').strip(),
            (before_message or '').strip(),
        )
    matched: List[dict] = []
    want = limit + 1
    skipped = 0

    for path in _log_files():
        for line in _iter_valid_log_lines_reverse(path):
            match = _LOG_LINE_RE.match(line)
            if not match:
                continue
            entry = {
                'time': match.group(1),
                'level': match.group(2),
                'logger': match.group(3),
                'message': match.group(4),
            }
            if not _entry_passes_filters(
                entry, level=level, service=service, instance=instance,
            ):
                continue
            if cursor is not None:
                key = (entry['time'], entry['logger'], entry['message'])
                if key >= cursor:
                    continue
            if skipped < offset:
                skipped += 1
                continue
            matched.append(entry)
            if len(matched) >= want:
                break
        if len(matched) >= want:
            break

    has_more = len(matched) > limit
    matched = matched[:limit]
    return [localize_system_log_entry(entry) for entry in matched], has_more
