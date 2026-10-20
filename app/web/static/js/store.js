/* 全局状态、数据加载与轮询 */

import * as api from './api.js';
import { embyVM, qbVM } from './adapt.js';

export const state = {
    view: 'qb',
    dev: '',
    tab: 'chart',
    focus: '',
    lastList: 'qb',
    feedOpen: { qb: false, emby: false },
    detailFold: { card: true, events: false, chart: false },
    detailTab: 'card',
    statsRange: 'day',
    statsSpan: '6',
    statsDevice: '',
    statsUser: '',
    statsDate: '',
    eventsDevice: '',
    eventsUser: '',
    eventsType: 'all',
    eventsHideLan: false,
    eventsDays: 7,
    logSrc: 'all',
    logLv: 'all',
    logQuery: '',
    hide: {},
    revealHost: {},
    qbRaw: [],
    embyRaw: [],
    global: {},
    prefs: {},
    version: '',
    merge: { qb: [], emby: [] },
    hist: {},
    ready: false,
};

const HIST_LEN = 24;

function pushHist(key, up, down) {
    const h = state.hist[key] || (state.hist[key] = { up: [], down: [] });
    h.up.push(Math.max(0, up));
    h.down.push(Math.max(0, down));
    if (h.up.length > HIST_LEN) { h.up.shift(); h.down.shift(); }
}

function sorted(list) {
    return [...list].sort((a, b) => (a.display_priority || 500) - (b.display_priority || 500));
}

export const embyOn = () => !!state.global.emby_enabled;
export const qbList = () => sorted(state.qbRaw).map(d => qbVM(d, state.hist['q:' + d.name]));
export const embyList = () => sorted(state.embyRaw).map(d => embyVM(d, state.hist['e:' + d.name]));
export const qbByName = name => qbList().find(d => d.name === name);
export const embyByName = name => embyList().find(d => d.name === name);

/** 合并视图里的设备选择与顺序：优先用账号偏好，缺失的设备追加在后面 */
export function syncMerge() {
    const build = (names, order, selection) => {
        const ord = (order || []).filter(n => names.includes(n));
        names.forEach(n => { if (!ord.includes(n)) ord.push(n); });
        const sel = selection && selection.length ? selection : names;
        return ord.map(name => ({ name, on: sel.includes(name) }));
    };
    state.merge.qb = build(qbList().map(d => d.name), state.prefs.merge_qb_order, state.prefs.merge_qb_selection);
    state.merge.emby = build(embyList().map(d => d.name), state.prefs.merge_emby_order, state.prefs.merge_emby_selection);
}

export async function savePrefs(partial) {
    Object.assign(state.prefs, partial);
    try { await api.put('/api/user/prefs/devices', partial); } catch (e) { /* 偏好保存失败不打断操作 */ }
}

export async function loadGlobal() {
    const data = await api.get('/api/config');
    state.global = data.global || {};
    return state.global;
}

export async function loadPrefs() {
    try {
        const data = await api.get('/api/user/prefs/devices');
        state.prefs = data.data || {};
    } catch (e) {
        state.prefs = {};
    }
}

export async function loadStatus() {
    const [qb, emby] = await Promise.all([
        api.get('/api/status'),
        embyOn() ? api.get('/api/emby/status').catch(() => ({ data: [] })) : Promise.resolve({ data: [] }),
    ]);
    state.qbRaw = qb.data || [];
    state.embyRaw = emby.data || [];
    recordHist();
}

function recordHist() {
    state.qbRaw.forEach(d => {
        const s = Math.max(1, d.refresh_interval || 1);
        pushHist('q:' + d.name, (d.recent_delta_bytes || 0) / s, (d.recent_delta_download_bytes || 0) / s);
    });
    state.embyRaw.forEach(d => {
        const s = Math.max(1, d.refresh_interval || 1);
        pushHist('e:' + d.name, (d.recent_delta_bytes || 0) / s, (d.recent_delta_download_bytes || 0) / s);
    });
}

/** 每个刷新周期调用：qB 只拉轻量接口，发现状态代号变化再拉完整状态 */
export async function refreshLive() {
    const calls = [api.get('/api/status/live')];
    if (embyOn()) calls.push(api.get('/api/emby/status/live').catch(() => null));
    const [qbLive, embyLive] = await Promise.all(calls);
    let needFull = false;
    (qbLive.data || []).forEach(l => {
        const cur = state.qbRaw.find(d => d.name === l.name);
        if (!cur || cur.state_generation !== l.state_generation) needFull = true;
    });
    if (state.qbRaw.length !== (qbLive.data || []).length) needFull = true;
    if (needFull) {
        const full = await api.get('/api/status');
        state.qbRaw = full.data || [];
    } else {
        (qbLive.data || []).forEach(l => {
            const cur = state.qbRaw.find(d => d.name === l.name);
            if (cur) Object.assign(cur, l);
        });
    }
    if (embyLive && embyLive.data) state.embyRaw = embyLive.data;
    recordHist();
}

/** 侧栏里被收起的设备分组 */
export const navCollapsed = () => (Array.isArray(state.prefs.nav_collapsed) ? state.prefs.nav_collapsed : []);

const PAGE_VIEWS = new Set(['qb', 'emby', 'merge', 'syslogs']);
const PAGE_TABS = new Set(['card', 'events', 'chart']);
const PAGE_RANGES = new Set(['hour', 'day', 'month']);
const PAGE_SPANS = new Set(['6', '12', '24', 'all']);
const PAGE_KEY = 'qb-page';
let pageSig = '';

/** 当前页：刷新后用来回到同一视图、设备和筛选 */
export function pageSnapshot() {
    return {
        view: state.view,
        dev: state.dev || '',
        detailTab: state.detailTab || 'card',
        statsRange: state.statsRange || 'day',
        statsDate: state.statsDate || '',
        statsSpan: String(state.statsSpan || '6'),
        statsUser: state.statsUser || '',
        eventsType: state.eventsType || 'all',
        eventsUser: state.eventsUser || '',
        eventsDays: Number(state.eventsDays) || 7,
        eventsHideLan: !!state.eventsHideLan,
        logSrc: state.logSrc || 'all',
        logLv: state.logLv || 'all',
    };
}

function applyPage(saved) {
    if (!saved || !PAGE_VIEWS.has(saved.view)) return false;
    state.view = saved.view;
    state.dev = typeof saved.dev === 'string' ? saved.dev : '';
    if (PAGE_TABS.has(saved.detailTab)) state.detailTab = saved.detailTab;
    if (PAGE_RANGES.has(saved.statsRange)) state.statsRange = saved.statsRange;
    if (typeof saved.statsDate === 'string') state.statsDate = saved.statsDate;
    if (PAGE_SPANS.has(String(saved.statsSpan || ''))) state.statsSpan = String(saved.statsSpan);
    if (typeof saved.statsUser === 'string') state.statsUser = saved.statsUser;
    if (typeof saved.eventsType === 'string') state.eventsType = saved.eventsType;
    if (typeof saved.eventsUser === 'string') state.eventsUser = saved.eventsUser;
    if (Number(saved.eventsDays) > 0) state.eventsDays = Number(saved.eventsDays);
    if (typeof saved.eventsHideLan === 'boolean') state.eventsHideLan = saved.eventsHideLan;
    if (typeof saved.logSrc === 'string') state.logSrc = saved.logSrc;
    if (typeof saved.logLv === 'string') state.logLv = saved.logLv;
    return true;
}

/** 记住当前页。内容没变时不重复写。 */
export function rememberPage() {
    const snap = pageSnapshot();
    const sig = JSON.stringify(snap);
    if (sig === pageSig) return;
    pageSig = sig;
    try { sessionStorage.setItem(PAGE_KEY, sig); } catch (e) { /* 隐私模式写不进时仍靠历史记录 */ }
    try { history.replaceState(snap, ''); } catch (e) { /* 历史记录不可用时忽略 */ }
}

/** 刷新时恢复当前页。没有记录则仍走默认视图。 */
export function restorePage() {
    let saved = history.state && history.state.view ? history.state : null;
    if (!saved) {
        try { saved = JSON.parse(sessionStorage.getItem(PAGE_KEY) || 'null'); } catch (e) { saved = null; }
    }
    const ok = applyPage(saved);
    if (ok) pageSig = JSON.stringify(pageSnapshot());
    return ok;
}

/** 启动时进入的设备视图：账号偏好优先，其次全局默认 */
export function pickView() {
    const own = state.prefs.device_view_mode;
    const def = state.global.emby_default_device_view;
    let p = own || def || 'qb';
    if (!embyOn() || !state.embyRaw.length) p = 'qb';
    if (p === 'merge' && (!state.qbRaw.length || !state.embyRaw.length)) p = state.embyRaw.length && !state.qbRaw.length ? 'emby' : 'qb';
    return ['qb', 'emby', 'merge'].includes(p) ? p : 'qb';
}
