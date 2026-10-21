/* 小弹窗：手动限速、清空统计、删除设备、添加设备选择（markup 与改造计划原型一致） */

import * as api from './api.js';
import { enc, q } from './api.js';
import { I, ico } from './icons.js';
import { esc } from './format.js';
import { qbByName, embyByName } from './store.js';
import {
    $, $$, busy, chkBox, closeModal, inp, mTop, notice, openSmall, reload, toast,
} from './ui.js';

const find = (kind, name) => (kind === 'qb' ? qbByName(name) : embyByName(name));

/* ---------- 添加设备：选择类型 ---------- */
export function openChooser() {
    const box = $('#modalBox');
    box.className = 'modal';
    box.innerHTML = `<div class="modal-head"><h3>添加设备</h3><button data-close>${I.x}</button></div>
    <div class="modal-body"><div class="choose">
        <button data-addkind="qb"><span class="ci"><img src="/static/img/qbittorrent-logo.svg" alt=""></span><b>qBittorrent</b><small>按周期统计上传流量，达量自动限速</small></button>
        <button data-addkind="emby"><span class="ci"><img src="/static/img/emby-icon.svg" alt=""></span><b>Emby</b><small>按用户统计播放流量，记录播放与选片</small></button>
    </div></div>`;
    $('#modal').classList.add('open');
}

/* ---------- 手动限速 ---------- */
export function openLimit(name) {
    const d = qbByName(name);
    if (!d) return;
    const alt = d.limit.on === 'backup';
    const rule = d.activeRule ? `规则 ${d.activeRule} 生效中` : '未触发规则';
    openSmall(`${mTop('violet', 'lim', '手动限速', `<b>${esc(d.name)}</b> · 临时调整全局上传限速`)}
    <div class="m-body">
        <div class="cur-box"><div><div class="k">当前全局上传限速</div><div class="v num">${d.limit.global}</div></div>
            <span class="chip ${d.triggered ? 'hot' : ''}" style="background:rgba(255,255,255,.12);color:#fff;border-color:transparent">${rule}</span></div>
        <label class="fl">全局限速至</label>
        ${inp({ id: 'limInp', type: 'number', ph: '0 表示不限速', suf: 'KB/s' })}
        <div class="presets">${[[0, '不限速'], [128, '128 K'], [256, '256 K'], [512, '512 K'], [1024, '1 M'], [2048, '2 M']].map(([v, l]) => `<button type="button" class="preset" data-preset="${v}">${l}</button>`).join('')}</div>
        <div class="hint-t" style="margin-top:12px">临时限速，直到下一条达量规则触发后自动接管；不影响 qB 备用限速的执行。</div>
        ${alt ? `<div style="margin-top:14px">${notice('warn', '<b>qB 当前处于备用限速（计划任务）中</b>，此时修改全局限速不会立即生效，会在备用限速结束后才起作用。', 'warn')}</div>${chkBox('我知道了，仍要设置', '#d97706', 'limAck')}` : ''}
    </div>
    <div class="m-foot"><button type="button" class="btn" data-close>取消</button><button type="button" class="btn primary" style="--sheet-accent:#7c3aed;--sheet-accent-2:#8b5cf6" id="limGo" data-name="${esc(d.name)}" disabled>${ico('lim', 15)}应用限速</button></div>`);
}

export function refreshLimit() {
    const v = $('#limInp').value.trim();
    const ack = $('#limAck');
    $('#limGo').disabled = v === '' || (ack && !ack.classList.contains('on'));
    $$('.preset').forEach(p => p.classList.toggle('on', p.dataset.preset === v));
}

export async function applyLimit(btn) {
    const v = Math.max(0, parseInt($('#limInp').value, 10) || 0);
    const name = btn.dataset.name;
    await busy(btn, '应用中…', async () => {
        try {
            await api.post('/api/control/limit', { instance_name: name, limit_kbps: v });
            closeModal();
            toast(`已将「${name}」全局上传限速设为 ${v ? v + ' KB/s' : '不限速'}`);
            reload(false);
        } catch (e) { toast(e.message, 'err'); }
    });
}

/** 在卡片上切换全局 / 备用限速模式 */
export async function switchLimitMode(el) {
    if (el.classList.contains('on')) return;
    const name = el.dataset.dev, alt = el.dataset.limMode === 'backup';
    try {
        await api.post('/api/control/speed-limits-mode', { instance_name: name, use_alt: alt });
        toast(`已切换为${alt ? '备用' : '全局'}限速模式`);
        reload(false);
    } catch (e) { toast(e.message, 'err'); }
}

/* ---------- 清空统计 ---------- */
export function openReset(kind, name) {
    const d = find(kind, name);
    if (!d) return;
    const t = d.traffic;
    openSmall(`${mTop('orange', 'rst', '清空流量统计', `确认要清空 <b>${esc(d.name)}</b> 的流量统计吗？`)}
    <div class="m-body">
        <div class="clear-sum">
            <div><small>${t[2][0]}上传</small><b class="num">${esc(t[2][1])}</b></div>
            <div><small>${t[3][0]}上传</small><b class="num">${esc(t[3][1])}</b></div>
            <div><small>${kind === 'qb' ? '已触发规则' : '统计用户'}</small><b class="num" id="rstCount">${kind === 'qb' ? d.rules.filter(r => r.st).length + ' 条' : '…'}</b></div>
        </div>
        <div style="margin-top:14px">${notice('danger', '将清空该设备的全部流量数据并重新累计，<b>此操作不可恢复</b>。', 'warn')}</div>
        ${chkBox('确认清空', '#ea580c', 'rstAck')}
    </div>
    <div class="m-foot"><button type="button" class="btn" data-close>取消</button><button type="button" class="btn warn" id="rstGo" data-kind="${kind}" data-name="${esc(d.name)}" disabled>${ico('rst', 15)}清空统计</button></div>`);
    if (kind === 'emby') {
        api.get(`/api/emby/playback-users${q({ instance: name })}`)
            .then(r => { const el = $('#rstCount'); if (el) el.textContent = `${(r.data || []).length} 人`; })
            .catch(() => { const el = $('#rstCount'); if (el) el.textContent = '—'; });
    }
}

export async function applyReset(btn) {
    const { kind, name } = btn.dataset;
    const url = kind === 'qb' ? '/api/control/reset-stats' : '/api/emby/control/reset-stats';
    await busy(btn, '清空中…', async () => {
        try {
            await api.post(url, { instance_name: name });
            closeModal();
            toast(`已清空「${name}」的流量统计`);
            reload(false);
        } catch (e) { toast(e.message, 'err'); }
    });
}

/* ---------- 删除设备 ---------- */
export function openDelete(kind, name) {
    const d = find(kind, name);
    if (!d) return;
    openSmall(`${mTop('red', 'del', '删除设备', `将从列表中移除 <b>${esc(d.name)}</b>，请选择如何处理它的数据`)}
    <div class="m-body"><div class="opts">
        <button type="button" class="opt" data-keep="1"><span class="radio"></span><div><b>保留数据</b><small>历史流量继续保存在数据库中；之后添加同名设备 <code>${esc(d.name)}</code> 即可恢复</small></div></button>
        <button type="button" class="opt danger" data-keep="0"><span class="radio"></span><div><b>不保留数据</b><small>同时清空该设备的全部历史记录，此操作不可恢复</small></div></button>
    </div></div>
    <div class="m-foot"><button type="button" class="btn" data-close>取消</button><button type="button" class="btn danger" id="delGo" data-kind="${kind}" data-name="${esc(d.name)}" disabled>${ico('del', 15)}请选择处理方式</button></div>`);
}

const MESSAGE_TIMEOUT_MS = 8000;

function sessionTitleLabel(s) {
    if (s.series_name) {
        const ep = s.episode_title || s.title || '';
        return ep ? `${s.series_name} — ${ep}` : s.series_name;
    }
    return s.title || '当前内容';
}

/** 向正在播放的客户端发一条屏幕消息 */
export function openSessionMessage(dev, sid) {
    const d = embyByName(dev);
    const s = (d && d.sessions || []).find(x => x.id === sid);
    if (!s) { toast('会话已结束或不存在', 'info'); return; }
    const user = s.user_name || '该用户';
    openSmall(`${mTop('green', 'msg', '发送消息', `发给 <b>${esc(user)}</b>`)}
    <div class="m-body">
        <p class="msg-hint">正在观看：${esc(sessionTitleLabel(s))}</p>
        <div class="msg-style">
            <div class="seg-f" id="msgStyle">
                <button type="button" class="on" data-msg-style="toast">短消息</button>
                <button type="button" data-msg-style="dialog">确认框</button>
            </div>
            <p class="msg-style-hint" id="msgStyleHint">短暂显示，3秒后自动消失</p>
        </div>
        <label class="fl" for="msgInp">消息内容</label>
        <textarea id="msgInp" class="msg-box" rows="3" maxlength="500" placeholder="输入消息" enterkeyhint="send" autocomplete="off"></textarea>
    </div>
    <div class="m-foot"><button type="button" class="btn" data-close>取消</button><button type="button" class="btn primary" style="--sheet-accent:#059669;--sheet-accent-2:#10b981" id="msgGo" data-dev="${esc(dev)}" data-sid="${esc(sid)}">${ico('msg', 15)}发送</button></div>`);
    const input = $('#msgInp');
    if (!input) return;
    input.focus();
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            const btn = $('#msgGo');
            if (btn && !btn.disabled) sendSessionMessage(btn);
        }
    });
}

export async function sendSessionMessage(btn) {
    const text = ($('#msgInp') && $('#msgInp').value || '').trim();
    if (!text) { toast('请输入消息内容', 'err'); return; }
    const { dev, sid } = btn.dataset;
    await busy(btn, '发送中…', async () => {
        try {
            const style = document.querySelector('#msgStyle button.on');
            const toastStyle = !style || style.dataset.msgStyle !== 'dialog';
            await api.post(`/api/emby/sessions/${enc(dev)}/${enc(sid)}/message`, {
                text, timeout_ms: toastStyle ? MESSAGE_TIMEOUT_MS : null,
            });
            closeModal();
            toast('消息已发送');
        } catch (e) { toast(e.message, 'err'); }
    });
}

export async function applyDelete(btn) {
    const { kind, name, keep } = btn.dataset;
    const base = kind === 'qb' ? '/api/config/instances' : '/api/emby/config/instances';
    await busy(btn, '删除中…', async () => {
        try {
            await api.del(`${base}/${enc(name)}?keep_data=${keep === '1' ? 1 : 0}`);
            closeModal();
            toast(`已删除「${name}」${keep === '1' ? '，数据已保留' : '及其全部数据'}`);
            reload();
        } catch (e) { toast(e.message, 'err'); }
    });
}
