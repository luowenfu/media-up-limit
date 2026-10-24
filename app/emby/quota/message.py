"""达量停播发给客户端的提示。通用三句带恢复时间，发送时按当时周期重算。"""

from __future__ import annotations

from datetime import datetime

from core.cycle import format_resume_when

# 值以 preset: 开头，文字要和界面对齐。编号沿用原句，已保存的选项不会串到另一句
PRESETS = {
    'preset:3': '服务器流量已达上限，请{when}后再看',
    'preset:2': '流量额度已用完，请{when}后再看',
    'preset:1': '本期流量已用完，{when}恢复',
}
PRESET_KEYS = frozenset(PRESETS)

# 确认框标题。编号和正文预设分开存，互不影响
HEADER_PRESETS = {
    'preset:1': '达量停播',
    'preset:2': '系统消息',
    'preset:3': '请联系管理员',
}
HEADER_PRESET_KEYS = frozenset(HEADER_PRESETS)

# 上一版写死的三句，读配置时换成对应预设
OLD_PRESETS = {
    '本周期上行流量已达额度，播放将在 5 秒后停止': 'preset:1',
    '流量额度已用完，5 秒后将停止播放，请下个周期再看': 'preset:2',
    '服务器流量已达上限，播放即将停止': 'preset:3',
}


def normalize_message_choice(raw) -> str:
    """存档用：预设编号，或自定义原文。空的按本期那句。"""
    text = str(raw or '').strip()
    text = OLD_PRESETS.get(text, text)
    if not text:
        return 'preset:1'
    return text


def normalize_header_choice(raw) -> str:
    """确认框标题。空的用达量停播。"""
    text = str(raw or '').strip()
    if not text or text in HEADER_PRESET_KEYS:
        return text or 'preset:1'
    return text


def render_quota_header(quota: dict) -> str:
    """确认框发出去的标题。短消息不走这里。"""
    text = normalize_header_choice((quota or {}).get('message_header'))
    return HEADER_PRESETS.get(text, text) or HEADER_PRESETS['preset:1']


def render_quota_message(quota: dict, now: datetime) -> str:
    """发出去的那一句。预设和自定义里的 {when} 都换成 now 对应的恢复时间。"""
    text = normalize_message_choice((quota or {}).get('message_text'))
    template = PRESETS.get(text, text)
    if '{when}' not in template:
        return template
    when = format_resume_when(now, (quota or {}).get('cycle') or {})
    return template.replace('{when}', when)
