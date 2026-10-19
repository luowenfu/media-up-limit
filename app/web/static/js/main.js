/* 入口：启动、导航、事件绑定、定时刷新 */

import * as api from './api.js';
import { exportLogs, loadMoreLogs, logListHtml } from './syslogs.js';
import {
    state, embyOn, loadGlobal, loadPrefs, loadStatus, pickPlatform, refreshLive, savePrefs, syncMerge,
} from './store.js';
import { render, refreshLogsInPlace } from './shell.js';
import { uiClick, uiInput, relockTiles } from './interact.js';
import { openSettings } from './settings.js';
import { openMergeEditor } from './merge.js';
import { openChooser } from './dialogs.js';
import { defaultAnchor } from './stats_data.js';
import {
    $, closeDrawer, closeModal, closeSheet, toast,
} from './ui.js';

const pad = n => String(n).padStart(2, '0');

/* ---------- 导航 ---------- */
function go(patch, push = true) {
    Object.assign(state, patch);
    if (push) history.pushState({ page: state.page, platform: state.platform }, '');
    closeDrawer();
    render();
    window.scrollTo({ top: 0 });
}

function resetFilters() {
    Object.assign(state, {
        statsDevice: '', statsUser: '', statsDate: '', eventsDevice: '', eventsUser: '', eventsType: 'all',
    });
}

function setPlatform(p) {
    if (p !== 'qb' && !embyOn()) { toast('Emby 监控未开启，可在全局设置中开启', 'info'); return; }
    if (p === 'merge' && state.platform === 'merge' && state.page === 'devices') { openMergeEditor(); return; }
    const toDevices = p === 'merge' && state.page !== 'devices';
    if (p !== state.platform) resetFilters();
    savePrefs({ device_view_mode: p });
    if (toDevices) { go({ platform: p, page: 'devices' }); return; }
    state.platform = p;
    render();
}

/* ---------- 点击 ---------- */
document.addEventListener('click', e => {
    const t = e.target.closest('button, .switch, .chk, .me-item, [data-lucky], [data-close], [data-lim-mode], [data-hidelan], [data-datef]');
    if (!t) return;
    const ctx = { render: () => render() };
    if (uiClick(t, e, ctx)) return;
    if (t.matches('[data-seg] button')) { setPlatform(t.dataset.p); return; }
    if (t.matches('.nav-item[data-page], .bottom-bar button')) { go({ page: t.dataset.page }); return; }
    if (t.dataset.open === 'add') { closeDrawer(); openChooser(); return; }
    if (t.dataset.open === 'settings') { closeDrawer(); openSettings(); return; }
    if (t.dataset.goto) {
        const patch = { page: t.dataset.goto };
        if (state.platform === 'merge') patch.platform = t.closest('.merge-col:last-child') ? 'emby' : 'qb';
        resetFilters();
        patch.statsDevice = patch.eventsDevice = t.dataset.dev || '';
        go(patch);
        return;
    }
    if (t.dataset.switch) { resetFilters(); state.platform = t.dataset.switch; render(); return; }
    if (t.closest('[data-range]') && t.dataset.r) { state.statsRange = t.dataset.r; state.statsDate = ''; render(); return; }
    if (t.closest('[data-evtype]')) { state.eventsType = t.dataset.v; render(); return; }
    if (t.closest('[data-logsrc]')) { state.logSrc = t.dataset.v; render(); return; }
    if (t.closest('[data-loglv]')) { state.logLv = t.dataset.v; render(); return; }
    if (t.hasAttribute('data-hidelan')) { state.eventsHideLan = !state.eventsHideLan; render(); return; }
    if (t.hasAttribute('data-datef')) { t.querySelector('input')?.showPicker?.(); return; }
    if (t.hasAttribute('data-log-export')) { exportLogs(state.logLv, state.logQuery); return; }
    if (t.hasAttribute('data-log-more')) { loadMoreLogs(); return; }
    if (t.hasAttribute('data-close') || t.id === 'modal') closeModal();
    if (t.classList.contains('logout')) logout();
});

async function logout() {
    try { await api.post('/api/auth/logout'); } catch (e) { /* 退出失败也回登录页 */ }
    window.location.replace('/login');
}

/* ---------- 下拉与输入 ---------- */
document.addEventListener('change', e => {
    const t = e.target;
    if (t.id === 'stDev') { state.statsDevice = t.value; state.statsUser = ''; render(); }
    else if (t.id === 'stUser') { state.statsUser = t.value === '__all_users__' ? '' : t.value; render(); }
    else if (t.id === 'stDate') { state.statsDate = t.value || defaultAnchor(state.statsRange); render(); }
    else if (t.id === 'stSpan') { state.statsSpan = t.value; render(); }
    else if (t.id === 'evDev') { state.eventsDevice = t.value; state.eventsUser = ''; render(); }
    else if (t.id === 'evUser') { state.eventsUser = t.value; render(); }
    else if (t.id === 'evDays') { state.eventsDays = +t.value; render(); }
});

let queryTimer = 0;
document.addEventListener('input', e => {
    uiInput(e);
    if (e.target.id === 'logQuery') {
        state.logQuery = e.target.value;
        clearTimeout(queryTimer);
        queryTimer = setTimeout(() => { $('#logList').innerHTML = logListHtml(state.logLv, state.logQuery); }, 120);
    }
});

document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeModal(); closeSheet(); } });
$('#sheetMask').addEventListener('click', closeSheet);
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
$('#menuBtn').addEventListener('click', () => document.body.classList.add('drawer'));
$('#scrim').addEventListener('click', closeDrawer);
window.addEventListener('resize', relockTiles);
window.addEventListener('popstate', e => {
    if (e.state) { Object.assign(state, e.state); render(); }
});

/* ---------- 刷新 ---------- */
let busyReload = false;
async function reloadAll(full) {
    if (busyReload) return;
    busyReload = true;
    try {
        if (full) await loadGlobal();
        await loadStatus();
        syncMerge();
        if (!embyOn() && state.platform !== 'qb') state.platform = 'qb';
        await render({ soft: !full });
    } catch (err) {
        toast(err.message || '刷新失败', 'err');
    } finally { busyReload = false; }
}
document.addEventListener('app:reload', e => reloadAll(e.detail.full));

const editing = () => {
    const a = document.activeElement;
    return a && a.closest('#content') && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);
};

async function refreshTick() {
    if (busyReload) return;
    try {
        await refreshLive();
        syncMerge();
        if (editing()) return;
        if (state.page === 'syslogs') await refreshLogsInPlace();
        else await render({ soft: true });
    } catch (e) { /* 单次刷新失败不打扰，下个周期再试 */ }
}

/* ---------- 时钟与倒计时 ---------- */
let left = 1;
let slow = 0;
const period = () => Math.max(1, state.global.refresh_interval || 1);
document.addEventListener('app:interval', () => { left = period(); });

function syncRing() {
    const ring = document.querySelector('.ring');
    const label = document.querySelector('.status-meta b');
    if (!ring || !label) return;
    const live = period() <= 1;
    ring.classList.toggle('breath', live);
    const text = live ? '实时刷新' : '自动刷新';
    let node = [...label.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
    if (!node) label.appendChild(node = document.createTextNode(text));
    else node.textContent = text;
    let dot = ring.querySelector('i') || label.querySelector('i');
    if (!dot) {
        dot = document.createElement('i');
        dot.id = 'liveDot';
    }
    if (live) {
        if (dot.parentElement !== ring) ring.appendChild(dot);
    } else if (dot.parentElement !== label) label.prepend(dot);
}

function tick() {
    const now = new Date();
    $('#clock').textContent = `${pad(now.getMonth() + 1)}/${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    syncRing();
    if (period() > 1) {
        $('#ringSec').textContent = left;
        $('#ringBar').style.strokeDashoffset = 97.4 * (1 - left / period());
    } else $('#ringBar').style.strokeDashoffset = '';
    if (left <= 1) {
        left = period();
        if (state.page === 'stats' || state.page === 'events') {
            // 图表和事件页每 30 个周期整页刷新一次，其余时间只更新设备数据
            if (++slow >= 30 && !editing()) { slow = 0; render({ soft: true }); }
            refreshLive().then(syncMerge).catch(() => {});
        } else refreshTick();
    } else left -= 1;
}

/* ---------- 启动 ---------- */
async function boot() {
    try {
        await loadGlobal();
        await loadPrefs();
        await loadStatus();
    } catch (e) {
        toast(e.message || '加载失败', 'err');
    }
    syncMerge();
    state.platform = pickPlatform();
    history.replaceState({ page: state.page, platform: state.platform }, '');
    state.ready = true;
    left = period();
    await render();
    tick();
    setInterval(tick, 1000);
}
boot();
