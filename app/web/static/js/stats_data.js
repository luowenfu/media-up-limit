/* 流量图表的数据层：按小时 / 天 / 月取数并整理成图表模型 */

import * as api from './api.js';
import { enc, q } from './api.js';
import { state, embyList, qbList } from './store.js';
import { ymd } from './format.js';

const p2 = n => String(n).padStart(2, '0');
export const ALL_USERS = '__all_users__';

const ym = d => `${d.getFullYear()}-${p2(d.getMonth() + 1)}`;
const addMonths = (anchor, n) => {
    const [y, m] = anchor.split('-').map(Number);
    return ym(new Date(y, m - 1 + n, 1));
};
const addDays = (anchor, n) => {
    const [y, m, d] = anchor.split('-').map(Number);
    return ymd(new Date(y, m - 1, d + n));
};

/** 当前统计范围的默认锚点 */
export function defaultAnchor(range) {
    const now = new Date();
    return range === 'hour' ? ymd(now) : ym(now);
}

const monthKey = r => {
    if (r.period) return String(r.period);
    const m = r.month;
    if (m != null && String(m).includes('-')) return String(m);
    if (r.year != null && m != null) return `${r.year}-${p2(m)}`;
    return '';
};

/** 按月窗口。span 为 6 / 12 / 24 / all，anchor 是窗口结束月 */
function monthSpec(span, anchor) {
    const end = anchor || ym(new Date());
    const n = span === 'all' ? 0 : ([6, 12, 24].includes(+span) ? +span : 6);
    if (!n) {
        return {
            period: 'monthly', all: true,
            params: { start: '2000-01', end }, end, prev: null, caption: '全部',
            labels: [`${end.slice(5)}月`], keys: [end], keyOf: monthKey,
        };
    }
    const months = Array.from({ length: n }, (_, i) => addMonths(end, i - (n - 1)));
    const label = k => (n > 6 ? `${k.slice(2, 4)}/${k.slice(5)}` : `${k.slice(5)}月`);
    return {
        period: 'monthly',
        params: { start: months[0], end: months[n - 1] },
        labels: months.map(label),
        keyOf: monthKey,
        keys: months,
        prev: addMonths(end, -n),
        caption: `近${n}个月`,
    };
}

/** 全部：从最早有数据的月份画到当前月，中间没有流量的月份补 0 */
function fitMonths(sp, groups) {
    const found = [];
    groups.flat().forEach(r => {
        const k = sp.keyOf(r);
        if (/^\d{4}-\d{2}$/.test(k)) found.push(k);
    });
    found.sort();
    const end = sp.end;
    const start = found.length ? found[0] : end;
    const months = [];
    let cur = start;
    while (cur <= end && months.length < 240) {
        months.push(cur);
        cur = addMonths(cur, 1);
    }
    if (!months.length) months.push(end);
    return {
        ...sp,
        keys: months,
        labels: months.map(k => `${k.slice(2, 4)}/${k.slice(5)}`),
        params: { start: months[0], end: months[months.length - 1] },
    };
}
export function spec(range, anchor) {
    const now = new Date();
    if (range === 'hour') {
        return {
            period: 'hourly',
            params: { start: `${anchor} 00:00`, end: `${anchor} 23:00` },
            labels: Array.from({ length: 24 }, (_, i) => `${p2(i)}:00`),
            keyOf: r => String(r.hour || '').slice(11, 13),
            keys: Array.from({ length: 24 }, (_, i) => p2(i)),
            prev: addDays(anchor, -1),
            caption: anchor,
        };
    }
    if (range === 'day') {
        const [y, m] = anchor.split('-').map(Number);
        const last = new Date(y, m, 0).getDate();
        const shown = anchor === ym(now) ? now.getDate() : last;
        const days = Array.from({ length: shown }, (_, i) => p2(i + 1));
        return {
            period: 'daily',
            params: { start: `${anchor}-01`, end: `${anchor}-${p2(last)}` },
            labels: days.map(d => `${p2(m)}-${d}`),
            keyOf: r => String(r.day || '').slice(8, 10),
            keys: days,
            prev: addMonths(anchor, -1),
            caption: `${anchor}-01 ~ ${p2(m)}-${days[days.length - 1]}`,
        };
    }
    return monthSpec(state.statsSpan || '6', anchor);
}

function fill(sp, rows, field) {
    const map = {};
    (rows || []).forEach(r => { map[sp.keyOf(r)] = (map[sp.keyOf(r)] || 0) + (Number(r[field]) || 0); });
    return sp.keys.map(k => map[k] || 0);
}

const sum = arr => arr.reduce((a, b) => a + b, 0);
const delta = (cur, prev) => (prev > 0 ? (cur - prev) / prev * 100 : null);

/** 事件时间是否落在当前图表所选时段内 */
function inSpan(time, sp) {
    const t = String(time || '');
    if (!t || !sp || !sp.params) return false;
    if (sp.period === 'hourly') return t.slice(0, 10) === String(sp.params.start).slice(0, 10);
    const start = String(sp.params.start);
    const end = String(sp.params.end);
    return t.slice(0, start.length) >= start && t.slice(0, end.length) <= end;
}

async function fetchRows(url, sp, extra) {
    const res = await api.get(`${url}/${sp.period}${q({ ...sp.params, ...extra })}`);
    return res.data || [];
}

/** qB：按累计只读当前种子快照；按小时、按天、按月查本项目库 */
export async function loadQbStats(name, range, anchor) {
    if (range === 'total') return loadQbCumulative(name);
    let sp = spec(range, anchor);
    const pv = sp.prev ? spec(range, sp.prev) : null;
    const base = `/api/stats/${enc(name)}`;
    const rankQuery = {
        instance: name, period: sp.period, start: sp.params.start, end: sp.params.end,
    };
    const [up, down, prev, prevDown, events, ranks] = await Promise.all([
        fetchRows(base, sp, { direction: 'upload' }),
        fetchRows(base, sp, { direction: 'download' }),
        pv ? fetchRows(base, pv, { direction: 'upload' }).catch(() => []) : [],
        pv ? fetchRows(base, pv, { direction: 'download' }).catch(() => []) : [],
        api.get(`/api/events${q({ instance: name, limit: 500 })}`).then(r => r.data || []).catch(() => []),
        api.get(`/api/qb/torrent-ranks${q(rankQuery)}`).then(r => r.data || {}).catch(() => ({})),
    ]);
    if (sp.all) sp = fitMonths(sp, [up, down]);
    const a = fill(sp, up, 'total_bytes'), b = fill(sp, down, 'total_bytes');
    const hits = events.filter(e => e.event_type === 'limit_applied' && inSpan(e.event_time, sp)).length;
    return {
        kind: 'qb', sp, a, b, name, hits, cumulative: false,
        torrents: ranks.torrents || [],
        ratios: (ranks.ratios || []).map(asRatioRow),
        sites: ranks.sites || [],
        change: pv ? delta(sum(a), sum(fill(pv, prev, 'total_bytes'))) : null,
        changeDown: pv ? delta(sum(b), sum(fill(pv, prevDown, 'total_bytes'))) : null,
    };
}

/** 按累计：一次 qB 种子列表，给出设备合计和三列排行 */
async function loadQbCumulative(name) {
    let ranks = {};
    try {
        ranks = (await api.get(`/api/qb/torrent-ranks${q({ instance: name, period: 'total' })}`)).data || {};
    } catch (e) { ranks = {}; }
    const available = ranks.available === true;
    const totals = ranks.totals || { uploaded: 0, downloaded: 0, count: 0, ratio: 0 };
    return {
        kind: 'qb', cumulative: true, available, totals, name,
        sp: { caption: '按设备累计', labels: [] },
        a: [], b: [], hits: 0,
        torrents: available ? (ranks.torrents || []) : [],
        ratios: available ? (ranks.ratios || []).map(asRatioRow) : [],
        sites: available ? (ranks.sites || []) : [],
        change: null, changeDown: null,
    };
}

/** qB 把无下载量的分享率封顶为 9999，或在旧版本里记成负数 */
function asRatioRow(row) {
    const raw = row && row[1];
    const n = Number(raw);
    const infinite = raw == null || !Number.isFinite(n) || n < 0 || n >= 9999;
    return [row[0], infinite ? Number.POSITIVE_INFINITY : n, Number(row[2]) || 0, Number(row[3]) || 0];
}

/** Emby：播放 + 选片（按用户或全部用户） */
export async function loadEmbyStats(name, range, anchor, user) {
    let sp = spec(range, anchor);
    const pv = sp.prev ? spec(range, sp.prev) : null;
    const u = user || ALL_USERS;
    const base = `/api/emby/playback-stats/${enc(name)}`;
    const [cur, prev, monthRank, records, content, filmRanks] = await Promise.all([
        fetchRows(base, sp, { user: u }),
        pv ? fetchRows(base, pv, { user: u }).catch(() => []) : [],
        loadUserRank(name, sp),
        api.get(`/api/emby/playback-records${q({ instance: name, limit: 500 })}`).then(r => r.data || []).catch(() => []),
        api.get(`/api/emby/content-rank${q({
            instance: name, user: u, period: sp.period,
            start: sp.params.start, end: sp.params.end,
        })}`).then(r => r.data || {}).catch(() => ({ genre_error: true })),
        api.get(`/api/emby/playback-film-ranks${q({
            instance: name, period: sp.period,
            start: sp.params.start, end: sp.params.end,
        })}`).then(r => r.data || {}).catch(() => ({})),
    ]);
    if (sp.all) sp = fitMonths(sp, [cur]);
    const a = fill(sp, cur, 'playback_bytes'), b = fill(sp, cur, 'browse_bytes');
    const len = sp.period === 'monthly' ? 7 : 10;
    const from = sp.params.start.slice(0, len), to = sp.params.end.slice(0, len);
    const plays = records.filter(r => {
        const day = String(r.started_at || '').slice(0, len);
        return day >= from && day <= to && (u === ALL_USERS || r.user_name === u);
    }).length;
    return {
        kind: 'emby', sp, a, b, name, user: u,
        change: pv ? delta(sum(a), sum(fill(pv, prev, 'playback_bytes'))) : null,
        users: monthRank.users, rank: monthRank.rank,
        filmTraffic: filmRanks.users || {},
        films: content.films || [], genres: content.genres || [],
        genreError: !!content.genre_error, rankTruncated: !!content.truncated,
        active: u === ALL_USERS ? monthRank.rank.length : monthRank.rank.some(r => r[0] === u) ? 1 : 0,
        plays: content.play_count != null ? content.play_count : plays,
    };
}

/** 用户流量排行：所选时段内每个用户的播放 + 选片上传 */
async function loadUserRank(name, sp) {
    let users = [];
    try {
        users = (await api.get(`/api/emby/playback-users${q({ instance: name })}`)).data || [];
    } catch (e) { return { users: [], rank: [] }; }
    const rows = await Promise.all(users.map(async user => {
        try {
            const data = await fetchRows(`/api/emby/playback-stats/${enc(name)}`, sp, { user });
            return [user, sum(data.map(r => Number(r.total_bytes) || 0))];
        } catch (e) { return [user, 0]; }
    }));
    return { users, rank: rows.filter(r => r[1] > 0).sort((x, y) => y[1] - x[1]) };
}

/** 设备下拉里的当前设备：没选或已删除时取第一台 */
export function currentDevice(kind) {
    const list = kind === 'emby' ? embyList() : qbList();
    const cur = state.statsDevice;
    return list.find(d => d.name === cur) || list[0] || null;
}
