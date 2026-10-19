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

/** 范围描述：标签、接口参数、上一周期锚点 */
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
    const months = Array.from({ length: 6 }, (_, i) => addMonths(anchor, i - 5));
    return {
        period: 'monthly',
        params: { start: months[0], end: months[5] },
        labels: months.map(k => `${k.slice(5)}月`),
        keyOf: r => String(r.period || `${r.year}-${p2(r.month)}`),
        keys: months,
        prev: addMonths(anchor, -6),
        caption: '近 6 个月',
    };
}

function fill(sp, rows, field) {
    const map = {};
    (rows || []).forEach(r => { map[sp.keyOf(r)] = (map[sp.keyOf(r)] || 0) + (Number(r[field]) || 0); });
    return sp.keys.map(k => map[k] || 0);
}

const sum = arr => arr.reduce((a, b) => a + b, 0);
const delta = (cur, prev) => (prev > 0 ? (cur - prev) / prev * 100 : null);

async function fetchRows(url, sp, extra) {
    const res = await api.get(`${url}/${sp.period}${q({ ...sp.params, ...extra })}`);
    return res.data || [];
}

/** qB：上传 + 下载，外加上一周期上传用于对比 */
export async function loadQbStats(name, range, anchor) {
    const sp = spec(range, anchor), pv = spec(range, sp.prev);
    const base = `/api/stats/${enc(name)}`;
    const [up, down, prev, prevDown, events] = await Promise.all([
        fetchRows(base, sp, { direction: 'upload' }),
        fetchRows(base, sp, { direction: 'download' }),
        fetchRows(base, pv, { direction: 'upload' }).catch(() => []),
        fetchRows(base, pv, { direction: 'download' }).catch(() => []),
        api.get(`/api/events${q({ instance: name, limit: 500 })}`).then(r => r.data || []).catch(() => []),
    ]);
    const a = fill(sp, up, 'total_bytes'), b = fill(sp, down, 'total_bytes');
    const dev = qbList().find(d => d.name === name);
    const cycleStart = dev && dev.raw.cycle ? String(dev.raw.cycle.start || '').slice(0, 10) : '';
    const hits = events.filter(e => e.event_type === 'limit_applied' && String(e.event_time).slice(0, 10) >= cycleStart).length;
    return {
        kind: 'qb', sp, a, b, name, hits,
        change: delta(sum(a), sum(fill(pv, prev, 'total_bytes'))),
        changeDown: delta(sum(b), sum(fill(pv, prevDown, 'total_bytes'))),
    };
}

/** Emby：播放 + 选片（按用户或全部用户） */
export async function loadEmbyStats(name, range, anchor, user) {
    const sp = spec(range, anchor), pv = spec(range, sp.prev);
    const u = user || ALL_USERS;
    const base = `/api/emby/playback-stats/${enc(name)}`;
    const [cur, prev, monthRank, records] = await Promise.all([
        fetchRows(base, sp, { user: u }),
        fetchRows(base, pv, { user: u }).catch(() => []),
        loadUserRank(name),
        api.get(`/api/emby/playback-records${q({ instance: name, limit: 500 })}`).then(r => r.data || []).catch(() => []),
    ]);
    const a = fill(sp, cur, 'playback_bytes'), b = fill(sp, cur, 'browse_bytes');
    const len = sp.period === 'monthly' ? 7 : 10;
    const from = sp.params.start.slice(0, len), to = sp.params.end.slice(0, len);
    const plays = records.filter(r => {
        const day = String(r.started_at || '').slice(0, len);
        return day >= from && day <= to && (u === ALL_USERS || r.user_name === u);
    }).length;
    return {
        kind: 'emby', sp, a, b, name, user: u,
        change: delta(sum(a), sum(fill(pv, prev, 'playback_bytes'))),
        users: monthRank.users, rank: monthRank.rank, plays,
    };
}

/** 用户流量排行：本月每个用户的播放 + 选片上传 */
async function loadUserRank(name) {
    let users = [];
    try {
        users = (await api.get(`/api/emby/playback-users${q({ instance: name })}`)).data || [];
    } catch (e) { return { users: [], rank: [] }; }
    const month = ym(new Date());
    const rows = await Promise.all(users.map(async user => {
        try {
            const res = await api.get(`/api/emby/playback-stats/${enc(name)}/monthly${q({ user, start: month, end: month })}`);
            return [user, sum((res.data || []).map(r => Number(r.total_bytes) || 0))];
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
