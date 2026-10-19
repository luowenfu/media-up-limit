/* 历史数据管理：查看并清理已删除设备留下的数据 */

import * as api from './api.js';
import { enc } from './api.js';
import { esc } from './format.js';
import { $, toast } from './ui.js';

const SOURCES = [
    ['qb', 'qB', '/api/config/instances/orphan-data'],
    ['emby', 'Emby', '/api/emby/config/instances/orphan-data'],
];

async function load() {
    const out = [];
    for (const [kind, label, url] of SOURCES) {
        try {
            (await api.get(url)).data.forEach(o => out.push({ kind, label, name: o.name, at: o.deleted_at }));
        } catch (e) { /* Emby 未启用时没有这个接口，跳过 */ }
    }
    return out;
}

const deletedLine = text => {
    const m = String(text || '').match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    return m ? `删除设备时间：${m[1]}/${m[2]}/${m[3]} ${m[4]}:${m[5]}` : '';
};

const rowHtml = o => {
    const when = deletedLine(o.at);
    return `<div class="orphan-row" data-orphan="${o.kind}" data-name="${esc(o.name)}">
    <div class="orphan-main">
        <div class="orphan-top"><code>${esc(o.name)}</code><span>${o.label} 数据</span></div>
        ${when ? `<div class="orphan-time">${when}</div>` : ''}
    </div>
    <button type="button" class="mini-btn" data-orphan-del>删除数据</button></div>`;
};

function paint(list) {
    $('#orphanPanel').innerHTML = list.length
        ? `<div class="orphan-list">${list.map(rowHtml).join('')}</div>`
        : '<div class="hint-t orphan-empty">没有遗留的历史数据</div>';
}

export async function openOrphans() {
    const panel = $('#orphanPanel');
    if (!panel || panel.dataset.open === '1') {
        panel?.scrollIntoView({ block: 'nearest' });
        return;
    }
    panel.hidden = false;
    panel.dataset.open = '1';
    panel.innerHTML = '<div class="hint-t orphan-empty">加载中…</div>';
    const btn = document.querySelector('[data-orphans]');
    if (btn) btn.hidden = true;
    let list = await load();
    paint(list);
    panel.scrollIntoView({ block: 'nearest' });
    panel.onclick = async e => {
        const btn = e.target.closest('[data-orphan-del]');
        if (!btn) return;
        if (!btn.dataset.sure) {
            btn.dataset.sure = '1';
            btn.textContent = '确认删除';
            btn.style.color = 'var(--danger)';
            return;
        }
        const row = btn.closest('[data-orphan]');
        const { orphan: kind, name } = row.dataset;
        const url = SOURCES.find(s => s[0] === kind)[2];
        try {
            await api.del(`${url}/${enc(name)}`);
            list = list.filter(o => !(o.kind === kind && o.name === name));
            paint(list);
            toast(`已删除「${name}」的历史数据`);
        } catch (err) { toast(err.message, 'err'); }
    };
}
