"""按 IP 限制登录失败次数。只使用标准库。"""

import os
import threading
import time

FAIL_LIMIT = 5
WINDOW_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60
_MAX_ENTRIES = 4096

_lock = threading.Lock()
_entries = {}


def resolve_client_ip(remote_addr: str, forwarded_for: str = '') -> str:
    """默认用直连地址。仅当设置了 TRUSTED_PROXY_COUNT 时，取 X-Forwarded-For 从右数第 N 个。"""
    remote = (remote_addr or '').strip() or 'unknown'
    raw = os.environ.get('TRUSTED_PROXY_COUNT', '').strip()
    if not raw:
        return remote
    try:
        count = int(raw)
    except ValueError:
        return remote
    if count <= 0:
        return remote
    parts = [part.strip() for part in (forwarded_for or '').split(',') if part.strip()]
    if len(parts) < count:
        return remote
    return parts[-count]


def retry_after(ip: str) -> int:
    """仍处于锁定时返回剩余秒数，否则返回 0。"""
    now = time.monotonic()
    with _lock:
        _purge(now)
        entry = _entries.get(ip)
        if not entry:
            return 0
        until = entry.get('locked_until') or 0
        if until > now:
            return max(1, int(until - now + 0.999))
        return 0


def record_failure(ip: str) -> int:
    """记一次失败。达到阈值后锁定，并返回锁定秒数；未锁定返回 0。"""
    now = time.monotonic()
    with _lock:
        _purge(now)
        entry = _entries.get(ip)
        if entry and (entry.get('locked_until') or 0) > now:
            return max(1, int(entry['locked_until'] - now + 0.999))
        if entry is None:
            if len(_entries) >= _MAX_ENTRIES:
                _evict()
            entry = {'fails': [], 'locked_until': 0}
            _entries[ip] = entry
        cutoff = now - WINDOW_SECONDS
        fails = [stamp for stamp in entry['fails'] if stamp >= cutoff]
        fails.append(now)
        entry['fails'] = fails
        if len(fails) >= FAIL_LIMIT:
            entry['locked_until'] = now + LOCK_SECONDS
            entry['fails'] = []
            return LOCK_SECONDS
        return 0


def record_success(ip: str) -> None:
    with _lock:
        _entries.pop(ip, None)


def lock_message(seconds: int) -> str:
    minutes = max(1, (int(seconds) + 59) // 60)
    return f'尝试次数过多，请 {minutes} 分钟后再试'


def _purge(now: float) -> None:
    cutoff = now - WINDOW_SECONDS
    stale = []
    for ip, entry in _entries.items():
        until = entry.get('locked_until') or 0
        fails = [stamp for stamp in entry['fails'] if stamp >= cutoff]
        entry['fails'] = fails
        if until <= now and not fails:
            stale.append(ip)
    for ip in stale:
        _entries.pop(ip, None)


def _evict() -> None:
    if len(_entries) < _MAX_ENTRIES:
        return

    def rank(item):
        entry = item[1]
        stamps = list(entry.get('fails') or [])
        until = entry.get('locked_until') or 0
        return max(stamps + [until])

    ordered = sorted(_entries.items(), key=rank)
    drop = max(1, len(_entries) - _MAX_ENTRIES + 1)
    for ip, _entry in ordered[:drop]:
        _entries.pop(ip, None)
