/* 同名历史数据的处理方式选择（添加设备 / 改名时） */

import { ico } from './icons.js';
import { esc } from './format.js';
import { $, closeModal, mTop, openSmall } from './ui.js';

const TEXT = {
    add: [
        ['restore', '恢复历史数据', '沿用之前同名设备留下的流量数据，继续累计'],
        ['fresh', '清空后新建', '丢弃同名历史数据，从零开始统计'],
    ],
    rename: [
        ['restore_orphan', '使用同名历史数据', '改名后接上历史数据，当前设备已有数据会被丢弃'],
        ['keep_current', '保留当前设备数据', '改名后继续使用当前数据，同名历史数据会被丢弃'],
    ],
};

/** 返回所选策略；取消返回 null */
export function askPolicy(mode, name) {
    return new Promise(resolve => {
        const opts = TEXT[mode];
        openSmall(`${mTop('orange', 'db', '发现同名历史数据', `数据库里已有名为 <b>${esc(name)}</b> 的历史数据，请选择如何处理`)}
        <div class="m-body"><div class="opts">${opts.map(o => `
            <button type="button" class="opt" data-pol="${o[0]}"><span class="radio"></span><div><b>${o[1]}</b><small>${o[2]}</small></div></button>`).join('')}
        </div></div>
        <div class="m-foot"><button type="button" class="btn" data-close>取消</button><button type="button" class="btn primary" id="polGo" disabled>${ico('check', 15)}继续</button></div>`);
        const modal = $('#modal');
        let picked = null, done = false;
        const finish = v => {
            if (done) return;
            done = true;
            obs.disconnect();
            modal.removeEventListener('click', onClick, true);
            resolve(v);
        };
        const onClick = e => {
            const opt = e.target.closest('[data-pol]');
            if (opt) {
                e.stopPropagation();
                picked = opt.dataset.pol;
                modal.querySelectorAll('[data-pol]').forEach(b => b.classList.toggle('on', b === opt));
                $('#polGo').disabled = false;
                return;
            }
            if (e.target.closest('#polGo')) {
                e.stopPropagation();
                closeModal();
                finish(picked);
            }
        };
        const obs = new MutationObserver(() => { if (!modal.classList.contains('open')) finish(null); });
        obs.observe(modal, { attributes: true, attributeFilter: ['class'] });
        modal.addEventListener('click', onClick, true);
    });
}
