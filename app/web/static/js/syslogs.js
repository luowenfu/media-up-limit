/* 系统日志页（markup 与改造计划原型一致） */

import * as api from './api.js';
import { q } from './api.js';
import { I } from './icons.js';
import { esc, ymd } from './format.js';

const LV = { DEBUG: 'DEBUG', INFO: 'INFO', WARNING: 'WARN', WARN: 'WARN', ERROR: 'ERROR', CRITICAL: 'ERROR' };
const SRC_NAME = { sys: '系统', qb: 'qB', emby: 'Emby' };

const LOGGER_LABELS = {
    __main__: '主程序', main: '主程序', scheduler: '调度器', speed_limiter: '限速器', monitor: '监控',
    config_manager: '配置管理', traffic_db: '流量数据库', client: '客户端', lucky: 'Lucky',
    user_sync: '用户同步', playback_traffic: '播放流量', continuous_playback: '连播', traffic_filter: '流量过滤',
    record_store: '播放记录', upload_repair: '播放上行修复', upload_settler: '选片结算',
    secrets_store: '密钥存储', user_prefs_store: '用户偏好', app: 'Web 服务', auth: 'Web 认证',
    waitress: 'Web 服务', urllib3: '网络库', werkzeug: 'Web 框架',
};
const MSG_REPLACE = [
    [/reason=account_superseded/g, '原因=账号被取代'], [/reason=user_switch/g, '原因=用户切换'],
    [/reason=playback_started/g, '原因=开始播放'], [/reason=disconnect/g, '原因=断开连接'],
    [/reason=browse_conn_end/g, '原因=选片连接结束'], [/reason=timeout_offline/g, '原因=超时离线'],
    [/reason=grace_expired/g, '原因=宽限期结束'], [/reason=item_change/g, '原因=切换条目'],
    [/\breason=/g, '原因='], [/\bbytes=/g, '字节='], [/\buser=/g, '用户='],
    [/\bWAN\b/g, '外网'],
];

const loggerLabel = name => {
    const last = String(name || '').split('.').pop();
    return LOGGER_LABELS[name] || LOGGER_LABELS[last] || name;
};
const msgText = m => MSG_REPLACE.reduce((s, [re, to]) => s.replace(re, to), String(m || ''));

function sourceOf(entry) {
    const name = String(entry.logger || '');
    if (name.startsWith('emby')) return 'emby';
    if (name.startsWith('qb')) return 'qb';
    return 'sys';
}

const normalize = e => ({
    time: e.time, lv: LV[String(e.level || '').toUpperCase()] || 'INFO',
    src: sourceOf(e), label: loggerLabel(e.logger), msg: msgText(e.message_display || e.message),
});

export const logState = { rows: [], hasMore: false, limit: 500 };

export async function loadLogs(src) {
    const service = src === 'sys' ? 'system' : src === 'all' ? '' : src;
    const res = await api.get(`/api/system-logs${q({ limit: logState.limit, service })}`);
    logState.rows = (res.data || []).map(normalize);
    logState.hasMore = !!res.has_more;
}

export function filterLogs(lv, query) {
    const needle = (query || '').trim().toLowerCase();
    return logState.rows.filter(l => (lv === 'all' || l.lv === lv)
        && (!needle || `${l.label} ${l.msg}`.toLowerCase().includes(needle)));
}

const logRows = list => list.map(l => `
    <div class="log ${l.lv}">
        <time>${esc(l.time)}</time>
        <span class="lv ${l.lv}">${l.lv}</span>
        <span class="tag ${l.src} src">${SRC_NAME[l.src]}</span>
        <span class="log-msg" title="${esc(l.msg)}"><b style="color:var(--text-2);font-weight:600">[${esc(l.label)}]</b> ${esc(l.msg)}</span>
    </div>`).join('') || '<div style="padding:40px;text-align:center;color:var(--text-3)">没有匹配的日志</div>';

export const logListHtml = (lv, query) => {
    const list = filterLogs(lv, query);
    return `<div class="panel-pad" style="padding-bottom:10px"><h3 class="panel-title" style="margin:0">运行日志<small>共 ${list.length} 条 · ${ymd(new Date())}</small><span class="r mini-btn" data-log-export>导出</span></h3></div>
        <div class="logs">${logRows(list)}</div>
        ${logState.hasMore ? '<div style="padding:12px;text-align:center"><button type="button" class="btn sm" data-log-more>加载更多</button></div>' : ''}`;
};

export function logStatsHtml() {
    const cnt = lv => logState.rows.filter(l => l.lv === lv).length;
    const stats = [['doc', '#f1f5f9', '#475569', logState.rows.length, '已加载日志'], ['info', '#eff6ff', '#2563eb', cnt('INFO'), '信息'], ['warn', '#fffbeb', '#d97706', cnt('WARN'), '警告'], ['err', '#fef2f2', '#dc2626', cnt('ERROR'), '错误']];
    return stats.map(s => `<div class="panel log-stat"><span class="kpi-ico" style="background:${s[1]};color:${s[2]}">${I[s[0]]}</span><div><b class="num">${s[3]}</b><small>${s[4]}</small></div></div>`).join('');
}

export function renderSyslogs(src, lv, query) {
    return `
    <div class="panel">
        <div class="toolbar">
            <div class="tabs" data-logsrc>${[['all', '全部'], ['sys', '系统'], ['qb', 'qB'], ['emby', 'Emby']].map(s => `<button data-v="${s[0]}" class="${src === s[0] ? 'on' : ''}">${s[1]}</button>`).join('')}</div>
            <div class="tabs" data-loglv>${[['all', '全部级别'], ['DEBUG', '调试'], ['INFO', '信息'], ['WARN', '警告'], ['ERROR', '错误']].map(s => `<button data-v="${s[0]}" class="${lv === s[0] ? 'on' : ''}">${s[1]}</button>`).join('')}</div>
            <span class="field search">${I.search}<input id="logQuery" type="search" placeholder="搜索日志内容…" value="${esc(query)}"></span>
            <div class="tool-r"><span class="live-dot"><i></i>实时追踪</span></div>
        </div>
    </div>
    <div class="log-stats" id="logStats">${logStatsHtml()}</div>
    <div class="panel" style="overflow:hidden" id="logList">${logListHtml(lv, query)}</div>`;
}

/** 导出当前筛选结果为文本文件 */
export function exportLogs(lv, query) {
    const text = filterLogs(lv, query).map(l => `${l.time} [${l.lv}] [${SRC_NAME[l.src]}] [${l.label}] ${l.msg}`).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    a.download = `qb-up-limit-${ymd(new Date())}.log`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
