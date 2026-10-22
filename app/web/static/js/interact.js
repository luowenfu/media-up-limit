/* 点击交互：设备卡片按钮、抽屉表单、小弹窗（行为与改造计划原型一致，接真实接口） */

import * as api from './api.js';
import { enc } from './api.js';
import { ico } from './icons.js';
import { esc } from './format.js';
import { deviceUrl } from './adapt.js';
import { state, qbByName, embyByName } from './store.js';
import { openQbForm } from './qbform.js';
import {
    addLuckyRuleRow, addQuotaRow, onQuotaMsgChange, openEmbyForm, refreshQuotaMessages, refreshQuotaView,
    removeLuckyRuleRow, removeQuotaRow,
} from './embyform.js';
import { refreshScale, ruleRow, switchCycleType } from './plan.js';
import { openMergeEditor, refreshMergeEditor, saveMerge, toggleAll } from './merge.js';
import {
    applyDelete, applyLimit, applyReset, openDelete, openLimit, openReset, openSessionMessage, refreshLimit, sendSessionMessage, switchLimitMode,
} from './dialogs.js';
import {
    $, $$, busy, closeModal, closeSheet, markField, reload, toast,
} from './ui.js';

/* ---------- 流量块的上传 / 下载显隐 ---------- */
export function setTilesHide(box, hide) {
    box.classList.remove('hide-u', 'hide-d');
    box.style.minHeight = '';
    if (!hide) return;
    box.style.minHeight = box.offsetHeight + 'px';
    box.classList.add('hide-' + hide);
}
export const relockTiles = () => $$('[data-tiles]').forEach(box => setTilesHide(box, state.hide[box.dataset.tiles] || ''));

function legendClick(t) {
    const key = t.closest('[data-lg]').dataset.lg, k = t.dataset.lgk, cur = state.hide[key] || '';
    if (cur && cur !== k) return;
    const next = cur === k ? '' : k;
    state.hide[key] = next;
    setTilesHide(document.querySelector(`[data-tiles="${key}"]`), next);
    t.parentElement.querySelectorAll('.lg-btn').forEach(b => {
        const off = b.dataset.lgk === next;
        b.classList.toggle('off', off);
        b.title = (off ? '显示' : '隐藏') + (b.dataset.lgk === 'u' ? '上传' : '下载');
    });
}

/* ---------- 设备卡片底部按钮 ---------- */
function cardAction(t) {
    const foot = t.closest('.dev-foot'), kind = foot.dataset.kind, name = foot.dataset.name;
    const d = kind === 'qb' ? qbByName(name) : embyByName(name);
    const act = t.dataset.act;
    if (act === 'web' && d) window.open(deviceUrl(d.raw), '_blank', 'noopener');
    if (act === 'set') (kind === 'qb' ? openQbForm : openEmbyForm)('edit', name);
    if (act === 'lim') openLimit(name);
    if (act === 'rst') openReset(kind, name);
    if (act === 'del') openDelete(kind, name);
}

async function sessionCommand(t) {
    const { sess, sid, dev } = t.dataset;
    if (sess === 'message') { openSessionMessage(dev, sid); return; }
    try {
        await api.post(`/api/emby/sessions/${enc(dev)}/${enc(sid)}/playing/${sess}`);
        toast(sess === 'stop' ? '已停止播放' : sess === 'pause' ? '已暂停' : '已继续播放');
        reload(false);
    } catch (e) { toast(e.message, 'err'); }
}

/* ---------- 抽屉里的测试按钮 ---------- */
function runTest(t) {
    const sheet = $('#sheet'), fn = sheet._tests && sheet._tests[t.dataset.test];
    const res = t.parentElement.querySelector('.test-res');
    if (!fn) return;
    res.className = 'test-res';
    busy(t, '测试中…', async () => {
        const r = await fn(sheet);
        res.className = `test-res ${r.ok ? 'ok' : 'err'} show`;
        res.innerHTML = `${ico(r.ok ? 'shield' : 'warn', 17)}<span>${esc(r.msg)}</span><span class="r">${r.ms} ms</span>`;
    });
}

function saveSheet(t) {
    const sheet = $('#sheet');
    busy(t, '保存中…', async () => {
        try { await sheet._onSave(sheet); } catch (e) {
            if (e.field) markField(e.field);
            toast(e.message, 'err');
        }
    });
}

/* ---------- 抽屉里的表单控件 ---------- */
function formControl(t) {
    if (t.dataset.step) {
        const input = t.closest('.stepper').querySelector('input');
        input.value = Math.min(+input.max, Math.max(+input.min, (+input.value || 0) + +t.dataset.step));
        input.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
    }
    if (t.closest('.seg-f')) {
        const prevCyc = t.dataset.cyc && t.parentElement.querySelector('button.on')?.dataset.cyc;
        t.parentElement.querySelectorAll('button').forEach(b => b.classList.toggle('on', b === t));
        if (t.dataset.msgStyle) {
            const hint = $('#msgStyleHint');
            if (hint) hint.textContent = t.dataset.msgStyle === 'dialog' ? '持续显示，用户需自行关闭' : '短暂显示，3秒后自动消失';
        }
        if (t.dataset.cyc) {
            switchCycleType(t.closest('.plan'), prevCyc, t.dataset.cyc);
            if (t.closest('#quotaPlan')) refreshQuotaMessages();
        }
        if (t.dataset.qmode) refreshQuotaView();
        return true;
    }
    if (t.matches('.switch') && !t.closest('[data-lucky]')) {
        t.classList.toggle('on');
        const name = t.dataset.sw;
        const dep = name && t.closest('.sw-list').querySelector(`[data-dep="${name}"]`);
        if (dep) {
            dep.classList.toggle('disabled', !t.classList.contains('on'));
            if (!t.classList.contains('on')) dep.querySelector('.switch').classList.remove('on');
        }
        if (name === 'browse') {
            const box = t.closest('#sheet')?.querySelector('#browseMin');
            if (box) box.hidden = !t.classList.contains('on');
        }
        return true;
    }
    if (t.hasAttribute('data-lucky-add')) { addLuckyRuleRow(); return true; }
    if (t.hasAttribute('data-lucky-del')) { removeLuckyRuleRow(t); return true; }
    if (t.hasAttribute('data-quota-add')) { addQuotaRow(); return true; }
    if (t.hasAttribute('data-quota-del')) { removeQuotaRow(t); return true; }
    const lucky = t.closest('[data-lucky]');
    if (lucky) {
        const on = !lucky.classList.contains('on');
        lucky.classList.toggle('on', on);
        lucky.querySelector('.switch').classList.toggle('on', on);
        $('#luckyOn').style.display = on ? '' : 'none';
        $('#luckyOff').style.display = on ? 'none' : '';
        return true;
    }
    return false;
}

function planControl(t) {
    if (t.hasAttribute('data-rule-add')) {
        const plan = t.closest('.plan'), list = plan.querySelector('.rules-list');
        list.insertAdjacentHTML('beforeend', ruleRow({ th: '', sp: '' }, list.children.length + 1));
        list.lastElementChild.querySelector('input').focus();
        refreshScale(plan);
        return true;
    }
    if (t.matches('.rule-del')) {
        const plan = t.closest('.plan'), list = plan.querySelector('.rules-list');
        if (list.children.length === 1) { toast('至少保留一条规则', 'info'); return true; }
        t.closest('.rule-ed').remove();
        [...list.children].forEach((r, k) => { r.querySelector('.n').textContent = k + 1; });
        refreshScale(plan);
        return true;
    }
    if (t.hasAttribute('data-next-add')) {
        t.style.display = 'none';
        $('#nextPlan').style.display = '';
        $('#nextPlan').scrollIntoView({ behavior: 'smooth', block: 'start' });
        return true;
    }
    if (t.hasAttribute('data-next-del')) {
        $('#nextPlan').style.display = 'none';
        $('[data-next-add]').style.display = '';
        return true;
    }
    return false;
}

/* ---------- 小弹窗里的按钮 ---------- */
function dialogControl(t) {
    if (t.dataset.preset !== undefined && t.matches('.preset')) { $('#limInp').value = t.dataset.preset; refreshLimit(); return true; }
    if (t.id === 'limAck') { t.classList.toggle('on'); refreshLimit(); return true; }
    if (t.id === 'limGo') { applyLimit(t); return true; }
    if (t.id === 'rstAck') { t.classList.toggle('on'); $('#rstGo').disabled = !t.classList.contains('on'); return true; }
    if (t.id === 'rstGo') { applyReset(t); return true; }
    if (t.dataset.keep) {
        $$('.opt').forEach(o => o.classList.toggle('on', o === t));
        const go = $('#delGo');
        go.disabled = false;
        go.innerHTML = `${ico('del', 15)}${t.dataset.keep === '1' ? '删除并保留数据' : '删除并清空数据'}`;
        go.dataset.keep = t.dataset.keep;
        return true;
    }
    if (t.id === 'delGo') { applyDelete(t); return true; }
    if (t.id === 'msgGo') { sendSessionMessage(t); return true; }
    return false;
}

function mergeControl(t, e, ctx) {
    if (t.hasAttribute('data-merge-edit')) { openMergeEditor(); return true; }
    if (t.matches('.me-item')) {
        if (e.target.closest('.me-drag')) return true;
        t.classList.toggle('on');
        refreshMergeEditor();
        return true;
    }
    if (t.hasAttribute('data-me-all')) { toggleAll(t); return true; }
    if (t.id === 'meGo') { saveMerge(ctx.render); return true; }
    return false;
}

/** 处理点击；已处理返回 true */
export function uiClick(t, e, ctx) {
    if (t.dataset.act) { cardAction(t); return true; }
    if (t.dataset.limMode) { switchLimitMode(t); return true; }
    if (t.dataset.sess) { sessionCommand(t); return true; }
    if (t.dataset.addkind) { closeModal(); (t.dataset.addkind === 'qb' ? openQbForm : openEmbyForm)('add'); return true; }
    if (t.hasAttribute('data-sheet-close')) { closeSheet(); return true; }
    if (t.dataset.stab) {
        const s = $('#sheet');
        s.querySelectorAll('[data-stab]').forEach(b => b.classList.toggle('on', b === t));
        s.querySelectorAll('[data-pane]').forEach(p => p.classList.toggle('on', p.dataset.pane === t.dataset.stab));
        s.querySelector('.sheet-body').scrollTop = 0;
        return true;
    }
    if (t.dataset.save) { saveSheet(t); return true; }
    if (t.dataset.test) { runTest(t); return true; }
    if (t.dataset.lgk) { legendClick(t); return true; }
    return formControl(t) || planControl(t) || dialogControl(t) || mergeControl(t, e, ctx);
}

/** 输入联动：手动限速按钮可用性、规则刻度预览 */
function clearBad(t) {
    const box = t.closest?.('.inp.bad');
    if (!box) return;
    box.classList.remove('bad');
    const err = box.closest('.qd-edit')?.querySelector('.qd-err');
    if (!err) return;
    err.remove();
    state.quotaEditErr = '';
}

export function uiChange(t) {
    clearBad(t);
    if (t.id === 'qMsgSel') onQuotaMsgChange(t);
    if (t.hasAttribute('data-anchor') && t.closest('#quotaPlan')) refreshQuotaMessages();
}

export function uiInput(e) {
    const t = e.target;
    clearBad(t);
    if (t.id === 'limInp') refreshLimit();
    if (t.dataset.f === 'th') refreshScale(t.closest('.plan'));
    if (t.hasAttribute('data-anchor') && t.closest('#quotaPlan')) refreshQuotaMessages();
}
