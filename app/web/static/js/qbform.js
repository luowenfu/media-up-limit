/* qB 设备的添加 / 设置抽屉（markup 与改造计划原型一致） */

import * as api from './api.js';
import { enc, q } from './api.js';
import { esc, shortStamp } from './format.js';
import { qbList } from './store.js';
import { askPolicy } from './policy.js';
import { planHtml, readPlan } from './plan.js';
import {
    $, closeSheet, fld, fs, inp, isOn, notice, openSheet, reload, stepper, swRow, testRow, toast, val,
} from './ui.js';

const NAME_MAX = 10;

const hostOf = cfg => `${cfg.host}:${cfg.port}`;
const cleanAddr = s => s.replace(/^https?:\/\//i, '').replace(/\/+$/, '');

function connectionPane(cfg, mode, rank, count) {
    const edit = mode === 'edit';
    return (edit ? '' : notice('', '<b>使用须知：</b>本工具通过 qB WebUI API 读取上传量并调整全局上传限速，请确认 WebUI 已开启且本机可访问。'))
        + fs('gear', '基础设置', '', `<div class="f-grid">
            ${fld('显示名称', inp({ id: 'fName', val: cfg.name || '', ph: '如：qb-做种-WAN2' }), { req: true, hint: `最多 ${NAME_MAX} 个字符，删除后可凭同名恢复数据` })}
            ${fld('设备序号', stepper(rank, 1, count, '', 'fRank'), { hint: `决定排列顺序，范围 1 – ${count}` })}
        </div>`)
        + fs('link', '连接设置', '', `<div class="f-grid">
            ${fld('地址与端口', inp({ id: 'fAddr', pre: 'globe', val: edit ? hostOf(cfg) : '', ph: '192.168.1.1:8080' }), { req: true, full: true })}
            ${fld('用户名', inp({ id: 'fUser', pre: 'user', val: cfg.username || '', ph: '可留空', auto: 'off' }))}
            ${fld('密码', inp({ id: 'fPass', pre: 'key', type: 'password', ph: cfg.has_password ? '留空则保持不变' : '可留空', auto: 'new-password' }))}
            <div class="full hint-t" style="margin-top:-6px">如果 qB 已为本机开启「免登录白名单」，用户名和密码可以都留空。</div>
        </div>
        <div class="sw-list" style="margin-top:14px">
            ${swRow({ t: '使用 HTTPS', d: 'WebUI 开启了 HTTPS 时打开', name: 'https', on: cfg.use_https })}
            ${swRow({ t: '验证 SSL 证书', d: '自签名证书请保持关闭', dep: 'https', disabled: !cfg.use_https, on: cfg.use_https && cfg.verify_ssl })}
        </div>
        ${testRow([['qbConn', '连通性测试', 'search'], ['qbLimit', '限速测试', 'lim']])}`);
}

function planPane(cfg, mode, used) {
    const toRules = list => (list || []).map(r => ({ th: r.cycle_upload_limit_gb, sp: r.speed_limit_kbps }));
    const live = qbList().find(d => d.name === cfg.name);
    const next = cfg.next_cycle_plan;
    const nextSub = live && live.raw.next_cycle_switch_at ? `将于 ${shortStamp(live.raw.next_cycle_switch_at).replace('/', '-')} 切换` : '本周期结束后自动切换';
    return planHtml({
        sub: mode === 'edit' && live ? live.range : '保存后从今天开始计算',
        cycle: cfg.cycle, rules: toRules(cfg.speed_rules), used,
    }) + (mode === 'edit' ? `<button type="button" class="plan-add" data-next-add style="margin-top:14px;${next ? 'display:none' : ''}"><span class="ic">+</span><div><b>添加下周期计划</b><small>本周期结束后自动切换为新的周期和规则，不影响当前周期</small></div></button>
        ${planHtml({
        next: true, id: 'nextPlan', hidden: !next, sub: nextSub,
        cycle: next ? next.cycle : cfg.cycle, rules: toRules(next ? next.speed_rules : cfg.speed_rules),
    })}` : '');
}

function readForm(root, cfg, mode) {
    const name = val(root, '#fName');
    const addr = cleanAddr(val(root, '#fAddr'));
    if (!name) throw new Error('请填写名称');
    if (!addr) throw new Error('请填写地址');
    const https = isOn(root, '[data-sw="https"]');
    const payload = {
        name, host: addr, use_https: https,
        verify_ssl: https && isOn(root, '.sw-row[data-dep="https"] .switch'),
        username: val(root, '#fUser'),
        display_priority: parseInt(val(root, '#fRank'), 10) || 1,
        allow_manual_unlimit: cfg.allow_manual_unlimit !== false,
        ...readPlan(root.querySelector('.plan:not(#nextPlan)')),
    };
    const pass = $('#fPass').value;
    if (pass) payload.password = pass;
    const nextEl = root.querySelector('#nextPlan');
    if (nextEl && nextEl.style.display !== 'none') payload.next_cycle_plan = readPlan(nextEl);
    return payload;
}

async function save(root, cfg, mode) {
    const payload = readForm(root, cfg, mode);
    const orig = cfg.name;
    let policy = null;
    const probe = mode === 'add'
        ? await api.get(`/api/config/instances/orphan-check${q({ name: payload.name })}`)
        : (payload.name !== orig ? await api.get(`/api/config/instances/orphan-check${q({ name: payload.name, renaming_from: orig })}`) : null);
    if (probe && probe.has_orphaned_data) {
        policy = await askPolicy(mode === 'add' ? 'add' : 'rename', payload.name);
        if (!policy) return;
        payload.data_policy = policy;
    }
    if (mode === 'add') await api.post('/api/config/instances', payload);
    else await api.put(`/api/config/instances/${enc(orig)}`, payload);
    closeSheet();
    toast(mode === 'add' ? '已添加 qB 设备' : `已保存「${payload.name}」的设置`);
    reload();
}

function testPayload(root, cfg, type) {
    const https = isOn(root, '[data-sw="https"]');
    const p = {
        name: val(root, '#fName') || cfg.name || '', host: cleanAddr(val(root, '#fAddr')), use_https: https,
        verify_ssl: https && isOn(root, '.sw-row[data-dep="https"] .switch'),
        username: val(root, '#fUser'), password: $('#fPass').value, test_type: type,
    };
    if (cfg.name) p._original_name = cfg.name;
    return p;
}

async function runTest(root, cfg, type) {
    const t0 = performance.now();
    let res;
    try {
        res = await api.post('/api/config/instances/test', testPayload(root, cfg, type));
    } catch (e) {
        return { ok: false, msg: e.message, ms: Math.round(performance.now() - t0) };
    }
    const steps = res.steps || [];
    const bad = steps.find(s => !s.ok);
    const msg = res.success ? (steps.length ? steps[steps.length - 1].message : '测试通过') : (bad ? bad.message : (res.error || '测试失败'));
    return { ok: !!res.success, msg, ms: Math.round(performance.now() - t0) };
}

export async function openQbForm(mode, name) {
    let cfg = { cycle: { type: 'month', reset_anchor: 1, reset_limit_kbps: 0 }, speed_rules: [{ cycle_upload_limit_gb: '', speed_limit_kbps: '' }] };
    const count = qbList().length + (mode === 'add' ? 1 : 0);
    let rank = count;
    if (mode === 'edit') {
        try { cfg = (await api.get(`/api/config/instances/${enc(name)}`)).data; } catch (e) { toast(e.message, 'err'); return; }
        rank = Math.max(1, qbList().findIndex(d => d.name === name) + 1);
    }
    const live = qbList().find(d => d.name === cfg.name);
    openSheet({
        kind: 'qb', icon: 'qB',
        title: mode === 'edit' ? '设备设置' : '添加 qB 设备',
        sub: mode === 'edit' ? `<span class="online"></span>${esc(cfg.name)} · ${esc(hostOf(cfg))}` : '接入一台 qBittorrent，按周期统计上传并自动限速',
        hint: mode === 'edit' ? '修改连接信息后，建议先做一次连通性测试' : '带 * 的为必填项',
        saveLabel: mode === 'edit' ? '保存' : '添加设备',
        tabs: [
            { label: '基础与连接', icon: 'link', html: connectionPane(cfg, mode, rank, count) },
            { label: '达量规则', icon: 'rules', html: planPane(cfg, mode, live ? live.raw.cycle_uploaded_gb : 0) },
        ],
        onSave: root => save(root, cfg, mode),
        tests: { qbConn: root => runTest(root, cfg, 'connect'), qbLimit: root => runTest(root, cfg, 'limit') },
    });
}
