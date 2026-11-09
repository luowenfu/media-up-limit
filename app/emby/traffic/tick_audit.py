"""Emby 外网流量分摊 — 单 tick 在线验算（内部守恒，不依赖路由）。"""

from __future__ import annotations

from typing import Iterable, List

from emby.traffic.filter import (
    allocate_wan_deltas,
    is_wan_playback_session,
    m3_allocate_wan_pool,
    session_docker_share_bps,
)


def _active_sessions(sessions: Iterable[dict]) -> List[dict]:
    return [
        s for s in (sessions or [])
        if isinstance(s, dict) and bool(s.get('is_playing')) and not bool(s.get('is_paused'))
    ]


def _wan_ratio(sessions: list) -> float:
    active = _active_sessions(sessions)
    if not active:
        return 0.0
    wan_w = sum(session_docker_share_bps(s) for s in active if is_wan_playback_session(s))
    total_w = sum(session_docker_share_bps(s) for s in active)
    if total_w <= 0:
        wan_n = sum(1 for s in active if is_wan_playback_session(s))
        return wan_n / len(active) if active else 0.0
    return max(0.0, min(1.0, wan_w / total_w))


def _check(check_id: str, label: str, ok: bool, *, detail: str = '',
           got: int = None, expect: int = None) -> dict:
    item = {
        'id': check_id,
        'label': label,
        'ok': bool(ok),
    }
    if detail:
        item['detail'] = detail
    if got is not None:
        item['got'] = max(0, int(got))
    if expect is not None:
        item['expect'] = max(0, int(expect))
    return item


def sum_wan_session_live_bytes(sessions: Iterable[dict]) -> int:
    total = 0
    for session in _active_sessions(sessions):
        if not is_wan_playback_session(session):
            continue
        total += max(0, int(session.get('estimated_upload_bytes_live') or 0))
    return total


def build_tick_audit(
    *,
    mode_code: str,
    live_raw_up: int,
    live_delta_up: int,
    alloc_input_up: int,
    effective_alloc_up: int,
    allocation_debug: dict,
    wan_backlog_before: int,
    wan_backlog_after: int,
    wan_backlog_applied: int,
    replay_alloc_up: int,
    m1_capture_bytes: int,
    mode_switch_pending_bytes: int,
    debug_total_up: int,
    debug_wan_up: int,
    debug_lan_up: int,
    debug_remainder_up: int,
    sessions: list,
    wan_only_enabled: bool = True,
    m3_wan_pool_scale: float = 1.0,
    tick_seconds: float = 1.0,
) -> dict:
    """对单 tick 做内部一致性验算，供在线观测与 CLI 轮询。"""
    raw = max(0, int(live_raw_up or 0))
    wan_pool = max(0, int(live_delta_up or 0))
    alloc_in = max(0, int(alloc_input_up or 0))
    effective = max(0, int(effective_alloc_up or 0))
    backlog_before = max(0, int(wan_backlog_before or 0))
    backlog_after = max(0, int(wan_backlog_after or 0))
    backlog_applied = max(0, int(wan_backlog_applied or 0))
    replay = max(0, int(replay_alloc_up or 0))
    capture = max(0, int(m1_capture_bytes or 0))
    pending = max(0, int(mode_switch_pending_bytes or 0))

    dbg = dict(allocation_debug or {})
    wan_assigned = max(0, int(dbg.get('wan_upload_bytes') or dbg.get('wan_pool_bytes') or 0))
    alloc_remainder = max(0, int(
        dbg.get('program_remainder_bytes') or dbg.get('remainder_bytes') or 0,
    ))
    alloc_total = max(0, int(dbg.get('total_upload_bytes') or 0))

    recomputed_wan, _ = allocate_wan_deltas(raw, 0, sessions)
    if mode_code == 'M3' and wan_only_enabled:
        recomputed_wan = m3_allocate_wan_pool(
            raw, sessions, scale=m3_wan_pool_scale,
            tick_seconds=max(1, int(tick_seconds or 1)),
        )
    ratio = _wan_ratio(sessions)
    wan_session_live = sum_wan_session_live_bytes(sessions)

    checks: List[dict] = []

    if wan_only_enabled and raw > 0 and _active_sessions(sessions):
        checks.append(_check(
            'wan_pool_recompute',
            'WAN 池与 filter 重算一致',
            abs(recomputed_wan - wan_pool) <= max(1, raw // 1000),
            got=wan_pool,
            expect=recomputed_wan,
        ))

    if mode_code == 'M1':
        checks.append(_check(
            'm1_wan_pool_zero',
            'M1 WAN 池为 0',
            wan_pool == 0,
            got=wan_pool,
            expect=0,
        ))
    elif mode_code == 'M2' and raw > 0:
        checks.append(_check(
            'm2_wan_pool_full',
            'M2 WAN 池≈本轮总上传',
            abs(wan_pool - raw) <= max(1, raw // 100),
            got=wan_pool,
            expect=raw,
        ))
    elif mode_code == 'M3' and raw > 0:
        expect_m3 = int(recomputed_wan or 0) or int(raw * ratio)
        checks.append(_check(
            'm3_wan_pool_ratio',
            'M3 WAN 池与 m3_allocate 一致',
            abs(wan_pool - expect_m3) <= max(1024, raw // 50),
            got=wan_pool,
            expect=expect_m3,
            detail=f'ratio={ratio:.3f}',
        ))

    if effective > 0 and alloc_total > 0:
        checks.append(_check(
            'alloc_input_equals_effective',
            '分摊输入=有效分摊量',
            alloc_total == effective or abs(alloc_total - effective) <= 1,
            got=alloc_total,
            expect=effective,
        ))
        checks.append(_check(
            'alloc_wan_plus_remainder',
            'WAN 分摊+余量=有效输入',
            wan_assigned + alloc_remainder == effective
            or abs(wan_assigned + alloc_remainder - effective) <= 1,
            got=wan_assigned + alloc_remainder,
            expect=effective,
        ))

    if backlog_applied > 0:
        checks.append(_check(
            'backlog_applied_le_input',
            '本 tick 灌入 backlog ≤ 有效输入',
            backlog_applied <= effective,
            got=backlog_applied,
            expect=effective,
        ))

    if mode_code in ('M2', 'M3') and effective > 0 and wan_assigned > 0:
        checks.append(_check(
            'alloc_no_internal_remainder',
            '有 WAN 分摊时内部分摊余量为 0',
            alloc_remainder == 0,
            got=alloc_remainder,
            expect=0,
        ))

    total_dbg = max(0, int(debug_total_up or 0))
    wan_dbg = max(0, int(debug_wan_up or 0))
    lan_dbg = max(0, int(debug_lan_up or 0))
    rem_dbg = max(0, int(debug_remainder_up or 0))
    if total_dbg > 0 and mode_code == 'M1' and wan_only_enabled:
        checks.append(_check(
            'debug_m1_lan_conservation',
            'M1 调试: LAN+余量≈总上传',
            lan_dbg + rem_dbg == total_dbg or abs(lan_dbg - total_dbg) <= 1024,
            got=lan_dbg + rem_dbg,
            expect=total_dbg,
        ))
    elif total_dbg > 0 and mode_code == 'M3':
        checks.append(_check(
            'debug_m3_conservation',
            'M3 调试: WAN+LAN+余量≈总上传',
            abs(wan_dbg + lan_dbg + rem_dbg - total_dbg) <= max(1024, total_dbg // 100),
            got=wan_dbg + lan_dbg + rem_dbg,
            expect=total_dbg,
        ))

    passed = all(c.get('ok') for c in checks) if checks else True
    failed = [c for c in checks if not c.get('ok')]

    return {
        'passed': passed,
        'check_count': len(checks),
        'failed_count': len(failed),
        'checks': checks,
        'failed_checks': failed,
        'inputs': {
            'live_raw_up': raw,
            'live_delta_up_wan_pool': wan_pool,
            'recomputed_wan_pool': recomputed_wan,
            'wan_ratio': round(ratio, 4),
            'alloc_input_up': alloc_in,
            'effective_alloc_up': effective,
            'replay_alloc_up': replay,
            'wan_backlog_before': backlog_before,
            'wan_backlog_applied': backlog_applied,
            'wan_backlog_after': backlog_after,
            'm1_capture_bytes': capture,
            'mode_switch_pending_bytes': pending,
        },
        'outputs': {
            'wan_assigned_tick': wan_assigned,
            'alloc_remainder': alloc_remainder,
            'wan_session_live_total': wan_session_live,
            'debug_total_up': total_dbg,
            'debug_wan_up': wan_dbg,
            'debug_lan_up': lan_dbg,
            'debug_remainder_up': rem_dbg,
        },
    }


def merge_cumulative(prev: dict, tick_audit: dict, *, mode_code: str,
                   tick_wan_assigned: int) -> dict:
    """跨 tick 累计验算（自上次重置起）。"""
    base = dict(prev or {})
    if not base:
        base = {
            'ticks': 0,
            'wan_pool_bytes': 0,
            'wan_assigned_bytes': 0,
            'failed_ticks': 0,
            'last_mode': '',
        }
    base['ticks'] = int(base.get('ticks') or 0) + 1
    inp = tick_audit.get('inputs') or {}
    out = tick_audit.get('outputs') or {}
    base['wan_pool_bytes'] = int(base.get('wan_pool_bytes') or 0) + max(
        0, int(inp.get('live_delta_up_wan_pool') or 0),
    )
    assigned = max(0, int(tick_wan_assigned or 0))
    base['wan_assigned_bytes'] = int(base.get('wan_assigned_bytes') or 0) + assigned
    base['wan_session_live_total'] = max(0, int(out.get('wan_session_live_total') or 0))
    if not tick_audit.get('passed'):
        base['failed_ticks'] = int(base.get('failed_ticks') or 0) + 1
    base['last_mode'] = mode_code or ''
    pool = max(0, int(base.get('wan_pool_bytes') or 0))
    live = max(0, int(base.get('wan_session_live_total') or 0))
    assigned_total = max(0, int(base.get('wan_assigned_bytes') or 0))
    base['pool_vs_session_gap'] = pool - live
    base['assigned_vs_session_gap'] = assigned_total - live
    return base
