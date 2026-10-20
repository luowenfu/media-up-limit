/* 系统日志页（markup 与改造计划原型一致） */

import * as api from './api.js';
import { q } from './api.js';
import { I } from './icons.js';
import { esc, ymd } from './format.js';
import { state } from './store.js';
import { toast } from './ui.js';

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

const DEV_TONES = [
    ['#c2410c', '#fdba74'], ['#7c3aed', '#c4b5fd'], ['#0369a1', '#7dd3fc'], ['#0f766e', '#5eead4'],
    ['#be185d', '#f9a8d4'], ['#b45309', '#fcd34d'], ['#4338ca', '#a5b4fc'], ['#047857', '#6ee7b7'],
];

function deviceTone(name) {
    let hash = 0;
    for (const ch of String(name || '')) hash = (hash * 33 + ch.codePointAt(0)) >>> 0;
    return DEV_TONES[hash % DEV_TONES.length];
}

function splitDevice(src, msg) {
    const text = String(msg || '');
    const re = src === 'emby' ? /^\[Emby:([^\]]+)\]\s*/ : src === 'qb' ? /^\[([^\]]+)\]\s*/ : null;
    const matched = re && re.exec(text);
    if (!matched || matched[1].includes(':')) return { device: '', msg: text };
    return { device: matched[1], msg: text.slice(matched[0].length) };
}

function sourceOf(entry) {
    const name = String(entry.logger || '');
    if (name.startsWith('emby')) return 'emby';
    if (name.startsWith('qb')) return 'qb';
    return 'sys';
}

const PAGE = 200;
const rawKey = e => `${e.time}\n${e.logger}\n${e.message}`;
const rowKey = r => `${r.time}\n${r.logger}\n${r.raw}`;

const normalize = e => {
    const src = sourceOf(e);
    const split = splitDevice(src, msgText(e.message_display || e.message));
    return {
        time: e.time, lv: LV[String(e.level || '').toUpperCase()] || 'INFO',
        src, device: split.device, label: loggerLabel(e.logger), msg: split.msg,
        logger: e.logger, raw: e.message,
    };
};

export const logState = { rows: [], hasMore: false, offset: 0, anchor: '', cursor: null, loading: false };

const serviceOf = src => (src === 'sys' ? 'system' : src === 'all' ? '' : src);

function newestFirst(raw) {
    if (raw.length >= 2 && raw[0].time < raw[raw.length - 1].time) return raw.slice().reverse();
    return raw;
}

async function fetchPage(offset, src, before) {
    const params = { limit: PAGE, service: serviceOf(src) };
    if (before) {
        params.before_time = before.time;
        params.before_logger = before.logger;
        params.before_message = before.raw;
    } else if (offset) params.offset = offset;
    const res = await api.get(`/api/system-logs${q(params)}`);
    const raw = newestFirst(res.data || []);
    return { raw, rows: raw.filter(e => e.logger !== 'waitress.queue').map(normalize), hasMore: !!res.has_more };
}

function shownOf(rows) {
    const lv = state.logLv;
    const needle = (state.logQuery || '').trim().toLowerCase();
    return rows.filter(l => (lv === 'all' || l.lv === lv)
        && (!needle || `${l.label} ${l.device || ''} ${l.msg}`.toLowerCase().includes(needle)));
}

function updateLogMeta() {
    const stats = document.querySelector('#logStats');
    const count = document.querySelector('#logCount');
    if (stats) stats.innerHTML = logStatsHtml();
    if (count) count.textContent = `共 ${filterLogs(state.logLv, state.logQuery).length} 条`;
    const btn = document.querySelector('[data-log-more]');
    if (btn && !logState.hasMore) btn.parentElement.remove();
}

async function take(src, minRows) {
    let rounds = 0;
    while (logState.rows.length < minRows && logState.hasMore && rounds < 20) {
        rounds += 1;
        let page = await fetchPage(logState.offset, src);
        const sameHead = page.raw.length && logState.anchor && rawKey(page.raw[0]) === logState.anchor;
        if (sameHead && logState.cursor) page = await fetchPage(0, src, logState.cursor);
        if (!page.raw.length) { logState.hasMore = false; break; }
        if (!logState.anchor) logState.anchor = rawKey(page.raw[0]);
        const have = new Set(logState.rows.map(rowKey));
        const room = minRows - logState.rows.length;
        const add = page.rows.filter(r => !have.has(rowKey(r))).slice(0, room);
        const marker = add[add.length - 1];
        const tail = marker
            ? { time: marker.time, logger: marker.logger, raw: marker.raw }
            : { time: page.raw[page.raw.length - 1].time, logger: page.raw[page.raw.length - 1].logger, raw: page.raw[page.raw.length - 1].message };
        const cursorKey = c => `${c.time}\n${c.logger}\n${c.raw}`;
        if (logState.cursor && cursorKey(logState.cursor) === cursorKey(tail)) { logState.hasMore = false; break; }
        logState.cursor = tail;
        logState.rows.push(...add);
        logState.offset += add.length ? add.length : page.raw.length;
        logState.hasMore = page.hasMore || page.rows.length > add.length;
    }
}

export async function loadLogs(src) {
    logState.rows = [];
    logState.offset = 0;
    logState.anchor = '';
    logState.cursor = null;
    logState.hasMore = true;
    await take(src, PAGE);
}

/** 再取更早的一页，接在列表下面，滚动位置保持不变 */
export async function loadMoreLogs() {
    if (!logState.hasMore || logState.loading) return;
    logState.loading = true;
    const y = window.scrollY;
    const btn = document.querySelector('[data-log-more]');
    if (btn) { btn.disabled = true; btn.textContent = '加载中…'; }
    try {
        const start = logState.rows.length;
        await take(state.logSrc, start + PAGE);
        const logs = document.querySelector('.logs');
        const chunk = shownOf(logState.rows.slice(start));
        if (logs && chunk.length) logs.insertAdjacentHTML('beforeend', logRows(chunk));
        updateLogMeta();
        const more = document.querySelector('[data-log-more]');
        if (more) { more.disabled = false; more.textContent = '加载更多'; }
        window.scrollTo(0, y);
    } catch (e) {
        toast(e.message || '加载失败', 'err');
        if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = '加载更多'; }
    } finally {
        logState.loading = false;
    }
}

/** 后台刷新只把新日志接到最前面；人已经往下滚动时，眼前的内容不挪动 */
export async function syncLogs() {
    if (logState.loading || !document.querySelector('#logList')) return;
    const page = await fetchPage(0, state.logSrc);
    if (!page.raw.length) { logState.hasMore = false; updateLogMeta(); return; }
    const anchor = logState.anchor;
    let cut = page.raw.findIndex(e => rawKey(e) === anchor);
    if (!anchor) cut = page.raw.length;
    const fresh = cut === -1 ? page.raw : page.raw.slice(0, cut);
    if (!fresh.length) return;
    const y = window.scrollY;
    const logs = document.querySelector('.logs');
    const h0 = logs ? logs.offsetHeight : 0;
    const have = new Set(logState.rows.map(rowKey));
    const add = fresh.filter(e => e.logger !== 'waitress.queue').map(normalize).filter(r => !have.has(rowKey(r)));
    logState.rows = add.concat(logState.rows);
    logState.offset += fresh.length;
    logState.anchor = rawKey(page.raw[0]);
    logState.hasMore = logState.hasMore || page.hasMore;
    const chunk = shownOf(add);
    if (logs && chunk.length) {
        logs.querySelector(':scope > div:not(.log)')?.remove();
        logs.insertAdjacentHTML('afterbegin', logRows(chunk));
    }
    updateLogMeta();
    const h1 = logs ? logs.offsetHeight : h0;
    window.scrollTo(0, y > 0 ? y + Math.max(0, h1 - h0) : 0);
}

export function filterLogs(lv, query) {
    const needle = (query || '').trim().toLowerCase();
    return logState.rows.filter(l => (lv === 'all' || l.lv === lv)
        && (!needle || `${l.label} ${l.device || ''} ${l.msg}`.toLowerCase().includes(needle)));
}

const srcTag = l => {
    const kind = SRC_NAME[l.src] || l.src;
    if (!l.device) return kind;
    const [light, dark] = deviceTone(l.device);
    return `${kind}<span class="src-dev" style="--dev:${light};--dev-dark:${dark}">${esc(l.device)}</span>`;
};

const logRows = list => list.map(l => `
    <div class="log ${l.lv}">
        <time>${esc(l.time)}</time>
        <span class="lv ${l.lv}">${l.lv}</span>
        <span class="tag ${l.src} src${l.device ? ' has-dev' : ''}">${srcTag(l)}</span>
        <span class="log-msg" title="${esc(l.msg)}"><b style="color:var(--text-2);font-weight:600">[${esc(l.label)}]</b> ${esc(l.msg)}</span>
    </div>`).join('') || '<div style="padding:40px;text-align:center;color:var(--text-3)">没有匹配的日志</div>';

export const logListHtml = (lv, query) => {
    const list = filterLogs(lv, query);
    return `<div class="panel-pad" style="padding-bottom:10px"><h3 class="panel-title" style="margin:0">运行日志<small><span id="logCount">共 ${list.length} 条</span> · ${ymd(new Date())}</small><span class="r mini-btn" data-log-export>导出</span></h3></div>
        <div class="logs">${logRows(list)}</div>
        ${logState.hasMore ? '<div style="padding:12px;text-align:center"><button type="button" class="btn sm" data-log-more>加载更多</button></div>' : ''}`;
};

export function logStatsHtml() {
    const cnt = lv => logState.rows.filter(l => l.lv === lv).length;
    const stats = [['doc', 'tone-slate', logState.rows.length, '已加载日志'], ['info', 'tone-info', cnt('INFO'), '信息'], ['warn', 'tone-amber', cnt('WARN'), '警告'], ['err', 'tone-red', cnt('ERROR'), '错误']];
    return stats.map(s => `<div class="panel log-stat"><span class="kpi-ico ${s[1]}">${I[s[0]]}</span><div><b class="num">${s[2]}</b><small>${s[3]}</small></div></div>`).join('');
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
    const text = filterLogs(lv, query).map(l => {
        const kind = l.device ? `${SRC_NAME[l.src]} • ${l.device}` : SRC_NAME[l.src];
        return `${l.time} [${l.lv}] [${kind}] [${l.label}] ${l.msg}`;
    }).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    a.download = `qb-up-limit-${ymd(new Date())}.log`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
