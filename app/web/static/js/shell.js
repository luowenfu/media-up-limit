/* 页面外壳：侧栏设备树、顶部栏与各视图内容的渲染 */

import { APP_VERSION } from './version.js';
import { esc } from './format.js';
import { embyPills, qbPills } from './adapt.js';
import { deviceCard, renderDevices } from './cards.js';
import { bindChart, renderStats } from './stats.js';
import { bindEventPop, renderEvents, syncPlayProgress } from './events.js';
import {
    loadLogs, renderSyslogs, syncLogs,
} from './syslogs.js';
import { relockTiles } from './interact.js';
import {
    state, qbList, embyList, embyOn, navCollapsed, rememberPage,
} from './store.js';
import { $, $$ } from './ui.js';
import { ensureSidebar } from './theme.js';

export const NAMES = { qb: 'qBittorrent', emby: 'Emby', merge: '合并视图' };
const TITLES = { merge: '合并视图', qb: 'qBittorrent', emby: 'Emby', syslogs: '系统日志' };
const LIST_VIEWS = ['merge', 'qb', 'emby'];

/** 图表、事件这类需要单独请求的区块，后台刷新时每隔这么多个周期才重新拉取 */
const SLOW_EVERY = 30;

export const listOf = kind => (kind === 'emby' ? embyList() : qbList());
export const isListView = v => LIST_VIEWS.includes(v);

const isMobile = () => matchMedia('(max-width: 992px)').matches;

/** 路由兜底：Emby 关闭时回到 qB，设备被删时回到所在分组。手机上不使用合并视图 */
export function fixRoute() {
    if (!embyOn() && (state.view === 'emby' || state.view === 'merge')) Object.assign(state, { view: 'qb', dev: '' });
    if (isMobile() && state.view === 'merge') {
        state.view = state.embyRaw.length && !state.qbRaw.length ? 'emby' : 'qb';
        state.dev = '';
        const cur = history.state;
        if (!cur || cur.view === 'merge') history.replaceState({ view: state.view, dev: '' }, '');
    }
    if (isMobile() && (state.view === 'qb' || state.view === 'emby') && !state.dev) {
        const first = listOf(state.view)[0];
        if (first) {
            state.dev = first.name;
            const cur = history.state;
            if (!cur || !cur.dev) history.replaceState({ view: state.view, dev: state.dev }, '');
        }
    }
    if (state.dev && (state.view === 'merge' || state.view === 'syslogs' || !listOf(state.view).some(d => d.name === state.dev))) state.dev = '';
    if (isListView(state.view)) state.lastList = state.view;
}

/* ---------- 侧栏设备树 ---------- */
const ICON_MERGE = '<svg width="18" height="18" viewBox="0 0 20 20" fill="none"><rect x="2.5" y="4" width="6.5" height="12" rx="1.2" stroke="currentColor" stroke-width="1.5"/><rect x="11" y="4" width="6.5" height="12" rx="1.2" stroke="currentColor" stroke-width="1.5"/></svg>';
const ICON_CARET = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M5 3l4 4-4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const GROUP_ICON = { qb: '/static/img/qbittorrent-logo.svg', emby: '/static/img/emby-icon.svg' };

const devState = (kind, d) => (!d.online ? 'off' : (kind === 'qb' && (d.triggered || d.raw.is_limited) ? 'hot' : 'ok'));

function navGroup(kind) {
    const list = listOf(kind);
    const open = !navCollapsed().includes(kind);
    const here = state.view === kind;
    const head = here && !state.dev ? 'active' : (here ? 'within' : '');
    const devs = list.map(d => `<button type="button" class="nav-item nav-dev ${here && state.dev === d.name ? 'active' : ''}" data-nav="${kind}" data-dev="${esc(d.name)}" title="${esc(d.name)}">
        <span class="nav-dot ${devState(kind, d)}"></span><span class="grow">${esc(d.name)}</span></button>`).join('');
    return `<div class="nav-group ${kind} ${open ? 'open' : ''}" data-group="${kind}">
        <div class="nav-row">
            <button type="button" class="nav-item ${head}" data-nav="${kind}"><img class="nav-ic" src="${GROUP_ICON[kind]}" alt=""><span class="grow">${NAMES[kind]}</span><span class="nav-badge">${list.length}</span></button>
            <button type="button" class="nav-caret" data-nav-toggle="${kind}" aria-expanded="${open}" aria-label="${open ? '收起' : '展开'}${NAMES[kind]}设备">${ICON_CARET}</button>
        </div>
        <div class="nav-sub"><div><div class="nav-devs">${devs || '<div class="nav-empty">暂无设备</div>'}</div></div></div>
    </div>`;
}

export function navTreeHtml() {
    const merge = embyOn()
        ? `<button type="button" class="nav-item ${state.view === 'merge' ? 'active' : ''}" data-nav="merge">${ICON_MERGE}<span class="grow">合并视图</span><span class="nav-badge">${qbList().length + embyList().length}</span></button>`
        : '';
    return merge + navGroup('qb') + (embyOn() ? navGroup('emby') : '');
}

/** 设备树只在内容变化时重画，避免每秒刷新打断点击 */
let lastNav = '';
function renderNav() {
    const html = navTreeHtml();
    if (html === lastNav) return;
    $('#navTree').innerHTML = html;
    lastNav = html;
}

/** 展开或收起分组：直接切换现有节点，保留过渡动画 */
export function toggleNavGroup(kind, open) {
    const g = document.querySelector(`.nav-group[data-group="${kind}"]`);
    if (g) {
        g.classList.toggle('open', open);
        const c = g.querySelector('.nav-caret');
        c.setAttribute('aria-expanded', String(open));
        c.setAttribute('aria-label', `${open ? '收起' : '展开'}${NAMES[kind]}设备`);
    }
    lastNav = navTreeHtml();
}

/* ---------- 顶部栏 ---------- */
const METRIC_ICON = {
    box: '<svg viewBox="0 0 20 20" fill="none"><rect x="2.5" y="3" width="15" height="11" rx="2" stroke="currentColor" stroke-width="1.6"/><path d="M7 17h6M10 14v3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    ok: '<svg viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="6.2" stroke="currentColor" stroke-width="1.6"/><path d="m7.2 10.1 1.9 1.9 3.8-4.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    warn: '<svg viewBox="0 0 20 20" fill="none"><path d="M10 3.2 16.8 15.4H3.2L10 3.2Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M10 8.2v3.1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="10" cy="13.3" r=".7" fill="currentColor"/></svg>',
    info: '<svg viewBox="0 0 20 20" fill="none"><rect x="3.5" y="4.5" width="13" height="12" rx="2" stroke="currentColor" stroke-width="1.6"/><path d="M3.5 8.5h13M7 3.2v2.6M13 3.2v2.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    lan: '<svg viewBox="0 0 20 20" fill="none"><path d="M4 9.2 10 4l6 5.2V16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V9.2Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
    wan: '<svg viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="6.5" stroke="currentColor" stroke-width="1.6"/><path d="M3.6 10h12.8M10 3.5c1.7 1.8 2.6 4 2.6 6.5s-.9 4.7-2.6 6.5c-1.7-1.8-2.6-4-2.6-6.5s.9-4.7 2.6-6.5Z" stroke="currentColor" stroke-width="1.6"/></svg>',
};
const METRIC_TONE = {
    '': 'tone-qb',
    ok: 'tone-ok',
    warn: 'tone-warn',
    info: 'tone-info',
    violet: 'tone-violet',
};
const metricGroup = (list, key, tag) => {
    const mark = tag ? `<span class="tb-tag ${key}">${tag}</span>` : '';
    const items = list.map(p => {
        const tone = METRIC_TONE[p[0]] || METRIC_TONE[''];
        const n = Number(p[1]) || 0;
        const hot = p[0] === 'warn' && n !== 0 ? ' hot' : '';
        const zero = n === 0 ? ' zero' : '';
        return `<div class="tb-metric${hot}${zero}"><span class="tb-mi ${tone}">${METRIC_ICON[p[3]] || METRIC_ICON.box}</span><div><b class="num">${n}</b><small>${esc(p[2])}</small></div></div>`;
    }).join('');
    return `<div class="tb-tray">${mark}${items}</div>`;
};

const titleOf = () => (state.dev || TITLES[state.view]);

const DETAIL_TABS = `<button type="button" data-detail-tab="card"><svg width="20" height="20" viewBox="0 0 20 20" fill="none"><rect x="3" y="3.5" width="14" height="5.5" rx="1.6" stroke="currentColor" stroke-width="1.7"/><rect x="3" y="11" width="14" height="5.5" rx="1.6" stroke="currentColor" stroke-width="1.7"/><circle cx="6.3" cy="6.25" r=".9" fill="currentColor"/><circle cx="6.3" cy="13.75" r=".9" fill="currentColor"/></svg>设备详情</button>
<button type="button" data-detail-tab="events"><svg width="20" height="20" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.8"/><path d="M10 6.5V10l2.5 1.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>事件记录</button>
<button type="button" data-detail-tab="chart"><svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M3 16V10M7.5 16V5M12 16V8.5M16.5 16V3.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>流量图表</button>`;

function ensureDetailTabs() {
    if (document.getElementById('detailTabs')) return;
    const nav = document.createElement('nav');
    nav.className = 'detail-tabs';
    nav.id = 'detailTabs';
    nav.setAttribute('aria-label', '设备详情');
    nav.innerHTML = DETAIL_TABS;
    document.body.appendChild(nav);
}

/** 运行中的服务会缓存旧页面。旧结构在这里换成顶栏，新页面则直接跳过。 */
function ensureTopbar() {
    if (document.querySelector('.topbar')) return;
    const main = document.querySelector('.main');
    const page = main?.querySelector('.page');
    if (!main || !page) return;
    const header = document.createElement('header');
    header.className = 'topbar';
    const inner = document.createElement('div');
    inner.className = 'topbar-in';
    const titleWrap = document.createElement('div');
    titleWrap.className = 'tb-title';
    titleWrap.innerHTML = '<i class="tb-mark" aria-hidden="true"></i>';
    const title = document.getElementById('title');
    if (title) titleWrap.appendChild(title);
    const kind = document.createElement('span');
    kind.id = 'kind';
    kind.className = 'kind';
    kind.hidden = true;
    titleWrap.appendChild(kind);
    const menu = document.getElementById('menuBtn');
    const back = document.getElementById('backBtn');
    if (menu) inner.appendChild(menu);
    if (back) inner.appendChild(back);
    inner.appendChild(titleWrap);
    const pills = document.getElementById('pills');
    if (pills) {
        pills.className = 'tb-metrics';
        inner.appendChild(pills);
    }
    header.appendChild(inner);
    main.insertBefore(header, page);
    document.querySelector('.mobile-top')?.remove();
    document.querySelector('.head')?.remove();
    ensureTbBar();
}

const METRICS_ICON = '<svg width="15" height="15" viewBox="0 0 20 20" fill="none"><path d="M5 7.5 10 12.5 15 7.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

/** 手机设备详情里，状态栏默认收起，点顶栏右侧图标再展开 */
let metricsOpen = false;

function ensureMetricsBtn() {
    if (document.getElementById('metricsBtn')) return;
    const bar = document.querySelector('.tb-bar');
    if (!bar) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'icon-btn';
    btn.id = 'metricsBtn';
    btn.setAttribute('aria-label', '展开状态栏');
    btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = METRICS_ICON;
    bar.appendChild(btn);
}

function syncMetricsToggle() {
    const open = metricsOpen && isMobile() && !!state.dev;
    document.body.classList.toggle('metrics-open', open);
    const bar = document.querySelector('.tb-bar');
    if (bar && isMobile()) document.documentElement.style.setProperty('--tb-bar-h', bar.getBoundingClientRect().height + 'px');
    const btn = document.getElementById('metricsBtn');
    if (!btn) return;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.setAttribute('aria-label', open ? '收起状态栏' : '展开状态栏');
}

export function toggleMetricsBar() {
    metricsOpen = !metricsOpen;
    syncMetricsToggle();
    syncMetricsEdge();
}

/** 标题行单独吸顶，统计留在文档流里，随滚动匀速滑到标题下面 */
function ensureTbBar() {
    if (document.querySelector('.tb-bar')) return;
    const inner = document.querySelector('.topbar-in');
    const metrics = document.getElementById('pills');
    if (!inner || !metrics) return;
    const bar = document.createElement('div');
    bar.className = 'tb-bar';
    const slot = document.getElementById('metricsSlot');
    [...inner.children].forEach(child => {
        if (child !== metrics && child !== slot) bar.appendChild(child);
    });
    inner.insertBefore(bar, slot || metrics);
}

const SPLIT_BTN = '<i class="tb-split-line" aria-hidden="true"></i><span class="tb-split-grip" aria-hidden="true"><svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M6.2 2.2 3.4 5l2.8 2.8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg></span>';

/** 电脑端状态图标默认展开，点分割线收进竖线里。只在当次打开里记住 */
let metricsDocked = false;

function syncMetricsDock() {
    const slot = document.getElementById('metricsSlot');
    const btn = document.getElementById('metricsSplit');
    if (slot) slot.classList.toggle('is-collapsed', metricsDocked);
    if (!btn) return;
    const label = metricsDocked ? '展开状态栏' : '收起状态栏';
    btn.setAttribute('aria-expanded', metricsDocked ? 'false' : 'true');
    btn.setAttribute('aria-label', label);
    btn.title = label;
}

export function toggleMetricsDock() {
    if (isMobile()) return;
    metricsDocked = !metricsDocked;
    syncMetricsDock();
}

/** 旧页面没有分割线时补上，统计仍留在顶栏里、不进标题行 */
function ensureMetricsDock() {
    const metrics = document.getElementById('pills');
    if (!metrics) return;
    let slot = document.getElementById('metricsSlot');
    if (!slot) {
        slot = document.createElement('div');
        slot.id = 'metricsSlot';
        slot.className = 'tb-metrics-slot';
        metrics.parentNode.insertBefore(slot, metrics);
    }
    let clip = slot.querySelector('.tb-metrics-clip');
    if (!clip) {
        clip = document.createElement('div');
        clip.className = 'tb-metrics-clip';
        slot.appendChild(clip);
    }
    if (metrics.parentElement !== clip) clip.appendChild(metrics);
    if (!document.getElementById('metricsSplit')) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'tb-split';
        btn.id = 'metricsSplit';
        btn.setAttribute('aria-controls', 'pills');
        btn.innerHTML = SPLIT_BTN;
        slot.insertBefore(btn, slot.firstChild);
    }
    const bar = document.querySelector('.tb-bar');
    const inner = document.querySelector('.topbar-in');
    if (bar && inner && slot.parentElement !== inner) inner.insertBefore(slot, bar.nextSibling);
    syncMetricsDock();
}

export function renderChrome() {
    ensureTopbar();
    ensureTbBar();
    ensureMetricsBtn();
    ensureMetricsDock();
    ensureDetailTabs();
    ensureSidebar();
    const { view, dev } = state;
    const b = document.body;
    b.dataset.view = view;
    b.dataset.detail = dev ? '1' : '';
    b.dataset.detailTab = state.detailTab || 'card';
    b.dataset.theme = view === 'merge' ? 'merge' : (view === 'emby' ? 'emby' : 'qb');
    renderNav();
    $$('[data-nav="syslogs"]').forEach(el => el.classList.toggle('active', view === 'syslogs'));
    $$('.bottom-bar [data-bar]').forEach(el => el.classList.toggle('active', el.dataset.bar === (view === 'syslogs' ? 'syslogs' : 'devices')));
    const title = titleOf();
    $('#title').textContent = title;
    const kind = $('#kind');
    const kindOn = view === 'qb' || view === 'emby';
    kind.hidden = !kindOn;
    kind.className = kindOn ? `kind ${view}` : 'kind';
    kind.textContent = view === 'emby' ? 'EMBY' : 'qB';
    const mobile = isMobile();
    $('#backBtn').hidden = !dev || mobile;
    $('#menuBtn').hidden = mobile ? false : !!dev;
    $$('#detailTabs [data-detail-tab]').forEach(el => el.classList.toggle('active', el.dataset.detailTab === (state.detailTab || 'card')));
    $('#ver').textContent = APP_VERSION;
    rememberPage();
    document.title = `${title} · qB 达量限速管理`;
    const q = qbPills(qbList()), e = embyPills(embyList());
    const groups = view === 'syslogs' || view === 'merge'
        ? metricGroup(q, 'qb', 'qB') + (embyOn() ? metricGroup(e, 'emby', 'EMBY') : '')
        : metricGroup(view === 'emby' ? e : q);
    $('#pills').innerHTML = `<div class="tb-metrics-in">${groups}</div>`;
    const metrics = document.querySelector('.tb-metrics');
    if (metrics) {
        metrics.style.height = '';
        metrics.style.transition = '';
    }
    syncMetricsToggle();
    syncMetricsDock();
}

/** 统计完全滑进标题栏后，给标题栏补上底边，避免和内容贴在一起 */
function syncMetricsEdge() {
    const bar = document.querySelector('.tb-bar');
    const metrics = document.querySelector('.tb-metrics');
    if (bar && isMobile()) document.documentElement.style.setProperty('--tb-bar-h', bar.getBoundingClientRect().height + 'px');
    const hidden = metrics && getComputedStyle(metrics).display === 'none';
    if (!bar || !metrics || !isMobile() || hidden) {
        document.body.classList.remove('metrics-gone');
        return;
    }
    const gone = metrics.getBoundingClientRect().bottom <= bar.getBoundingClientRect().bottom + 0.5;
    document.body.classList.toggle('metrics-gone', gone);
}
window.addEventListener('scroll', syncMetricsEdge, { passive: true });
window.addEventListener('resize', syncMetricsEdge);

/* ---------- 页面区块 ---------- */
const LOADING = '<div class="panel panel-pad" style="text-align:center;color:var(--text-3);padding:48px 16px">加载中…</div>';
const ERROR = msg => `<div class="panel panel-pad" style="text-align:center;color:var(--danger);padding:48px 16px">${esc(msg)}</div>`;

const chartKey = () => [state.view, state.dev, state.statsRange, state.statsChartRange, state.statsDate, state.statsFrom, state.statsSpan, state.statsUser].join('\0');
const playStateKey = () => embyList().flatMap(d => (d.sessions || []).map(s => `${s.id}:${s.item_id || ''}:${s.playback_started_at || ''}:${s.is_paused ? 1 : 0}:${s.upload_anomaly ? 1 : 0}`)).join('|');
const eventsKey = (kind, dev) => [kind, dev, kind === 'qb' ? state.eventsType : playStateKey(), state.eventsUser, state.eventsDays, state.eventsHideLan].join('\0');

/** 需要请求接口的区块：筛选没变就沿用，后台刷新到期才重新拉取 */
const slowCache = new Map();
function slowPart(id, soft, key, load) {
    const c = slowCache.get(id);
    if (c && c.key === key && (!soft || c.age < SLOW_EVERY)) {
        if (soft) c.age += 1;
        return { id, html: c.html };
    }
    return {
        id,
        cached: c ? c.html : '',
        load: async () => {
            let html;
            try { html = await load(); } catch (e) { html = ERROR(e.message || '加载失败'); }
            slowCache.set(id, { key, html, age: 0 });
            return html;
        },
    };
}

function feedPart(kind, soft) {
    const open = state.feedOpen[kind];
    const n = listOf(kind).length;
    const head = `<button type="button" class="feed-head" data-feed="${kind}" aria-expanded="${open}">
        <span class="feed-caret">${ICON_CARET}</span><b>最近事件</b><small>全部 ${n} 台${NAMES[kind]}设备</small></button>`;
    if (!open) return { id: 'feed', html: `<section class="feed">${head}</section>` };
    const p = slowPart('feed', soft, eventsKey(kind, ''), () => {
        state.eventsDevice = '';
        return renderEvents(kind);
    });
    const wrap = body => `<section class="feed open">${head}<div class="feed-body">${body}</div></section>`;
    if (p.html !== undefined) return { id: 'feed', html: wrap(p.html) };
    return { id: 'feed', html: wrap(p.cached || LOADING), load: async () => wrap(await p.load()) };
}

function pack(id, part) {
    if (part.html !== undefined) return { id, html: part.html };
    return { id, html: part.cached || LOADING, load: part.load };
}

function parts(soft) {
    const { view, dev } = state;
    if (view === 'merge') return [{ id: 'cards', html: renderDevices('merge') }];
    if (view === 'syslogs') {
        return [{
            id: 'logs',
            html: soft ? undefined : LOADING,
            load: async () => {
                try {
                    await loadLogs(state.logSrc);
                    return renderSyslogs(state.logSrc, state.logLv, state.logQuery);
                } catch (e) { return ERROR(e.message || '加载失败'); }
            },
        }];
    }
    if (!dev) return [feedPart(view, soft), { id: 'cards', html: renderDevices(view) }];
    const events = slowPart('events', soft, eventsKey(view, dev), () => {
        state.eventsDevice = dev;
        return renderEvents(view);
    });
    const chart = slowPart('chart', soft, chartKey(), () => {
        state.statsDevice = dev;
        return renderStats(view);
    });
    return [
        { id: 'card', html: deviceCard(view, dev) },
        pack('events', events),
        pack('chart', chart),
    ];
}

/* ---------- 渲染 ---------- */
const layoutKey = () => `${state.view}|${state.dev}`;
const shellHtml = list => list.map(p => `<div class="sec sec-${p.id}" id="sec-${p.id}">${p.html ?? ''}</div>`).join('');

let lastLayout = '';
const lastHtml = {};
let seq = 0;

/** 区块内容在不重新拉数时改了，让缓存和下次绘制沿用这份 html */
export function rememberPart(id, html) {
    lastHtml[id] = html;
    const cached = slowCache.get(id);
    if (cached) cached.html = html;
}

/** 写入各区块：布局没变时只更新内容有变化的区块，避免闪烁和打断悬停 */
function paint(list) {
    const same = layoutKey() === lastLayout && list.every(p => document.getElementById('sec-' + p.id))
        && $$('#content > .sec').length === list.length;
    if (!same) {
        $('#content').innerHTML = shellHtml(list);
        list.forEach(p => { lastHtml[p.id] = p.html; });
        lastLayout = layoutKey();
        return;
    }
    list.forEach(p => {
        if (p.html === undefined || p.html === lastHtml[p.id]) return;
        document.getElementById('sec-' + p.id).innerHTML = p.html;
        lastHtml[p.id] = p.html;
    });
}

function holdScroll(y) {
    if (y == null) return;
    const back = () => window.scrollTo(0, y);
    back();
    requestAnimationFrame(() => { back(); requestAnimationFrame(back); });
}

export async function render({ soft = false } = {}) {
    const mine = ++seq;
    const y = layoutKey() === lastLayout ? window.scrollY : null;
    fixRoute();
    renderChrome();
    const list = parts(soft);
    paint(list);
    holdScroll(y);
    const pending = list.filter(p => p.load);
    if (pending.length) {
        const done = await Promise.all(pending.map(p => p.load()));
        if (mine !== seq) return;
        paint(list.map(p => (p.load ? { id: p.id, html: done[pending.indexOf(p)] } : { id: p.id })));
        holdScroll(y);
    }
    if (mine !== seq) return;
    bindChart(state.view === 'emby' ? 'emby' : 'qb');
    bindEventPop();
    syncPlayProgress();
    relockTiles();
    syncDetailFold();
    if (state.focus) {
        const focus = state.focus;
        state.focus = '';
        revealSection(focus);
    }
}

/** 从设备卡片进入图表或记录后滚到对应区块。延后到布局完成，避免被「回到顶部」盖掉。 */
export function revealSection(focus) {
    if (focus !== 'chart' && focus !== 'events') return;
    const id = focus === 'events' ? 'sec-events' : 'sec-chart';
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            const el = document.getElementById(id);
            if (!el || getComputedStyle(el).display === 'none') return;
            el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    });
}

/** 手机上的折叠状态写在卡片上；事件和图表有缓存，不能只靠重新生成 */
export function syncDetailFold() {
    document.querySelectorAll('[data-fold]').forEach(btn => {
        const open = !!(state.detailFold && state.detailFold[btn.dataset.fold]);
        const box = btn.closest('.panel, .dev');
        if (!box) return;
        box.classList.toggle('open', open);
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
}

/** 日志页的后台刷新：只更新统计和列表，保留搜索框焦点 */
export async function refreshLogsInPlace() {
    await syncLogs();
}
