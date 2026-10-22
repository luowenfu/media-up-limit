/* Emby 设备的添加 / 设置抽屉（markup 与改造计划原型一致） */

import * as api from './api.js';
import { enc, q } from './api.js';
import { ico } from './icons.js';
import { esc } from './format.js';
import { embyList } from './store.js';
import { askPolicy } from './policy.js';
import { anchorHtml, anchorsAttr, readPlan } from './plan.js';
import {
    closeSheet, fld, fs, inp, isOn, notice, openSheet, reload, sel, segF, stepper, swRow, testRow, toast, val,
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
const QUOTA_MSG_MAX = 60;
const QUOTA_MSG_CUSTOM = '__custom__';
const WEEK_LABEL = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
// 模板要和 emby/quota/message.py 的 PRESETS 一致，{when} 在发送时按周期重算
const QUOTA_PRESETS = [
    ['preset:1', '本期流量已用完，{when}恢复，播放将在 5 秒后停止'],
    ['preset:2', '流量额度已用完，5 秒后停止播放，请{when}再看'],
    ['preset:3', '服务器流量已达上限，请{when}再看'],
];
const OLD_QUOTA_PRESETS = {
    '本周期上行流量已达额度，播放将在 5 秒后停止': 'preset:1',
    '流量额度已用完，5 秒后将停止播放，请下个周期再看': 'preset:2',
    '服务器流量已达上限，播放即将停止': 'preset:3',
};

function sameIsoWeek(a, b) {
    const key = d => {
        const utc = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
        const day = utc.getUTCDay() || 7;
        utc.setUTCDate(utc.getUTCDate() + 4 - day);
        const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
        const week = Math.ceil((((utc - yearStart) / 86400000) + 1) / 7);
        return `${utc.getUTCFullYear()}-${week}`;
    };
    return key(a) === key(b);
}

/** 下一周期起点。口径同 core.cycle.format_resume_when */
function resumeWhen(cycle, now = new Date()) {
    const type = cycle.type || 'month';
    let anchor = parseInt(cycle.reset_anchor, 10);
    if (type === 'week') anchor = Math.min(7, Math.max(1, anchor || 1));
    else if (type === 'day') anchor = Math.min(23, Math.max(0, Number.isNaN(anchor) ? 0 : anchor));
    else anchor = Math.min(28, Math.max(1, anchor || 1));
    const end = nextCycleStart(now, type, anchor);
    if (type === 'day') {
        const today = end.getFullYear() === now.getFullYear()
            && end.getMonth() === now.getMonth() && end.getDate() === now.getDate();
        return `${today ? '今日' : '明日'}${end.getHours()}点`;
    }
    if (type === 'week') return `${sameIsoWeek(now, end) ? '本' : '下'}${WEEK_LABEL[anchor - 1]}`;
    const thisMonth = end.getFullYear() === now.getFullYear() && end.getMonth() === now.getMonth();
    return `${thisMonth ? '本月' : '下月'}${end.getDate()}日`;
}

function nextCycleStart(now, type, anchor) {
    if (type === 'month') {
        let y = now.getFullYear();
        let m = now.getMonth();
        if (now.getDate() < anchor) {
            m -= 1;
            if (m < 0) { m = 11; y -= 1; }
        }
        const end = new Date(y, m + 1, anchor);
        return end;
    }
    if (type === 'week') {
        const wd = (now.getDay() + 6) % 7;
        const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((wd - (anchor - 1) + 7) % 7));
        return new Date(start.getFullYear(), start.getMonth(), start.getDate() + 7);
    }
    const candidate = new Date(now.getFullYear(), now.getMonth(), now.getDate(), anchor);
    const start = now >= candidate
        ? candidate
        : new Date(candidate.getFullYear(), candidate.getMonth(), candidate.getDate() - 1, anchor);
    return new Date(start.getFullYear(), start.getMonth(), start.getDate() + 1, anchor);
}

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
        <div class="inp" style="flex:0 0 150px"><input type="number" min="0" step="any" data-q-gb value="${esc(row.limit_gb ?? '')}" placeholder="0 为不限"><span class="suf">GB</span></div>
        <button type="button" class="rule-x" data-quota-del aria-label="移除这个用户">${ico('x', 14)}</button>
    </div>`;
}

function quotaPane(cfg, users) {
    const qt = cfg.user_quota || {};
    const cyc = qt.cycle || { type: 'month', reset_anchor: 1 };
    const label = CYC_LABEL[cyc.type] || '按月';
    const rows = (qt.users && qt.users.length ? qt.users : [{}]).map(r => quotaRow(users, r)).join('');
    const isTotal = qt.mode === 'total';
    const msgText = String(qt.message_text || '');
    const stored = OLD_QUOTA_PRESETS[msgText] || msgText;
    const msgSel = QUOTA_PRESETS.some(([id]) => id === stored) || !stored ? (stored || 'preset:1') : QUOTA_MSG_CUSTOM;
    const whenNow = resumeWhen(cyc);
    const msgOptions = QUOTA_PRESETS
        .map(([id, tpl]) => [id, tpl.replaceAll('{when}', whenNow)])
        .concat([[QUOTA_MSG_CUSTOM, '自定义']]);
    return notice('', '<b>达量停播：</b>利用 Emby 官方接口控制播放会话；额度用完后再开始播放时将收到提示，5 秒后停止。需要开启「流量采集」里的 Lucky 反代模式，局域网播放不受影响。')
        + fs('shield', '达量停播', '', `<div class="sw-list">
            ${swRow({ t: '启用达量停播', d: '总开关', on: !!qt.enabled, name: 'qEnable' })}
            ${swRow({ t: '只记录不执行', d: '到点只写系统日志，不发提示也不停止。确认用量准确后再关闭', on: qt.dry_run !== false, name: 'qDry' })}
        </div>`)
        + fs('clock', '统计周期', '', `<div class="plan" id="quotaPlan" ${anchorsAttr(label, cyc.reset_anchor)}><div class="f-grid">
            ${fld('周期类型', segF(['按月', '按周', '按天'], label, 'data-cyc'), { full: true })}
            <div class="anchor">${anchorHtml(label, cyc.reset_anchor)}</div>
        </div></div>
        <div class="f-grid q-msg">
            ${fld(`<span class="pre-ic">${ico('msg', 14)}</span>提示文案`, sel(msgOptions, msgSel, 'qMsgSel'), { full: true })}
            <div class="full" id="qMsgBox" style="${msgSel === QUOTA_MSG_CUSTOM ? '' : 'display:none'}">
                ${inp({ id: 'qMsgText', val: msgSel === QUOTA_MSG_CUSTOM ? msgText : '', ph: `最多 ${QUOTA_MSG_MAX} 个字` })}
                <div class="hint-t" id="qMsgHint">${quotaWhenHint(whenNow)}</div>
            </div>
        </div>`)
        + fs('sum', '额度设置', '', `${segF(['按总额度', '按用户'], isTotal ? '按总额度' : '按用户', 'data-qmode')}
            <div class="sub-panel" id="qTotalPane" style="${isTotal ? '' : 'display:none'}">
                ${fld('总额度', inp({ id: 'qTotal', type: 'number', val: qt.total_limit_gb > 0 ? qt.total_limit_gb : '', ph: '所有用户合计', suf: 'GB', attr: 'min="0" step="any"' }), { hint: '所有用户本周期上行合计达到后，之后开播的每个用户都会被停播' })}
            </div>
            <div class="sub-panel" id="qUserPane" style="${isTotal ? 'display:none' : ''}">
                <div class="sw-list"><div class="sw-row">
                    <div class="t"><b>全部用户</b><small>每位用户各自累计，用同一个额度</small></div>
                    <div class="inp" id="qDefaultBox" style="flex:0 0 150px"><input id="qDefault" type="number" min="0" step="any" value="${esc(qt.default_limit_gb > 0 ? qt.default_limit_gb : '')}" placeholder="每人额度"><span class="suf">GB</span></div>
                </div></div>
                <div id="qList">
                    <div class="line-open"></div>
                    <div class="q-head">
                        <div class="t"><b>用户独立额度</b><small>优先级高于全部用户</small></div>
                        <button type="button" class="rule-add" data-quota-add aria-label="添加用户">${ico('plus', 14)}</button>
                    </div>
                    <div class="rule-rows" id="qRows">${rows}</div>
                    <div class="hint-t">额度填 0 表示不限，例如管理员</div>
                </div>
            </div>
            <div class="hint-t">1 GB = 1000 MB。用量归属可能有些误差，额度请留余量；选片流量是否计入见「流量采集」</div>`);
}

/** 按当前选择显示 / 收起额度规则的各块 */
export function refreshQuotaView(root = document.getElementById('sheet')) {
    if (!root || !root.querySelector('#quotaPlan')) return;
    const total = root.querySelector('[data-qmode].on')?.dataset.qmode === '按总额度';
    const show = (sel_, on) => { root.querySelector(sel_).style.display = on ? '' : 'none'; };
    show('#qTotalPane', total);
    show('#qUserPane', !total);
}

/** 自定义文案下方的提示：变量和当前会换成的时间都带底色 */
function quotaWhenHint(when) {
    return `<code>{when}</code>为时间变量，将换成：<code>${esc(when)}</code>`;
}

/** 周期类型或恢复时间变了，下拉里的三句通用文案换成当时的恢复时间 */
export function refreshQuotaMessages(root) {
    root = root || document.getElementById('sheet');
    const select = root?.querySelector('#qMsgSel');
    const plan = root?.querySelector('#quotaPlan');
    if (!select || !plan) return;
    const when = resumeWhen(readPlan(plan).cycle);
    for (const opt of select.options) {
        const preset = QUOTA_PRESETS.find(([id]) => id === opt.value);
        if (preset) opt.text = preset[1].replaceAll('{when}', when);
    }
    const hint = root.querySelector('#qMsgHint');
    if (hint) hint.innerHTML = quotaWhenHint(when);
}

/** 提示文案下拉变化：只有「自定义」才显示输入框 */
export function onQuotaMsgChange(select) {
    const root = select.closest('#sheet') || document.getElementById('sheet');
    const box = root?.querySelector('#qMsgBox');
    if (box) box.style.display = select.value === QUOTA_MSG_CUSTOM ? '' : 'none';
    refreshQuotaMessages(root);
}

function readQuota(root) {
    const rows = [...root.querySelectorAll('#qRows [data-quota-row]')].map(row => ({
        user_name: row.querySelector('[data-q-user]').value,
        limit_gb: row.querySelector('[data-q-gb]').value,
    })).filter(r => r.user_name || String(r.limit_gb).trim());
    const total = root.querySelector('[data-qmode].on')?.dataset.qmode === '按总额度';
    const enabled = isOn(root, '[data-sw="qEnable"]');
    if (!total) {
        rows.forEach(r => {
            if (!r.user_name) throw new Error('请为每个额度选择用户');
            if (!(parseFloat(r.limit_gb) >= 0)) throw new Error(`请填写用户「${r.user_name}」的额度（0 表示不限）`);
        });
    }
    const users = rows
        .filter(r => r.user_name && parseFloat(r.limit_gb) >= 0)
        .map(r => ({ user_name: r.user_name, limit_gb: parseFloat(r.limit_gb) }));
    const totalGb = parseFloat(val(root, '#qTotal')) || 0;
    const defaultGb = parseFloat(val(root, '#qDefault')) || 0;
    if (enabled) {
        if (total && !(totalGb > 0)) throw new Error('启用达量停播前，请设置总额度');
        if (!total && !(defaultGb > 0 || users.some(u => u.limit_gb > 0))) {
            throw new Error('启用达量停播前，请设置「全部用户」额度，或给某个用户设置额度');
        }
    }
    const sel_ = val(root, '#qMsgSel');
    const message = sel_ === QUOTA_MSG_CUSTOM ? val(root, '#qMsgText') : sel_;
    if (sel_ === QUOTA_MSG_CUSTOM && !message) throw new Error('请填写自定义提示文案');
    if (sel_ === QUOTA_MSG_CUSTOM && message.length > QUOTA_MSG_MAX) throw new Error(`提示文案最多 ${QUOTA_MSG_MAX} 个字`);
    return {
        enabled,
        dry_run: isOn(root, '[data-sw="qDry"]'),
        cycle: readPlan(root.querySelector('#quotaPlan')).cycle,
        message_text: message,
        mode: total ? 'total' : 'user',
        total_limit_gb: totalGb,
        all_users: false,
        default_limit_gb: defaultGb,
        users,
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
