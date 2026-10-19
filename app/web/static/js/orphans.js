/* 历史数据管理：查看并清理已删除设备留下的数据 */

import * as api from './api.js';
import { enc } from './api.js';
import { esc, shortStamp } from './format.js';
import { $, mTop, openSmall, toast } from './ui.js';

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

const rowHtml = o => `<div class="file-row" data-orphan="${o.kind}" data-name="${esc(o.name)}">
    <code>${esc(o.name)}</code><span>${o.label} 数据${o.at ? ' · ' + shortStamp(o.at) + ' 删除' : ''}</span>
    <button type="button" class="mini-btn" data-orphan-del style="margin-left:auto">删除数据</button></div>`;

function paint(list) {
    $('#orphanList').innerHTML = list.length
        ? `<div class="file-list">${list.map(rowHtml).join('')}</div>`
        : '<div class="hint-t" style="padding:18px 0;text-align:center">没有遗留的历史数据</div>';
}

export async function openOrphans() {
    openSmall(`${mTop('blue', 'db', '历史数据', '已删除设备保留下来的数据，添加同名设备即可恢复；不再需要时可以彻底删除')}
    <div class="m-body"><div id="orphanList"><div class="hint-t" style="padding:18px 0;text-align:center">加载中…</div></div></div>
    <div class="m-foot"><button type="button" class="btn" data-close>关闭</button></div>`);
    let list = await load();
    paint(list);
    $('#orphanList').onclick = async e => {
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
