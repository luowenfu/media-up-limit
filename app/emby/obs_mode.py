"""观测模式：逐轮记录 Emby 会话、Lucky 连接与本项目归属结果，供事后核对。

开启：环境变量 OBS_MODE=1，或 python main.py --obs。
数据写到 /data/Obs_mode_data/：
  stream/  每小时一个 JSONL，换小时压成 .gz
  raw/     每小时一份 Emby、Lucky 未裁剪的原始返回
  logs/    emby 日志按天保留
关闭后不再写入，已有数据保留。
"""

import gzip
import json
import logging
import os
import queue
import shutil
import threading
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from core.paths import OBS_DATA_DIR

logger = logging.getLogger(__name__)

_ENABLED = os.environ.get('OBS_MODE', '').strip().lower() in ('1', 'true', 'yes', 'on')
_KEEP_DAYS = max(1, int(os.environ.get('OBS_KEEP_DAYS') or 14))
_MAX_MB = max(100, int(os.environ.get('OBS_MAX_MB') or 5000))

_LUCKY_FULL_EVERY = 600
_RAW_EVERY = 3600
_SNAPSHOT_EVERY = 60
_SESSIONS_EVERY = 5

_queue: 'queue.Queue' = queue.Queue(maxsize=5000)
_started = False
_dropped = 0


def enabled() -> bool:
    return _ENABLED


def start() -> None:
    """main 启动时调用；未开启时什么都不做。"""
    global _started
    if not _ENABLED or _started:
        return
    _started = True
    root = Path(OBS_DATA_DIR)
    for sub in ('stream', 'raw', 'logs'):
        (root / sub).mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        str(root / 'logs' / 'emby.log'), when='midnight',
        backupCount=_KEEP_DAYS, encoding='utf-8',
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    logging.getLogger('emby').addHandler(handler)
    threading.Thread(target=_Writer(root).run, name='obs-writer', daemon=True).start()
    logger.warning('观测模式已开启，数据写入 %s（保留 %s 天，上限 %sMB）', root, _KEEP_DAYS, _MAX_MB)


def record_tick(instance: str, **payload) -> None:
    """调度器每轮调用。只入队，整理和写盘都在后台线程，不拖慢采集。"""
    global _dropped
    if not _started:
        return
    try:
        _queue.put_nowait((time.time(), instance, payload))
    except queue.Full:
        _dropped += 1


def _pick(src: dict, keys) -> dict:
    out = {}
    for k in keys:
        v = (src or {}).get(k)
        if v not in (None, '', [], {}):
            out[k] = v
    return out


_SESSION_KEYS = (
    'Id', 'UserId', 'UserName', 'Client', 'DeviceName', 'DeviceId',
    'ApplicationVersion', 'RemoteEndPoint', 'LastActivityDate',
    'LastPlaybackCheckIn', 'AdditionalUsers',
)
_NP_KEYS = (
    'Id', 'Name', 'SeriesName', 'Type', 'IndexNumber', 'ParentIndexNumber',
    'RunTimeTicks', 'Bitrate', 'Container', 'Size',
)
_PS_KEYS = (
    'PositionTicks', 'IsPaused', 'PlayMethod', 'MediaSourceId', 'CanSeek',
    'PlaybackRate',
)
_TI_KEYS = (
    'Bitrate', 'Container', 'VideoCodec', 'AudioCodec', 'IsVideoDirect',
    'IsAudioDirect', 'CompletionPercentage', 'TranscodingPositionTicks',
    'TranscodingStartPositionTicks', 'Width', 'Height', 'TranscodeReasons',
    'CurrentThrottle',
)
# 每秒都会变的字段，不触发会话记录
_VOLATILE = ('LastActivityDate', 'LastPlaybackCheckIn', 'PositionTicks',
             'CompletionPercentage', 'TranscodingPositionTicks', 'CurrentThrottle')


def _compact_session(s: dict) -> dict:
    out = _pick(s, _SESSION_KEYS)
    for key, name, keys in (
        ('NowPlayingItem', 'np', _NP_KEYS),
        ('PlayState', 'ps', _PS_KEYS),
        ('TranscodingInfo', 'ti', _TI_KEYS),
        ('NowViewingItem', 'nv', ('Id', 'Name', 'SeriesName', 'Type')),
    ):
        sub = s.get(key)
        if isinstance(sub, dict) and sub:
            picked = _pick(sub, keys)
            if picked:
                out[name] = picked
    return out


def _stable_sig(sessions: list) -> str:
    def strip(obj):
        if isinstance(obj, dict):
            return {k: strip(v) for k, v in obj.items() if k not in _VOLATILE}
        return obj
    return json.dumps([strip(s) for s in sessions], sort_keys=True, ensure_ascii=False)


_APP_SESSION_KEYS = (
    'id', 'user_name', 'client', 'device_name', 'client_ip', 'session_mode',
    'is_playing', 'is_paused', 'is_remote', 'is_abnormal_disconnect',
    'play_method', 'bitrate', 'item_id',
)
_APP_SESSION_WORDS = ('upload', 'anomaly', 'persist', 'superseded')


def _compact_app_session(s: dict) -> dict:
    out = {k: s.get(k) for k in _APP_SESSION_KEYS if s.get(k) not in (None, '')}
    for k, v in s.items():
        if k not in out and any(w in k for w in _APP_SESSION_WORDS):
            out[k] = v
    return out


def _app_stable_sig(sessions: list) -> str:
    return json.dumps(
        [{k: v for k, v in s.items() if not any(w in k for w in ('upload', 'anomaly'))}
         for s in sessions],
        sort_keys=True, ensure_ascii=False, default=str,
    )


_VERDICT_KEYS = (
    'remote_addr', 'billing_state', 'billing_persist_key', 'browse_persist_key',
    'persist_key', 'emby_mode', 'conn_role', 'confidence', 'session_match_key',
    'match_score', 'ambiguous', 'time_match_seconds', 'delta_out', 'traffic_out',
    'accept_time', 'wave_id', 'emby_user', 'client_ip',
)


class _Writer:
    def __init__(self, root: Path):
        self.root = root
        self.stream = root / 'stream'
        self.raw = root / 'raw'
        self.hour = ''
        self.fh = None
        self.last = {}
        self.sess_sig = {}
        self.app_sig = {}
        self.lucky_sig = {}
        self.binds = {}
        self.over_quota = False

    def due(self, key: str, every: float, now: float) -> bool:
        if now - self.last.get(key, -1e18) >= every:
            self.last[key] = now
            return True
        return False

    def run(self) -> None:
        while True:
            ts, inst, payload = _queue.get()
            try:
                self.handle(ts, inst, payload)
            except Exception:  # noqa: BLE001
                logger.warning('观测记录写入失败', exc_info=True)

    def write(self, ts: float, kind: str, inst: str, body: dict) -> None:
        if self.over_quota:
            return
        hour = datetime.fromtimestamp(ts).strftime('%Y%m%d-%H')
        if hour != self.hour:
            self.rotate(hour)
            if self.over_quota:
                return
        rec = {'t': round(ts, 3), 'kind': kind, 'inst': inst}
        rec.update(body)
        self.fh.write(json.dumps(rec, ensure_ascii=False, separators=(',', ':'), default=str) + '\n')
        self.fh.flush()

    def rotate(self, hour: str) -> None:
        if self.fh:
            self.fh.close()
            self.fh = None
        for p in self.stream.glob('*.jsonl'):
            if p.stem != hour:
                with open(p, 'rb') as src, gzip.open(str(p) + '.gz', 'ab') as dst:
                    shutil.copyfileobj(src, dst)
                p.unlink()
        cutoff = time.time() - _KEEP_DAYS * 86400
        total = 0
        for d in (self.stream, self.raw, self.root / 'logs'):
            for p in d.iterdir():
                if p.is_file():
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                    else:
                        total += p.stat().st_size
        if total > _MAX_MB * 1024 * 1024:
            self.over_quota = True
            logger.warning('观测数据已超过 %sMB，停止写入', _MAX_MB)
            return
        self.hour = hour
        self.fh = open(self.stream / f'{hour}.jsonl', 'a', encoding='utf-8')
        if _dropped:
            self.write(time.time(), 'note', '', {'dropped_ticks': _dropped})

    def raw_sample(self, ts: float, name: str, data) -> None:
        if self.over_quota:
            return
        stamp = datetime.fromtimestamp(ts).strftime('%Y%m%d-%H%M%S')
        with gzip.open(self.raw / f'{name}-{stamp}.json.gz', 'wt', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, default=str)

    def handle(self, ts: float, inst: str, p: dict) -> None:
        mono = time.monotonic()
        raw_sessions = p.get('raw_sessions')
        loaded_rules = p.get('loaded_rules') or []

        if self.due(f'raw:{inst}', _RAW_EVERY, mono):
            if raw_sessions is not None:
                self.raw_sample(ts, f'emby_sessions_{inst}', raw_sessions)
            for rule, detail in loaded_rules:
                self.raw_sample(ts, f'lucky_{rule.get("rule_key")}_{inst}', detail)

        if raw_sessions is not None:
            sessions = [_compact_session(s) for s in raw_sessions if isinstance(s, dict)]
            sig = _stable_sig(sessions)
            playing = any('np' in s for s in sessions)
            if sig != self.sess_sig.get(inst) or (
                playing and self.due(f'sess:{inst}', _SESSIONS_EVERY, mono)
            ):
                self.sess_sig[inst] = sig
                self.last[f'sess:{inst}'] = mono
                self.write(ts, 'emby', inst, {'sessions': sessions})

        app_sessions = p.get('app_sessions')
        if app_sessions is not None:
            compact = [_compact_app_session(s) for s in app_sessions if isinstance(s, dict)]
            sig = _app_stable_sig(compact)
            playing = any(s.get('is_playing') for s in compact)
            if sig != self.app_sig.get(inst) or (
                playing and self.due(f'app_sess:{inst}', _SESSIONS_EVERY, mono)
            ):
                self.app_sig[inst] = sig
                self.last[f'app_sess:{inst}'] = mono
                self.write(ts, 'app_sessions', inst, {'sessions': compact})

        full_lucky = self.due(f'lucky_full:{inst}', _LUCKY_FULL_EVERY, mono)
        for rule, detail in loaded_rules:
            self.lucky(ts, inst, rule, detail, full_lucky)

        conn_d = {k: v for k, v in (p.get('conn_deltas') or {}).items() if v}
        ip_d = {k: v for k, v in (p.get('ip_deltas') or {}).items() if v}
        tick_bucket = {k: v for k, v in (p.get('tick_bucket') or {}).items() if v}
        analysis = p.get('lucky_conn_debug') or {}
        rows = analysis.get('rows') or []
        verdict = []
        for r in rows:
            if int(r.get('delta_out') or 0) <= 0:
                continue
            row = _pick(r, _VERDICT_KEYS)
            if r.get('billing_state') != 'credited':
                row['reasons'] = r.get('reasons')
            verdict.append(row)
        if conn_d or ip_d or tick_bucket or verdict or p.get('full'):
            self.write(ts, 'tick', inst, {
                'full': bool(p.get('full')),
                'fresh': bool(loaded_rules),
                'mode': p.get('mode_code'),
                'ip_d': ip_d,
                'conn_d': conn_d,
                'tick_bucket': tick_bucket,
                'alloc': p.get('allocation_debug'),
                'verdict': verdict,
                'audit': p.get('tick_audit') if (p.get('full') or verdict) else None,
            })

        if self.due(f'snap:{inst}', _SNAPSHOT_EVERY, mono):
            binds = dict(p.get('bindings') or {})
            prev_binds = self.binds.get(inst) or {}
            self.binds[inst] = binds
            self.write(ts, 'buckets', inst, {
                'upload': p.get('upload_bucket'),
                'browse': p.get('browse_bucket'),
                'bindings_total': len(binds),
                'bindings_changed': {a: k for a, k in binds.items() if prev_binds.get(a) != k},
                'bindings_gone': [a for a in prev_binds if a not in binds],
            })
            if any(int(r.get('delta_out') or 0) > 0 for r in rows):
                groups = [
                    {k: v for k, v in g.items() if k != 'rows'}
                    for g in analysis.get('groups') or []
                ]
                self.write(ts, 'analysis', inst, {
                    'rows': rows,
                    'groups': groups,
                    'emby_without_lucky': analysis.get('emby_without_lucky'),
                })

    def lucky(self, ts: float, inst: str, rule: dict, detail: dict, full: bool) -> None:
        rule_id = f'{rule.get("rule_key")}/{rule.get("sub_key")}'
        key = f'{inst}|{rule_id}'
        prev = self.lucky_sig.get(key) or {}
        cur, changed = {}, []
        for it in (detail or {}).get('resList') or []:
            if not isinstance(it, dict):
                continue
            ip = str(it.get('IP') or '')
            entry = {
                'ip': ip,
                'out': int(it.get('TrafficOut') or 0),
                'in': int(it.get('TrafficIn') or 0),
                'conns': [
                    [c.get('RemoteAddr'), c.get('AcceptTime'),
                     int(c.get('TrafficOut') or 0), int(c.get('TrafficIn') or 0)]
                    for c in it.get('ConnsStatistics') or [] if isinstance(c, dict)
                ],
            }
            extra = {k: v for k, v in it.items()
                     if k not in ('IP', 'TrafficOut', 'TrafficIn', 'ConnsStatistics')}
            if extra:
                entry['extra'] = extra
            sig = json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)
            cur[ip] = sig
            if full or prev.get(ip) != sig:
                changed.append(entry)
        gone = [ip for ip in prev if ip not in cur]
        self.lucky_sig[key] = cur
        if changed or gone or full:
            self.write(ts, 'lucky', inst, {
                'rule': rule_id, 'full': full,
                'ip_total': (detail or {}).get('ipTotal'),
                'conn_total': (detail or {}).get('connectiontotal'),
                'changed': changed, 'gone': gone,
            })
