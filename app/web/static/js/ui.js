/* 通用界面件：表单构件、提示、抽屉、小弹窗（与改造计划原型一致） */

import { I, ico } from './icons.js';
import { esc } from './format.js';

export const $ = s => document.querySelector(s);
export const $$ = s => [...document.querySelectorAll(s)];

/** 手机。平板和电脑不走这套；横屏后仍然很矮的也算手机。 */
const PHONE_MQ = '(max-width: 767px), (max-width: 1199px) and (orientation: landscape) and (max-height: 500px)';
export const isPhone = () => matchMedia(PHONE_MQ).matches;

/** 平板。侧栏可以收起，页面结构仍跟电脑一样。 */
const PAD_MQ = '(min-width: 768px) and (max-width: 1199px) and (min-height: 501px)';
export const isPad = () => matchMedia(PAD_MQ).matches;

/**
 * 就地更新一块 HTML。标签对得上的节点留着改，不整段拆掉。
 * 刷新时鼠标底下的按钮还是原来那个，悬停样式就不会重播一遍。
 */
export function patchHtml(host, html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html == null ? '' : html;
    patchKids(host, tpl.content);
}

const KEEP_ATTR = new Set(['data-bound']);

function patchKids(host, source) {
    const oldNodes = [...host.childNodes];
    const fresh = [...source.childNodes];
    const common = Math.min(oldNodes.length, fresh.length);
    for (let i = 0; i < common; i++) patchNode(oldNodes[i], fresh[i]);
    for (let i = common; i < fresh.length; i++) host.appendChild(fresh[i].cloneNode(true));
    for (let i = oldNodes.length - 1; i >= fresh.length; i--) oldNodes[i].remove();
}

function sameKind(a, b) {
    if (a.nodeType !== b.nodeType) return false;
    if (a.nodeType === Node.TEXT_NODE) return true;
    return a.nodeType === Node.ELEMENT_NODE && a.tagName === b.tagName;
}

function patchNode(oldNode, fresh) {
    if (!sameKind(oldNode, fresh)) {
        oldNode.replaceWith(fresh.cloneNode(true));
        return;
    }
    if (oldNode.nodeType === Node.TEXT_NODE) {
        if (oldNode.data !== fresh.data) oldNode.data = fresh.data;
        return;
    }
    const tag = oldNode.tagName;
    const focused = document.activeElement === oldNode
        && (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT');
    if (focused) return;
    patchAttrs(oldNode, fresh);
    patchKids(oldNode, fresh);
    if (tag === 'OPTION') oldNode.selected = fresh.hasAttribute('selected');
    else if (tag === 'INPUT' || tag === 'TEXTAREA') {
        const type = (oldNode.type || '').toLowerCase();
        if (type === 'checkbox' || type === 'radio') oldNode.checked = fresh.checked;
        else if (oldNode.value !== fresh.value) oldNode.value = fresh.value;
    } else if (tag === 'SELECT' && oldNode.value !== fresh.value) oldNode.value = fresh.value;
}

function patchAttrs(live, fresh) {
    const next = new Map([...fresh.attributes].map(attr => [attr.name, attr.value]));
    for (const attr of [...live.attributes]) {
        if (KEEP_ATTR.has(attr.name)) continue;
        if (!next.has(attr.name)) live.removeAttribute(attr.name);
    }
    for (const [name, value] of next) {
        if (live.getAttribute(name) !== value) live.setAttribute(name, value);
    }
}

export const COL = { qb: ['#2563eb', '#3b82f6'], emby: ['#059669', '#10b981'], sys: ['#334155', '#64748b'] };

/* ---------- 表单构件 ---------- */
export const fld = (label, inner, o = {}) => `<div class="${o.full ? 'full' : ''}">
    <label class="fl">${label}${o.req ? '<span class="req">*</span>' : ''}${o.opt ? `<span class="opt">${o.opt}</span>` : ''}</label>
    ${inner}${o.hint ? `<div class="hint-t">${o.hint}</div>` : ''}</div>`;

export const inp = (o = {}) => `<div class="inp ${o.ro ? 'ro' : ''}">${o.pre ? `<span class="pre">${ico(o.pre, 15)}</span>` : ''}<input ${o.id ? `id="${o.id}"` : ''} ${o.attr || ''} type="${o.type || 'text'}" value="${esc(o.val ?? '')}" placeholder="${esc(o.ph || '')}" ${o.ro ? 'readonly' : ''} ${o.auto ? `autocomplete="${o.auto}"` : ''}>${o.suf ? `<span class="suf">${o.suf}</span>` : ''}</div>`;

export const stepper = (val, min, max, suf, id, attr = '') => `<div class="inp stepper"><button type="button" data-step="-1">−</button><input ${id ? `id="${id}"` : ''} ${attr} type="number" value="${val}" min="${min}" max="${max}">${suf ? `<span class="suf">${suf}</span>` : ''}<button type="button" data-step="1">+</button></div>`;

export const sel = (opts, cur, id, attr = '') => `<div class="inp"><select ${id ? `id="${id}"` : ''} ${attr}>${opts.map(v => {
    const [val, label] = Array.isArray(v) ? v : [v, v];
    return `<option value="${esc(val)}" ${val === cur ? 'selected' : ''}>${esc(label)}</option>`;
}).join('')}</select></div>`;

export const segF = (opts, cur, attr = '') => `<div class="seg-f">${opts.map(v => `<button type="button" class="${v === cur ? 'on' : ''}" ${attr ? `${attr}="${v}"` : ''}>${v}</button>`).join('')}</div>`;

export const swRow = o => `<div class="sw-row ${o.disabled ? 'disabled' : ''}" ${o.dep ? `data-dep="${o.dep}"` : ''}>
    <div class="t"><b>${o.t}</b>${o.d ? `<small>${o.d}</small>` : ''}</div><span class="switch ${o.on ? 'on' : ''}" ${o.name ? `data-sw="${o.name}"` : ''}></span></div>`;

export const fs = (icon, t, s, body, r = '') => `<section class="fs"><div class="fs-head"><span class="ic">${ico(icon, 16)}</span><div><b>${t}</b>${s ? `<small>${s}</small>` : ''}</div>${r ? `<div class="r">${r}</div>` : ''}</div>${body}</section>`;

export const notice = (type, html, icon = 'info') => `<div class="notice ${type}">${ico(icon, 17)}<div>${html}</div></div>`;

export const testRow = list => `<div class="test-row">${list.map(([k, label, icon]) => `<button type="button" class="btn sm" data-test="${k}">${ico(icon, 15)}${label}</button>`).join('')}<div class="test-res"></div></div>`;

export const mTop = (cls, icon, title, p) => `<div class="m-top"><span class="m-ico ${cls}">${ico(icon, 22)}</span><div style="flex:1;min-width:0"><h3>${title}</h3><p>${p}</p></div><button type="button" class="x-btn" data-close aria-label="关闭">${I.x}</button></div>`;

export const chkBox = (label, color, id) => `<div class="chk" id="${id}" style="--chk:${color}"><span class="box">${ico('check', 12)}</span>${label}</div>`;

/* ---------- 读取表单值 ---------- */
export const val = (root, sel_) => (root.querySelector(sel_)?.value ?? '').trim();
export const isOn = (root, sel_) => !!root.querySelector(sel_)?.classList.contains('on');

/** 表单校验失败：带上出错的输入框，保存处用来标红 */
export function fieldError(el, message) {
    const err = new Error(message);
    err.field = el || true;
    return err;
}

/** 标红一个输入框，并滚到它。开始修改后由输入事件去掉 */
export function markField(el) {
    document.querySelectorAll('.inp.bad').forEach(node => node.classList.remove('bad'));
    if (!el || typeof el.closest !== 'function' || !el.isConnected) return;
    const box = el.classList.contains('inp') ? el : el.closest('.inp');
    if (!box) return;
    const pane = box.closest('.sheet-pane');
    if (pane && !pane.classList.contains('on')) {
        const sheet = pane.closest('#sheet');
        sheet?.querySelectorAll('.sheet-pane').forEach(p => p.classList.toggle('on', p === pane));
        sheet?.querySelectorAll('[data-stab]').forEach(b => b.classList.toggle('on', b.dataset.stab === pane.dataset.pane));
    }
    box.classList.add('bad');
    const focus = box.querySelector('input, select, textarea');
    if (!focus) return;
    focus.focus({ preventScroll: true });
    box.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

/* ---------- 提示 ---------- */
export function toast(msg, type = 'ok') {
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.innerHTML = `<span class="ti">${ico(type === 'ok' ? 'check' : type === 'err' ? 'warn' : 'info', 13)}</span>${esc(msg)}`;
    $('#toasts').appendChild(el);
    setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 220); }, type === 'ok' ? 2400 : 3600);
}

/** 按钮进入忙碌态，异步任务结束后恢复 */
export async function busy(btn, label, task) {
    const html = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spin"></span>${label}`;
    try {
        return await task();
    } finally {
        if (btn.isConnected) { btn.disabled = false; btn.innerHTML = html; }
    }
}

/** 通知主程序重新取数并刷新页面 */
export const reload = (full = true) => document.dispatchEvent(new CustomEvent('app:reload', { detail: { full } }));

/* ---------- 侧栏抽屉（移动端菜单）---------- */
export const closeDrawer = () => document.body.classList.remove('drawer');

/* ---------- 设置抽屉 ---------- */
export function openSheet(o) {
    const s = $('#sheet');
    s.style.setProperty('--sheet-accent', COL[o.kind][0]);
    s.style.setProperty('--sheet-accent-2', COL[o.kind][1]);
    s.innerHTML = `
    <div class="sheet-head">
        <div class="sheet-ico ${o.kind}">${o.icon}</div>
        <div class="sheet-title"><h3>${o.title}</h3>${o.sub ? `<small>${o.sub}</small>` : ''}</div>
        <button type="button" class="x-btn" data-sheet-close aria-label="关闭">${I.x}</button>
    </div>
    <div class="sheet-tabs">${o.tabs.map((t, k) => `<button type="button" class="${k ? '' : 'on'}" data-stab="${k}">${ico(t.icon, 15)}${t.label}</button>`).join('')}</div>
    <div class="sheet-body">${o.tabs.map((t, k) => `<div class="sheet-pane ${k ? '' : 'on'}" data-pane="${k}">${t.html}</div>`).join('')}</div>
    <div class="sheet-foot">
        ${o.hint ? `<span class="l">${ico('info', 15)}${o.hint}</span>` : '<span class="l"></span>'}
        <button type="button" class="btn primary" data-save="1">${ico('check', 15)}${o.saveLabel || '保存'}</button>
        <button type="button" class="btn" data-sheet-close>取消</button>
    </div>`;
    s._onSave = o.onSave;
    s._tests = o.tests || {};
    s.oninput = null;
    s.onclick = null;
    closeDrawer();
    requestAnimationFrame(() => { s.classList.add('open'); $('#sheetMask').classList.add('open'); });
}

export function closeSheet() {
    $('#sheet').classList.remove('open');
    $('#sheetMask').classList.remove('open');
}

/* ---------- 小弹窗 ---------- */
export function openSmall(html, wide = false) {
    const box = $('#modalBox');
    box.className = 'modal sm' + (wide ? ' wide' : '');
    box.innerHTML = html;
    closeDrawer();
    $('#modal').classList.add('open');
}

export const closeModal = () => $('#modal').classList.remove('open');
