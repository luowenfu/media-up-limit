/* 事件记录页：qB 设备事件、Emby 播放 / 选片记录（markup 与改造计划原型一致） */

import * as api from './api.js';
import { q } from './api.js';
import { I } from './icons.js';
import {
    dayTitle, esc, fmtBytes, hhmm, isLanIp, parseTime,
} from './format.js';
import { state, qbList, embyList } from './store.js';

const DAY = 86400000;

/* ---------- qB 事件 ---------- */
const QB_TYPES = {
    limit_applied: ['danger', 'bolt', '触发达量限速', 'limit'],
    limit_applied_manual: ['violet', 'lim', '手动限速', 'limit'],
    limit_removed_manual: ['ok', 'unlock', '手动解除限速', 'limit'],
    speed_mode_switch: ['info', 'loop', '切换限速模式', 'limit'],
    limit_restored: ['ok', 'unlock', '解除限速', 'plan'],
    traffic_reset: ['warn', 'rst', '清空统计', 'other'],
    instance_added: ['ok', 'plus', '添加设备', 'other'],
    instance_updated: ['info', 'gear', '更新配置', 'other'],
    instance_deleted: ['warn', 'del', '删除设备', 'other'],
    device_online: ['ok', 'wifi', '设备上线', 'alert'],
    device_offline: ['warn', 'wifi', '设备离线', 'alert'],
};

function qbEvent(e) {
    const [cls, icon, title, cat] = QB_TYPES[e.event_type] || ['info', 'info', e.event_type, 'other'];
    const meta = [];
    if (e.speed_limit_kbps !== null && e.speed_limit_kbps !== undefined && cat === 'limit') {
        meta.push(['限速', e.speed_limit_kbps > 0 ? `${e.speed_limit_kbps} KB/s` : '不限速']);
    }
    return { t: parseTime(e.event_time), cls, icon, title, tag: e.instance_name, desc: e.reason || '', meta, cat, device: e.instance_name };
}

async function loadQb() {
    const res = await api.get(`/api/events${q({ instance: state.eventsDevice, limit: 500 })}`);
    return (res.data || []).map(qbEvent).filter(e => e.t);
}

/* ---------- Emby 事件 ---------- */
const place = remote => (remote ? '外网' : '局域网');
const seriesText = r => (r.series_name ? `${r.series_name}${r.episode_label ? ' ' + r.episode_label : ''}${r.episode_title ? ' · ' + r.episode_title : ''}` : (r.item_title || r.episode_title || '未知内容'));
const client = r => [r.client, r.device_name].filter(Boolean).join(' · ');

function playbackEvent(r) {
    const playing = r.status === 'playing';
    const interrupted = !!r.interrupt_reason;
    const t = parseTime(playing ? r.started_at : (r.stopped_at || r.started_at));
    const bytes = r.estimated_upload_bytes ?? r.live_upload_checkpoint_bytes;
    const meta = [];
    if (r.is_remote) meta.push([playing ? '已用' : '流量', bytes ? fmtBytes(bytes, 1000) : '统计中']);
    else meta.push(['流量', '不计（局域网）']);
    if (r.played_seconds) meta.push(['时长', `${Math.floor(r.played_seconds / 60)} 分 ${r.played_seconds % 60} 秒`]);
    const run = Number(r.runtime_seconds) || 0, pos = Number(r.end_position_seconds ?? r.start_position_seconds) || 0;
    return {
        t, cls: interrupted ? 'warn' : playing ? 'ok' : 'info', icon: interrupted ? 'warn' : playing ? 'film' : 'stop',
        title: interrupted ? '播放中断' : playing ? '开始播放' : '播放结束',
        tag: `${r.user_name} · ${place(r.is_remote)}`,
        desc: `《${seriesText(r)}》${client(r) ? ' · ' + client(r) : ''}${interrupted ? ' · ' + r.interrupt_reason : ''}`,
        meta, prog: run ? Math.min(100, pos / run * 100) : 0,
        cat: interrupted ? 'alert' : 'play', device: r.instance_name, user: r.user_name, lan: !r.is_remote,
    };
}

function browseEvent(r) {
    const lan = isLanIp(r.client_ip);
    const what = r.series_name || r.viewing_title || r.episode_title || '影片';
    return {
        t: parseTime(r.stopped_at), cls: 'violet', icon: 'eye', title: '选片记录',
        tag: `${r.user_name} · ${place(!lan)}`,
        desc: `浏览《${what}》${client(r) ? ' · ' + client(r) : ''}`,
        meta: [['选片流量', fmtBytes(r.estimated_upload_bytes, 1000)]],
        cat: 'browse', device: r.instance_name, user: r.user_name, lan,
    };
}

async function loadEmby() {
    const names = state.eventsDevice ? [state.eventsDevice] : embyList().map(d => d.name);
    const lists = await Promise.all([
        api.get(`/api/emby/playback-records${q({ instance: state.eventsDevice, limit: 500 })}`).then(r => (r.data || []).map(playbackEvent)).catch(() => []),
        ...names.map(n => api.get(`/api/emby/browse-records${q({ instance: n, limit: 200 })}`).then(r => (r.data || []).map(browseEvent)).catch(() => [])),
    ]);
    return lists.flat().filter(e => e.t);
}

/* ---------- 页面 ---------- */
const TYPE_TABS = {
    qb: [['all', '全部'], ['limit', '限速'], ['plan', '周期计划'], ['alert', '异常']],
    emby: [['all', '全部'], ['play', '播放'], ['browse', '选片'], ['alert', '异常']],
};
const RANGES = [[7, '最近 7 天'], [30, '最近 30 天'], [0, '全部']];

/** 取数据并生成 html */
export async function renderEvents(kind) {
    const all = (kind === 'emby' ? await loadEmby() : await loadQb()).sort((a, b) => b.t - a.t);
    const since = state.eventsDays ? Date.now() - state.eventsDays * DAY : 0;
    let list = all.filter(e => e.t.getTime() >= since);
    if (kind === 'emby') {
        if (state.eventsHideLan) list = list.filter(e => !e.lan);
        if (state.eventsUser) list = list.filter(e => e.user === state.eventsUser);
    }
    const users = kind === 'emby' ? [...new Set(all.map(e => e.user).filter(Boolean))] : [];
    const tabs = TYPE_TABS[kind].map(([k, label]) => [k, label, k === 'all' ? list.length : list.filter(e => e.cat === k).length]);
    const shown = state.eventsType === 'all' ? list : list.filter(e => e.cat === state.eventsType);
    const devices = kind === 'emby' ? embyList() : qbList();

    const groups = [];
    shown.forEach(e => {
        const [day, date] = dayTitle(e.t);
        const last = groups[groups.length - 1];
        if (last && last.day === day && last.date === date) last.list.push(e);
        else groups.push({ day, date, list: [e] });
    });

    return `
    <div class="panel" style="margin-bottom:16px">
        <div class="toolbar">
            <span class="field"><span class="k">设备</span><select id="evDev"><option value="">全部设备</option>${devices.map(d => `<option value="${esc(d.name)}" ${d.name === state.eventsDevice ? 'selected' : ''}>${esc(d.name)}</option>`).join('')}</select>${I.chev}</span>
            ${kind === 'emby' ? `<span class="field"><span class="k">用户</span><select id="evUser"><option value="">全部用户</option>${users.map(u => `<option value="${esc(u)}" ${u === state.eventsUser ? 'selected' : ''}>${esc(u)}</option>`).join('')}</select>${I.chev}</span>` : ''}
            <div class="tabs" data-evtype>${tabs.map(t => `<button data-v="${t[0]}" class="${state.eventsType === t[0] ? 'on' : ''}">${t[1]}<span class="c">${t[2]}</span></button>`).join('')}</div>
            <div class="tool-r">
                ${kind === 'emby' ? `<span class="field" data-hidelan style="cursor:pointer">排除局域网 <span class="switch ${state.eventsHideLan ? 'on' : ''}" style="transform:scale(.8);margin-right:-6px"></span></span>` : ''}
                <span class="field">${I.cal.replace(/16/g, '14')}<select id="evDays">${RANGES.map(([v, l]) => `<option value="${v}" ${v === state.eventsDays ? 'selected' : ''}>${l}</option>`).join('')}</select></span>
            </div>
        </div>
    </div>
    <div class="panel"><div class="timeline">${groups.map(g => `
        <div class="day">${g.day}<small>${g.date}</small></div>
        ${g.list.map(e => `<div class="ev">
            <div class="ev-time">${hhmm(e.t)}</div>
            <div class="ev-ico ${e.cls}">${I[e.icon]}</div>
            <div class="ev-card">
                <div class="ev-top"><b>${esc(e.title)}</b><span class="tag ${kind}">${esc(e.tag)}</span><span class="r num">${g.day} ${hhmm(e.t)}</span></div>
                ${e.desc ? `<div class="ev-desc">${esc(e.desc)}</div>` : ''}
                ${e.meta.length ? `<div class="ev-meta">${e.meta.map(m => `<span>${esc(m[0])} <b>${esc(m[1])}</b></span>`).join('')}</div>` : ''}
                ${e.prog ? `<div class="ev-prog"><i style="width:${e.prog.toFixed(1)}%"></i></div>` : ''}
            </div>
        </div>`).join('')}`).join('') || '<div style="padding:40px;text-align:center;color:var(--text-3)">没有匹配的事件</div>'}
    </div></div>`;
}

