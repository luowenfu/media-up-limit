/* 把接口数据转成原型模板使用的视图模型 */

import {
    fmtBytes, fmtGb, fmtKbps, fmtLimit, shortStamp, splitSpeed, trimNum,
} from './format.js';

const WEEKDAYS = ['', '周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const CYCLE_LABEL = { month: '按月', week: '按周', day: '按天' };

export function planText(cycle) {
    const c = cycle || {};
    const back = fmtLimit(c.reset_limit_kbps);
    if (c.type === 'week') return `每${WEEKDAYS[c.reset_anchor] || '周一'}恢复至 ${back}`;
    if (c.type === 'day') return `每天 ${String(c.reset_anchor ?? 0).padStart(2, '0')}:00 恢复至 ${back}`;
    return `每月 ${c.reset_anchor || 1} 日恢复至 ${back}`;
}

export const deviceUrl = d => `${d.use_https ? 'https' : 'http'}://${d.host}:${d.port}`;

/** 近一个刷新周期的字节数 → 速度 */
export function liveSpeed(bytes, seconds, base = 1024) {
    const sec = Math.max(1, Number(seconds) || 1);
    return splitSpeed((Number(bytes) || 0) / sec, base);
}

function ruleRows(d) {
    const rules = d.speed_rules || [];
    let curIdx = -1;
    rules.forEach((r, i) => { if (r.triggered) curIdx = i; });
    return rules.map((r, i) => {
        const pct = Math.min(100, Number(r.progress) || 0);
        const row = {
            th: fmtGb(r.threshold_gb), pct: trimNum(pct), pctNum: pct,
            speed: fmtKbps(r.limit_kbps),
        };
        if (r.triggered) {
            const when = shortStamp(r.triggered_at);
            row.st = i === curIdx ? 'cur' : 'hit';
            row.note = when ? `${when} ${i === curIdx ? '最近触发' : '触发'}` : '已触发';
        } else {
            const left = Math.max(0, (Number(r.threshold_gb) || 0) - (Number(d.cycle_uploaded_gb) || 0));
            row.note = `剩余 ${fmtGb(left)}`;
        }
        return row;
    });
}

export function qbVM(d, hist) {
    const refresh = d.refresh_interval || 1;
    const up = d.is_online ? liveSpeed(d.recent_delta_bytes, refresh) : ['--', ''];
    const down = d.is_online ? liveSpeed(d.recent_delta_download_bytes, refresh) : ['--', ''];
    const triggered = !!d.is_quota_limited;
    return {
        raw: d,
        name: d.name,
        url: deviceUrl(d),
        online: !!d.is_online,
        cycle: (d.cycle && d.cycle.type_label) || CYCLE_LABEL[d.cycle && d.cycle.type] || '按月',
        triggered,
        live: { up, down },
        limit: {
            global: fmtLimit(d.global_upload_limit_kbps),
            backup: fmtLimit(d.alt_upload_limit_kbps),
            on: d.alt_speed_limits_active ? 'backup' : 'global',
        },
        range: (d.cycle && d.cycle.range_label) || '',
        plan: planText(d.cycle),
        traffic: [
            ['今日', fmtBytes(d.today_uploaded_bytes), fmtBytes(d.today_downloaded_bytes)],
            ['昨日', fmtBytes(d.yesterday_uploaded_bytes), fmtBytes(d.yesterday_downloaded_bytes)],
            ['周期内', fmtBytes(d.cycle_uploaded_bytes), fmtBytes(d.cycle_downloaded_bytes), triggered],
            ['设备总计', fmtBytes(d.device_uploaded_bytes), fmtBytes(d.device_downloaded_bytes)],
        ],
        rules: ruleRows(d),
        activeRule: (d.speed_rules || []).filter(r => r.triggered).length,
        spark: hist ? hist.up : [],
        sparkDown: hist ? hist.down : [],
    };
}

function embyModeText(d) {
    if (d.traffic_collect_mode === 'lucky') return d.lucky_available === false ? 'Lucky 未就绪' : 'Lucky 准确模式';
    return '未启用流量采集';
}

export function embyVM(d, hist) {
    const refresh = d.refresh_interval || 1;
    const collect = d.collect_interval || 5;
    const sessions = d.sessions || [];
    const wan = sessions.filter(s => s.is_remote).length;
    const lan = sessions.length - wan;
    const up = d.api_online ? liveSpeed(d.recent_delta_bytes, refresh, 1000) : ['--', ''];
    const down = d.api_online ? liveSpeed(d.recent_delta_download_bytes, refresh, 1000) : ['--', ''];
    return {
        raw: d,
        name: d.name,
        url: deviceUrl(d),
        online: !!d.api_online,
        mode: embyModeText(d),
        lucky: d.traffic_collect_mode === 'lucky',
        collect: `${refresh} 秒刷新 · ${collect} 秒采集`,
        playing: `局域网 ${lan} · 外网 ${wan}`,
        wan, lan,
        sessions,
        live: { up, down },
        traffic: [
            ['今日', fmtBytes(d.today_uploaded_bytes, 1000), fmtBytes(d.today_downloaded_bytes, 1000)],
            ['昨日', fmtBytes(d.yesterday_uploaded_bytes, 1000), fmtBytes(d.yesterday_downloaded_bytes, 1000)],
            ['本月', fmtBytes(d.monthly_uploaded_bytes, 1000), fmtBytes(d.monthly_downloaded_bytes, 1000)],
            ['总计', fmtBytes(d.device_uploaded_bytes, 1000), fmtBytes(d.device_downloaded_bytes, 1000)],
        ],
        spark: hist ? hist.up : [],
        sparkDown: hist ? hist.down : [],
    };
}

/** 顶部胶囊统计 */
export function qbPills(list) {
    return [
        ['', list.length, '设备数'],
        ['ok', list.filter(d => d.online).length, '在线'],
        ['warn', list.filter(d => d.triggered || d.raw.is_limited).length, '限速中'],
        ['info', list.filter(d => d.raw.has_next_cycle_plan).length, '下周期计划'],
    ];
}

export function embyPills(list) {
    return [
        ['', list.length, '设备数'],
        ['ok', list.filter(d => d.online).length, '在线'],
        ['warn', list.reduce((n, d) => n + d.lan, 0), '局域网播放'],
        ['violet', list.reduce((n, d) => n + d.wan, 0), '外网播放'],
    ];
}
