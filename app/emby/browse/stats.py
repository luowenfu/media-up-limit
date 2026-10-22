"""选片流量统计查询与播放统计合并（图表用）。"""

from datetime import datetime

import emby.traffic.db as db


def resolve_instance_credit_browse(instance_config) -> bool:
    if not instance_config:
        return False
    return (
        instance_config.get('traffic_collect_mode') == 'lucky'
        and bool(instance_config.get('lucky_credit_browse_traffic', False))
    )


def _merge_playback_browse_stat_rows(
    playback_rows, browse_rows, label_key: str, *, credit_browse: bool = True,
) -> list:
    pb_map = {}
    br_map = {}
    extra = {}
    for row in playback_rows or []:
        key = str(row.get(label_key) or '')
        if not key:
            continue
        pb_map[key] = int(row.get('total_bytes') or 0)
        if label_key == 'period':
            extra[key] = {
                'cycle_start': row.get('cycle_start'),
                'period': row.get('period'),
            }
    if credit_browse:
        for row in browse_rows or []:
            key = str(row.get(label_key) or '')
            if not key:
                continue
            br_map[key] = int(row.get('total_bytes') or 0)
            if label_key == 'period' and key not in extra:
                extra[key] = {
                    'cycle_start': row.get('cycle_start'),
                    'period': row.get('period'),
                }
    merged = []
    for key in sorted(set(pb_map) | set(br_map)):
        playback_bytes = pb_map.get(key, 0)
        browse_bytes = br_map.get(key, 0) if credit_browse else 0
        item = {
            label_key: key,
            'playback_bytes': playback_bytes,
            'browse_bytes': browse_bytes,
            'total_bytes': playback_bytes + browse_bytes,
            'backfilled_bytes': browse_bytes,
        }
        if label_key == 'period' and key in extra:
            item.update(extra[key])
        merged.append(item)
    return merged


def get_browse_upload_period_bytes(instance_name: str, start_dt: datetime) -> int:
    name = (instance_name or '').strip()
    if not name:
        return 0
    start_s = start_dt.strftime('%Y-%m-%d %H:%M:%S')
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                SELECT COALESCE(SUM(estimated_upload_bytes), 0) AS total
                FROM emby_browse_upload_facts
                WHERE instance_name = ? AND stopped_at >= ?
            ''', (name, start_s))
            row = c.fetchone()
            return int(row['total']) if row else 0
        finally:
            conn.close()


def get_browse_upload_period_bytes_batch(instance_names: list,
                                         start_dt: datetime) -> dict:
    names = [n for n in (instance_names or []) if n]
    if not names:
        return {}
    start_s = start_dt.strftime('%Y-%m-%d %H:%M:%S')
    placeholders = ','.join('?' * len(names))
    result = {n: 0 for n in names}
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            c.execute(f'''
                SELECT instance_name, COALESCE(SUM(estimated_upload_bytes), 0) AS total
                FROM emby_browse_upload_facts
                WHERE instance_name IN ({placeholders})
                  AND stopped_at >= ?
                GROUP BY instance_name
            ''', (*names, start_s))
            for row in c.fetchall():
                result[row['instance_name']] = int(row['total'])
        finally:
            conn.close()
    return result


def get_browse_upload_total_bytes(instance_name: str) -> int:
    name = (instance_name or '').strip()
    if not name:
        return 0
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                SELECT COALESCE(SUM(estimated_upload_bytes), 0) AS total
                FROM emby_browse_upload_facts
                WHERE instance_name = ?
            ''', (name,))
            row = c.fetchone()
            return int(row['total']) if row else 0
        finally:
            conn.close()


def get_browse_upload_total_bytes_batch(instance_names: list) -> dict:
    names = [n for n in (instance_names or []) if n]
    if not names:
        return {}
    placeholders = ','.join('?' * len(names))
    result = {n: 0 for n in names}
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            c.execute(f'''
                SELECT instance_name, COALESCE(SUM(estimated_upload_bytes), 0) AS total
                FROM emby_browse_upload_facts
                WHERE instance_name IN ({placeholders})
                GROUP BY instance_name
            ''', names)
            for row in c.fetchall():
                result[row['instance_name']] = int(row['total'])
        finally:
            conn.close()
    return result


def get_live_status_upload_batch(
    instance_names: list,
    credit_browse_map: dict,
    today_start: datetime,
    yesterday_start: datetime,
    month_start: datetime,
) -> dict:
    """批量读取 Emby 状态 API 所需上传统计（播放 + 可选选片）。"""
    names = [n for n in (instance_names or []) if n]
    if not names:
        return {}
    playback_today = db.get_playback_upload_period_bytes_batch(names, today_start)
    playback_yesterday = db.get_playback_upload_period_bytes_batch(
        names, yesterday_start,
    )
    playback_month = db.get_playback_upload_period_bytes_batch(names, month_start)
    playback_total = db.get_playback_upload_total_bytes_batch(names)
    browse_names = [n for n in names if credit_browse_map.get(n)]
    browse_today = (
        get_browse_upload_period_bytes_batch(browse_names, today_start)
        if browse_names else {}
    )
    browse_yesterday = (
        get_browse_upload_period_bytes_batch(browse_names, yesterday_start)
        if browse_names else {}
    )
    browse_month = (
        get_browse_upload_period_bytes_batch(browse_names, month_start)
        if browse_names else {}
    )
    browse_total = (
        get_browse_upload_total_bytes_batch(browse_names)
        if browse_names else {}
    )
    result = {}
    for name in names:
        credit = bool(credit_browse_map.get(name))
        today_up = playback_today.get(name, 0) + (
            browse_today.get(name, 0) if credit else 0
        )
        yesterday_base = playback_yesterday.get(name, 0) + (
            browse_yesterday.get(name, 0) if credit else 0
        )
        month_up = playback_month.get(name, 0) + (
            browse_month.get(name, 0) if credit else 0
        )
        device_up = playback_total.get(name, 0) + (
            browse_total.get(name, 0) if credit else 0
        )
        result[name] = {
            'today_upload': today_up,
            'yesterday_upload': max(0, yesterday_base - today_up),
            'month_upload': month_up,
            'device_upload': device_up,
        }
    return result


def get_upload_period_bytes(
    instance_name: str, start_dt: datetime, *, credit_browse: bool = False,
) -> int:
    playback = db.get_playback_upload_period_bytes(instance_name, start_dt)
    if not credit_browse:
        return playback
    return playback + get_browse_upload_period_bytes(instance_name, start_dt)


def get_upload_total_bytes(instance_name: str, *, credit_browse: bool = False) -> int:
    playback = db.get_playback_upload_total_bytes(instance_name)
    if not credit_browse:
        return playback
    return playback + get_browse_upload_total_bytes(instance_name)


def get_combined_upload_period_bytes(
    instance_name: str, start_dt: datetime, *, credit_browse: bool = True,
) -> int:
    return get_upload_period_bytes(instance_name, start_dt, credit_browse=credit_browse)


def get_combined_upload_total_bytes(
    instance_name: str, *, credit_browse: bool = True,
) -> int:
    return get_upload_total_bytes(instance_name, credit_browse=credit_browse)


def _browse_facts_daily(name, user, days, start, end):
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            if start and end:
                start_s = db.traffic_db._normalize_range_start(start)
                end_s = db.traffic_db._normalize_range_end_exclusive(end, hourly=False)
                c.execute('''
                    SELECT date(stopped_at) AS day,
                           SUM(estimated_upload_bytes) AS total_bytes
                    FROM emby_browse_upload_facts
                    WHERE instance_name = ? AND user_name = ?
                      AND stopped_at >= ? AND stopped_at < ?
                    GROUP BY date(stopped_at) ORDER BY day
                ''', (name, user, start_s, end_s))
            else:
                cutoff = db._cutoff_str(days=days)
                c.execute('''
                    SELECT date(stopped_at) AS day,
                           SUM(estimated_upload_bytes) AS total_bytes
                    FROM emby_browse_upload_facts
                    WHERE instance_name = ? AND user_name = ? AND stopped_at >= ?
                    GROUP BY date(stopped_at) ORDER BY day
                ''', (name, user, cutoff))
            return [dict(r) for r in c.fetchall()]
        finally:
            conn.close()


def _browse_facts_daily_all(name, days, start, end):
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            if start and end:
                start_s = db.traffic_db._normalize_range_start(start)
                end_s = db.traffic_db._normalize_range_end_exclusive(end, hourly=False)
                c.execute('''
                    SELECT date(stopped_at) AS day,
                           SUM(estimated_upload_bytes) AS total_bytes
                    FROM emby_browse_upload_facts
                    WHERE instance_name = ?
                      AND stopped_at >= ? AND stopped_at < ?
                    GROUP BY date(stopped_at) ORDER BY day
                ''', (name, start_s, end_s))
            else:
                cutoff = db._cutoff_str(days=days)
                c.execute('''
                    SELECT date(stopped_at) AS day,
                           SUM(estimated_upload_bytes) AS total_bytes
                    FROM emby_browse_upload_facts
                    WHERE instance_name = ? AND stopped_at >= ?
                    GROUP BY date(stopped_at) ORDER BY day
                ''', (name, cutoff))
            return [dict(r) for r in c.fetchall()]
        finally:
            conn.close()


def _browse_hourly(name, user, hours, start, end):
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            if start and end:
                start_s = db.traffic_db._normalize_range_start(start)
                end_s = db.traffic_db._normalize_range_end_exclusive(end, hourly=True)
                c.execute('''
                    SELECT hour_start AS hour, uploaded_bytes AS total_bytes
                    FROM emby_browse_upload_hourly
                    WHERE instance_name = ? AND user_name = ?
                      AND hour_start >= ? AND hour_start < ?
                    ORDER BY hour_start
                ''', (name, user, start_s, end_s))
            else:
                cutoff = db._cutoff_str(hours=hours)
                c.execute('''
                    SELECT hour_start AS hour, uploaded_bytes AS total_bytes
                    FROM emby_browse_upload_hourly
                    WHERE instance_name = ? AND user_name = ? AND hour_start >= ?
                    ORDER BY hour_start
                ''', (name, user, cutoff))
            return [dict(r) for r in c.fetchall()]
        finally:
            conn.close()


def _browse_hourly_all(name, hours, start, end):
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            if start and end:
                start_s = db.traffic_db._normalize_range_start(start)
                end_s = db.traffic_db._normalize_range_end_exclusive(end, hourly=True)
                c.execute('''
                    SELECT hour_start AS hour, SUM(uploaded_bytes) AS total_bytes
                    FROM emby_browse_upload_hourly
                    WHERE instance_name = ?
                      AND hour_start >= ? AND hour_start < ?
                    GROUP BY hour_start ORDER BY hour_start
                ''', (name, start_s, end_s))
            else:
                cutoff = db._cutoff_str(hours=hours)
                c.execute('''
                    SELECT hour_start AS hour, SUM(uploaded_bytes) AS total_bytes
                    FROM emby_browse_upload_hourly
                    WHERE instance_name = ? AND hour_start >= ?
                    GROUP BY hour_start ORDER BY hour_start
                ''', (name, cutoff))
            return [dict(r) for r in c.fetchall()]
        finally:
            conn.close()


def _browse_facts_grouped(name, user, label_sql, label_key, cutoff_kw, limit_kw,
                          start=None, end=None, all_users=False):
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            user_clause = '' if all_users else 'AND user_name = ?'
            params_tail = () if all_users else (user,)
            if start and end:
                start_s = db.traffic_db._normalize_range_start(start)
                end_s = db.traffic_db._normalize_range_end_exclusive(end, hourly=False)
                c.execute(f'''
                    SELECT {label_sql} AS {label_key},
                           SUM(estimated_upload_bytes) AS total_bytes
                    FROM emby_browse_upload_facts
                    WHERE instance_name = ? {user_clause}
                      AND stopped_at >= ? AND stopped_at < ?
                    GROUP BY {label_key} ORDER BY {label_key}
                ''', (name, *params_tail, start_s, end_s))
            else:
                cutoff = db._cutoff_str(**{cutoff_kw: limit_kw})
                c.execute(f'''
                    SELECT {label_sql} AS {label_key},
                           SUM(estimated_upload_bytes) AS total_bytes
                    FROM emby_browse_upload_facts
                    WHERE instance_name = ? {user_clause} AND stopped_at >= ?
                    GROUP BY {label_key} ORDER BY {label_key}
                ''', (name, *params_tail, cutoff))
            return [dict(r) for r in c.fetchall()]
        finally:
            conn.close()


def get_combined_playback_upload_hourly_stats(instance_name, user_name, *,
                                              hours=24, start=None, end=None,
                                              credit_browse=True):
    playback = db.get_playback_upload_hourly_stats(
        instance_name, user_name, hours=hours, start=start, end=end,
    )
    browse = (
        _browse_hourly(instance_name, user_name, hours, start, end)
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'hour', credit_browse=credit_browse,
    )


def get_combined_playback_upload_hourly_stats_all_users(instance_name, *,
                                                        hours=24, start=None, end=None,
                                                        credit_browse=True):
    playback = db.get_playback_upload_hourly_stats_all_users(
        instance_name, hours=hours, start=start, end=end,
    )
    browse = (
        _browse_hourly_all(instance_name, hours, start, end)
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'hour', credit_browse=credit_browse,
    )


def get_combined_playback_upload_daily_stats(instance_name, user_name, *,
                                             days=31, start=None, end=None,
                                             credit_browse=True):
    playback = db.get_playback_upload_daily_stats(
        instance_name, user_name, days=days, start=start, end=end,
    )
    browse = (
        _browse_facts_daily(instance_name, user_name, days, start, end)
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'day', credit_browse=credit_browse,
    )


def get_combined_playback_upload_daily_stats_all_users(instance_name, *,
                                                       days=31, start=None, end=None,
                                                       credit_browse=True):
    playback = db.get_playback_upload_daily_stats_all_users(
        instance_name, days=days, start=start, end=end,
    )
    browse = (
        _browse_facts_daily_all(instance_name, days, start, end)
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'day', credit_browse=credit_browse,
    )


def get_combined_playback_upload_weekly_stats(instance_name, user_name, *,
                                              weeks=12, start=None, end=None,
                                              credit_browse=True):
    playback = db.get_playback_upload_weekly_stats(
        instance_name, user_name, weeks=weeks, start=start, end=end,
    )
    browse = (
        _browse_facts_grouped(
            instance_name, user_name,
            "strftime('%Y-W%W', stopped_at)", 'week', 'weeks', weeks, start, end,
        )
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'week', credit_browse=credit_browse,
    )


def get_combined_playback_upload_weekly_stats_all_users(instance_name, *,
                                                        weeks=12, start=None, end=None,
                                                        credit_browse=True):
    playback = db.get_playback_upload_weekly_stats_all_users(
        instance_name, weeks=weeks, start=start, end=end,
    )
    browse = (
        _browse_facts_grouped(
            instance_name, '', "strftime('%Y-W%W', stopped_at)", 'week',
            'weeks', weeks, start, end, all_users=True,
        )
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'week', credit_browse=credit_browse,
    )


def get_combined_playback_upload_monthly_stats(instance_name, user_name, *,
                                               months=12, start=None, end=None,
                                               credit_browse=True):
    playback = db.get_playback_upload_monthly_stats(
        instance_name, user_name, months=months, start=start, end=end,
    )
    browse = (
        _browse_facts_grouped(
            instance_name, user_name,
            "strftime('%Y-%m', stopped_at)", 'month', 'months', months, start, end,
        )
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'month', credit_browse=credit_browse,
    )


def get_combined_playback_upload_monthly_stats_all_users(instance_name, *,
                                                         months=12, start=None, end=None,
                                                         credit_browse=True):
    playback = db.get_playback_upload_monthly_stats_all_users(
        instance_name, months=months, start=start, end=end,
    )
    browse = (
        _browse_facts_grouped(
            instance_name, '', "strftime('%Y-%m', stopped_at)", 'month',
            'months', months, start, end, all_users=True,
        )
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'month', credit_browse=credit_browse,
    )


def get_combined_playback_upload_yearly_stats(instance_name, user_name, *,
                                              years=5, start=None, end=None,
                                              start_year=None, end_year=None,
                                              credit_browse=True):
    playback = db.get_playback_upload_yearly_stats(
        instance_name, user_name, years=years,
        start=start, end=end, start_year=start_year, end_year=end_year,
    )
    browse = (
        _browse_facts_grouped(
            instance_name, user_name,
            "strftime('%Y', stopped_at)", 'year', 'years', years, start, end,
        )
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'year', credit_browse=credit_browse,
    )


def get_combined_playback_upload_yearly_stats_all_users(instance_name, *,
                                                        years=5, start=None, end=None,
                                                        start_year=None, end_year=None,
                                                        credit_browse=True):
    playback = db.get_playback_upload_yearly_stats_all_users(
        instance_name, years=years,
        start=start, end=end, start_year=start_year, end_year=end_year,
    )
    browse = (
        _browse_facts_grouped(
            instance_name, '', "strftime('%Y', stopped_at)", 'year',
            'years', years, start, end, all_users=True,
        )
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'year', credit_browse=credit_browse,
    )


def get_combined_playback_upload_cycle_stats(instance_name, user_name, periods, *,
                                             credit_browse=True):
    playback = db.get_playback_upload_cycle_stats(instance_name, user_name, periods)
    browse = (
        _browse_cycle_stats(instance_name, user_name, periods, all_users=False)
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'period', credit_browse=credit_browse,
    )


def get_combined_playback_upload_cycle_stats_all_users(instance_name, periods, *,
                                                       credit_browse=True):
    playback = db.get_playback_upload_cycle_stats_all_users(instance_name, periods)
    browse = (
        _browse_cycle_stats(instance_name, '', periods, all_users=True)
        if credit_browse else []
    )
    return _merge_playback_browse_stat_rows(
        playback, browse, 'period', credit_browse=credit_browse,
    )


def _browse_cycle_stats(instance_name, user_name, periods, *, all_users=False):
    name = (instance_name or '').strip()
    if not name or not periods:
        return []
    result = []
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            for p in periods:
                start_dt = p.get('cycle_start')
                end_dt = p.get('cycle_end')
                if hasattr(start_dt, 'strftime'):
                    start_s = start_dt.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    start_s = str(start_dt)[:19]
                if hasattr(end_dt, 'strftime'):
                    end_s = end_dt.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    end_s = str(end_dt)[:19]
                if all_users:
                    c.execute('''
                        SELECT COALESCE(SUM(estimated_upload_bytes), 0) AS total
                        FROM emby_browse_upload_facts
                        WHERE instance_name = ?
                          AND stopped_at >= ? AND stopped_at < ?
                    ''', (name, start_s, end_s))
                else:
                    c.execute('''
                        SELECT COALESCE(SUM(estimated_upload_bytes), 0) AS total
                        FROM emby_browse_upload_facts
                        WHERE instance_name = ? AND user_name = ?
                          AND stopped_at >= ? AND stopped_at < ?
                    ''', (name, user_name, start_s, end_s))
                row = c.fetchone()
                cycle_start = p.get('cycle_start')
                if hasattr(cycle_start, 'strftime'):
                    cycle_start_label = cycle_start.strftime('%Y-%m-%d')
                else:
                    cycle_start_label = str(cycle_start)[:10]
                result.append({
                    'period': p.get('period') or cycle_start_label,
                    'cycle_start': cycle_start_label,
                    'total_bytes': int(row['total']) if row else 0,
                })
        finally:
            conn.close()
    return result


def get_user_upload_totals(instance_name, *, start, end, hourly=False,
                           credit_browse=True) -> dict:
    """所选时段内每个用户的上行合计（播放 + 可选选片）。键是用户名。"""
    name = (instance_name or '').strip()
    if not name or not start or not end:
        return {}
    start_s = db.traffic_db._normalize_range_start(start)
    end_s = db.traffic_db._normalize_range_end_exclusive(end, hourly=hourly)
    if not start_s or not end_s:
        return {}
    used = {}
    db._ensure_emby_schema()
    with db._lock:
        conn = db.get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                SELECT user_name, SUM(uploaded_bytes) AS total
                FROM emby_playback_upload_hourly
                WHERE instance_name = ? AND hour_start >= ? AND hour_start < ?
                GROUP BY user_name
            ''', (name, start_s, end_s))
            for row in c.fetchall():
                user = str(row['user_name'] or '').strip()
                if user:
                    used[user] = used.get(user, 0) + max(0, int(row['total'] or 0))
            if credit_browse:
                if hourly:
                    c.execute('''
                        SELECT user_name, SUM(uploaded_bytes) AS total
                        FROM emby_browse_upload_hourly
                        WHERE instance_name = ? AND hour_start >= ? AND hour_start < ?
                        GROUP BY user_name
                    ''', (name, start_s, end_s))
                else:
                    c.execute('''
                        SELECT user_name, SUM(estimated_upload_bytes) AS total
                        FROM emby_browse_upload_facts
                        WHERE instance_name = ? AND stopped_at >= ? AND stopped_at < ?
                        GROUP BY user_name
                    ''', (name, start_s, end_s))
                for row in c.fetchall():
                    user = str(row['user_name'] or '').strip()
                    if user:
                        used[user] = used.get(user, 0) + max(0, int(row['total'] or 0))
        finally:
            conn.close()
    return used
