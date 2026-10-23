"""达量停播：用户本周期用量达额度后，再开播时弹出提示并暂停，5 秒后停止该会话。

只在播放开始时判定一次。程序启动或刚启用时已在播的会话不处理。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable, Dict, Optional

import qb.traffic_db as traffic_db
from emby.client import REMOTE_CONTROL_CLOSED_ERROR
from emby.quota.message import render_quota_message
from emby.quota.usage import (
    GB as _GB, cycle_total_bytes, cycle_used_bytes, has_any_limit, is_total_mode,
    listed_limit_gb, user_limit_gb,
)
from emby.traffic.filter import is_wan_playback_session

logger = logging.getLogger(__name__)

STOP_DELAY_SECONDS = 5.0
MESSAGE_HEADER = '达量停播'
MESSAGE_TIMEOUT_MS = 5000
# 会话短暂从列表消失后再出现，不当作新一次开播
ABSENT_GRACE_SECONDS = 30.0
SESSION_ENDED_ERROR = '会话已结束'
_TERMINAL_ERRORS = frozenset({REMOTE_CONTROL_CLOSED_ERROR, SESSION_ENDED_ERROR})

# 状态：ignored 不处理；pending 待提示并暂停；notifying 正在提示并暂停；
# messaged 已提示并暂停，等停止；stopped 已处理完
_IGNORED = 'ignored'
_PENDING = 'pending'
_NOTIFYING = 'notifying'
_MESSAGED = 'messaged'
_STOPPED = 'stopped'


def _fmt_gb(value: float) -> str:
    return f'{value / _GB:.2f} GB'


def _session_key(session: dict) -> str:
    sid = str(session.get('id') or '').strip()
    if not sid:
        return ''
    return f"{sid}|{str(session.get('item_id') or '').strip()}"


def _thread_dispatch(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name='emby-quota-action', daemon=True).start()


class QuotaEnforcer:
    def __init__(
        self,
        instance_name: str,
        *,
        usage_fn: Optional[Callable[..., int]] = None,
        total_fn: Optional[Callable[..., int]] = None,
        clock: Callable[[], float] = time.monotonic,
        dispatch: Callable[[Callable[[], None]], None] = _thread_dispatch,
    ):
        self.instance_name = instance_name
        self._usage_fn = usage_fn
        self._total_fn = total_fn
        self._clock = clock
        self._dispatch = dispatch
        self._lock = threading.RLock()
        self._state: Dict[str, dict] = {}
        self._baselined = False

    def _used_bytes(self, user_name: str, quota: dict, credit_browse: bool,
                    user_ids: set) -> int:
        fn = self._usage_fn or cycle_used_bytes
        return int(fn(
            self.instance_name, user_name, quota.get('cycle') or {},
            credit_browse=credit_browse, user_ids=user_ids,
        ))

    def _total_used_bytes(self, quota: dict, credit_browse: bool) -> int:
        fn = self._total_fn or cycle_total_bytes
        return int(fn(self.instance_name, quota.get('cycle') or {}, credit_browse=credit_browse))

    def on_tick(self, client, inst_cfg: dict, sessions: list) -> None:
        """每个采集周期调用一次。sessions 为当前在线的播放会话（含暂停）。"""
        now = self._clock()
        sessions = [s for s in (sessions or []) if isinstance(s, dict)]
        present = {_session_key(s): s for s in sessions if _session_key(s)}
        with self._lock:
            self._purge(present, now)
            quota = (inst_cfg or {}).get('user_quota') or {}
            active = (
                self._baselined
                and bool(quota.get('enabled'))
                and str((inst_cfg or {}).get('traffic_collect_mode') or '') == 'lucky'
                and has_any_limit(quota)
            )
            credit_browse = (
                str((inst_cfg or {}).get('traffic_collect_mode') or '') == 'lucky'
                and bool((inst_cfg or {}).get('lucky_credit_browse_traffic', False))
            )
            for key, session in present.items():
                if key in self._state:
                    continue
                # 首次调用时已在线的会话（含暂停）一律不处理
                if self._baselined and not is_wan_playback_session(session):
                    continue
                self._state[key] = self._judge(
                    client, session, sessions, quota, credit_browse, active, now,
                )
            self._baselined = True
            self._advance(client, now)

    def _purge(self, present: dict, now: float) -> None:
        for key in list(self._state):
            entry = self._state[key]
            if key in present:
                entry['last_seen'] = now
            elif entry['stage'] == _STOPPED or now - entry['last_seen'] > ABSENT_GRACE_SECONDS:
                del self._state[key]

    def _judge(self, client, session, sessions, quota, credit_browse,
               active, now) -> dict:
        entry = {
            'stage': _IGNORED, 'last_seen': now, 'at': now,
            'sid': str(session.get('id') or '').strip(),
            'user': str(session.get('user_name') or '').strip(),
        }
        if not active:
            return entry
        total_mode = is_total_mode(quota)
        # 按总额度时，单独列出的用户不看合计，只看自己的额度
        own = listed_limit_gb(quota, entry['user']) if total_mode else None
        use_total = total_mode and own is None
        if use_total:
            limit_gb = float(quota.get('total_limit_gb') or 0)
        elif total_mode:
            limit_gb = own
        else:
            limit_gb = user_limit_gb(quota, entry['user'])
        if not limit_gb > 0:
            return entry
        user_ids = {
            str(s.get('user_id') or '').strip() for s in sessions
            if str(s.get('user_name') or '').strip().casefold() == entry['user'].casefold()
        }
        try:
            if use_total:
                used = self._total_used_bytes(quota, credit_browse)
            else:
                used = self._used_bytes(entry['user'], quota, credit_browse, user_ids)
        except Exception:
            logger.warning(
                f'[Emby:{self.instance_name}] 达量停播：读取 {entry["user"]} 的用量失败',
                exc_info=True,
            )
            return entry
        limit_bytes = limit_gb * _GB
        if used < limit_bytes:
            return entry
        if use_total:
            detail = (f'{entry["user"]} 开播，全部用户本周期合计已用 {_fmt_gb(used)}，'
                      f'总额度 {limit_gb:g} GB')
        else:
            detail = f'{entry["user"]} 本周期已用 {_fmt_gb(used)}，额度 {limit_gb:g} GB'
        if not session.get('supports_remote_control'):
            logger.warning(
                f'[Emby:{self.instance_name}] 达量停播：{detail}，'
                f'但控制通道关闭，无法拦截',
            )
            return entry
        if quota.get('dry_run', True):
            logger.info(
                f'[Emby:{self.instance_name}] 达量停播（只记录不执行）：{detail}，'
                f'开播后将弹出提示并暂停，5 秒后停止',
            )
            return entry
        logger.info(
            f'[Emby:{self.instance_name}] 达量停播：{detail}，'
            f'已排期弹出提示并暂停，5 秒后停止',
        )
        entry['stage'] = _PENDING
        entry['detail'] = detail
        entry['text'] = render_quota_message(
            quota, datetime.now(traffic_db.get_config_timezone()),
        )
        return entry

    def _advance(self, client, now: float) -> None:
        for entry in self._state.values():
            if entry['stage'] == _PENDING:
                entry['stage'] = _NOTIFYING
                self._dispatch(lambda c=client, e=entry: self._notify_and_pause(c, e))
            elif entry['stage'] == _MESSAGED and now - entry['at'] >= STOP_DELAY_SECONDS:
                entry['stage'] = _STOPPED
                self._dispatch(lambda c=client, e=entry: self._send_stop(c, e))

    def _notify_and_pause(self, client, entry: dict) -> None:
        """提示和暂停一起发出，互不等待。暂停因控制通道关闭或会话结束而失败时，不再停止。"""
        boxes = [{}, {}]

        def run(box, fn):
            try:
                box['result'] = fn()
            except Exception as exc:
                box['result'] = {'ok': False, 'error': str(exc)}

        threads = [
            threading.Thread(
                target=run, name='emby-quota-message', daemon=True,
                args=(boxes[0], lambda: client.send_session_message(
                    entry['sid'], entry.get('text') or '',
                    header=MESSAGE_HEADER, timeout_ms=MESSAGE_TIMEOUT_MS,
                )),
            ),
            threading.Thread(
                target=run, name='emby-quota-pause', daemon=True,
                args=(boxes[1], lambda: client.send_session_playing_command(
                    entry['sid'], 'Pause',
                )),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        user = entry['user']
        message = boxes[0].get('result') or {'ok': False, 'error': '提示未发出'}
        pause = boxes[1].get('result') or {'ok': False, 'error': '暂停未发出'}
        message_error = str(message.get('error') or '')
        pause_error = str(pause.get('error') or '')
        if message.get('ok'):
            logger.info(f'[Emby:{self.instance_name}] 达量停播：已向 {user} 弹出提示')
        else:
            logger.warning(
                f'[Emby:{self.instance_name}] 达量停播：提示发送失败（{user}）：{message_error}',
            )
        if pause.get('ok'):
            logger.info(f'[Emby:{self.instance_name}] 达量停播：已暂停 {user} 的播放')
        else:
            logger.warning(
                f'[Emby:{self.instance_name}] 达量停播：暂停 {user} 的播放失败：{pause_error}',
            )
        # 5 秒从提示和暂停都发出去之后再算。暂停已成功时仍要停止
        cannot_stop = (
            not pause.get('ok')
            and (pause_error in _TERMINAL_ERRORS or message_error in _TERMINAL_ERRORS)
        )
        with self._lock:
            if entry['stage'] != _NOTIFYING:
                return
            if cannot_stop:
                entry['stage'] = _STOPPED
                return
            entry['stage'] = _MESSAGED
            entry['at'] = self._clock()

    def _send_stop(self, client, entry: dict) -> None:
        result = client.send_session_playing_command(entry['sid'], 'Stop')
        error = str(result.get('error') or '')
        if result.get('ok'):
            logger.info(
                f'[Emby:{self.instance_name}] 达量停播：已停止 {entry["user"]} 的播放',
            )
        elif error == SESSION_ENDED_ERROR:
            logger.info(
                f'[Emby:{self.instance_name}] 达量停播：{entry["user"]} 的会话已结束，无需停止',
            )
        else:
            logger.warning(
                f'[Emby:{self.instance_name}] 达量停播：停止 {entry["user"]} 的播放失败：{error}',
            )
