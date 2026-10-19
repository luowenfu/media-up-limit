/* Emby 设备的添加 / 设置抽屉（markup 与改造计划原型一致） */

import * as api from './api.js';
import { enc, q } from './api.js';
import { ico } from './icons.js';
import { esc } from './format.js';
import { embyList } from './store.js';
import { askPolicy } from './policy.js';
import {
    closeSheet, fld, fs, inp, isOn, notice, openSheet, reload, sel, stepper, swRow, testRow, toast, val,
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

function luckyPane(cfg) {
    const on = cfg.traffic_collect_mode === 'lucky';
    const lk = splitLucky(cfg.lucky_base_url);
    const ruleOpts = cfg.lucky_rule_label ? [['keep', cfg.lucky_rule_label]] : [['', '请先完成连通性测试']];
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
                ${fld('反代规则', sel(ruleOpts, ruleOpts[0][0], 'lRule'), { full: true, hint: '连通性测试通过后自动加载 Lucky 中的反代规则' })}
            </div>
            <div class="sw-list" style="margin-top:14px">
                ${swRow({ t: '使用 HTTPS', d: 'Lucky 管理端开启了 HTTPS 时打开', name: 'lhttps', on: lk.https })}
                ${swRow({ t: '验证 SSL 证书', d: '自签名证书请保持关闭', dep: 'lhttps', disabled: !lk.https, on: lk.https && cfg.lucky_verify_ssl })}
            </div>
            ${testRow([['luckyConn', '连通性测试', 'search'], ['luckyFlow', '流量接口测试', 'wave']])}`)}
            ${fs('film', '统计口径', '', `<div class="sw-list">${swRow({ t: '「选片」流量计入统计', d: '浏览海报、详情页时产生的流量也计入用户', on: cfg.lucky_credit_browse_traffic !== false, name: 'browse' })}</div>`)}
        </div>`;
}

const luckyOn = root => root.querySelector('[data-lucky]').classList.contains('on');
const luckyUrl = root => `${isOn(root, '[data-sw="lhttps"]') ? 'https' : 'http'}://${cleanAddr(val(root, '#lAddr'))}`;

/** 把 Lucky 反代规则候选填进下拉框，并记下对应的密钥 */
function fillRules(root, cands, matched) {
    root._cand = cands;
    const box = root.querySelector('#lRule');
    box.innerHTML = cands.map((c, i) => `<option value="${i}" ${matched && matched.rule_key === c.rule_key && matched.sub_key === c.sub_key ? 'selected' : ''}>${esc(c.label)}</option>`).join('')
        || '<option value="">Lucky 中没有反代规则</option>';
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
    if (!lucky) return p;
    if (!cleanAddr(val(root, '#lAddr'))) throw new Error('请填写 Lucky 管理地址');
    p.lucky_base_url = luckyUrl(root);
    p.lucky_verify_ssl = isOn(root, '[data-sw="lhttps"]') && isOn(root, '.sw-row[data-dep="lhttps"] .switch');
    p.lucky_credit_browse_traffic = isOn(root, '[data-sw="browse"]');
    if (root.querySelector('#lToken').value) p.lucky_open_token = root.querySelector('#lToken').value;
    const pick = val(root, '#lRule');
    if (pick === 'keep') p.lucky_rule_label = cfg.lucky_rule_label;
    else if (pick !== '' && root._cand && root._cand[+pick]) {
        const c = root._cand[+pick];
        Object.assign(p, { lucky_rule_key: c.rule_key, lucky_sub_key: c.sub_key, lucky_rule_label: c.label });
    } else throw new Error('请选择 Lucky 反代规则');
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
            fillRules(root, d.candidates || [], d.matched);
            return { ok: true, msg: `Lucky 连接成功，已加载 ${(d.candidates || []).length} 条反代规则`, ms: r.ms };
        },
        luckyFlow: async root => {
            const pick = val(root, '#lRule');
            const extra = luckyBody(root);
            if (pick !== '' && pick !== 'keep' && root._cand && root._cand[+pick]) {
                extra.lucky_rule_key = root._cand[+pick].rule_key;
                extra.lucky_sub_key = root._cand[+pick].sub_key;
            }
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
    openSheet({
        kind: 'emby', icon: ico('play', 22),
        title: mode === 'edit' ? 'Emby 设置' : '添加 Emby 设备',
        sub: mode === 'edit' ? `<span class="online"></span>${esc(cfg.name)} · ${esc(hostOf(cfg))}` : '接入一台 Emby，按用户统计播放与选片流量',
        hint: mode === 'edit' ? '修改 Lucky 配置后，建议测试流量接口' : '带 * 的为必填项',
        saveLabel: mode === 'edit' ? '保存' : '添加设备',
        tabs: [
            { label: '基础与连接', icon: 'link', html: connectionPane(cfg, mode, rank, count) },
            { label: '流量采集', icon: 'wave', html: luckyPane(cfg) },
        ],
        onSave: root => save(root, cfg, mode),
        tests: makeTests(cfg),
    });
}
