/* 流量图表页：柱状图、KPI、设备占比 / 用户排行（markup 与改造计划原型一致） */

import { I } from './icons.js';
import { esc, fmtBytes, splitBytes, trimNum } from './format.js';
import { state, qbList, embyList } from './store.js';
import { ALL_USERS, currentDevice, defaultAnchor, loadEmbyStats, loadQbStats } from './stats_data.js';

const SERIES = {
    qb: { nameA: '上传', nameB: '下载', colorA: '#3b82f6', colorB: '#c7d7fe' },
    emby: { nameA: '播放上传', nameB: '选片上传', colorA: '#10b981', colorB: '#a7f3d0' },
};
const UNIT_NAMES = ['KB', 'MB', 'GB', 'TB'];
const PALETTE = ['#3b82f6', '#a5b4fc', '#60a5fa', '#c7d2fe', '#818cf8', '#93c5fd'];

const sum = arr => arr.reduce((a, b) => a + b, 0);
const baseOf = kind => (kind === 'emby' ? 1000 : 1024);

/** 按最大值挑一个合适的单位，返回换算函数 */
function pickUnit(maxBytes, base) {
    const [, unit] = splitBytes(maxBytes, base);
    const idx = Math.max(0, UNIT_NAMES.indexOf(unit));
    const div = base ** (idx + 1);
    return { unit, conv: v => v / div };
}

const kbig = (bytes, base) => {
    const [n, u] = splitBytes(bytes, base);
    return `${n}<small>${u}</small>`;
};

const changeHtml = (pct, rev) => {
    if (pct === null || pct === undefined) return '较上一周期 —';
    const up = pct >= 0;
    const cls = (rev ? !up : up) ? 'up' : 'dn';
    return `<span class="${cls}">${up ? '▲' : '▼'} ${Math.abs(pct).toFixed(0)}%</span> 较上一周期`;
};

/* ---------- 柱状图 ---------- */
export function barChart(m) {
    const S = SERIES[m.kind], emby = m.kind === 'emby';
    const base = baseOf(m.kind);
    const stackedMax = Math.max(...m.a.map((v, i) => v + (emby ? m.b[i] : 0)), 0);
    const { unit, conv } = pickUnit(stackedMax, base);
    const a = m.a.map(conv), b = m.b.map(conv), labels = m.sp.labels;
    const W = 900, H = 300, L = 44, R = 10, T = 14, B = 30;
    const values = emby ? a.map((v, i) => v + b[i]) : a;
    const max = Math.max(...values) * 1.15 || 1;
    const nice = (() => { const k = Math.pow(10, Math.floor(Math.log10(max))); return Math.ceil(max / k) * k; })();
    const n = labels.length, slot = (W - L - R) / n, bw = Math.min(28, slot * .58);
    const y = v => T + (H - T - B) * (1 - v / nice);
    let grid = '';
    for (let k = 0; k <= 4; k++) {
        const v = nice * k / 4, yy = y(v);
        grid += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" stroke="#eef2f7" ${k ? 'stroke-dasharray="3 4"' : ''}/>
                 <text x="${L - 8}" y="${yy + 4}" text-anchor="end" font-size="11" fill="#94a3b8">${+v.toFixed(1)}</text>`;
    }
    const every = Math.ceil(n / 12);
    const cols = labels.map((lab, i) => {
        const cx = L + slot * i + slot / 2;
        const av = a[i], bv = emby ? b[i] : 0;
        const ya = y(av), yb = y(av + bv);
        const tip = `${lab}|${S.nameA} ${trimNum(+av.toFixed(2))} ${unit}|${S.nameB} ${trimNum(+b[i].toFixed(2))} ${unit}`;
        return `<g class="col" data-tip="${esc(tip)}" data-x="${cx}" data-y="${Math.min(ya, yb)}">
            <rect x="${L + slot * i}" y="${T}" width="${slot}" height="${H - T - B}" fill="transparent"/>
            ${emby && bv ? `<rect class="bar" x="${cx - bw / 2}" y="${yb}" width="${bw}" height="${ya - yb}" rx="4" fill="${S.colorB}"/>` : ''}
            <rect class="bar" x="${cx - bw / 2}" y="${ya}" width="${bw}" height="${Math.max(0, H - B - ya)}" rx="${emby && bv ? 0 : 5}" fill="url(#g${m.kind})"/>
            ${i % every === 0 ? `<text x="${cx}" y="${H - 9}" text-anchor="middle" font-size="11" fill="#94a3b8">${lab}</text>` : ''}
        </g>`;
    }).join('');
    return `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <defs><linearGradient id="g${m.kind}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${S.colorA}"/><stop offset="1" stop-color="${S.colorA}" stop-opacity=".55"/></linearGradient></defs>
        ${grid}${cols}
    </svg>`;
}

/* ---------- 设备占比环形图 ---------- */
export function donut(parts, totalText) {
    const total = parts.reduce((s, p) => s + p[1], 0) || 1;
    const r = 54, c = 2 * Math.PI * r;
    let off = 0;
    const arcs = parts.map(p => {
        const len = p[1] / total * c;
        const s = `<circle cx="70" cy="70" r="${r}" fill="none" stroke="${p[2]}" stroke-width="18" stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${-off}" transform="rotate(-90 70 70)"/>`;
        off += len;
        return s;
    }).join('');
    return `<div style="display:flex;justify-content:center"><svg width="150" height="150" viewBox="0 0 140 140">
        <circle cx="70" cy="70" r="${r}" fill="none" stroke="#eef2f7" stroke-width="18"/>${arcs}
        <text x="70" y="66" text-anchor="middle" font-size="12" fill="#64748b">合计</text>
        <text x="70" y="86" text-anchor="middle" font-size="17" font-weight="800" fill="#0f172a">${esc(totalText)}</text>
    </svg></div>`;
}

/* ---------- KPI 与侧栏 ---------- */
function kpisOf(m) {
    const base = baseOf(m.kind);
    const sa = sum(m.a), sb = sum(m.b);
    const peak = Math.max(...m.a, 0), idx = m.a.indexOf(peak);
    const peakLab = peak > 0 ? m.sp.labels[idx] : '—';
    if (m.kind === 'emby') {
        return [
            ['sum', '#ecfdf5', '#059669', '播放上传合计', kbig(sa, base), changeHtml(m.change)],
            ['film', '#f5f3ff', '#7c3aed', '选片流量', kbig(sb, base), `占比 ${(sa + sb ? sb / (sa + sb) * 100 : 0).toFixed(1)}%`],
            ['peak', '#fff7ed', '#ea580c', '峰值时段', kbig(peak, base), peakLab],
            ['user', '#eff6ff', '#2563eb', '活跃用户', `${m.rank.length}<small>人</small>`, `共 ${m.plays} 次播放`],
        ];
    }
    return [
        ['up', '#eff6ff', '#2563eb', '上传合计', kbig(sa, base), changeHtml(m.change)],
        ['down', '#ecfdf5', '#059669', '下载合计', kbig(sb, base), changeHtml(m.changeDown, true)],
        ['peak', '#fff7ed', '#ea580c', '上传峰值', kbig(peak, base), peakLab],
        ['shield', '#fef2f2', '#dc2626', '限速触发', `${m.hits}<small>次</small>`, `本周期 · ${esc(m.name)}`],
    ];
}

function sideOf(m) {
    if (m.kind === 'emby') {
        const rank = m.rank.slice(0, 7), top = rank[0] ? rank[0][1] : 1;
        return `<h3 class="panel-title">用户流量排行<small>本月</small></h3>
           <div class="rank">${rank.map(([u, v], i) => `
             <div class="rank-row"><span class="rank-no">${i + 1}</span><div><div class="rank-name">${esc(u)}</div><div class="rank-bar"><i style="width:${v / top * 100}%"></i></div></div><span class="rank-v num">${fmtBytes(v, 1000)}</span></div>`).join('') || '<div class="hint-t">本月暂无用户流量</div>'}</div>`;
    }
    const list = qbList().map(d => [d.name, Number(d.raw.cycle_uploaded_bytes) || 0]);
    const total = sum(list.map(x => x[1]));
    const parts = list.map((x, i) => [x[0], x[1], PALETTE[i % PALETTE.length]]);
    return `<h3 class="panel-title">设备流量占比<small>本周期上传</small></h3>
       ${donut(parts, fmtBytes(total))}
       <div class="rank" style="margin-top:16px">${list.map((r, i) => `
         <div class="rank-row"><span class="rank-no">${i + 1}</span><div><div class="rank-name">${esc(r[0])}</div><div class="rank-bar"><i style="width:${total ? r[1] / total * 100 : 0}%;background:${PALETTE[i % PALETTE.length]}"></i></div></div><span class="rank-v num">${fmtBytes(r[1])}</span></div>`).join('')}</div>`;
}

/* ---------- 页面 ---------- */
function toolbar(m, kind, dev) {
    const list = kind === 'emby' ? embyList() : qbList();
    const range = state.statsRange;
    const anchor = state.statsDate || defaultAnchor(range);
    const dateField = range === 'hour'
        ? `<input type="date" id="stDate" value="${anchor}" max="${defaultAnchor('hour')}">`
        : range === 'day' ? `<input type="month" id="stDate" value="${anchor}" max="${defaultAnchor('day')}">` : '';
    return `<div class="panel">
        <div class="toolbar">
            <span class="field"><span class="k">设备</span><select id="stDev">${list.map(d => `<option ${d.name === dev.name ? 'selected' : ''} value="${esc(d.name)}">${esc(d.name)}</option>`).join('')}</select>${I.chev}</span>
            ${kind === 'emby' ? `<span class="field"><span class="k">用户</span><select id="stUser"><option value="${ALL_USERS}">全部用户</option>${(m.users || []).map(u => `<option ${u === state.statsUser ? 'selected' : ''} value="${esc(u)}">${esc(u)}</option>`).join('')}</select>${I.chev}</span>` : ''}
            <div class="tabs" data-range>
                <button data-r="hour" class="${range === 'hour' ? 'on' : ''}">按小时</button>
                <button data-r="day" class="${range === 'day' ? 'on' : ''}">按天</button>
                <button data-r="month" class="${range === 'month' ? 'on' : ''}">按月</button>
            </div>
            <div class="tool-r">
                <span class="field datef" data-datef>${I.cal.replace(/16/g, '14')}${m.sp.caption}${dateField}</span>
            </div>
        </div>
    </div>`;
}

export function statsHtml(m, kind, dev) {
    const S = SERIES[kind];
    const kpis = kpisOf(m);
    return `${toolbar(m, kind, dev)}
    <div class="kpis">${kpis.map(k => `
        <div class="panel kpi"><div class="kpi-k"><span class="kpi-ico" style="background:${k[1]};color:${k[2]}">${I[k[0]]}</span>${k[3]}</div><div class="kpi-v num">${k[4]}</div><div class="kpi-d">${k[5]}</div></div>`).join('')}
    </div>
    <div class="chart-grid">
        <div class="panel panel-pad">
            <h3 class="panel-title">${kind === 'emby' ? '播放流量趋势' : '上传流量趋势'}<small>${esc(dev.name)}</small>
                <span class="r chart-legend"><span><i style="background:${S.colorA}"></i>${S.nameA}</span>${kind === 'emby' ? `<span><i style="background:${S.colorB}"></i>${S.nameB}</span>` : ''}</span>
            </h3>
            <div class="chart-wrap" id="chartWrap">${barChart(m)}<div class="chart-tip" id="chartTip"></div></div>
        </div>
        <div class="panel panel-pad">${sideOf(m)}</div>
    </div>`;
}

const NO_DEVICE = kind => `<div class="panel panel-pad" style="text-align:center;color:var(--text-3);padding:48px 16px"><b style="display:block;color:var(--text-1);font-size:15px;margin-bottom:6px">还没有${kind === 'emby' ? ' Emby ' : ' qB '}设备</b>添加设备后这里会显示流量图表</div>`;

/** 拉取数据并生成页面 html */
export async function renderStats(kind) {
    const dev = currentDevice(kind);
    if (!dev) return NO_DEVICE(kind);
    state.statsDevice = dev.name;
    const anchor = state.statsDate || defaultAnchor(state.statsRange);
    const m = kind === 'emby'
        ? await loadEmbyStats(dev.name, state.statsRange, anchor, state.statsUser)
        : await loadQbStats(dev.name, state.statsRange, anchor);
    return statsHtml(m, kind, dev);
}

/** 鼠标悬停提示 */
export function bindChart(kind) {
    const wrap = document.getElementById('chartWrap');
    if (!wrap) return;
    const tip = document.getElementById('chartTip');
    const svg = wrap.querySelector('svg');
    const color = SERIES[kind];
    wrap.querySelectorAll('.col').forEach(g => {
        g.addEventListener('mouseenter', () => {
            const [lab, a, bb] = g.dataset.tip.split('|');
            tip.innerHTML = `<b>${esc(lab)}</b><span><i style="background:${color.colorA}"></i>${esc(a)}</span><span><i style="background:${color.colorB}"></i>${esc(bb)}</span>`;
            const sx = svg.clientWidth / 900, sy = svg.clientHeight / 300;
            tip.style.left = (+g.dataset.x * sx) + 'px';
            tip.style.top = (+g.dataset.y * sy - 6) + 'px';
            tip.style.opacity = 1;
        });
        g.addEventListener('mouseleave', () => { tip.style.opacity = 0; });
    });
}
