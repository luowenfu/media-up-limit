/* Emby 设备的添加 / 设置抽屉（markup 与改造计划原型一致） */

import * as api from './api.js';
import { enc, q } from './api.js';
import { ico } from './icons.js';
import { esc } from './format.js';
import { embyList } from './store.js';
import { askPolicy } from './policy.js';
import { anchorHtml, readPlan } from './plan.js';
import {
    closeSheet, fld, fs, inp, isOn, notice, openSheet, reload, segF, stepper, swRow, testRow, toast, val,
} from './ui.js';

const NAME_MAX = 10;
const hostOf = cfg => `${cfg.host}:${cfg.port}`;
const cleanAddr = s => s.replace(/^https?:\/\//i, '').replace(/\/+$/, '');
const splitLucky = url => {
    const m = String(url || '').match(/^(https?):\/\/(.+?)\/*$/i);
    return m ? { https: m[1].toLowerCase() === 'https', addr: m[2] } : { https: false, addr: '' };
};

function connectionPane(cfg, mode, rank, count) {
    const edit = mode === 'edit';
    return notice('', '<b>使用须知：</b>流量按十进制单位统计（1 GB = 1000 MB）；要准确统计外网流量，请开启 Lucky 反代模式；Emby 与本工具需处于同一时区。')
        + fs('gear', '基础设置', '', `<div class="f-grid">
            ${fld('显示名称', inp({ id: 'fName', val: cfg.name || '', ph: '如：大富影库' }), { req: true, hint: `最多 ${NAME_MAX} 个字符` })}
            ${fld('设备序号', stepper(rank, 1, count, '', 'fRank'), { hint: `范围 1 – ${count}` })}
        </div>`)
        + fs('link', 'Emby 连接', '用于读取用户、会话和媒体信息', `<div class="f-grid">
            ${fld('地址与端口', inp({ id: 'fAddr', pre: 'globe', val: edit ? hostOf(cfg) : '', ph: '192.168.1.1:8096' }), { req: true, full: true })}
            ${fld('API Key', inp({ id: 'fKey', pre: 'key', type: 'password', ph: cfg.has_api_key ? '留空则保持不变' : '在 Emby 控制台 › 高级 › API 密钥 中创建', auto: 'new-password' }), { req: !cfg.has_api_key, full: true })}
        </div>
        <div class="sw-list" style="margin-top:14px">
            ${swRow({ t: '使用 HTTPS', d: 'Emby 开启了 HTTPS 时打开', name: 'https', on: cfg.use_https })}
            ${swRow({ t: '验证 SSL 证书', d: '自签名证书请保持关闭', dep: 'https', disabled: !cfg.use_https, on: cfg.use_https && cfg.verify_ssl })}
        </div>
        ${testRow([['embyConn', '连通性测试', 'search'], ['embyApi', 'API 测试', 'key']])}`);
}

function savedRuleLabels(cfg) {
    const rows = Array.isArray(cfg.lucky_rules) ? cfg.lucky_rules : [];
    const labels = rows.map(row => String(row.label || '').trim()).filter(Boolean);
    if (labels.length) return labels;
    return String(cfg.lucky_rule_label || '').split('；').map(part => part.trim()).filter(Boolean);
}

function ruleOptions(cands, selectedIdx, keepLabel) {
    const opts = ['<option value="">请选择反代规则</option>'];
    if (keepLabel) opts.push(`<option value="keep" selected>${esc(keepLabel)}</option>`);
    (cands || []).forEach((c, i) => {
        opts.push(`<option value="${i}" ${selectedIdx === i ? 'selected' : ''}>${esc(c.label)}</option>`);
    });
    return opts.join('');
}

function ruleRow(cands, selectedIdx, keepLabel) {
    const keep = keepLabel ? ` data-keep="${esc(keepLabel)}"` : '';
    return `<div class="rule-row"><div class="inp"><select data-rule${keep}>${ruleOptions(cands, selectedIdx, keepLabel)}</select></div><button type="button" class="rule-x" data-rule-del aria-label="移除这条反代">${ico('x', 14)}</button></div>`;
}

function luckyPane(cfg) {
    const on = cfg.traffic_collect_mode === 'lucky';
    const lk = splitLucky(cfg.lucky_base_url);
    const saved = savedRuleLabels(cfg);
    const rows = (saved.length ? saved : ['']).map(label => ruleRow([], -1, label)).join('');
    return `<div class="big-toggle ${on ? 'on' : ''}" data-lucky>
            <span class="ic">${ico('plug', 20)}</span>
            <div class="t"><b>开启 Lucky 反代模式</b><small>通过 Lucky 反代统计流量，可准确采集外网播放与选片流量</small></div>
            <span class="badge">推荐</span>
            <span class="switch ${on ? 'on' : ''}"></span>
        </div>
        <div class="sub-panel" id="luckyOff" style="${on ? 'display:none' : ''}">${notice('warn', '未开启流量采集：只能显示播放会话，无法统计外网播放与选片产生的流量。', 'warn')}</div>
        <div class="sub-panel" id="luckyOn" style="${on ? '' : 'display:none'}">
            ${fs('server', 'Lucky 连接', 'Emby 外网访问所经过的 Lucky 实例', `<div class="f-grid">
                ${fld('地址与端口', inp({ id: 'lAddr', pre: 'globe', val: lk.addr, ph: '192.168.1.1:16601' }), { req: true })}
                ${fld('OpenToken', inp({ id: 'lToken', pre: 'key', type: 'password', ph: cfg.has_lucky_open_token ? '留空则保持不变' : 'Lucky › 设置 › OpenToken', auto: 'new-password' }), { req: !cfg.has_lucky_open_token })}
                <div class="full"><div class="sw-list">
                    ${swRow({ t: '使用 HTTPS', d: 'Lucky 管理端开启了 HTTPS 时打开', name: 'lhttps', on: lk.https })}
                    ${swRow({ t: '验证 SSL 证书', d: '自签名证书请保持关闭', dep: 'lhttps', disabled: !lk.https, on: lk.https && cfg.lucky_verify_ssl })}
                </div></div>
                <div class="full">
                    <div class="fl">反代规则<span class="req">*</span><button type="button" class="rule-add" data-rule-add aria-label="添加反代规则">${ico('plus', 14)}</button></div>
                    <div class="rule-rows" id="lRules">${rows}</div>
                    <div class="hint-t">连通性测试会自动加入指向这台 Emby 的反代。点 + 可以再加一条，每条单独选择</div>
                </div>
            </div>
            ${testRow([['luckyConn', '连通性测试', 'search'], ['luckyFlow', '流量接口测试', 'wave']])}`)}
            ${fs('film', '统计口径', '', `<div class="sw-list">${swRow({ t: '「选片」流量计入统计', d: '浏览海报、详情页时产生的流量也计入用户', on: cfg.lucky_credit_browse_traffic !== false, name: 'browse' })}</div>`)}
        </div>`;
}

/* ---------- 达量停播规则 ---------- */
const CYC_LABEL = { month: '按月', week: '按周', day: '按天' };

function quotaUserOptions(users, current) {
    const names = [...users];
    if (current && !names.some(n => n.toLowerCase() === current.toLowerCase())) names.unshift(current);
    return ['<option value="">请选择用户</option>']
        .concat(names.map(n => `<option value="${esc(n)}" ${n === current ? 'selected' : ''}>${esc(n)}</option>`))
        .join('');
}

function quotaRow(users, row = {}) {
    return `<div class="rule-row" data-quota-row>
        <div class="inp"><select data-q-user>${quotaUserOptions(users, row.user_name || '')}</select></div>
        <div class="inp" style="flex:0 0 150px"><input type="number" min="0" step="any" data-q-gb value="${esc(row.limit_gb ?? '')}" placeholder="额度"><span class="suf">GB</span></div>
        <button type="button" class="rule-x" data-quota-del aria-label="移除这个用户">${ico('x', 14)}</button>
    </div>`;
}

function quotaPane(cfg, users) {
    const qt = cfg.user_quota || {};
    const cyc = qt.cycle || { type: 'month', reset_anchor: 1 };
    const label = CYC_LABEL[cyc.type] || '按月';
    const rows = (qt.users && qt.users.length ? qt.users : [{}]).map(r => quotaRow(users, r)).join('');
    return notice('', '<b>达量停播：</b>用户本周期外网上行达到额度后，再开始播放时，3 秒后收到提示，再过 5 秒被停止。需要开启「流量采集」里的 Lucky 反代模式，局域网播放不受影响')
        + fs('shield', '达量停播', '', `<div class="sw-list">
            ${swRow({ t: '启用达量停播', d: '只对下面列出的用户生效，没列出的不限', on: !!qt.enabled, name: 'qEnable' })}
            ${swRow({ t: '只记录不执行', d: '到点只写系统日志，不发提示也不停止。确认用量准确后再关闭', on: qt.dry_run !== false, name: 'qDry' })}
        </div>`)
        + fs('clock', '统计周期', '', `<div class="plan" id="quotaPlan"><div class="f-grid">
            ${fld('周期类型', segF(['按月', '按周', '按天'], label, 'data-cyc'), { full: true })}
            <div class="anchor">${anchorHtml(label, cyc.reset_anchor)}</div>
        </div></div>`)
        + fs('user', '用户额度', '', `<div class="rule-rows" id="qRows">${rows}</div>
            <div class="hint-t">额度按本周期外网上行（播放，选片按「流量采集」里的设置）累计，单位 GB（1 GB = 1000 MB）。归属可能有些误差，额度请留余量</div>`,
        `<button type="button" class="rule-add" data-quota-add aria-label="添加用户">${ico('plus', 14)}</button>`);
}

function readQuota(root) {
    const rows = [...root.querySelectorAll('#qRows [data-quota-row]')].map(row => ({
        user_name: row.querySelector('[data-q-user]').value,
        limit_gb: row.querySelector('[data-q-gb]').value,
    })).filter(r => r.user_name || String(r.limit_gb).trim());
    rows.forEach(r => {
        if (!r.user_name) throw new Error('请为每个额度选择用户');
        if (!(parseFloat(r.limit_gb) > 0)) throw new Error(`请填写用户「${r.user_name}」的额度`);
        r.limit_gb = parseFloat(r.limit_gb);
    });
    const enabled = isOn(root, '[data-sw="qEnable"]');
    if (enabled && !rows.length) throw new Error('启用达量停播前，请至少添加一个用户额度');
    return {
        enabled,
        dry_run: isOn(root, '[data-sw="qDry"]'),
        cycle: readPlan(root.querySelector('#quotaPlan')).cycle,
        users: rows,
    };
}

/** 点 + 加一行用户额度 */
export function addQuotaRow() {
    const root = document.getElementById('sheet');
    const box = root && root.querySelector('#qRows');
    if (!box) return;
    box.insertAdjacentHTML('beforeend', quotaRow(root._quotaUsers || []));
}

export function removeQuotaRow(btn) {
    const box = btn.closest('#qRows');
    const row = btn.closest('[data-quota-row]');
    if (!box || !row) return;
    if (box.querySelectorAll('[data-quota-row]').length <= 1) {
        row.querySelector('[data-q-user]').value = '';
        row.querySelector('[data-q-gb]').value = '';
        return;
    }
    row.remove();
}

const luckyOn = root => root.querySelector('[data-lucky]').classList.contains('on');
const luckyUrl = root => `${isOn(root, '[data-sw="lhttps"]') ? 'https' : 'http'}://${cleanAddr(val(root, '#lAddr'))}`;

const backendOf = raw => String(raw || '').replace(/^https?:\/\//i, '').replace(/\/+$/, '').toLowerCase();

const bareFront = label => String(label || '').replace(/^https?:\/\//i, '');

function ruleIndexes(cands, selected, kept, addr) {
    const picked = new Set((selected || []).map(c => `${c.rule_key}|${c.sub_key}`));
    const keptBare = new Set([...kept].map(bareFront));
    const idxs = [];
    cands.forEach((c, i) => {
        const hit = picked.has(`${c.rule_key}|${c.sub_key}`)
            || kept.has(c.label)
            || keptBare.has(bareFront(c.label))
            || (addr && backendOf(c.backend) === addr);
        if (hit) idxs.push(i);
    });
    return idxs;
}

/** 连通性测试后，探测到的每条反代各放一个下拉框 */
function fillRules(root, cands, selected) {
    root._cand = cands;
    const box = root.querySelector('#lRules');
    const kept = new Set([...box.querySelectorAll('select[data-keep]')].map(el => el.dataset.keep || '').filter(Boolean));
    if (!cands.length) {
        box.innerHTML = ruleRow([], -1, '');
        return;
    }
    const idxs = ruleIndexes(cands, selected, kept, backendOf(val(root, '#fAddr')));
    box.innerHTML = (idxs.length ? idxs : [-1]).map(i => ruleRow(cands, i, '')).join('');
}

/** 点 + 再加一条空白下拉框 */
export function addLuckyRuleRow() {
    const root = document.getElementById('sheet');
    const box = root && root.querySelector('#lRules');
    if (!box) return;
    const cands = root._cand || [];
    if (!cands.length) {
        toast('请先完成连通性测试', 'info');
        return;
    }
    box.insertAdjacentHTML('beforeend', ruleRow(cands, -1, ''));
}

export function removeLuckyRuleRow(btn) {
    const box = btn.closest('#lRules');
    const row = btn.closest('.rule-row');
    if (!box || !row) return;
    if (box.querySelectorAll('.rule-row').length <= 1) {
        const sel = row.querySelector('select');
        if (sel) sel.value = '';
        return;
    }
    row.remove();
}

function chosenRules(root) {
    const rules = [];
    const seen = new Set();
    root.querySelectorAll('#lRules select').forEach(sel => {
        const value = sel.value;
        if (!value) return;
        if (value === 'keep') {
            const label = sel.dataset.keep || '';
            if (!label || seen.has(`keep:${label}`)) return;
            seen.add(`keep:${label}`);
            rules.push({ label, keep: true });
            return;
        }
        const cand = root._cand && root._cand[+value];
        if (!cand) return;
        const key = `${cand.rule_key}|${cand.sub_key}`;
        if (seen.has(key)) return;
        seen.add(key);
        rules.push({ rule_key: cand.rule_key, sub_key: cand.sub_key, label: cand.label });
    });
    return rules;
}

function readForm(root, cfg) {
    const name = val(root, '#fName'), addr = cleanAddr(val(root, '#fAddr'));
    if (!name) throw new Error('请填写名称');
    if (!addr) throw new Error('请填写地址');
    const https = isOn(root, '[data-sw="https"]');
    const lucky = luckyOn(root);
    const p = {
        name, host: addr, use_https: https,
        verify_ssl: https && isOn(root, '.sw-row[data-dep="https"] .switch'),
        display_priority: parseInt(val(root, '#fRank'), 10) || 1,
        wan_traffic_only: cfg.wan_traffic_only !== false,
        lucky_frontend_host: cfg.lucky_frontend_host || '',
        traffic_collect_mode: lucky ? 'lucky' : '',
    };
    if (root.querySelector('#fKey').value) p.api_key = root.querySelector('#fKey').value;
    if (root.querySelector('#quotaPlan')) {
        p.user_quota = readQuota(root);
        if (p.user_quota.enabled && !lucky) throw new Error('启用达量停播需要先开启 Lucky 反代模式');
    }
    if (!lucky) return p;
    if (!cleanAddr(val(root, '#lAddr'))) throw new Error('请填写 Lucky 管理地址');
    p.lucky_base_url = luckyUrl(root);
    p.lucky_verify_ssl = isOn(root, '[data-sw="lhttps"]') && isOn(root, '.sw-row[data-dep="lhttps"] .switch');
    p.lucky_credit_browse_traffic = isOn(root, '[data-sw="browse"]');
    if (root.querySelector('#lToken').value) p.lucky_open_token = root.querySelector('#lToken').value;
    const rules = chosenRules(root);
    if (!rules.length) throw new Error('请选择 Lucky 反代规则');
    p.lucky_rules = rules;
    p.lucky_rule_label = rules.map(rule => rule.label).filter(Boolean).join('；');
    return p;
}

async function save(root, cfg, mode) {
    const payload = readForm(root, cfg);
    const orig = cfg.name;
    const probe = mode === 'add'
        ? await api.get(`/api/emby/config/instances/orphan-check${q({ name: payload.name })}`)
        : (payload.name !== orig ? await api.get(`/api/emby/config/instances/orphan-check${q({ name: payload.name, renaming_from: orig })}`) : null);
    if (probe && probe.has_orphaned_data) {
        const policy = await askPolicy(mode === 'add' ? 'add' : 'rename', payload.name);
        if (!policy) return;
        payload.data_policy = policy;
    }
    if (mode === 'add') await api.post('/api/emby/config/instances', payload);
    else await api.put(`/api/emby/config/instances/${enc(orig)}`, payload);
    closeSheet();
    toast(mode === 'add' ? '已添加 Emby 设备' : `已保存「${payload.name}」的设置`);
    reload();
}

/* ---------- 测试 ---------- */
async function post(root, cfg, type, extra = {}) {
    const t0 = performance.now();
    const https = isOn(root, '[data-sw="https"]');
    const body = {
        name: val(root, '#fName') || cfg.name || '', host: cleanAddr(val(root, '#fAddr')), use_https: https,
        verify_ssl: https && isOn(root, '.sw-row[data-dep="https"] .switch'),
        api_key: root.querySelector('#fKey').value, test_type: type, ...extra,
    };
    try {
        const res = await api.post('/api/emby/config/instances/test', body);
        return { res, ms: Math.round(performance.now() - t0) };
    } catch (e) {
        return { err: e.message, ms: Math.round(performance.now() - t0) };
    }
}

const luckyBody = root => ({
    lucky_base_url: luckyUrl(root), lucky_open_token: root.querySelector('#lToken').value,
    lucky_verify_ssl: isOn(root, '[data-sw="lhttps"]') && isOn(root, '.sw-row[data-dep="lhttps"] .switch'),
});

function verdict(r, okText) {
    if (r.err) return { ok: false, msg: r.err, ms: r.ms };
    const d = r.res.data || {};
    return { ok: !!r.res.success, msg: r.res.success ? (d.message || okText) : (r.res.error || d.error || '测试失败'), ms: r.ms };
}

function makeTests(cfg) {
    return {
        embyConn: async root => verdict(await post(root, cfg, 'connectivity'), '连接成功'),
        embyApi: async root => verdict(await post(root, cfg, 'api'), 'API Key 有效'),
        luckyConn: async root => {
            const r = await post(root, cfg, 'lucky_rules', luckyBody(root));
            if (r.err || !r.res.success) return verdict(r, '');
            const d = r.res.data;
            fillRules(root, d.candidates || [], d.selected || (d.matched ? [d.matched] : []));
            const added = [...root.querySelectorAll('#lRules select')].filter(sel => sel.value).length;
            return { ok: true, msg: added ? `Lucky 连接成功，已自动加入 ${added} 条反代规则` : 'Lucky 连接成功，没有探测到指向这台 Emby 的反代', ms: r.ms };
        },
        luckyFlow: async root => {
            const extra = luckyBody(root);
            const rules = chosenRules(root);
            if (!rules.length) return { ok: false, msg: '请选择 Lucky 反代规则', ms: 0 };
            extra.lucky_rules = rules;
            return verdict(await post(root, cfg, 'lucky', extra), '流量接口正常');
        },
    };
}

export async function openEmbyForm(mode, name) {
    let cfg = { traffic_collect_mode: '', lucky_credit_browse_traffic: true };
    const count = embyList().length + (mode === 'add' ? 1 : 0);
    let rank = count;
    if (mode === 'edit') {
        try { cfg = (await api.get(`/api/emby/config/instances/${enc(name)}`)).data; } catch (e) { toast(e.message, 'err'); return; }
        rank = Math.max(1, embyList().findIndex(d => d.name === name) + 1);
    }
    let quotaUsers = [];
    if (mode === 'edit') {
        try { quotaUsers = (await api.get(`/api/emby/playback-users${q({ instance: name })}`)).data || []; } catch (e) { quotaUsers = []; }
    }
    const quotaHtml = mode === 'edit'
        ? quotaPane(cfg, quotaUsers)
        : notice('', '保存设备后再设置达量停播规则');
    openSheet({
        kind: 'emby', icon: ico('play', 22),
        title: mode === 'edit' ? 'Emby 设置' : '添加 Emby 设备',
        sub: mode === 'edit' ? `<span class="online"></span>${esc(cfg.name)} · ${esc(hostOf(cfg))}` : '接入一台 Emby，按用户统计播放与选片流量',
        hint: mode === 'edit' ? '修改 Lucky 配置后，建议测试流量接口' : '带 * 的为必填项',
        saveLabel: mode === 'edit' ? '保存' : '添加设备',
        tabs: [
            { label: '基础与连接', icon: 'link', html: connectionPane(cfg, mode, rank, count) },
            { label: '流量采集', icon: 'wave', html: luckyPane(cfg) },
            { label: '达量停播规则', icon: 'shield', html: quotaHtml },
        ],
        onSave: root => save(root, cfg, mode),
        tests: makeTests(cfg),
    });
    document.getElementById('sheet')._quotaUsers = quotaUsers;
}
