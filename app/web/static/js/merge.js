/* 合并视图的设备选择与拖动排序（markup 与改造计划原型一致） */

import { GRIP, ico } from './icons.js';
import { esc, maskIp } from './format.js';
import { state, qbList, embyList, savePrefs } from './store.js';
import {
    $, $$, closeModal, mTop, openSmall, toast,
} from './ui.js';

const hostText = d => `${maskIp(d.raw.host)}:${d.raw.port}`;

export function openMergeEditor() {
    const src = { qb: qbList(), emby: embyList() };
    const col = (side, title) => `<div class="me-col" data-side="${side}">
        <div class="me-head"><span class="dot"></span><b>${title}</b><small class="me-cnt"></small><button type="button" class="me-all" data-me-all>全选</button></div>
        <div class="me-list">${state.merge[side].map(o => {
        const d = src[side].find(x => x.name === o.name);
        return d ? `
            <div class="me-item ${o.on ? 'on' : ''}" data-n="${esc(o.name)}">
                <span class="box">${ico('check', 12)}</span>
                <span class="me-ic ${side}">${side === 'qb' ? 'qB' : ico('play', 15)}</span>
                <div class="me-t"><b>${esc(d.name)}</b><small>${esc(hostText(d))}</small></div>
                <span class="me-drag" title="拖动调整顺序">${GRIP}</span>
            </div>` : '';
    }).join('')}
        </div>
    </div>`;
    openSmall(`${mTop('blue', 'rules', '合并显示', '勾选要在合并视图中显示的设备，拖动右侧把手调整顺序')}
    <div class="m-body"><div class="me-cols">${col('qb', 'qB 设备')}${col('emby', 'Emby 设备')}</div></div>
    <div class="m-foot"><span class="me-err" id="meErr"></span><button type="button" class="btn" data-close>取消</button><button type="button" class="btn primary" style="--sheet-accent:#2563eb;--sheet-accent-2:#3b82f6" id="meGo">${ico('check', 15)}保存并应用</button></div>`);
    $('#modalBox').classList.add('wide');
    $$('.me-list').forEach(setupDrag);
    refreshMergeEditor();
}

export function refreshMergeEditor() {
    const cnt = {};
    $$('.me-col').forEach(c => {
        const items = c.querySelectorAll('.me-item'), on = c.querySelectorAll('.me-item.on').length;
        cnt[c.dataset.side] = on;
        c.querySelector('.me-cnt').textContent = `已选 ${on}/${items.length}`;
        c.querySelector('[data-me-all]').textContent = on === items.length ? '取消全选' : '全选';
    });
    const err = !cnt.qb && !cnt.emby ? '请分别在两侧至少选择一个设备' : !cnt.qb ? '请至少选择一个 qB 设备' : !cnt.emby ? '请至少选择一个 Emby 设备' : '';
    $('#meErr').innerHTML = err ? `${ico('warn', 15)}${err}` : '';
    $('#meGo').disabled = !!err;
}

export function toggleAll(btn) {
    const items = btn.closest('.me-col').querySelectorAll('.me-item');
    const all = [...items].every(x => x.classList.contains('on'));
    items.forEach(x => x.classList.toggle('on', !all));
    refreshMergeEditor();
}

/** 保存选择与顺序：写回本地状态，并存到登录账号的偏好里 */
export async function saveMerge(render) {
    const patch = {};
    $$('.me-col').forEach(c => {
        const side = c.dataset.side;
        const rows = [...c.querySelectorAll('.me-item')].map(x => ({ name: x.dataset.n, on: x.classList.contains('on') }));
        state.merge[side] = rows;
        patch[`merge_${side}_order`] = rows.map(r => r.name);
        patch[`merge_${side}_selection`] = rows.filter(r => r.on).map(r => r.name);
    });
    closeModal();
    render();
    toast('已保存合并显示的设备与顺序');
    await savePrefs(patch);
}

function setupDrag(list) {
    let dragging = null;
    const move = e => {
        if (!dragging) return;
        e.preventDefault();
        const next = [...list.querySelectorAll('.me-item')].filter(x => x !== dragging)
            .find(x => { const r = x.getBoundingClientRect(); return e.clientY < r.top + r.height / 2; });
        if (next) list.insertBefore(dragging, next); else list.appendChild(dragging);
    };
    const end = () => {
        if (!dragging) return;
        dragging.classList.remove('dragging');
        list.classList.remove('drag-active');
        dragging = null;
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', end);
        document.removeEventListener('pointercancel', end);
    };
    list.addEventListener('pointerdown', e => {
        const h = e.target.closest('.me-drag');
        if (!h) return;
        e.preventDefault();
        dragging = h.closest('.me-item');
        dragging.classList.add('dragging');
        list.classList.add('drag-active');
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', end);
        document.addEventListener('pointercancel', end);
    });
}
