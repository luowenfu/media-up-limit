"""达量停播发给客户端的提示。通用三句带恢复时间，发送时按当时周期重算。"""

from __future__ import annotations

from datetime import datetime

from core.cycle import format_resume_when

# 值以 preset: 开头，界面上的文字要和这里保持一致
PRESETS = {
    'preset:1': '本期流量已用完，{when}恢复，播放将在 5 秒后停止',
    'preset:2': '流量额度已用完，5 秒后停止播放，请{when}再看',
    'preset:3': '服务器流量已达上限，请{when}再看',
}
PRESET_KEYS = frozenset(PRESETS)

# 上一版写死的三句，读配置时换成对应预设
OLD_PRESETS = {
    '本周期上行流量已达额度，播放将在 5 秒后停止': 'preset:1',
    '流量额度已用完，5 秒后将停止播放，请下个周期再看': 'preset:2',
    '服务器流量已达上限，播放即将停止': 'preset:3',
}


def normalize_message_choice(raw) -> str:
    """存档用：预设编号，或自定义原文。空的按第一句。"""
    text = str(raw or '').strip()
    text = OLD_PRESETS.get(text, text)
    if not text:
        return 'preset:1'
    return text


def render_quota_message(quota: dict, now: datetime) -> str:
    """发出去的那一句。预设和自定义里的 {when} 都换成 now 对应的恢复时间。"""
    text = normalize_message_choice((quota or {}).get('message_text'))
    template = PRESETS.get(text, text)
    if '{when}' not in template:
        return template
    when = format_resume_when(now, (quota or {}).get('cycle') or {})
    return template.replace('{when}', when)
