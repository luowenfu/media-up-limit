"""限速周期计算：按月 / 按周 / 按天"""

from datetime import datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

CYCLE_TYPES = ('month', 'week', 'day')

WEEKDAY_LABELS = {
    1: '周一', 2: '周二', 3: '周三', 4: '周四',
    5: '周五', 6: '周六', 7: '周日',
}

TYPE_LABELS = {
    'month': '按月',
    'week': '按周',
    'day': '按天',
}


def _parse_int(value, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_cycle(cycle: dict) -> dict:
    cycle = cycle or {}
    ctype = str(cycle.get('type', 'month')).strip().lower()
    if ctype not in CYCLE_TYPES:
        ctype = 'month'
    if ctype == 'month':
        anchor = max(1, min(28, _parse_int(cycle.get('reset_anchor'), 1)))
    elif ctype == 'week':
        anchor = max(1, min(7, _parse_int(cycle.get('reset_anchor'), 1)))
    else:
        anchor = max(0, min(23, _parse_int(cycle.get('reset_anchor'), 0)))
    return {
        'type': ctype,
        'reset_anchor': anchor,
        'reset_limit_kbps': max(0, int(cycle.get('reset_limit_kbps', 0) or 0)),
    }


def migrate_legacy_cycle(inst: dict) -> dict:
    """从旧版 reset_day / speed_rules 字段迁移为 cycle 结构"""
    item = dict(inst)
    if 'cycle' not in item or not item.get('cycle'):
        item['cycle'] = {
            'type': 'month',
            'reset_anchor': max(1, min(28, _parse_int(item.get('reset_day'), 1))),
            'reset_limit_kbps': max(0, int(item.get('reset_day_limit_kbps', 0) or 0)),
        }
    item['cycle'] = _normalize_cycle(item['cycle'])

    if item.get('restore_on_reset') is not None and 'allow_manual_unlimit' not in item:
        item['allow_manual_unlimit'] = bool(item.get('restore_on_reset', True))

    rules = []
    for rule in item.get('speed_rules') or []:
        threshold = rule.get('cycle_upload_limit_gb')
        if threshold is None:
            threshold = rule.get('monthly_upload_limit_gb', 500)
        rules.append({
            'cycle_upload_limit_gb': float(threshold),
            'speed_limit_kbps': int(rule.get('speed_limit_kbps', 0) or 0),
        })
    item['speed_rules'] = rules
    return item


def _weekday_to_python(anchor: int) -> int:
    """用户编号 1=周一 … 7=周日 → Python weekday 0=周一 … 6=周日"""
    return max(0, min(6, anchor - 1))


def _add_months(year: int, month: int, delta: int) -> Tuple[int, int]:
    month += delta
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    return year, month


def _clamp_month_day(year: int, month: int, day: int) -> datetime:
    for d in range(day, 0, -1):
        try:
            return datetime(year, month, d)
        except ValueError:
            continue
    return datetime(year, month, 1)


def get_cycle_start(now: datetime, cycle: dict) -> datetime:
    """返回当前所在周期的起始时刻（与 now 同为 aware 或 naive）"""
    cfg = _normalize_cycle(cycle)
    ctype = cfg['type']
    anchor = cfg['reset_anchor']

    if ctype == 'month':
        if now.day >= anchor:
            y, m = now.year, now.month
        else:
            y, m = _add_months(now.year, now.month, -1)
        base = _clamp_month_day(y, m, anchor)
        return now.replace(
            year=base.year, month=base.month, day=base.day,
            hour=0, minute=0, second=0, microsecond=0,
        )

    if ctype == 'week':
        target = _weekday_to_python(anchor)
        days_since = (now.weekday() - target) % 7
        start = now - timedelta(days=days_since)
        return start.replace(hour=0, minute=0, second=0, microsecond=0)

    candidate = now.replace(hour=anchor, minute=0, second=0, microsecond=0)
    if now >= candidate:
        return candidate
    return candidate - timedelta(days=1)


def get_cycle_end(cycle_start: datetime, cycle: dict) -> datetime:
    """返回当前周期结束时刻（下一周期起点，不含）"""
    cfg = _normalize_cycle(cycle)
    ctype = cfg['type']
    anchor = cfg['reset_anchor']

    if ctype == 'month':
        y, m = _add_months(cycle_start.year, cycle_start.month, 1)
        base = _clamp_month_day(y, m, anchor)
        return cycle_start.replace(
            year=base.year, month=base.month, day=base.day,
            hour=0, minute=0, second=0, microsecond=0,
        )

    if ctype == 'week':
        return cycle_start + timedelta(days=7)

    return cycle_start + timedelta(days=1)


def get_reset_anchor_label(cycle: dict) -> str:
    cfg = _normalize_cycle(cycle)
    ctype = cfg['type']
    anchor = cfg['reset_anchor']
    if ctype == 'month':
        return f'每月{anchor}日'
    if ctype == 'week':
        return f'每{WEEKDAY_LABELS.get(anchor, "周一")}'
    return f'每日{anchor:02d}:00'


def format_cycle_range(cycle_start: datetime, cycle_end: datetime,
                       cycle: dict) -> str:
    cfg = _normalize_cycle(cycle)
    if cfg['type'] == 'month':
        return (
            f"{cycle_start.strftime('%m-%d')} ~ "
            f"{(cycle_end - timedelta(seconds=1)).strftime('%m-%d')}"
        )
    if cfg['type'] == 'week':
        return (
            f"{cycle_start.strftime('%m-%d')} ~ "
            f"{(cycle_end - timedelta(seconds=1)).strftime('%m-%d')}"
        )
    return (
        f"{cycle_start.strftime('%m-%d %H:00')} ~ "
        f"{cycle_end.strftime('%m-%d %H:00')}"
    )


def cycle_info(now: datetime, cycle: dict) -> dict:
    cfg = _normalize_cycle(cycle)
    start = get_cycle_start(now, cfg)
    end = get_cycle_end(start, cfg)
    return {
        'type': cfg['type'],
        'type_label': TYPE_LABELS[cfg['type']],
        'reset_anchor': cfg['reset_anchor'],
        'reset_anchor_label': get_reset_anchor_label(cfg),
        'reset_limit_kbps': cfg['reset_limit_kbps'],
        'start': start.isoformat(),
        'end': end.isoformat(),
        'range_label': format_cycle_range(start, end, cfg),
    }


def iter_cycle_periods(cycle: dict, tz: ZoneInfo,
                       count: int = 12,
                       end_at: Optional[datetime] = None) -> list:
    """生成最近 count 个完整周期（从旧到新），用于图表「按限速周期」"""
    cfg = _normalize_cycle(cycle)
    if end_at is None:
        end_at = datetime.now(tz)
    elif end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=tz)

    current_start = get_cycle_start(end_at, cfg)
    periods = []
    start = current_start
    for _ in range(count):
        end = get_cycle_end(start, cfg)
        label = format_cycle_range(start, end, cfg)
        periods.append({
            'period': label,
            'cycle_start': start,
            'cycle_end': end,
        })
        if cfg['type'] == 'month':
            y, m = _add_months(start.year, start.month, -1)
            prev = _clamp_month_day(y, m, cfg['reset_anchor'])
            start = start.replace(
                year=prev.year, month=prev.month, day=prev.day,
                hour=0, minute=0, second=0, microsecond=0,
            )
        elif cfg['type'] == 'week':
            start = start - timedelta(days=7)
        else:
            start = start - timedelta(days=1)

    periods.reverse()
    return periods


def cycle_start_key(dt: datetime) -> str:
    """用于持久化比较周期起点"""
    if getattr(dt, 'tzinfo', None):
        dt = dt.replace(tzinfo=None)
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def get_next_cycle_switch_at(now: datetime, cycle: dict) -> datetime:
    """按当前周期配置返回下一周期起点（切换时刻）"""
    start = get_cycle_start(now, cycle)
    return get_cycle_end(start, cycle)


def format_next_cycle_switch_label(now: datetime, cycle: dict) -> str:
    return get_next_cycle_switch_at(now, cycle).strftime('%Y-%m-%d %H:%M:%S')


def format_resume_when(now: datetime, cycle: dict) -> str:
    """下一周期起点的口语说法，用于达量停播提示。

    按天：今日8点、明日0点。按周：本周五、下周一。按月：本月28日、下月1日。
    """
    cfg = _normalize_cycle(cycle)
    end = get_next_cycle_switch_at(now, cfg)
    if cfg['type'] == 'day':
        day = '今日' if end.date() == now.date() else '明日'
        return f'{day}{end.hour}点'
    if cfg['type'] == 'week':
        same_week = end.isocalendar()[:2] == now.isocalendar()[:2]
        return ('本' if same_week else '下') + WEEKDAY_LABELS[cfg['reset_anchor']]
    same_month = end.year == now.year and end.month == now.month
    return f"{'本月' if same_month else '下月'}{end.day}日"
