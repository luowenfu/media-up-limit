/* 全局设置抽屉（markup 与改造计划原型一致） */

import * as api from './api.js';
import { ico } from './icons.js';
import { fmtFileSize, fmtUptime } from './format.js';
import { APP_VERSION } from './version.js';
import { loadGlobal } from './store.js';
import { openOrphans } from './orphans.js';
import {
    $, closeSheet, fld, fs, inp, isOn, notice, openSheet, reload, segF, sel, stepper, swRow, toast, val,
} from './ui.js';

const ZONES = ['Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Tokyo', 'UTC'];
const FILE_NOTE = {
    'traffic.db': 'qB 流量数据', 'emby_traffic.db': 'Emby 流量数据',
    'config.yaml': '设备与全局配置', 'app.log': '运行日志',
};
const VIEW_LABEL = { qb: 'qB', emby: 'Emby', merge: '合并' };
const VIEW_VALUE = { qB: 'qb', Emby: 'emby', 合并: 'merge' };
const collectOf = refresh => 5 * Math.max(1, Math.min(10, refresh));

function loginPane(g) {
    return fs('lock', 'Web 登录', '访问本管理页面所需的账号', `<div class="f-grid">
        ${fld('管理账号', inp({ id: 'sUser', pre: 'user', val: g.web_username || '', auto: 'username' }), { full: true })}
        ${fld('新密码', inp({ id: 'sPass', pre: 'key', type: 'password', ph: '留空则不修改', auto: 'new-password' }), { hint: '至少 6 位' })}
        ${fld('确认密码', inp({ id: 'sPass2', pre: 'key', type: 'password', ph: '再次输入新密码', auto: 'new-password' }))}
    </div>`);
}

function systemPane(g) {
    const zones = ZONES.includes(g.timezone) ? ZONES : [g.timezone, ...ZONES];
    const locked = g.emby_enabled && g.emby_instance_count > 0;
    return fs('globe', '时区与端口', '', `<div class="f-grid">
        ${fld('时区', sel(zones, g.timezone, 'sZone'), { hint: '影响周期切换与统计的日期边界' })}
        ${fld('Web 端口', inp({ id: 'sPort', val: g.web_port, type: 'number' }), { hint: '默认 8765，范围 1024 – 65535，修改后需重启' })}
    </div>`)
        + fs('play', 'Emby 监控', '', `<div class="sw-list">
        ${swRow({ t: '开启 Emby 监控功能', d: locked ? '已存在 Emby 设备，需先删除全部 Emby 设备才能关闭' : '开启后可添加 Emby 设备，按用户统计播放与选片流量', on: g.emby_enabled, disabled: locked, name: 'emby' })}
    </div>
    <div style="margin-top:12px">${fld('默认显示', segF(['qB', 'Emby', '合并'], VIEW_LABEL[g.emby_default_device_view] || 'qB', 'data-view'), { hint: '打开页面时侧栏默认选中的平台' })}</div>
    <div style="margin-top:12px">${fld('选片流量记录下限', inp({ id: 'sBrowse', val: g.emby_browse_upload_min_mb, type: 'number', suf: 'MB', attr: 'step="any"' }), { hint: '单次选片上传不足该值时不记入统计与日志' })}</div>`);
}

function dataPane(g, info) {
    const files = (info.files || []).map(f => `<div class="file-row"><code>${f.name}</code><span>${FILE_NOTE[f.name] || ''}</span><span class="sz">${fmtFileSize(f.size)}</span></div>`).join('');
    return fs('loop', '采集与刷新', '', `<div class="f-grid">
        ${fld('页面刷新间隔', stepper(g.refresh_interval, 1, 10, '秒', 'sRefresh'), { hint: '设备卡片与统计的刷新频率，1 – 10 秒' })}
        ${fld('数据采集间隔', inp({ id: 'sCollect', val: `自动 · 当前 ${g.collect_interval} 秒`, ro: true, pre: 'clock' }), { hint: '根据刷新间隔自动调整' })}
    </div>`)
        + fs('db', '数据保存', '', `<div class="f-grid">
        ${fld('数据保存年限', stepper(g.data_retention_years, 1, 20, '年', 'keepYears'), { hint: '1 – 20 年，超出年限的历史数据会被自动清理' })}
    </div>
    <div id="yearsWarn" style="display:none;margin-top:14px">${notice('warn', '缩短保存年限后，超出范围的历史数据将在保存时<b>立即清理</b>，且无法恢复。', 'warn')}</div>`)
        + fs('doc', '文件作用', '均位于容器的 data 目录', `<div class="file-list">${files}</div>`)
        + fs('db', '历史数据', '查看、导出或清理已删除设备保留下来的数据', `<button type="button" class="btn" data-orphans>${ico('db', 15)}管理历史数据</button><div id="orphanPanel" hidden></div>`);
}

async function save(root, g) {
    const refresh = parseInt(val(root, '#sRefresh'), 10);
    const years = parseInt(val(root, '#keepYears'), 10);
    const pass = $('#sPass').value, pass2 = $('#sPass2').value;
    if (pass && pass !== pass2) throw new Error('两次输入的密码不一致');
    const payload = {
        refresh_interval: refresh, data_retention_years: years,
        web_username: val(root, '#sUser'), timezone: val(root, '#sZone'),
        web_port: parseInt(val(root, '#sPort'), 10),
        emby_enabled: isOn(root, '[data-sw="emby"]'),
        emby_default_device_view: VIEW_VALUE[root.querySelector('[data-view].on')?.dataset.view] || 'qb',
        emby_browse_upload_min_mb: parseFloat(val(root, '#sBrowse')),
    };
    if (pass) payload.web_password = pass;
    const res = await api.put('/api/config/global', payload);
    await loadGlobal();
    closeSheet();
    toast(res.message || '全局设置已保存');
    reload();
    if (payload.refresh_interval !== g.refresh_interval) document.dispatchEvent(new Event('app:interval'));
}

export async function openSettings() {
    let g, info = {};
    try {
        g = (await api.get('/api/config/global')).data;
        info = await api.get('/api/system/info');
    } catch (e) { toast(e.message, 'err'); return; }
    openSheet({
        kind: 'sys', icon: ico('gear', 22),
        title: '全局设置', sub: `${APP_VERSION} · 已运行 ${fmtUptime(info.uptime_seconds)}`,
        hint: '修改 Web 端口需重启后生效', saveLabel: '保存',
        tabs: [
            { label: '登录安全', icon: 'lock', html: loginPane(g) },
            { label: '系统', icon: 'server', html: systemPane(g) },
            { label: '数据', icon: 'db', html: dataPane(g, info) },
        ],
        onSave: root => save(root, g),
    });
    const sheet = $('#sheet');
    sheet.oninput = e => {
        if (e.target.id === 'keepYears') $('#yearsWarn').style.display = +e.target.value < g.data_retention_years ? '' : 'none';
        if (e.target.id === 'sRefresh') $('#sCollect').value = `自动 · 当前 ${collectOf(+e.target.value || 1)} 秒`;
    };
    sheet.onclick = e => { if (e.target.closest('[data-orphans]')) openOrphans(); };
}
