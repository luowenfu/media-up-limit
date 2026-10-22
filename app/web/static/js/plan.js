/* qB 周期计划编辑块（当前周期 / 下周期共用，markup 同改造计划原型） */

import { ico } from './icons.js';
import { fieldError, fld, inp, sel, segF, stepper } from './ui.js';

const CYC_LABEL = { month: '按月', week: '按周', day: '按天' };
const CYC_TYPE = { 按月: 'month', 按周: 'week', 按天: 'day' };
const WEEK = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

export const anchorHtml = (label, value) => {
    if (label === '按月') {
        return fld('每月恢复日', stepper(value || 1, 1, 28, '日', '', 'data-anchor'), { hint: '最多 28 日，避免月份天数差异' });
    }
    if (label === '按周') {
        return fld('每周恢复日', sel(WEEK.map((w, i) => [String(i + 1), w]), String(value || 1), '', 'data-anchor'), { hint: '当天 00:00 开始新周期' });
    }
    const hours = Array.from({ length: 24 }, (_, h) => [String(h), String(h).padStart(2, '0') + ':00']);
    return fld('每天恢复时间', sel(hours, String(value || 0), '', 'data-anchor'), { hint: '到点后开始新周期' });
};

const SPEED_MAX = 2097151;

/** 限速：空着当 0（不限速）。必须是整数 KB/s。el 是出错时要标红的输入框。 */
export function readSpeedKbps(raw, el) {
    const text = String(raw ?? '').trim();
    if (text === '') return 0;
    const n = Number(text);
    if (!Number.isFinite(n)) throw fieldError(el, '请填写限速');
    if (!Number.isInteger(n)) throw fieldError(el, '限速最小单位是 1 KB/s');
    if (n < 0 || n > SPEED_MAX) throw fieldError(el, `限速须在 0–${SPEED_MAX} 之间（0 表示不限速）`);
    return n;
}

function readThresholdGb(raw, el) {
    const text = String(raw ?? '').trim();
    if (text === '') throw fieldError(el, '请填写阈值');
    const n = Number(text);
    if (!Number.isFinite(n)) throw fieldError(el, '请填写阈值');
    if (!Number.isInteger(n) || n < 1) throw fieldError(el, '阈值最小单位是 1 GB');
    return n;
}

export const ruleRow = (r, n) => `<div class="rule-ed">
    <span class="n">${n}</span>
    <div class="inp"><span class="k">阈值</span><input type="number" min="1" step="1" value="${r.th ?? ''}" placeholder="如 300" data-f="th"><span class="suf">GB</span></div>
    <div class="inp"><span class="k">限速至</span><input type="number" min="0" step="1" value="${r.sp ?? ''}" placeholder="0 表示不限速" data-f="sp"><span class="suf">KB/s</span></div>
    <button type="button" class="rule-del" title="删除规则">${ico('del', 15)}</button>
</div>`;

/** 记下已保存周期类型的恢复日，切换类型后再切回来时还原 */
export const anchorsAttr = (label, anchor) => `data-anchors='${JSON.stringify({ [label]: anchor })}'`;

/** 切换周期类型：先记下旧类型当前填的值，再用新类型记过的值（没有就用默认）重画恢复日 */
export function switchCycleType(plan, prevLabel, label) {
    const box = plan.querySelector('.anchor');
    let memo = {};
    try { memo = JSON.parse(plan.dataset.anchors || '{}'); } catch (e) { memo = {}; }
    const cur = box.querySelector('[data-anchor]');
    if (prevLabel && cur) memo[prevLabel] = cur.value;
    plan.dataset.anchors = JSON.stringify(memo);
    box.innerHTML = anchorHtml(label, memo[label]);
}

export function scaleHtml(ths, used) {
    const vals = ths.filter(v => v > 0);
    if (!vals.length) return '<div class="hint-t" style="padding-top:12px">填写阈值后，这里会预览各规则在周期用量上的位置</div>';
    const max = Math.max(...vals, used || 0) * 1.12;
    return `<div class="scale-track"></div>
        ${vals.map(v => `<div class="scale-mk" style="left:${v / max * 100}%"><i></i><span>${v}G</span></div>`).join('')}
        ${used ? `<div class="scale-now" style="left:${used / max * 100}%"><span>已用 ${used} GB</span><i></i></div>` : ''}`;
}

export function refreshScale(plan) {
    const box = plan.querySelector('.scale');
    if (!box) return;
    const ths = [...plan.querySelectorAll('[data-f="th"]')].map(x => parseFloat(x.value) || 0);
    box.innerHTML = scaleHtml(ths, +box.dataset.used || 0);
}

/** o: { next, id, hidden, sub, used, cycle:{type,reset_anchor,reset_limit_kbps}, rules:[{th,sp}] } */
export const planHtml = o => {
    const c = o.cycle || { type: 'month', reset_anchor: 1, reset_limit_kbps: 0 };
    const label = CYC_LABEL[c.type] || '按月';
    const rules = o.rules && o.rules.length ? o.rules : [{ th: '', sp: '' }];
    return `<div class="plan" ${o.id ? `id="${o.id}"` : ''} ${anchorsAttr(label, c.reset_anchor)} ${o.hidden ? 'style="display:none"' : ''}>
    <div class="plan-head"><b>${o.next ? '下周期计划' : '当前周期计划'}</b><small>${o.sub}</small>
        ${o.next ? '<span class="r"><button type="button" class="link-danger" data-next-del>移除</button></span>' : ''}</div>
    <div class="f-grid">
        ${fld('周期类型', segF(['按月', '按周', '按天'], label, 'data-cyc'), { full: true })}
        <div class="anchor">${anchorHtml(label, c.reset_anchor)}</div>
        ${fld('周期恢复后限速至', inp({ val: c.reset_limit_kbps || 0, type: 'number', suf: 'KB/s', attr: 'data-reset min="0" step="1"' }), { hint: '0 表示不限速，最小单位 1 KB/s' })}
    </div>
    <div class="rules-head"><b>规则</b><small>周期内上传达到阈值后，按对应速度限速</small></div>
    ${o.next ? '' : `<div class="scale" data-used="${o.used || 0}">${scaleHtml(rules.map(r => +r.th || 0), o.used)}</div>`}
    <div class="rules-list">${rules.map((r, n) => ruleRow(r, n + 1)).join('')}</div>
    <button type="button" class="add-rule" data-rule-add>${ico('plus', 15)}添加规则</button>
</div>`;
};

/** 从界面读取一个计划块，格式同后端 cycle / speed_rules */
export function readPlan(plan) {
    const label = plan.querySelector('[data-cyc].on')?.dataset.cyc || '按月';
    const anchor = parseInt(plan.querySelector('[data-anchor]')?.value, 10);
    const resetEl = plan.querySelector('[data-reset]');
    const rows = [...plan.querySelectorAll('.rule-ed')];
    const cycle = {
        type: CYC_TYPE[label],
        reset_anchor: Number.isNaN(anchor) ? 1 : anchor,
        reset_limit_kbps: readSpeedKbps(resetEl?.value, resetEl),
    };
    const speed_rules = rows.map(row => {
        const th = row.querySelector('[data-f="th"]');
        const sp = row.querySelector('[data-f="sp"]');
        return {
            cycle_upload_limit_gb: readThresholdGb(th?.value, th),
            speed_limit_kbps: readSpeedKbps(sp?.value, sp),
        };
    });
    for (let i = 1; i < speed_rules.length; i++) {
        if (speed_rules[i].cycle_upload_limit_gb <= speed_rules[i - 1].cycle_upload_limit_gb) {
            throw fieldError(rows[i].querySelector('[data-f="th"]'), `规则 ${i + 1} 的上行阈值须大于规则 ${i}`);
        }
    }
    return { cycle, speed_rules };
}
