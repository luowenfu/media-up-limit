/* 页面外壳：侧栏、标题、胶囊统计与各页面内容的渲染（同改造计划原型的 render） */

import { I } from './icons.js';
import { APP_VERSION } from './version.js';
import { embyPills, qbPills } from './adapt.js';
import { renderDevices } from './cards.js';
import { bindChart, renderStats } from './stats.js';
import { renderEvents } from './events.js';
import {
    loadLogs, renderSyslogs, syncLogs,
} from './syslogs.js';
import { relockTiles } from './interact.js';
import {
    state, qbList, embyList, embyOn,
} from './store.js';
import { $, $$ } from './ui.js';

export const TITLES = { devices: '设备管理', stats: '流量图表', events: '事件记录', syslogs: '系统日志' };
export const NAMES = { qb: 'qBittorrent', emby: 'Emby', merge: '合并视图' };

/** 图表和事件页在合并视图下按 qB 显示 */
export const effKind = () => (state.page === 'devices' ? state.platform : (state.platform === 'merge' ? 'qb' : state.platform));

const pillGroup = (list, key, tag) => `<div class="pill-group">${tag ? `<span class="pill-group-tag ${key}">${tag}</span>` : ''}${list.map(p => `<span class="pill ${p[0]}"><i></i>${p[2]}<b class="num">${p[1]}</b></span>`).join('')}</div>`;

export function renderChrome() {
    const { platform, page } = state;
    const eff = effKind();
    const b = document.body;
    b.dataset.platform = platform;
    b.dataset.page = page;
    b.dataset.theme = page === 'syslogs' ? (platform === 'merge' ? 'qb' : platform) : eff;
    $$('[data-seg] button').forEach(el => el.classList.toggle('active', el.dataset.p === platform));
    $$('.nav-item[data-page], .bottom-bar button').forEach(el => el.classList.toggle('active', el.dataset.page === page));
    $('#addLabel').textContent = { qb: '添加 qB 设备', emby: '添加 Emby 设备', merge: '添加设备' }[platform];
    const nq = qbList().length, ne = embyList().length;
    $('#navDevCount').textContent = platform === 'merge' ? nq + ne : (platform === 'qb' ? nq : ne);
    $('#title').textContent = TITLES[page];
    $('#mobileTitle').textContent = TITLES[page];
    $('#crumb').textContent = page === 'syslogs' ? '系统 · 全部来源' : page === 'devices' ? NAMES[platform] : NAMES[eff];
    $('#ver').textContent = APP_VERSION;
    document.title = `${TITLES[page]} · qB 达量限速管理`;
    const showBoth = page === 'syslogs' || (page === 'devices' && platform === 'merge');
    const q = qbPills(qbList()), e = embyPills(embyList());
    $('#pills').innerHTML = showBoth
        ? pillGroup(q, 'qb', 'qB') + (embyOn() ? pillGroup(e, 'emby', 'EMBY') : '')
        : pillGroup(eff === 'emby' ? e : q);
}

const LOADING = '<div class="panel panel-pad" style="text-align:center;color:var(--text-3);padding:48px 16px">加载中…</div>';
const ERROR = msg => `<div class="panel panel-pad" style="text-align:center;color:var(--danger);padding:48px 16px">${msg}</div>`;

/** 生成当前页内容；soft 为后台刷新，不显示「加载中」 */
async function pageHtml(soft) {
    const { page, platform } = state;
    const eff = effKind();
    let html = '';
    if (platform === 'merge' && (page === 'stats' || page === 'events')) {
        html += `<div class="hint">${I.info.replace('viewBox', 'width="16" height="16" viewBox')}合并视图只作用于设备管理，当前页面按 qB 显示。<button data-switch="emby">查看 Emby</button></div>`;
    }
    if (page === 'devices') return html + renderDevices(platform);
    if (!soft) $('#content').innerHTML = html + LOADING;
    try {
        if (page === 'stats') return html + await renderStats(eff);
        if (page === 'events') return html + await renderEvents(eff);
        await loadLogs(state.logSrc);
        return html + renderSyslogs(state.logSrc, state.logLv, state.logQuery);
    } catch (e) {
        return html + ERROR(e.message || '加载失败');
    }
}

/** 渲染到页面；后台刷新时内容相同就不动 DOM，避免闪烁 */
let lastHtml = '';
let seq = 0;
export async function render({ soft = false } = {}) {
    const mine = ++seq;
    renderChrome();
    const html = await pageHtml(soft);
    if (mine !== seq) return;
    if (html !== lastHtml || !soft) {
        $('#content').innerHTML = html;
        lastHtml = html;
    }
    if (state.page === 'stats') bindChart(effKind());
    relockTiles();
}

/** 日志页的后台刷新：只更新统计和列表，保留搜索框焦点 */
export async function refreshLogsInPlace() {
    await syncLogs();
}
