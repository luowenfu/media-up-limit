/* 全局状态、数据加载与轮询 */

import * as api from './api.js';
import { embyVM, qbVM } from './adapt.js';

export const state = {
    platform: 'qb',
    page: 'devices',
    statsRange: 'day',
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

export function pickPlatform() {
    const own = state.prefs.device_view_mode;
    const def = state.global.emby_default_device_view;
    let p = own || def || 'qb';
    if (!embyOn() || !state.embyRaw.length) p = 'qb';
    if (p === 'merge' && (!state.qbRaw.length || !state.embyRaw.length)) p = state.embyRaw.length && !state.qbRaw.length ? 'emby' : 'qb';
    return ['qb', 'emby', 'merge'].includes(p) ? p : 'qb';
}
