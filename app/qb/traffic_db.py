import json
import sqlite3
import os
import logging
from datetime import datetime, timedelta, timezone
import threading

logger = logging.getLogger(__name__)

from core.paths import QB_DB_PATH as DB_PATH  # noqa: E402
_lock = threading.Lock()
_schema_ensured = False

_SCHEMA_COLUMNS = (
    ('traffic_hourly', 'downloaded_bytes', 'BIGINT NOT NULL DEFAULT 0'),
    ('traffic_monthly', 'downloaded_bytes', 'BIGINT NOT NULL DEFAULT 0'),
    ('instance_status', 'monthly_downloaded_bytes', 'BIGINT DEFAULT 0'),
    ('instance_status', 'last_total_downloaded', 'BIGINT DEFAULT 0'),
    ('instance_status', 'last_delta_download_bytes', 'BIGINT DEFAULT 0'),
    ('instance_status', 'is_quota_limited', 'INTEGER DEFAULT 0'),
    ('instance_status', 'has_upload_limit', 'INTEGER DEFAULT 0'),
    ('instance_status', 'limit_source', "TEXT DEFAULT ''"),
    ('instance_status', 'last_applied_cycle_start', 'TEXT'),
    ('instance_status', 'skip_auto_unlimit_once', 'INTEGER DEFAULT 0'),
    ('instance_status', 'manual_baseline_threshold_gb', 'REAL DEFAULT 0'),
    ('instance_status', 'rule_trigger_times', "TEXT DEFAULT ''"),
    ('instance_status', 'manual_limit_trigger_at', 'DATETIME'),
    ('instance_status', 'manual_limit_trigger_kbps', 'INTEGER DEFAULT 0'),
    ('instance_status', 'normal_global_upload_limit_kbps', 'INTEGER DEFAULT -1'),
    ('instance_status', 'last_programmed_limit_kbps', 'INTEGER DEFAULT -1'),
    ('instance_status', 'alt_upload_limit_kbps', 'INTEGER DEFAULT 0'),
    ('instance_status', 'alt_speed_limits_active', 'INTEGER DEFAULT 0'),
    ('instance_status', 'deleted_at', 'DATETIME'),
    ('traffic_hourly', 'backfilled_uploaded_bytes', 'BIGINT NOT NULL DEFAULT 0'),
    ('traffic_hourly', 'backfilled_downloaded_bytes', 'BIGINT NOT NULL DEFAULT 0'),
    ('device_events', 'event_key', 'TEXT'),
)

# 数据保留策略（防止数据库无限增长）。种子历史按添加、完成时间补记后，
# 每台设备大约两条乘以当前种子数，1000 条会把较早的补记清掉。
EVENT_RETENTION_COUNT = 5000
_VACUUM_INTERVAL_DAYS = 30
_last_vacuum_day = None
_vacuum_running = False
_retention_years = 5
RETENTION_YEARS_MIN = 1
RETENTION_YEARS_MAX = 20
RETENTION_YEARS_DEFAULT = 5


def set_retention_years(years: int) -> bool:
    """设置数据保留年数（1-20），返回是否缩小了保留年限"""
    global _retention_years
    years = max(RETENTION_YEARS_MIN, min(RETENTION_YEARS_MAX, int(years)))
    decreased = years < _retention_years
    _retention_years = years
    return decreased


def get_retention_years() -> int:
    return _retention_years


def _month_cutoff_ym() -> int:
    """与保留年数对齐的自然月截止（YYYYMM）"""
    now = now_local()
    return (now.year - _retention_years) * 100 + now.month

# 限速来源
LIMIT_SOURCE_NONE = ''
LIMIT_SOURCE_AUTO = 'auto'
LIMIT_SOURCE_MANUAL = 'manual'
LIMIT_SOURCE_CYCLE = 'cycle_reset'

_timezone = None


def set_timezone(tz):
    """注入与 scheduler 一致的时区（ZoneInfo）"""
    global _timezone
    _timezone = tz


def get_config_timezone():
    """返回与 scheduler 一致的配置时区，未注入时默认 Asia/Shanghai。"""
    if _timezone is not None:
        return _timezone
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo('Asia/Shanghai')
    except Exception:
        return timezone.utc


def now_local() -> datetime:
    """配置时区下的当前本地 naive 时间（用于入库与查询）"""
    if _timezone is not None:
        return datetime.now(_timezone).replace(tzinfo=None)
    return datetime.now()


def _to_local_naive(dt):
    if dt is None:
        return dt
    if getattr(dt, 'tzinfo', None):
        if _timezone is not None:
            return dt.astimezone(_timezone).replace(tzinfo=None)
        return dt.replace(tzinfo=None)
    return dt


def _bytes_column(direction: str) -> str:
    return 'downloaded_bytes' if direction == 'download' else 'uploaded_bytes'


def _backfill_column(direction: str) -> str:
    return (
        'backfilled_downloaded_bytes' if direction == 'download'
        else 'backfilled_uploaded_bytes'
    )


def _normalize_direction(direction: str) -> str:
    return 'download' if direction == 'download' else 'upload'


def get_conn():
    conn = sqlite3.connect(
        DB_PATH, check_same_thread=False, timeout=30.0,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_torrent_rank_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS torrent_rank_state (
            instance_name TEXT NOT NULL,
            torrent_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            size BIGINT NOT NULL DEFAULT 0,
            tracker TEXT NOT NULL DEFAULT '',
            last_uploaded BIGINT NOT NULL DEFAULT 0,
            last_downloaded BIGINT NOT NULL DEFAULT 0,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (instance_name, torrent_hash)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS torrent_traffic_hourly (
            instance_name TEXT NOT NULL,
            torrent_hash TEXT NOT NULL,
            hour_start DATETIME NOT NULL,
            uploaded_bytes BIGINT NOT NULL DEFAULT 0,
            downloaded_bytes BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (instance_name, torrent_hash, hour_start)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_torrent_hourly_instance_time
        ON torrent_traffic_hourly(instance_name, hour_start)
    ''')


def ensure_schema():
    """热更新：运行时补齐数据库结构，无需重启服务"""
    global _schema_ensured
    if _schema_ensured:
        return
    with _lock:
        if _schema_ensured:
            return
        conn = get_conn()
        try:
            c = conn.cursor()
            for table, column, col_type in _SCHEMA_COLUMNS:
                try:
                    c.execute(
                        f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
                except sqlite3.OperationalError as exc:
                    if 'duplicate column' not in str(exc).lower():
                        raise
            _ensure_torrent_rank_tables(c)
            conn.commit()
            _schema_ensured = True
        finally:
            conn.close()


def init_db():
    """初始化数据库"""
    global _schema_ensured
    with _lock:
        conn = get_conn()
        c = conn.cursor()

        c.execute('''
            CREATE TABLE IF NOT EXISTS traffic_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_name TEXT NOT NULL,
                hour_start DATETIME NOT NULL,
                uploaded_bytes BIGINT NOT NULL DEFAULT 0,
                UNIQUE(instance_name, hour_start)
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS traffic_monthly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_name TEXT NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                uploaded_bytes BIGINT NOT NULL DEFAULT 0,
                UNIQUE(instance_name, year, month)
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS device_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_name TEXT NOT NULL,
                event_time DATETIME NOT NULL,
                event_type TEXT NOT NULL,
                speed_limit_kbps INTEGER,
                reason TEXT
            )
        ''')
        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='speed_limit_events'"
        )
        if c.fetchone():
            c.execute('INSERT OR IGNORE INTO device_events SELECT * FROM speed_limit_events')
            c.execute('DROP TABLE speed_limit_events')

        c.execute('''
            CREATE TABLE IF NOT EXISTS instance_status (
                instance_name TEXT PRIMARY KEY,
                last_seen DATETIME,
                is_online INTEGER DEFAULT 0,
                current_speed_limit_kbps INTEGER DEFAULT 0,
                is_limited INTEGER DEFAULT 0,
                monthly_uploaded_bytes BIGINT DEFAULT 0,
                last_total_uploaded BIGINT DEFAULT 0,
                last_update DATETIME
            )
        ''')

        try:
            c.execute('ALTER TABLE instance_status ADD COLUMN last_delta_bytes BIGINT DEFAULT 0')
        except sqlite3.OperationalError as exc:
            if 'duplicate column' not in str(exc).lower():
                raise

        for table, column, col_type in _SCHEMA_COLUMNS:
            try:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
            except sqlite3.OperationalError as exc:
                if 'duplicate column' not in str(exc).lower():
                    raise

        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_hourly_instance_time
            ON traffic_hourly(instance_name, hour_start)
        ''')
        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_events_time
            ON device_events(event_time DESC)
        ''')
        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_events_key
            ON device_events(instance_name, event_type, event_key)
        ''')
        _ensure_torrent_rank_tables(c)

        conn.commit()
        c.execute('PRAGMA journal_mode=WAL').fetchall()  # 读完结果，否则新版 Python 下连接关闭后仍占着锁
        conn.commit()
        conn.close()
        _schema_ensured = True

    cleanup_old_data()


def _calc_session_delta(session_value: int, last_session: int) -> int:
    if last_session > 0:
        if session_value >= last_session:
            return session_value - last_session
        return session_value
    return session_value if session_value > 0 else 0


def has_session_baseline(instance_name: str) -> bool:
    """是否已有会话流量基线（用于判断恢复上线时是否补录）"""
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            last_up = _get_last_session_value_unlocked(
                c, instance_name, 'last_total_uploaded')
            return last_up > 0
        finally:
            conn.close()


def save_snapshot(instance_name: str, session_uploaded: int,
                  session_downloaded: int = 0, is_backfill: bool = False):
    """基于 qB 会话流量计算增量，写入小时表与自然月汇总表"""
    with _lock:
        conn = get_conn()
        try:
            now = now_local()
            c = conn.cursor()

            last_session_up = _get_last_session_value_unlocked(
                c, instance_name, 'last_total_uploaded')
            last_session_dl = _get_last_session_value_unlocked(
                c, instance_name, 'last_total_downloaded')

            session_reset_up = (
                last_session_up > 0 and session_uploaded < last_session_up
            )
            session_reset_dl = (
                last_session_dl > 0 and session_downloaded < last_session_dl
            )
            if session_reset_up or session_reset_dl:
                if session_reset_up:
                    logger.warning(
                        f"[{instance_name}] qB 会话上传计数已重置，"
                        "跳过补录并同步新基线"
                    )
                if session_reset_dl:
                    logger.warning(
                        f"[{instance_name}] qB 会话下载计数已重置，"
                        "跳过补录并同步新基线"
                    )
                is_backfill = False

            if last_session_up == 0:
                delta_up = 0
            elif session_reset_up:
                delta_up = 0
            else:
                delta_up = _calc_session_delta(session_uploaded, last_session_up)
            if last_session_dl == 0:
                delta_dl = 0
            elif session_reset_dl:
                delta_dl = 0
            else:
                delta_dl = _calc_session_delta(session_downloaded, last_session_dl)

            backfill_up = delta_up if is_backfill and delta_up > 0 else 0
            backfill_dl = delta_dl if is_backfill and delta_dl > 0 else 0

            if delta_up > 0 or delta_dl > 0:
                hour_start = now.replace(minute=0, second=0, microsecond=0)
                c.execute('''
                    INSERT INTO traffic_hourly
                    (instance_name, hour_start, uploaded_bytes, downloaded_bytes,
                     backfilled_uploaded_bytes, backfilled_downloaded_bytes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(instance_name, hour_start)
                    DO UPDATE SET
                        uploaded_bytes = uploaded_bytes + ?,
                        downloaded_bytes = downloaded_bytes + ?,
                        backfilled_uploaded_bytes = backfilled_uploaded_bytes + ?,
                        backfilled_downloaded_bytes = backfilled_downloaded_bytes + ?
                ''', (
                    instance_name, hour_start, delta_up, delta_dl,
                    backfill_up, backfill_dl,
                    delta_up, delta_dl, backfill_up, backfill_dl,
                ))

                c.execute('''
                    INSERT INTO traffic_monthly
                    (instance_name, year, month, uploaded_bytes, downloaded_bytes)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(instance_name, year, month)
                    DO UPDATE SET
                        uploaded_bytes = uploaded_bytes + ?,
                        downloaded_bytes = downloaded_bytes + ?
                ''', (instance_name, now.year, now.month,
                      delta_up, delta_dl, delta_up, delta_dl))

            c.execute('''
                INSERT INTO instance_status (
                    instance_name, last_total_uploaded, last_total_downloaded,
                    last_delta_bytes, last_delta_download_bytes, last_update
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET
                    last_total_uploaded = ?,
                    last_total_downloaded = ?,
                    last_delta_bytes = ?,
                    last_delta_download_bytes = ?,
                    last_update = ?,
                    deleted_at = NULL
            ''', (
                instance_name, session_uploaded, session_downloaded,
                delta_up, delta_dl, now,
                session_uploaded, session_downloaded,
                delta_up, delta_dl, now
            ))

            conn.commit()
            return delta_up, delta_dl, backfill_up, backfill_dl
        finally:
            conn.close()


def save_torrent_counters(instance_name: str, rows: list) -> None:
    """按种子累计计数记增量。第一次见到某颗种子只建基线，不把历史累计算进当前小时。"""
    if not rows:
        return
    now = now_local()
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                SELECT torrent_hash, name, size, tracker,
                       last_uploaded, last_downloaded
                FROM torrent_rank_state
                WHERE instance_name = ?
            ''', (instance_name,))
            prev = {
                row['torrent_hash']: row for row in c.fetchall()
            }
            state_rows = []
            hour_rows = []
            for row in rows or []:
                key = str((row or {}).get('hash') or '').strip().lower()
                if not key:
                    continue
                known = prev.get(key)
                has_up = 'uploaded' in row
                has_down = 'downloaded' in row
                if known is None and not has_up and not has_down:
                    continue
                name = row.get('name') or (known['name'] if known else '') or key
                size = row['size'] if 'size' in row else (
                    int(known['size']) if known else 0
                )
                tracker = row['tracker'] if 'tracker' in row else (
                    known['tracker'] if known else ''
                )
                if known is None:
                    last_up = int(row['uploaded']) if has_up else 0
                    last_down = int(row['downloaded']) if has_down else 0
                    delta_up = delta_down = 0
                else:
                    last_up = int(known['last_uploaded'])
                    last_down = int(known['last_downloaded'])
                    delta_up = delta_down = 0
                    if has_up:
                        current = int(row['uploaded'])
                        if current >= last_up:
                            delta_up = current - last_up
                        last_up = current
                    if has_down:
                        current = int(row['downloaded'])
                        if current >= last_down:
                            delta_down = current - last_down
                        last_down = current
                changed = known is None or (
                    name != known['name']
                    or int(size) != int(known['size'])
                    or tracker != known['tracker']
                    or last_up != int(known['last_uploaded'])
                    or last_down != int(known['last_downloaded'])
                )
                if not changed and delta_up == 0 and delta_down == 0:
                    continue
                if changed:
                    state_rows.append((
                        instance_name, key, name, int(size), tracker or '',
                        last_up, last_down, now,
                    ))
                    prev[key] = {
                        'name': name,
                        'size': int(size),
                        'tracker': tracker or '',
                        'last_uploaded': last_up,
                        'last_downloaded': last_down,
                    }
                if delta_up > 0 or delta_down > 0:
                    hour_rows.append((
                        instance_name, key, hour_start, delta_up, delta_down,
                        delta_up, delta_down,
                    ))
            if state_rows:
                c.executemany('''
                    INSERT INTO torrent_rank_state (
                        instance_name, torrent_hash, name, size, tracker,
                        last_uploaded, last_downloaded, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(instance_name, torrent_hash) DO UPDATE SET
                        name = excluded.name,
                        size = excluded.size,
                        tracker = excluded.tracker,
                        last_uploaded = excluded.last_uploaded,
                        last_downloaded = excluded.last_downloaded,
                        updated_at = excluded.updated_at
                ''', state_rows)
            if hour_rows:
                c.executemany('''
                    INSERT INTO torrent_traffic_hourly (
                        instance_name, torrent_hash, hour_start,
                        uploaded_bytes, downloaded_bytes
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(instance_name, torrent_hash, hour_start)
                    DO UPDATE SET
                        uploaded_bytes = uploaded_bytes + ?,
                        downloaded_bytes = downloaded_bytes + ?
                ''', hour_rows)
            if state_rows or hour_rows:
                conn.commit()
        finally:
            conn.close()


def get_torrent_period_ranks(instance_name: str, start: str, end: str,
                             hourly: bool = True) -> list:
    """所选时段内每颗种子的上传、下载，以及最近一次的名称、体积、tracker。"""
    start_s = _normalize_range_start(start)
    end_s = _normalize_range_end_exclusive(end, hourly=hourly)
    if not start_s or not end_s:
        return []
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                SELECT s.name AS name, s.size AS size, s.tracker AS tracker,
                       t.uploaded AS uploaded, t.downloaded AS downloaded
                FROM (
                    SELECT torrent_hash,
                           SUM(uploaded_bytes) AS uploaded,
                           SUM(downloaded_bytes) AS downloaded
                    FROM torrent_traffic_hourly
                    WHERE instance_name = ?
                      AND hour_start >= ?
                      AND hour_start < ?
                    GROUP BY torrent_hash
                ) t
                JOIN torrent_rank_state s
                  ON s.instance_name = ?
                 AND s.torrent_hash = t.torrent_hash
            ''', (instance_name, start_s, end_s, instance_name))
            rows = []
            for row in c.fetchall():
                rows.append({
                    'name': row['name'] or '',
                    'size': int(row['size'] or 0),
                    'tracker': row['tracker'] or '',
                    'uploaded': int(row['uploaded'] or 0),
                    'downloaded': int(row['downloaded'] or 0),
                })
            return rows
        finally:
            conn.close()


def _get_last_session_value_unlocked(cursor, instance_name: str,
                                     column: str) -> int:
    cursor.execute(
        f'SELECT {column} FROM instance_status WHERE instance_name = ?',
        (instance_name,)
    )
    row = cursor.fetchone()
    return row[column] if row else 0


def get_monthly_upload(instance_name: str, year: int = None, month: int = None) -> int:
    return _get_monthly_bytes(instance_name, 'uploaded_bytes', year, month)


def get_monthly_download(instance_name: str, year: int = None, month: int = None) -> int:
    return _get_monthly_bytes(instance_name, 'downloaded_bytes', year, month)


def get_cycle_bytes(instance_name: str, cycle_start,
                    direction: str = 'upload') -> int:
    direction = _normalize_direction(direction)
    column = _bytes_column(direction)
    start_str = _to_local_naive(cycle_start).strftime('%Y-%m-%d %H:%M:%S')

    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(f'''
                SELECT COALESCE(SUM({column}), 0) as total
                FROM traffic_hourly
                WHERE instance_name = ?
                AND hour_start >= ?
            ''', (instance_name, start_str))
            row = c.fetchone()
            return int(row['total']) if row else 0
        finally:
            conn.close()


def get_total_bytes(instance_name: str, direction: str = 'upload') -> int:
    direction = _normalize_direction(direction)
    column = _bytes_column(direction)

    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(f'''
                SELECT COALESCE(SUM({column}), 0) as total
                FROM traffic_hourly
                WHERE instance_name = ?
            ''', (instance_name,))
            row = c.fetchone()
            return int(row['total']) if row else 0
        finally:
            conn.close()


def get_yesterday_bytes(instance_name: str, direction: str = 'upload') -> int:
    """获取昨日（本地时区）上传或下载总量"""
    direction = _normalize_direction(direction)
    column = _bytes_column(direction)
    yesterday = (now_local() - timedelta(days=1)).strftime('%Y-%m-%d')

    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(f'''
                SELECT COALESCE(SUM({column}), 0) as total
                FROM traffic_hourly
                WHERE instance_name = ?
                AND DATE(hour_start) = ?
            ''', (instance_name, yesterday))
            row = c.fetchone()
            return int(row['total']) if row else 0
        finally:
            conn.close()


def get_today_bytes(instance_name: str, direction: str = 'upload') -> int:
    """获取今日（本地时区）上传或下载总量"""
    direction = _normalize_direction(direction)
    column = _bytes_column(direction)
    today = now_local().strftime('%Y-%m-%d')

    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(f'''
                SELECT COALESCE(SUM({column}), 0) as total
                FROM traffic_hourly
                WHERE instance_name = ?
                AND DATE(hour_start) = ?
            ''', (instance_name, today))
            row = c.fetchone()
            return int(row['total']) if row else 0
        finally:
            conn.close()


def get_status_traffic_batch(instance_names: list, cycle_starts: dict) -> dict:
    """单次加锁批量读取状态 API 所需流量字段。"""
    names = [n for n in (instance_names or []) if n]
    if not names:
        return {}
    today = now_local().strftime('%Y-%m-%d')
    yesterday = (now_local() - timedelta(days=1)).strftime('%Y-%m-%d')
    placeholders = ','.join('?' * len(names))
    result = {
        n: {
            'cycle_upload': 0,
            'cycle_download': 0,
            'device_upload': 0,
            'device_download': 0,
            'today_upload': 0,
            'today_download': 0,
            'yesterday_upload': 0,
            'yesterday_download': 0,
            'data_start_time': None,
        }
        for n in names
    }

    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            for direction in ('upload', 'download'):
                col = _bytes_column(direction)
                suffix = direction
                c.execute(f'''
                    SELECT instance_name, COALESCE(SUM({col}), 0) AS total
                    FROM traffic_hourly
                    WHERE instance_name IN ({placeholders})
                    GROUP BY instance_name
                ''', names)
                for row in c.fetchall():
                    result[row['instance_name']][f'device_{suffix}'] = int(row['total'])

                c.execute(f'''
                    SELECT instance_name, COALESCE(SUM({col}), 0) AS total
                    FROM traffic_hourly
                    WHERE instance_name IN ({placeholders})
                      AND DATE(hour_start) = ?
                    GROUP BY instance_name
                ''', (*names, today))
                for row in c.fetchall():
                    result[row['instance_name']][f'today_{suffix}'] = int(row['total'])

                c.execute(f'''
                    SELECT instance_name, COALESCE(SUM({col}), 0) AS total
                    FROM traffic_hourly
                    WHERE instance_name IN ({placeholders})
                      AND DATE(hour_start) = ?
                    GROUP BY instance_name
                ''', (*names, yesterday))
                for row in c.fetchall():
                    result[row['instance_name']][f'yesterday_{suffix}'] = int(row['total'])

            c.execute(f'''
                SELECT instance_name, MIN(hour_start) AS start_time
                FROM traffic_hourly
                WHERE instance_name IN ({placeholders})
                GROUP BY instance_name
            ''', names)
            for row in c.fetchall():
                result[row['instance_name']]['data_start_time'] = row['start_time']

            for name in names:
                cycle_start = (cycle_starts or {}).get(name)
                if not cycle_start:
                    continue
                start_str = _to_local_naive(cycle_start).strftime('%Y-%m-%d %H:%M:%S')
                for direction in ('upload', 'download'):
                    col = _bytes_column(direction)
                    c.execute(f'''
                        SELECT COALESCE(SUM({col}), 0) AS total
                        FROM traffic_hourly
                        WHERE instance_name = ? AND hour_start >= ?
                    ''', (name, start_str))
                    row = c.fetchone()
                    result[name][f'cycle_{direction}'] = int(row['total']) if row else 0
        finally:
            conn.close()
    return result


def get_data_start_time(instance_name: str):
    """获取该设备流量数据的最早记录时间"""
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                SELECT MIN(hour_start) as start_time
                FROM traffic_hourly
                WHERE instance_name = ?
            ''', (instance_name,))
            row = c.fetchone()
            return row['start_time'] if row and row['start_time'] else None
        finally:
            conn.close()


def get_cycle_stats(instance_name: str, periods: list,
                    direction: str = 'upload') -> list:
    direction = _normalize_direction(direction)
    column = _bytes_column(direction)
    result = []

    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            for p in periods:
                start_str = _to_local_naive(p['cycle_start']).strftime('%Y-%m-%d %H:%M:%S')
                end_str = _to_local_naive(p['cycle_end']).strftime('%Y-%m-%d %H:%M:%S')
                c.execute(f'''
                    SELECT COALESCE(SUM({column}), 0) as total
                    FROM traffic_hourly
                    WHERE instance_name = ?
                    AND hour_start >= ?
                    AND hour_start < ?
                ''', (instance_name, start_str, end_str))
                row = c.fetchone()
                result.append({
                    'period': p['period'],
                    'cycle_start': _to_local_naive(p['cycle_start']).strftime('%Y-%m-%d'),
                    'total_bytes': int(row['total']) if row else 0,
                })
        finally:
            conn.close()
    return result


def _get_monthly_bytes(instance_name: str, column: str,
                       year: int = None, month: int = None) -> int:
    now = now_local()
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(f'''
                SELECT {column} FROM traffic_monthly
                WHERE instance_name = ? AND year = ? AND month = ?
            ''', (instance_name, year, month))
            row = c.fetchone()
            return row[column] if row else 0
        finally:
            conn.close()


def _cutoff_str(days: int = 0, hours: int = 0) -> str:
    dt = now_local() - timedelta(days=days, hours=hours)
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _normalize_range_start(value: str) -> str:
    if not value:
        return None
    value = value.strip().replace('T', ' ')
    if len(value) == 7 and value[4] == '-':
        return f'{value}-01 00:00:00'
    if len(value) == 10:
        return f'{value} 00:00:00'
    if len(value) == 16:
        return f'{value}:00'
    return value[:19]


def _normalize_range_end_exclusive(value: str, *, hourly: bool = True) -> str:
    if not value:
        return None
    v = value.strip()
    if len(v) == 7 and v[4] == '-':
        year = int(v[:4])
        month = int(v[5:])
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
        return f'{year:04d}-{month:02d}-01 00:00:00'
    start_s = _normalize_range_start(value)
    dt = datetime.strptime(start_s[:19], '%Y-%m-%d %H:%M:%S')
    if hourly:
        dt += timedelta(hours=1)
    else:
        dt += timedelta(days=1)
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def get_hourly_stats(instance_name: str, hours: int = 24,
                     direction: str = 'upload',
                     start: str = None, end: str = None) -> list:
    direction = _normalize_direction(direction)
    column = _bytes_column(direction)
    backfill_col = _backfill_column(direction)
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            if start and end:
                start_s = _normalize_range_start(start)
                end_s = _normalize_range_end_exclusive(end, hourly=True)
                c.execute(f'''
                    SELECT hour_start as hour, {column} as total_bytes,
                           {backfill_col} as backfilled_bytes
                    FROM traffic_hourly
                    WHERE instance_name = ?
                    AND hour_start >= ?
                    AND hour_start < ?
                    ORDER BY hour_start ASC
                ''', (instance_name, start_s, end_s))
            else:
                cutoff = _cutoff_str(hours=hours)
                c.execute(f'''
                    SELECT hour_start as hour, {column} as total_bytes,
                           {backfill_col} as backfilled_bytes
                    FROM traffic_hourly
                    WHERE instance_name = ?
                    AND hour_start >= ?
                    ORDER BY hour_start ASC
                ''', (instance_name, cutoff))
            rows = [dict(row) for row in c.fetchall()]
            for r in rows:
                if r.get('hour'):
                    r['hour'] = str(r['hour'])[:16]
                r['backfilled_bytes'] = int(r.get('backfilled_bytes') or 0)
            return rows
        finally:
            conn.close()


def get_daily_stats(instance_name: str, days: int = 30,
                    direction: str = 'upload',
                    start: str = None, end: str = None) -> list:
    direction = _normalize_direction(direction)
    column = _bytes_column(direction)
    backfill_col = _backfill_column(direction)
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            if start and end:
                start_s = _normalize_range_start(start)
                end_s = _normalize_range_end_exclusive(end, hourly=False)
                params = (instance_name, start_s, end_s)
                where_time = 'hour_start >= ? AND hour_start < ?'
            else:
                cutoff = _cutoff_str(days=days)
                params = (instance_name, cutoff)
                where_time = 'hour_start >= ?'
            c.execute(f'''
                SELECT DATE(hour_start) as day, SUM({column}) as total_bytes,
                       SUM({backfill_col}) as backfilled_bytes
                FROM traffic_hourly
                WHERE instance_name = ?
                AND {where_time}
                GROUP BY DATE(hour_start)
                ORDER BY day ASC
            ''', params)
            rows = [dict(row) for row in c.fetchall()]
            for r in rows:
                r['backfilled_bytes'] = int(r.get('backfilled_bytes') or 0)
            return rows
        finally:
            conn.close()


def get_weekly_stats(instance_name: str, weeks: int = 12,
                     direction: str = 'upload',
                     start: str = None, end: str = None) -> list:
    direction = _normalize_direction(direction)
    column = _bytes_column(direction)
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            if start and end:
                start_s = _normalize_range_start(start)
                end_s = _normalize_range_end_exclusive(end, hourly=False)
                params = (instance_name, start_s, end_s)
                where_time = 'hour_start >= ? AND hour_start < ?'
            else:
                cutoff = _cutoff_str(days=weeks * 7)
                params = (instance_name, cutoff)
                where_time = 'hour_start >= ?'
            backfill_col = _backfill_column(direction)
            c.execute(f'''
                SELECT strftime('%G-W%V', hour_start) as week,
                       SUM({column}) as total_bytes,
                       SUM({backfill_col}) as backfilled_bytes
                FROM traffic_hourly
                WHERE instance_name = ?
                AND {where_time}
                GROUP BY strftime('%G-W%V', hour_start)
                ORDER BY week ASC
            ''', params)
            rows = [dict(row) for row in c.fetchall()]
            for r in rows:
                r['backfilled_bytes'] = int(r.get('backfilled_bytes') or 0)
            return rows
        finally:
            conn.close()


def get_yearly_stats(instance_name: str, years: int = 10,
                     direction: str = 'upload',
                     start_year: int = None, end_year: int = None) -> list:
    direction = _normalize_direction(direction)
    column = _bytes_column(direction)
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            if start_year is not None and end_year is not None:
                c.execute(f'''
                    SELECT year, SUM({column}) as total_bytes
                    FROM traffic_monthly
                    WHERE instance_name = ?
                      AND year BETWEEN ? AND ?
                    GROUP BY year
                    ORDER BY year ASC
                ''', (instance_name, start_year, end_year))
                rows = [dict(row) for row in c.fetchall()]
            else:
                c.execute(f'''
                    SELECT year, SUM({column}) as total_bytes
                    FROM traffic_monthly
                    WHERE instance_name = ?
                    GROUP BY year
                    ORDER BY year DESC
                    LIMIT ?
                ''', (instance_name, years))
                rows = list(reversed([dict(row) for row in c.fetchall()]))
            for r in rows:
                r['period'] = str(r['year'])
            return rows
        finally:
            conn.close()


def get_monthly_stats(instance_name: str, months: int = 12,
                      direction: str = 'upload',
                      start: str = None, end: str = None) -> list:
    """自然月统计（与达量限速周期可能不同，图表标注为自然月）"""
    direction = _normalize_direction(direction)
    column = _bytes_column(direction)
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            if start and end:
                try:
                    sy, sm = int(start[:4]), int(start[5:7])
                    ey, em = int(end[:4]), int(end[5:7])
                    start_key = sy * 100 + sm
                    end_key = ey * 100 + em
                except (ValueError, IndexError):
                    start_key = end_key = None
                if start_key is not None and end_key is not None:
                    c.execute(f'''
                        SELECT year, month, {column} as total_bytes
                        FROM traffic_monthly
                        WHERE instance_name = ?
                          AND (year * 100 + month) BETWEEN ? AND ?
                        ORDER BY year ASC, month ASC
                    ''', (instance_name, start_key, end_key))
                    rows = [dict(row) for row in c.fetchall()]
                    for r in rows:
                        r['period'] = f"{r['year']}-{r['month']:02d}"
                    return rows
            c.execute(f'''
                SELECT year, month, {column} as total_bytes
                FROM traffic_monthly
                WHERE instance_name = ?
                ORDER BY year DESC, month DESC
                LIMIT ?
            ''', (instance_name, months))
            rows = list(reversed([dict(row) for row in c.fetchall()]))
            for r in rows:
                r['period'] = f"{r['year']}-{r['month']:02d}"
            return rows
        finally:
            conn.close()


def update_instance_status(instance_name: str, is_online: bool,
                           current_speed_limit_kbps: int = -1,
                           is_quota_limited: bool = None,
                           has_upload_limit: bool = None,
                           is_limited: bool = None,
                           limit_source: str = None,
                           monthly_uploaded_bytes: int = 0,
                           monthly_downloaded_bytes: int = 0,
                           alt_upload_limit_kbps: int = None,
                           alt_speed_limits_active: bool = None):
    prev_online = None
    with _lock:
        conn = get_conn()
        try:
            now = now_local()
            c = conn.cursor()

            c.execute(
                'SELECT is_online FROM instance_status WHERE instance_name = ?',
                (instance_name,),
            )
            prev_row = c.fetchone()
            if prev_row is not None:
                prev_online = bool(prev_row['is_online'])

            if is_quota_limited is None and is_limited is not None:
                is_quota_limited = is_limited
            if is_quota_limited is None:
                is_quota_limited = False
            if has_upload_limit is None:
                has_upload_limit = current_speed_limit_kbps > 0
            if limit_source is None:
                limit_source = LIMIT_SOURCE_NONE
            if alt_upload_limit_kbps is None:
                alt_upload_limit_kbps = 0
            if alt_speed_limits_active is None:
                alt_speed_limits_active = False
            if current_speed_limit_kbps < 0:
                c.execute(
                    'SELECT current_speed_limit_kbps FROM instance_status WHERE instance_name = ?',
                    (instance_name,)
                )
                row = c.fetchone()
                current_speed_limit_kbps = (
                    row['current_speed_limit_kbps'] if row else 0
                )

            c.execute('''
                INSERT INTO instance_status
                (instance_name, last_seen, is_online, current_speed_limit_kbps,
                 is_limited, is_quota_limited, has_upload_limit, limit_source,
                 monthly_uploaded_bytes, monthly_downloaded_bytes,
                 alt_upload_limit_kbps, alt_speed_limits_active, last_update)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET
                    last_seen = CASE WHEN ? = 1 THEN ? ELSE last_seen END,
                    is_online = ?,
                    current_speed_limit_kbps = ?,
                    is_limited = ?,
                    is_quota_limited = ?,
                    has_upload_limit = ?,
                    limit_source = ?,
                    monthly_uploaded_bytes = ?,
                    monthly_downloaded_bytes = ?,
                    alt_upload_limit_kbps = ?,
                    alt_speed_limits_active = ?,
                    last_update = ?,
                    deleted_at = NULL
            ''', (
                instance_name, now if is_online else None,
                1 if is_online else 0,
                current_speed_limit_kbps,
                1 if is_quota_limited else 0,
                1 if is_quota_limited else 0,
                1 if has_upload_limit else 0,
                limit_source,
                monthly_uploaded_bytes, monthly_downloaded_bytes,
                alt_upload_limit_kbps,
                1 if alt_speed_limits_active else 0,
                now,
                1 if is_online else 0, now,
                1 if is_online else 0,
                current_speed_limit_kbps,
                1 if is_quota_limited else 0,
                1 if is_quota_limited else 0,
                1 if has_upload_limit else 0,
                limit_source,
                monthly_uploaded_bytes, monthly_downloaded_bytes,
                alt_upload_limit_kbps,
                1 if alt_speed_limits_active else 0,
                now,
            ))
            conn.commit()
        finally:
            conn.close()

    if prev_online is not None and prev_online != is_online:
        add_device_event(
            instance_name,
            'device_online' if is_online else 'device_offline',
            None,
            '连接恢复' if is_online else '连接中断，进入离线探测模式',
        )


def get_limit_source(instance_name: str) -> str:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(
                'SELECT limit_source FROM instance_status WHERE instance_name = ?',
                (instance_name,)
            )
            row = c.fetchone()
            return (row['limit_source'] or '') if row else LIMIT_SOURCE_NONE
        finally:
            conn.close()


def set_limit_source(instance_name: str, source: str):
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                INSERT INTO instance_status (instance_name, limit_source)
                VALUES (?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET limit_source = ?
            ''', (instance_name, source, source))
            conn.commit()
        finally:
            conn.close()


def get_skip_auto_unlimit_once(instance_name: str) -> bool:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(
                'SELECT skip_auto_unlimit_once FROM instance_status WHERE instance_name = ?',
                (instance_name,)
            )
            row = c.fetchone()
            return bool(row and row['skip_auto_unlimit_once'])
        finally:
            conn.close()


def set_skip_auto_unlimit_once(instance_name: str, value: bool):
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                INSERT INTO instance_status (instance_name, skip_auto_unlimit_once)
                VALUES (?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET skip_auto_unlimit_once = ?
            ''', (instance_name, 1 if value else 0, 1 if value else 0))
            conn.commit()
        finally:
            conn.close()


def get_manual_baseline_threshold_gb(instance_name: str) -> float:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(
                'SELECT manual_baseline_threshold_gb FROM instance_status WHERE instance_name = ?',
                (instance_name,),
            )
            row = c.fetchone()
            if not row or row['manual_baseline_threshold_gb'] is None:
                return 0.0
            return float(row['manual_baseline_threshold_gb'])
        finally:
            conn.close()


def set_manual_baseline_threshold_gb(instance_name: str, value: float):
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                INSERT INTO instance_status (instance_name, manual_baseline_threshold_gb)
                VALUES (?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET manual_baseline_threshold_gb = ?
            ''', (instance_name, value, value))
            conn.commit()
        finally:
            conn.close()


def get_normal_global_upload_limit_kbps(instance_name: str):
    """达量覆盖前保存的常规全局上传限速（KB/s）；未记录时返回 None"""
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(
                'SELECT normal_global_upload_limit_kbps FROM instance_status WHERE instance_name = ?',
                (instance_name,),
            )
            row = c.fetchone()
            if not row or row['normal_global_upload_limit_kbps'] is None:
                return None
            value = int(row['normal_global_upload_limit_kbps'])
            return None if value < 0 else value
        finally:
            conn.close()


def set_normal_global_upload_limit_kbps(instance_name: str, limit_kbps: int):
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            stored = -1 if limit_kbps is None else max(0, int(limit_kbps))
            c.execute('''
                INSERT INTO instance_status (instance_name, normal_global_upload_limit_kbps)
                VALUES (?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET normal_global_upload_limit_kbps = ?
            ''', (instance_name, stored, stored))
            conn.commit()
        finally:
            conn.close()


def clear_normal_global_upload_limit(instance_name: str):
    set_normal_global_upload_limit_kbps(instance_name, None)


def get_last_programmed_limit_kbps(instance_name: str):
    """程序上次成功写入的全局限速 (KB/s)。None 表示尚未记录。"""
    with get_connection() as conn:
        row = conn.execute(
            'SELECT last_programmed_limit_kbps FROM instance_status WHERE instance_name = ?',
            (instance_name,),
        ).fetchone()
        if not row or row['last_programmed_limit_kbps'] is None:
            return None
        value = int(row['last_programmed_limit_kbps'])
        if value < 0:
            return None
        return value


def set_last_programmed_limit_kbps(instance_name: str, limit_kbps: int):
    kbps = max(0, int(limit_kbps or 0))
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO instance_status (instance_name, last_programmed_limit_kbps)\n"
            "               VALUES (?, ?)\n"
            "               ON CONFLICT(instance_name) DO UPDATE SET last_programmed_limit_kbps = ?",
            (instance_name, kbps, kbps),
        )


def clear_last_programmed_limit_kbps(instance_name: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO instance_status (instance_name, last_programmed_limit_kbps)\n"
            "               VALUES (?, -1)\n"
            "               ON CONFLICT(instance_name) DO UPDATE SET last_programmed_limit_kbps = -1",
            (instance_name,),
        )



def get_last_applied_cycle_start(instance_name: str) -> str:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(
                'SELECT last_applied_cycle_start FROM instance_status WHERE instance_name = ?',
                (instance_name,)
            )
            row = c.fetchone()
            return row['last_applied_cycle_start'] if row else None
        finally:
            conn.close()


def _format_trigger_time(dt: datetime = None) -> str:
    return (dt or now_local()).strftime('%Y-%m-%d %H:%M:%S')


def _parse_rule_trigger_times(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): v for k, v in data.items()}
    except (json.JSONDecodeError, TypeError):
        return {}


def _get_trigger_record_fields(c, instance_name: str):
    c.execute(
        '''SELECT rule_trigger_times, manual_limit_trigger_at,
                  manual_limit_trigger_kbps
           FROM instance_status WHERE instance_name = ?''',
        (instance_name,)
    )
    row = c.fetchone()
    if not row:
        return {}, None, 0
    return (
        _parse_rule_trigger_times(row['rule_trigger_times']),
        row['manual_limit_trigger_at'],
        row['manual_limit_trigger_kbps'] or 0,
    )


def get_rule_trigger_times(instance_name: str) -> dict:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            times, _, _ = _get_trigger_record_fields(c, instance_name)
            return times
        finally:
            conn.close()


def get_manual_limit_trigger_kbps(instance_name: str) -> int:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            _, _, manual_kbps = _get_trigger_record_fields(c, instance_name)
            return manual_kbps or 0
        finally:
            conn.close()


def record_rule_trigger(instance_name: str, rule_index: int) -> bool:
    """记录规则在本周期内首次触发时间，已存在则跳过"""
    key = str(rule_index)
    with _lock:
        conn = get_conn()
        try:
            now = _format_trigger_time()
            c = conn.cursor()
            times, _, _ = _get_trigger_record_fields(c, instance_name)
            if key in times:
                return False
            times[key] = now
            c.execute('''
                INSERT INTO instance_status (instance_name, rule_trigger_times)
                VALUES (?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET rule_trigger_times = ?
            ''', (instance_name, json.dumps(times, ensure_ascii=False), json.dumps(times, ensure_ascii=False)))
            conn.commit()
            return True
        finally:
            conn.close()


def record_rule_trigger_force(instance_name: str, rule_index: int):
    """强制刷新规则触发时间为当前时刻（保存后立即生效等场景）"""
    key = str(rule_index)
    with _lock:
        conn = get_conn()
        try:
            now = _format_trigger_time()
            c = conn.cursor()
            times, _, _ = _get_trigger_record_fields(c, instance_name)
            times[key] = now
            c.execute('''
                INSERT INTO instance_status (instance_name, rule_trigger_times)
                VALUES (?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET rule_trigger_times = ?
            ''', (instance_name, json.dumps(times, ensure_ascii=False), json.dumps(times, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()


def _get_active_rule_index(speed_rules: list, cycle_gb: float):
    active_idx = None
    active_threshold = -1.0
    for idx, rule in enumerate(speed_rules, start=1):
        threshold = rule.get(
            'cycle_upload_limit_gb',
            rule.get('monthly_upload_limit_gb', 0),
        )
        if cycle_gb >= threshold and threshold > active_threshold:
            active_threshold = threshold
            active_idx = idx
    return active_idx


def record_manual_limit_trigger(instance_name: str, limit_kbps: int):
    with _lock:
        conn = get_conn()
        try:
            now = _format_trigger_time()
            c = conn.cursor()
            c.execute('''
                INSERT INTO instance_status
                (instance_name, manual_limit_trigger_at, manual_limit_trigger_kbps)
                VALUES (?, ?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET
                    manual_limit_trigger_at = ?,
                    manual_limit_trigger_kbps = ?
            ''', (instance_name, now, limit_kbps, now, limit_kbps))
            conn.commit()
        finally:
            conn.close()


def clear_manual_limit_trigger(instance_name: str):
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                INSERT INTO instance_status
                (instance_name, manual_limit_trigger_at, manual_limit_trigger_kbps)
                VALUES (?, NULL, 0)
                ON CONFLICT(instance_name) DO UPDATE SET
                    manual_limit_trigger_at = NULL,
                    manual_limit_trigger_kbps = 0
            ''', (instance_name,))
            conn.commit()
        finally:
            conn.close()


def clear_limit_trigger_records(instance_name: str):
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                INSERT INTO instance_status
                (instance_name, rule_trigger_times, manual_limit_trigger_at,
                 manual_limit_trigger_kbps)
                VALUES (?, '', NULL, 0)
                ON CONFLICT(instance_name) DO UPDATE SET
                    rule_trigger_times = '',
                    manual_limit_trigger_at = NULL,
                    manual_limit_trigger_kbps = 0
            ''', (instance_name,))
            conn.commit()
        finally:
            conn.close()


def _save_rule_trigger_times(instance_name: str, times: dict):
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            payload = json.dumps(times, ensure_ascii=False)
            c.execute('''
                INSERT INTO instance_status (instance_name, rule_trigger_times)
                VALUES (?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET rule_trigger_times = ?
            ''', (instance_name, payload, payload))
            conn.commit()
        finally:
            conn.close()


def prune_stale_rule_triggers(instance_name: str, speed_rules: list,
                              cycle_uploaded_bytes: int) -> bool:
    """移除当前流量未达阈值的规则触发记录（阈值调高后立即生效时同步卡片状态）"""
    if not speed_rules:
        return False
    cycle_gb = cycle_uploaded_bytes / (1024 ** 3)
    times = get_rule_trigger_times(instance_name)
    if not times:
        return False
    changed = False
    for idx, rule in enumerate(speed_rules, start=1):
        threshold = rule.get(
            'cycle_upload_limit_gb',
            rule.get('monthly_upload_limit_gb', 0),
        )
        key = str(idx)
        if cycle_gb < threshold and key in times:
            del times[key]
            changed = True
    if changed:
        _save_rule_trigger_times(instance_name, times)
    return changed


def sync_triggered_rules(instance_name: str, speed_rules: list, cycle_uploaded_bytes: int):
    """同步规则触发时间：清除未达阈值的旧记录，补记新触发的规则"""
    if not speed_rules:
        return
    prune_stale_rule_triggers(instance_name, speed_rules, cycle_uploaded_bytes)
    cycle_gb = cycle_uploaded_bytes / (1024 ** 3)
    for idx, rule in enumerate(speed_rules, start=1):
        threshold = rule.get(
            'cycle_upload_limit_gb',
            rule.get('monthly_upload_limit_gb', 0),
        )
        if cycle_gb >= threshold:
            record_rule_trigger(instance_name, idx)


def build_limit_trigger_summary(instance_name: str, speed_rules: list = None,
                                cycle_uploaded_bytes: int = None,
                                limit_source: str = None,
                                rule_trigger_times=None,
                                manual_limit_trigger_at=None,
                                manual_limit_trigger_kbps=None) -> dict:
    if rule_trigger_times is not None:
        if isinstance(rule_trigger_times, str):
            rule_times = _parse_rule_trigger_times(rule_trigger_times)
        elif isinstance(rule_trigger_times, dict):
            rule_times = rule_trigger_times
        else:
            rule_times = {}
        manual_at = manual_limit_trigger_at
        manual_kbps = manual_limit_trigger_kbps or 0
    else:
        with _lock:
            conn = get_conn()
            try:
                c = conn.cursor()
                rule_times, manual_at, manual_kbps = _get_trigger_record_fields(
                    c, instance_name)
            finally:
                conn.close()

    manual_at_str = None
    if manual_at:
        manual_at_str = manual_at if isinstance(manual_at, str) else str(manual_at)

    last_at = None
    last_label = None

    if limit_source == LIMIT_SOURCE_MANUAL and manual_at_str:
        last_at = manual_at_str
        last_label = '手动覆盖'
    elif speed_rules and cycle_uploaded_bytes is not None:
        cycle_gb = cycle_uploaded_bytes / (1024 ** 3)
        active_idx = _get_active_rule_index(speed_rules, cycle_gb)
        if active_idx is not None:
            last_at = rule_times.get(str(active_idx))
            last_label = f'规则{active_idx}'
    else:
        candidates = []
        for idx, trigger_at in rule_times.items():
            if trigger_at:
                candidates.append((trigger_at, f'规则{idx}'))
        if manual_at_str:
            candidates.append((manual_at_str, '手动覆盖'))
        if candidates:
            last_at, last_label = max(candidates, key=lambda item: item[0])

    return {
        'last_limit_trigger_at': last_at,
        'last_limit_trigger_label': last_label,
        'manual_limit_trigger_at': manual_at_str,
        'manual_limit_trigger_kbps': manual_kbps,
        'rule_trigger_times': rule_times,
    }


def set_last_applied_cycle_start(instance_name: str, cycle_start_key: str):
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                INSERT INTO instance_status (instance_name, last_applied_cycle_start)
                VALUES (?, ?)
                ON CONFLICT(instance_name) DO UPDATE SET last_applied_cycle_start = ?
            ''', (instance_name, cycle_start_key, cycle_start_key))
            conn.commit()
        finally:
            conn.close()


def try_begin_cycle_transition(instance_name: str,
                               expected_prev_key,
                               new_key: str) -> bool:
    """原子 claim：仅当 last_applied_cycle_start 仍为 expected_prev_key 时写入 new_key。"""
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            if expected_prev_key is None:
                c.execute('''
                    INSERT INTO instance_status (instance_name, last_applied_cycle_start)
                    VALUES (?, ?)
                    ON CONFLICT(instance_name) DO UPDATE SET
                        last_applied_cycle_start = excluded.last_applied_cycle_start
                    WHERE last_applied_cycle_start IS NULL
                       OR trim(last_applied_cycle_start) = ''
                ''', (instance_name, new_key))
            else:
                c.execute('''
                    UPDATE instance_status
                    SET last_applied_cycle_start = ?
                    WHERE instance_name = ?
                      AND last_applied_cycle_start = ?
                ''', (new_key, instance_name, expected_prev_key))
            conn.commit()
            return c.rowcount > 0
        finally:
            conn.close()


def _coerce_event_time(value):
    """入库时间：空为当前；整数按 Unix 秒转到配置时区。"""
    if value is None or value == '':
        return now_local()
    if isinstance(value, datetime):
        return _to_local_naive(value)
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return now_local()
    if ts <= 0:
        return now_local()
    return datetime.fromtimestamp(
        ts, timezone.utc,
    ).astimezone(get_config_timezone()).replace(tzinfo=None)


def _backfill_exists(cursor, instance_name: str, event_type: str,
                     event_key: str, reason: str, when: datetime) -> bool:
    """同一哈希已补过，或旧记录在同一小时内已有同名事件，就不再插入。"""
    if event_key:
        cursor.execute('''
            SELECT 1 FROM device_events
            WHERE instance_name = ? AND event_type = ? AND event_key = ?
            LIMIT 1
        ''', (instance_name, event_type, event_key))
        if cursor.fetchone():
            return True
        if reason and when is not None:
            cursor.execute('''
                SELECT 1 FROM device_events
                WHERE instance_name = ? AND event_type = ? AND reason = ?
                  AND (event_key IS NULL OR event_key = '')
                  AND event_time >= ? AND event_time <= ?
                LIMIT 1
            ''', (
            instance_name, event_type, reason,
            when - timedelta(hours=1), when + timedelta(hours=1),
        ))
        if cursor.fetchone():
            return True
    return False


def add_device_events(instance_name: str, events: list):
    """写入设备事件。backfill 为真时按哈希跳过已有记录，并只在末尾做一次保留裁剪。"""
    if not events:
        return
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            for event in events:
                event_type = event.get('event_type')
                if not event_type:
                    continue
                when = _coerce_event_time(event.get('event_time'))
                reason = event.get('reason') or None
                event_key = event.get('event_key') or None
                if event.get('backfill') and _backfill_exists(
                    c, instance_name, event_type, event_key, reason, when,
                ):
                    continue
                c.execute('''
                    INSERT INTO device_events
                    (instance_name, event_time, event_type,
                     speed_limit_kbps, reason, event_key)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    instance_name, when, event_type,
                    event.get('speed_limit_kbps'), reason, event_key,
                ))
            c.execute('''
                DELETE FROM device_events WHERE id NOT IN (
                    SELECT id FROM device_events
                    ORDER BY event_time DESC LIMIT ?
                )
            ''', (EVENT_RETENTION_COUNT,))
            conn.commit()
        finally:
            conn.close()


def add_device_event(instance_name: str, event_type: str,
                     speed_limit_kbps: int = None, reason: str = None):
    add_device_events(instance_name, [{
        'event_type': event_type,
        'speed_limit_kbps': speed_limit_kbps,
        'reason': reason,
    }])


add_speed_event = add_device_event


def get_device_events(instance_name: str = None, limit: int = 500) -> list:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            if instance_name:
                c.execute('''
                    SELECT * FROM device_events
                    WHERE instance_name = ?
                    ORDER BY event_time DESC LIMIT ?
                ''', (instance_name, limit))
            else:
                c.execute('''
                    SELECT * FROM device_events
                    ORDER BY event_time DESC LIMIT ?
                ''', (limit,))
            return [dict(row) for row in c.fetchall()]
        finally:
            conn.close()


get_speed_events = get_device_events


def get_last_offline_times() -> dict:
    """返回各实例最近一次 device_offline 事件时间 {instance_name: event_time}"""
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                SELECT instance_name, MAX(event_time) AS offline_since
                FROM device_events
                WHERE event_type = 'device_offline'
                GROUP BY instance_name
            ''')
            return {
                row['instance_name']: row['offline_since']
                for row in c.fetchall()
                if row['offline_since']
            }
        finally:
            conn.close()


def get_last_online_times() -> dict:
    """返回各实例最近一次 device_online 事件时间 {instance_name: event_time}"""
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''
                SELECT instance_name, MAX(event_time) AS online_since
                FROM device_events
                WHERE event_type = 'device_online'
                GROUP BY instance_name
            ''')
            return {
                row['instance_name']: row['online_since']
                for row in c.fetchall()
                if row['online_since']
            }
        finally:
            conn.close()


def is_instance_online(instance_name: str) -> bool:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(
                'SELECT is_online FROM instance_status WHERE instance_name = ?',
                (instance_name,),
            )
            row = c.fetchone()
            return bool(row and row['is_online'])
        finally:
            conn.close()


def get_all_instance_status() -> list:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('SELECT * FROM instance_status')
            return [dict(row) for row in c.fetchall()]
        finally:
            conn.close()


def rename_instance_data(old_name: str, new_name: str):
    if old_name == new_name:
        return
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(
                'SELECT 1 FROM instance_status WHERE instance_name = ?',
                (new_name,),
            )
            new_status_exists = c.fetchone() is not None
            if new_status_exists:
                if not _has_meaningful_instance_data_unlocked(c, new_name):
                    c.execute(
                        'DELETE FROM instance_status WHERE instance_name = ?',
                        (new_name,),
                    )
                    c.execute(
                        'UPDATE instance_status SET instance_name = ? '
                        'WHERE instance_name = ?',
                        (new_name, old_name),
                    )
                else:
                    c.execute(
                        'DELETE FROM instance_status WHERE instance_name = ?',
                        (old_name,),
                    )
            else:
                c.execute(
                    'UPDATE instance_status SET instance_name = ? '
                    'WHERE instance_name = ?',
                    (new_name, old_name),
                )
            for table in _MEANINGFUL_DATA_TABLES:
                c.execute(
                    f'UPDATE {table} SET instance_name = ? '
                    f'WHERE instance_name = ?',
                    (new_name, old_name),
                )
            c.execute(
                'UPDATE torrent_rank_state SET instance_name = ? '
                'WHERE instance_name = ?',
                (new_name, old_name),
            )
            for table in _DATA_INSTANCE_TABLES:
                c.execute(
                    f'DELETE FROM {table} WHERE instance_name = ?',
                    (old_name,),
                )
            conn.commit()
            logger.info(f"实例数据已重命名: {old_name} -> {new_name}")
        finally:
            conn.close()


def reset_instance_traffic(instance_name: str):
    """清空流量统计；保留 session 基准与当前限速状态"""
    with _lock:
        conn = get_conn()
        try:
            now = now_local()
            c = conn.cursor()
            c.execute('DELETE FROM traffic_hourly WHERE instance_name = ?',
                      (instance_name,))
            c.execute('DELETE FROM traffic_monthly WHERE instance_name = ?',
                      (instance_name,))
            c.execute(
                'DELETE FROM torrent_traffic_hourly WHERE instance_name = ?',
                (instance_name,),
            )
            c.execute('''
                UPDATE instance_status SET
                    monthly_uploaded_bytes = 0,
                    monthly_downloaded_bytes = 0,
                    last_delta_bytes = 0,
                    last_delta_download_bytes = 0,
                    skip_auto_unlimit_once = 1,
                    rule_trigger_times = '',
                    manual_limit_trigger_at = NULL,
                    manual_limit_trigger_kbps = 0,
                    last_update = ?
                WHERE instance_name = ?
            ''', (now, instance_name))
            conn.commit()
            logger.info(f"流量统计已重置: {instance_name}")
        finally:
            conn.close()


def delete_instance_data(instance_name: str):
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            for table in ('traffic_hourly', 'traffic_monthly',
                          'device_events', 'instance_status',
                          'torrent_rank_state', 'torrent_traffic_hourly'):
                c.execute(f'DELETE FROM {table} WHERE instance_name = ?',
                          (instance_name,))
            conn.commit()
            logger.info(f"已清理实例数据: {instance_name}")
        finally:
            conn.close()


_DATA_INSTANCE_TABLES = (
    'instance_status', 'traffic_hourly', 'traffic_monthly', 'device_events',
    'torrent_rank_state', 'torrent_traffic_hourly',
)

_MEANINGFUL_DATA_TABLES = (
    'traffic_hourly', 'traffic_monthly', 'device_events',
    'torrent_traffic_hourly',
)


def _collect_db_instance_names_unlocked(cursor) -> set:
    names = set()
    for table in _DATA_INSTANCE_TABLES:
        cursor.execute(f'SELECT DISTINCT instance_name FROM {table}')
        names.update(row['instance_name'] for row in cursor.fetchall())
    return names


def _has_meaningful_instance_data_unlocked(cursor, instance_name: str) -> bool:
    for table in _MEANINGFUL_DATA_TABLES:
        cursor.execute(
            f'SELECT 1 FROM {table} WHERE instance_name = ? LIMIT 1',
            (instance_name,),
        )
        if cursor.fetchone():
            return True
    return False


def has_instance_data(instance_name: str) -> bool:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            for table in _DATA_INSTANCE_TABLES:
                c.execute(
                    f'SELECT 1 FROM {table} WHERE instance_name = ? LIMIT 1',
                    (instance_name,),
                )
                if c.fetchone():
                    return True
            return False
        finally:
            conn.close()


def has_meaningful_instance_data(instance_name: str) -> bool:
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            return _has_meaningful_instance_data_unlocked(c, instance_name)
        finally:
            conn.close()


def is_orphaned_instance(instance_name: str, active_names: list,
                         renaming_from: str = None) -> bool:
    ensure_schema()
    if instance_name in set(active_names or []):
        return False
    if not has_instance_data(instance_name):
        return False
    if renaming_from:
        renaming_from = str(renaming_from).strip()
        active = set(active_names or [])
        if renaming_from and renaming_from in active:
            if (has_instance_data(renaming_from)
                    and not has_meaningful_instance_data(instance_name)):
                return False
    return True


def mark_instance_orphan_deleted(instance_name: str):
    """保留数据删除时写入删除时间"""
    ensure_schema()
    now_str = _format_datetime_seconds(now_local())
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute(
                'SELECT 1 FROM instance_status WHERE instance_name = ?',
                (instance_name,),
            )
            if c.fetchone():
                c.execute(
                    'UPDATE instance_status SET deleted_at = ? WHERE instance_name = ?',
                    (now_str, instance_name),
                )
            else:
                c.execute(
                    'INSERT INTO instance_status (instance_name, deleted_at) '
                    'VALUES (?, ?)',
                    (instance_name, now_str),
                )
            conn.commit()
            logger.info(f"已记录孤儿删除时间: {instance_name} @ {now_str}")
        finally:
            conn.close()


def _resolve_orphan_time_unlocked(cursor, name: str) -> str:
    """返回展示用删除时间：优先真实删除时间，否则回退最近采集时间"""
    cursor.execute(
        'SELECT deleted_at, last_update FROM instance_status WHERE instance_name = ?',
        (name,),
    )
    status_row = cursor.fetchone()

    if status_row and status_row['deleted_at']:
        formatted = _format_datetime_seconds(status_row['deleted_at'])
        if formatted:
            return formatted

    cursor.execute('''
        SELECT event_time FROM device_events
        WHERE instance_name = ? AND event_type = 'instance_deleted'
        ORDER BY event_time DESC LIMIT 1
    ''', (name,))
    event_row = cursor.fetchone()
    if event_row:
        formatted = _format_datetime_seconds(event_row['event_time'])
        if formatted:
            if status_row:
                cursor.execute(
                    'UPDATE instance_status SET deleted_at = ? WHERE instance_name = ?',
                    (formatted, name),
                )
            else:
                cursor.execute(
                    'INSERT INTO instance_status (instance_name, deleted_at) '
                    'VALUES (?, ?)',
                    (name, formatted),
                )
            return formatted

    if status_row and status_row['last_update']:
        formatted = _format_datetime_seconds(status_row['last_update'])
        if formatted:
            return formatted

    return None


def get_orphaned_instances(active_names: list) -> list:
    ensure_schema()
    active = set(active_names or [])
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            orphan_names = _collect_db_instance_names_unlocked(c) - active
            result = []
            for name in orphan_names:
                display_time = _resolve_orphan_time_unlocked(c, name)
                result.append({
                    'name': name,
                    'deleted_at': display_time,
                })
            conn.commit()
            result.sort(key=lambda x: x['deleted_at'] or '', reverse=True)
            return result
        finally:
            conn.close()


def _format_datetime_seconds(value) -> str:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    text = str(value).strip()
    if len(text) >= 19:
        return text[:19]
    return text or None


def _run_vacuum_background():
    """后台执行 incremental_vacuum，避免阻塞采集与 API。"""
    global _vacuum_running
    with _lock:
        if _vacuum_running:
            return
        _vacuum_running = True

    def _worker():
        global _vacuum_running
        try:
            conn = get_conn()
            try:
                conn.execute('PRAGMA incremental_vacuum')
                conn.commit()
                logger.info('数据库 incremental_vacuum 完成')
            finally:
                conn.close()
        except Exception as e:
            logger.warning('数据库 incremental_vacuum 失败: %s', e)
        finally:
            with _lock:
                _vacuum_running = False

    threading.Thread(
        target=_worker, name='traffic-db-vacuum', daemon=True,
    ).start()


def cleanup_old_data():
    """清理过期数据并定期 VACUUM，防止数据库无限增长"""
    global _last_vacuum_day
    schedule_vacuum = False
    hourly_deleted = 0
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            hourly_cutoff = _cutoff_str(days=_retention_years * 365)
            c.execute('''
                DELETE FROM traffic_hourly WHERE hour_start < ?
            ''', (hourly_cutoff,))
            hourly_deleted = c.rowcount
            c.execute('''
                DELETE FROM torrent_traffic_hourly WHERE hour_start < ?
            ''', (hourly_cutoff,))
            c.execute('''
                DELETE FROM torrent_rank_state WHERE updated_at < ?
            ''', (hourly_cutoff,))

            month_cutoff_ym = _month_cutoff_ym()
            c.execute('''
                DELETE FROM traffic_monthly
                WHERE (year * 100 + month) < ?
            ''', (month_cutoff_ym,))

            c.execute('''
                DELETE FROM device_events WHERE id NOT IN (
                    SELECT id FROM device_events
                    ORDER BY event_time DESC LIMIT ?
                )
            ''', (EVENT_RETENTION_COUNT,))

            conn.commit()

            today = now_local().date()
            if (
                _last_vacuum_day != today
                and now_local().day % _VACUUM_INTERVAL_DAYS == 1
            ):
                _last_vacuum_day = today
                schedule_vacuum = True

            if hourly_deleted > 0:
                logger.info(f"数据清理完成: 删除 {hourly_deleted} 条过期小时统计")
        except Exception as e:
            logger.warning(f"数据清理失败: {e}")
        finally:
            conn.close()

    if schedule_vacuum:
        _run_vacuum_background()
