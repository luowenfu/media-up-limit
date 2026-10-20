"""qB 采集摘要的一行文案。"""

_MB = 1024 * 1024
_GB = 1024 * _MB


def format_byte_amount(num_bytes: int) -> str:
    """0 不带单位。不足 1GB 用 MB，10GB 以下保留两位，再大保留一位。"""
    n = max(0, int(num_bytes or 0))
    if n <= 0:
        return '0'
    if n < _GB:
        text = f'{n / _MB:.1f}MB'
        if text.startswith('0.0'):
            return '<0.1MB'
        return text
    gb = n / _GB
    if gb < 10:
        return f'{gb:.2f}GB'
    return f'{gb:.1f}GB'


def format_collect_summary(
    name: str,
    elapsed: float,
    ticks: int,
    expected_ticks: int,
    delta_up: int,
    delta_dl: int,
    cycle_upload: int,
    is_quota_limited: bool,
    limit_kbps: int,
    backfill_up: int,
    backfill_dl: int,
) -> str:
    """固定写窗口、上传增量、周期上传和限速。下载、补录、采样不足才附上。"""
    seconds = max(0, int(elapsed or 0))
    window = f'{seconds}秒' if seconds < 60 else f'{seconds // 60}分钟'
    parts = [f'上传 +{format_byte_amount(delta_up)}']
    if int(delta_dl or 0) > 0:
        parts.append(f'下载 +{format_byte_amount(delta_dl)}')
    parts.append(f'周期 {format_byte_amount(cycle_upload)}')
    if is_quota_limited:
        parts.append(f'限速 {int(limit_kbps or 0)}KB/s')
    else:
        parts.append('未限速')
    if int(backfill_up or 0) > 0:
        parts.append(f'补录上传 {format_byte_amount(backfill_up)}')
    if int(backfill_dl or 0) > 0:
        parts.append(f'补录下载 {format_byte_amount(backfill_dl)}')
    low_ticks = int(expected_ticks or 0) > 0 and int(ticks or 0) < expected_ticks * 0.8
    if seconds < 8 * 60 or low_ticks:
        parts.append(f'采样 {int(ticks or 0)} 次')
    return f'[{name}] {window} ' + '，'.join(parts)
