/* 流量图表的范围选择：按天是日期范围，按月是月份范围 */

const p2 = n => String(n).padStart(2, '0');
const WEEK = ['一', '二', '三', '四', '五', '六', '日'];
const addMonths = (ym, n) => {
    const [y, m] = ym.split('-').map(Number);
    const d = new Date(y, m - 1 + n, 1);
    return `${d.getFullYear()}-${p2(d.getMonth() + 1)}`;
};

let root = null;
let onDone = null;
let mode = 'day';
let max = '';
let start = '';
let end = '';
let hover = '';
let left = '';
let backup = null;

const ymOf = value => String(value || '').slice(0, 7);
const inRange = (value, a, b) => {
    if (!a || !b || !value) return false;
    const [lo, hi] = a < b ? [a, b] : [b, a];
    return value >= lo && value <= hi;
};

function field() {
    return document.querySelector('[data-range-field]');
}

function inputs() {
    return [document.getElementById('stFrom'), document.getElementById('stTo')];
}

function writeInputs(a, b) {
    const [from, to] = inputs();
    if (from) from.value = a || '';
    if (to) to.value = b || '';
}

export function closeRangePicker(restore) {
    if (restore && backup) writeInputs(backup.start, backup.end);
    root?.remove();
    root = null;
    field()?.classList.remove('is-open');
    backup = null;
    hover = '';
}

function place() {
    const host = field();
    if (!root || !host) return;
    const rect = host.getBoundingClientRect();
    const width = root.offsetWidth;
    const height = root.offsetHeight;
    let x = rect.right - width;
    if (x < 8) x = Math.min(rect.left, window.innerWidth - width - 8);
    let y = rect.bottom + 6;
    if (y + height > window.innerHeight - 8) y = Math.max(8, rect.top - height - 6);
    root.style.left = `${Math.max(8, x)}px`;
    root.style.top = `${y}px`;
}

function monthCells(ym) {
    const [y, m] = ym.split('-').map(Number);
    const first = new Date(y, m - 1, 1);
    const lead = (first.getDay() + 6) % 7;
    const count = new Date(y, m, 0).getDate();
    const cells = Array(lead).fill('');
    for (let d = 1; d <= count; d++) cells.push(`${ym}-${p2(d)}`);
    while (cells.length % 7) cells.push('');
    return cells;
}

function dayPane(ym, side) {
    const [y, m] = ym.split('-').map(Number);
    const nav = side === 'left'
        ? `<button type="button" data-nav="-12" aria-label="上一年">«</button><button type="button" data-nav="-1" aria-label="上个月">‹</button>`
        : `<button type="button" data-nav="1" aria-label="下个月">›</button><button type="button" data-nav="12" aria-label="下一年">»</button>`;
    const cells = monthCells(ym).map(day => {
        if (!day) return '<span></span>';
        const off = day > max || day < '2000-01-01';
        const on = day === start || day === end;
        const mid = inRange(day, start, end || hover);
        return `<button type="button" data-val="${day}" class="${on ? 'is-on' : ''} ${mid && !on ? 'is-in' : ''}" ${off ? 'disabled' : ''}>${+day.slice(8)}</button>`;
    }).join('');
    return `<section class="rp-pane"><div class="rp-head">${side === 'left' ? nav : ''}<b>${y}年${m}月</b>${side === 'right' ? nav : ''}</div><div class="rp-week">${WEEK.map(w => `<span>${w}</span>`).join('')}</div><div class="rp-grid">${cells}</div></section>`;
}

function monthPane(year, side) {
    const nav = side === 'left'
        ? `<button type="button" data-nav="-1" aria-label="上一年">«</button>`
        : `<button type="button" data-nav="1" aria-label="下一年">»</button>`;
    const cells = Array.from({ length: 12 }, (_, i) => {
        const value = `${year}-${p2(i + 1)}`;
        const off = value > max || value < '2000-01';
        const on = value === start || value === end;
        const mid = inRange(value, start, end || hover);
        return `<button type="button" data-val="${value}" class="${on ? 'is-on' : ''} ${mid && !on ? 'is-in' : ''}" ${off ? 'disabled' : ''}>${i + 1}月</button>`;
    }).join('');
    return `<section class="rp-pane"><div class="rp-head">${side === 'left' ? nav : ''}<b>${year}年</b>${side === 'right' ? nav : ''}</div><div class="rp-months">${cells}</div></section>`;
}

function paint() {
    if (!root) return;
    const body = mode === 'day'
        ? `${dayPane(left, 'left')}${dayPane(addMonths(left, 1), 'right')}`
        : `${monthPane(+left, 'left')}${monthPane(+left + 1, 'right')}`;
    root.innerHTML = body;
    place();
}

function choose(value) {
    if (!start || end) {
        start = value;
        end = '';
        hover = '';
        writeInputs(start, '');
        if (mode === 'day') left = ymOf(start);
        else left = String(Math.min(+start.slice(0, 4), +max.slice(0, 4) - 1));
        paint();
        return;
    }
    if (value < start) [start, end] = [value, start];
    else end = value;
    writeInputs(start, end);
    const done = onDone;
    closeRangePicker(false);
    done?.();
}

export function openRangePicker(done) {
    const host = field();
    if (!host) return;
    if (root) { closeRangePicker(true); return; }
    mode = host.dataset.mode === 'month' ? 'month' : 'day';
    max = host.dataset.max || '';
    const [from, to] = inputs();
    start = from?.value || '';
    end = to?.value || '';
    backup = { start, end };
    hover = '';
    onDone = done;
    if (mode === 'day') left = ymOf(start || max);
    else {
        const year = +(start || max).slice(0, 4) || new Date().getFullYear();
        const cap = (+max.slice(0, 4) || year) - 1;
        left = String(Math.max(2000, Math.min(year, cap)));
    }
    root = document.createElement('div');
    root.className = `rp-pop${mode === 'month' ? ' is-month' : ''}`;
    document.body.appendChild(root);
    host.classList.add('is-open');
    paint();
}

export function bindRangePicker(done) {
    document.addEventListener('mousedown', e => {
        if (e.target.closest('[data-range-open], .rp-pop')) e.preventDefault();
        else if (root && !e.target.closest('[data-range-field]')) closeRangePicker(true);
    });
    document.addEventListener('click', e => {
        const openBtn = e.target.closest('[data-range-open]');
        if (openBtn) { openRangePicker(done); return; }
        if (!root) return;
        const nav = e.target.closest('[data-nav]');
        if (nav) {
            const step = +nav.dataset.nav;
            if (mode === 'day') {
                const next = addMonths(left, step);
                if (next < '2000-01' || next > max.slice(0, 7)) return;
                left = next;
            } else {
                const next = +left + step;
                if (next < 2000 || next > +max.slice(0, 4) - 1) return;
                left = String(next);
            }
            paint();
            return;
        }
        const cell = e.target.closest('[data-val]');
        if (cell && !cell.disabled) choose(cell.dataset.val);
    });
    document.addEventListener('mouseover', e => {
        const cell = e.target.closest?.('[data-val]');
        if (!root || !cell || !root.contains(cell) || cell.disabled || !start || end) return;
        if (hover === cell.dataset.val) return;
        hover = cell.dataset.val;
        root.querySelectorAll('[data-val]').forEach(btn => {
            const value = btn.dataset.val;
            const on = value === start || value === end;
            const mid = inRange(value, start, hover);
            btn.classList.toggle('is-on', on);
            btn.classList.toggle('is-in', mid && !on);
        });
    });
}
