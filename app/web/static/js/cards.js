/* 设备卡片与设备管理页（markup 与改造计划原型一致） */

import { I, ico } from './icons.js';
import { esc, fmtBytes, fmtDuration, maskIp, splitSpeed } from './format.js';
import { state, qbList, embyList } from './store.js';

/* ---------- 迷你曲线 ---------- */
export function spark(points, color, id) {
    let pts = points.slice(-12);
    while (pts.length < 12) pts.unshift(0);
    const w = 120, h = 38, max = Math.max(...pts, 1);
    const step = w / (pts.length - 1);
    const xy = pts.map((v, i) => [i * step, h - 4 - (v / max) * (h - 10)]);
    const line = xy.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".22"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
        <path d="${line} L${w} ${h} L0 ${h} Z" fill="url(#${id})"/>
        <path d="${line}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>
    </svg>`;
}

const actBtn = (cls, icon, label) => `<button type="button" class="act ${cls}" data-act="${cls}"><span class="ai">${I[icon]}</span>${label}</button>`;

const legend = key => {
    const h = state.hide[key] || '';
    return `<span class="more lg" data-lg="${key}">
        <button type="button" class="lg-btn u ${h === 'u' ? 'off' : ''}" data-lgk="u" title="${h === 'u' ? '显示' : '隐藏'}上传">上传</button>
        <button type="button" class="lg-btn d ${h === 'd' ? 'off' : ''}" data-lgk="d" title="${h === 'd' ? '显示' : '隐藏'}下载">下载</button>
    </span>`;
};

const tiles = (list, key) => `<div class="tiles ${state.hide[key] ? 'hide-' + state.hide[key] : ''}" data-tiles="${key}">${list.map(t => `
    <div class="tile ${t[3] ? 'alert' : ''}">
        <div class="tile-k"><span>${t[0]}</span>${t[3] ? '<span style="color:var(--danger);font-weight:700">超额</span>' : ''}</div>
        <div class="tile-v num"><span class="u">${esc(t[1])}</span><span class="d">${esc(t[2])}</span></div>
    </div>`).join('')}</div>`;

const liveBoxes = (d, key) => `<div class="live">
    <div class="live-box up"><div class="live-label">${I.up}实时上传</div><div class="live-val num">${d.live.up[0]}<small>${d.live.up[1]}</small></div>${spark(d.spark, '#3b82f6', 'su' + key)}</div>
    <div class="live-box down"><div class="live-label">${I.down}实时下载</div><div class="live-val num">${d.live.down[0]}<small>${d.live.down[1]}</small></div>${spark(d.sparkDown, '#10b981', 'sd' + key)}</div>
</div>`;

const urlLine = d => {
    const raw = d.raw;
    return `${raw.use_https ? 'https' : 'http'}://${esc(maskIp(raw.host))}:${raw.port}`;
};
const dot = d => `<span class="online ${d.online ? '' : 'off'}"></span>`;

/* ---------- qB 卡片 ---------- */
export function qbCard(d, i) {
    return `<article class="dev ${d.online ? '' : 'is-off'}">
        <header class="dev-head">
            <div class="dev-icon">qB</div>
            <div class="dev-title">
                <div class="dev-name">${esc(d.name)}</div>
                <div class="dev-url">${dot(d)}${urlLine(d)}</div>
            </div>
            <div class="dev-chips">
                ${d.online ? '' : '<span class="chip">离线</span>'}
                ${d.triggered ? `<span class="chip hot">${I.bolt.replace('16', '13').replace('16', '13')}已触发限速</span>` : ''}
                <span class="chip">${I.cal.replace('16', '13').replace('16', '13')}${esc(d.cycle)}</span>
            </div>
        </header>
        <div class="dev-body">
            <div class="dev-col">
                ${liveBoxes(d, 'q' + i)}
                <div class="kv">
                    <div class="kv-row">${I.bolt}<span class="k">当前上传限速</span>
                        <span class="toggle"><span class="${d.limit.on === 'global' ? 'on' : ''}" data-lim-mode="global" data-dev="${esc(d.name)}" title="切换为全局限速">全局<b>${d.limit.global}</b></span><span class="${d.limit.on === 'backup' ? 'on' : ''}" data-lim-mode="backup" data-dev="${esc(d.name)}" title="切换为备用限速">备用<b>${d.limit.backup}</b></span></span>
                    </div>
                    <div class="kv-row">${I.cal}<span class="k">当前周期</span><span class="v num">${esc(d.range)}</span></div>
                    <div class="kv-row">${I.loop}<span class="k">周期计划</span><span class="v">${esc(d.plan)}</span></div>
                </div>
                <h4 class="sec-title">${I.wave}流量统计${legend('q' + i)}</h4>
                ${tiles(d.traffic, 'q' + i)}
            </div>
            <div class="dev-col">
                <h4 class="sec-title">${I.rules}达量限速规则<span style="color:var(--text-3);font-weight:600">${d.rules.length}</span>
                    <span class="more"><button class="mini-btn" data-goto="stats" data-dev="${esc(d.name)}">${I.chart}图表</button><button class="mini-btn" data-goto="events" data-dev="${esc(d.name)}">${I.list}记录</button></span>
                </h4>
                <div class="rules">${d.rules.map((r, n) => `
                    <div class="rule ${r.st || ''}">
                        <div class="rule-top"><span class="n">${n + 1}</span><b>阈值 ${r.th}</b><span class="pct num">${r.pct}%</span></div>
                        <div class="prog"><i style="width:${r.pctNum}%"></i></div>
                        <div class="rule-bot"><span>限速至 <b style="color:var(--text-1)">${r.speed}</b></span><span class="r ${r.st ? 'st' : ''}">${r.note}</span></div>
                    </div>`).join('')}
                </div>
            </div>
        </div>
        <footer class="dev-foot" data-kind="qb" data-name="${esc(d.name)}">
            ${actBtn('web', 'web', '打开 Web')}${actBtn('set', 'gear', '设置')}${actBtn('lim', 'lim', '手动限速')}${actBtn('rst', 'rst', '清空统计')}${actBtn('del', 'del', '删除')}
        </footer>
    </article>`;
}

/* ---------- Emby 会话 ---------- */
function sessionTitle(s) {
    if (s.session_mode === 'browsing') return s.viewing_title || s.viewing_series_name || '正在选片';
    const base = s.series_name
        ? `${s.series_name}${s.episode_label ? ' ' + s.episode_label : ''}${s.episode_title ? ' · ' + s.episode_title : ''}`
        : (s.title || s.episode_title || '未知内容');
    return base;
}

function sessionHtml(s, d) {
    const base = 1000;
    const wan = !!s.is_remote;
    const browse = s.session_mode === 'browsing';
    const speed = splitSpeed(s.estimated_upload_bytes_1s_live || 0, base);
    const pos = Number(s.position_seconds) || 0;
    const run = Number(s.runtime_seconds) || 0;
    const pct = s.progress_percent != null ? Math.min(100, Number(s.progress_percent) || 0) : (run ? Math.min(100, pos / run * 100) : 0);
    const user = I.user.replace('viewBox', 'width="12" height="12" viewBox');
    const ctl = browse ? '' : `<span style="display:flex;gap:4px">
        <button type="button" class="mini-btn" data-sess="${s.is_paused ? 'unpause' : 'pause'}" data-sid="${esc(s.id)}" data-dev="${esc(d.name)}">${s.is_paused ? '继续' : '暂停'}</button>
        <button type="button" class="mini-btn" data-sess="stop" data-sid="${esc(s.id)}" data-dev="${esc(d.name)}">停止</button></span>`;
    return `<div class="session"><div class="poster">▶</div><div class="session-main">
        <div class="session-title">${esc(sessionTitle(s))}</div>
        <div class="session-sub"><span class="tag ${wan ? 'wan' : 'lan'}">${wan ? '外网' : '局域网'}</span>${browse ? '<span class="tag emby">选片</span>' : ''}${s.is_paused ? '<span class="tag sys">已暂停</span>' : ''}<span>${user}</span>${esc(s.user_name || '')} · ${esc(s.client || '')} · ${speed[0]} ${speed[1]}</div>
        ${browse ? '' : `<div class="ev-prog"><i style="width:${pct.toFixed(1)}%"></i></div>
        <div class="session-sub" style="margin:5px 0 0;justify-content:space-between"><span>${fmtDuration(pos)}${run ? ' / ' + fmtDuration(run) : ''}</span><span>已用 ${fmtBytes(s.estimated_upload_bytes_live || 0, base)}</span></div>`}
        ${ctl ? `<div class="session-sub" style="margin:6px 0 0;justify-content:flex-end">${ctl}</div>` : ''}
    </div></div>`;
}

const emptySession = `<div class="session-empty"><div><div class="ico">${I.tv}</div></div><div><b>暂无活跃播放</b>有人开始播放时，这里会实时显示会话</div></div>`;

/* ---------- Emby 卡片 ---------- */
export function embyCard(d, i) {
    const has = d.sessions.length > 0;
    const key = 'e' + i;
    return `<article class="dev ${d.online ? '' : 'is-off'}">
        <header class="dev-head emby">
            <div class="dev-icon"><svg width="18" height="18" viewBox="0 0 20 20"><path d="M6.5 4.5v11l9-5.5-9-5.5z" fill="currentColor"/></svg></div>
            <div class="dev-title">
                <div class="dev-name">${esc(d.name)}</div>
                <div class="dev-url">${dot(d)}${urlLine(d)}</div>
            </div>
            <div class="dev-chips">
                <span class="chip">${d.online ? 'API 在线' : 'API 离线'}</span>
                <span class="chip">${d.lucky ? 'Lucky 采集' : '未采集'}</span>
                <span class="chip">${d.sessions.length} 路播放</span>
            </div>
        </header>
        <div class="dev-body">
            <div class="dev-col">
                ${liveBoxes(d, key)}
                <div class="kv">
                    <div class="kv-row">${I.link}<span class="k">采集模式</span><span class="v">${esc(d.mode)}</span></div>
                    <div class="kv-row">${I.loop}<span class="k">采集频率</span><span class="v">${esc(d.collect)}</span></div>
                    <div class="kv-row">${I.play}<span class="k">当前播放</span><span class="v">${esc(d.playing)}</span></div>
                </div>
                <h4 class="sec-title">${I.wave}流量统计${legend(key)}</h4>
                ${tiles(d.traffic, key)}
                <div class="tiles-note">按用户统计外网流量</div>
            </div>
            <div class="dev-col" style="display:flex;flex-direction:column">
                <h4 class="sec-title">${I.play}当前播放会话
                    <span class="more"><button class="mini-btn" data-goto="stats" data-dev="${esc(d.name)}">${I.chart}图表</button><button class="mini-btn" data-goto="events" data-dev="${esc(d.name)}">${I.list}记录</button></span>
                </h4>
                <div style="flex:1;display:flex;flex-direction:column;justify-content:${has ? 'flex-start' : 'stretch'}">${has ? d.sessions.map(s => sessionHtml(s, d)).join('') : emptySession}</div>
            </div>
        </div>
        <footer class="dev-foot" data-kind="emby" data-name="${esc(d.name)}">
            ${actBtn('web', 'web', '打开 Web')}${actBtn('set', 'gear', '设置')}${actBtn('rst', 'rst', '清空统计')}${actBtn('del', 'del', '删除')}
        </footer>
    </article>`;
}

const EMPTY = (title, sub) => `<div class="panel panel-pad" style="text-align:center;color:var(--text-3);padding:48px 16px"><b style="display:block;color:var(--text-1);font-size:15px;margin-bottom:6px">${title}</b>${sub}</div>`;

/* ---------- 设备管理页 ---------- */
export function renderDevices(p) {
    const qb = qbList(), em = embyList();
    if (p === 'qb') return qb.length ? `<div class="grid">${qb.map(qbCard).join('')}</div>` : EMPTY('还没有 qB 设备', '点击左侧「添加设备」接入第一台');
    if (p === 'emby') return em.length ? `<div class="grid">${em.map(embyCard).join('')}</div>` : EMPTY('还没有 Emby 设备', '点击左侧「添加设备」接入第一台');
    const qbOn = state.merge.qb.filter(o => o.on), embyOnList = state.merge.emby.filter(o => o.on);
    const qi = n => qb.findIndex(d => d.name === n), ei = n => em.findIndex(d => d.name === n);
    return `<div class="merge-bar">
        <span class="mb-ico">${ico('rules', 16)}</span>
        <div class="mb-t"><b>合并显示</b><small>
            <span><i style="background:var(--qb)"></i>qB ${qbOn.length}/${qb.length}</span>
            <span><i style="background:var(--emby)"></i>Emby ${embyOnList.length}/${em.length}</span>
        </small></div>
        <button type="button" class="btn sm" data-merge-edit>${ico('gear', 15)}选择与排序</button>
    </div>
    <div class="merge">
        <div class="merge-col"><div class="col-head"><span class="dot" style="background:var(--qb)"></span>qBittorrent<small>${qbOn.length} 台</small></div>${qbOn.map(o => qbCard(qb[qi(o.name)], qi(o.name))).join('')}</div>
        <div class="merge-col"><div class="col-head"><span class="dot" style="background:var(--emby)"></span>Emby<small>${embyOnList.length} 台</small></div>${embyOnList.map(o => embyCard(em[ei(o.name)], ei(o.name))).join('')}</div>
    </div>`;
}
