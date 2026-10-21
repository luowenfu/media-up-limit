"""外网播放估算上行：累加器 take 一次，写入播放记录。"""

import logging
from typing import List, Optional

import emby.traffic.playback as emby_playback_traffic
from emby.client import EmbyClient

logger = logging.getLogger(__name__)


def _upload_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        v = int(val)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def upload_subject_candidates(subject: dict) -> List[dict]:
    seen = set()
    result: List[dict] = []
    for client in (subject.get('client'), subject.get('device_name')):
        if not client:
            continue
        key = EmbyClient._normalize_client_key(str(client))
        if not key or key in seen:
            continue
        seen.add(key)
        if key == EmbyClient._normalize_client_key(subject.get('client') or ''):
            result.append(subject)
        else:
            result.append({**subject, 'client': client})
    return result or [subject]


def try_take_upload(instance_name: str, subject: dict) -> Optional[int]:
    if not subject or not subject.get('is_remote'):
        return None
    for cand in upload_subject_candidates(subject):
        taken = emby_playback_traffic.take_accumulated_upload(instance_name, cand)
        if taken is not None and taken > 0:
            return taken
    return None


def _adjust_taken_for_legacy_checkpoint(
    playback_record: dict,
    existing: int,
    taken: int,
) -> int:
    """续传缺陷：checkpoint 未扣除已入账部分时，避免 finalize 重复累加。"""
    taken = max(0, int(taken or 0))
    if taken <= 0 or existing <= 0:
        return taken
    chk = max(0, int(playback_record.get('live_upload_checkpoint_bytes') or 0))
    if chk <= 0:
        return taken
    # 旧逻辑 checkpoint 仅镜像累加器且未随入账更新：chk == existing 时 taken 含重复部分
    if chk <= existing:
        overlap = min(existing, taken)
        if overlap > 0:
            logger.debug(
                '[Playback] 结案去重 legacy checkpoint overlap=%s existing=%s taken=%s',
                overlap,
                existing,
                taken,
            )
            return max(0, taken - overlap)
    return taken


def resolve_upload_bytes(instance_name: str, *, playback_record: dict) -> Optional[int]:
    """解析估算上行并写入播放记录。返回写入的字节数。"""
    if not playback_record or not playback_record.get('is_remote'):
        return None

    existing = max(0, int(playback_record.get('estimated_upload_bytes') or 0))
    raw_taken = try_take_upload(instance_name, playback_record)
    increment = _adjust_taken_for_legacy_checkpoint(
        playback_record,
        existing,
        max(0, int(raw_taken or 0)),
    )
    total = existing + increment
    if total > 0:
        playback_record['estimated_upload_bytes'] = total
        try:
            from emby.repair.playback_upload import warn_if_inflated_playback_upload
            warn_if_inflated_playback_upload(
                instance_name,
                playback_record,
                upload_bytes=total,
            )
        except Exception:
            logger.warning(
                '播放上行异常检查失败: %s',
                instance_name,
                exc_info=True,
            )
        return total
    return None
