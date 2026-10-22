"""用户本周期上行用量：已结算 + 还没结算的实时累加器。"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

import emby.browse.stats as browse_stats
import emby.traffic.playback as emby_playback_traffic
from core.cycle import iter_cycle_periods
from emby.lucky.verdict import persist_key_belongs_to_user
import qb.traffic_db as traffic_db

logger = logging.getLogger(__name__)


def _live_bytes(bucket: dict, user_fold: str, user_ids: set) -> int:
    total = 0
    for key, amount in (bucket or {}).items():
        if persist_key_belongs_to_user(key, user_fold, user_ids):
            total += max(0, int(amount or 0))
    return total


def cycle_used_bytes(
    instance_name: str,
    user_name: str,
    cycle: dict,
    *,
    credit_browse: bool,
    user_ids: Optional[Iterable[str]] = None,
    tz=None,
) -> int:
    """该用户在当前周期内的上行字节数（播放，选片按设备设置计入）。"""
    user = str(user_name or '').strip()
    if not user:
        return 0
    periods = iter_cycle_periods(cycle, tz or traffic_db.get_config_timezone(), count=1)
    rows = browse_stats.get_combined_playback_upload_cycle_stats(
        instance_name, user, periods, credit_browse=credit_browse,
    )
    settled = sum(max(0, int(row.get('total_bytes') or 0)) for row in rows)

    fold = user.casefold()
    ids = {str(x).strip() for x in (user_ids or []) if str(x).strip()}
    live = _live_bytes(emby_playback_traffic.peek_upload_bucket(instance_name), fold, ids)
    if credit_browse:
        live += _live_bytes(
            emby_playback_traffic.peek_browse_upload_bucket(instance_name), fold, ids,
        )
    return settled + live
