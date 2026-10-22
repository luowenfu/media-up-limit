/* 事件记录、日志很长时，往下滑超过约一屏就在右下角显示「回到顶部」。
   页面整体滚动和列表内部滚动都支持。
   浏览器自带的平滑滚动在特别长的列表上会停在半路，刷新重绘又会把位置拉回去，所以这里自己滚到顶。 */

const LIST = '.timeline, .logs';
let btn = null;
let active = null;
let mark = 0;
let run = 0;
let stick = false;
let quietUntil = 0;
let pinCap = 0;
const pinned = new Set();
let pinning = false;

/** 刷新若想把滚动位置拉回去，先记下这个数；中间变过就说明人点了回到顶部。 */
export function toTopMark() { return mark; }
/** 回到顶部已经完成，刷新时不要再把人拉走。用户一滚动这个就失效。 */
export function toTopStick() { return stick; }

function pos(el) { return el === window ? scrollY : el.scrollTop; }
function view(el) { return el === window ? innerHeight : el.clientHeight; }

function hasList(el) {
    if (el === window) return !!document.querySelector(LIST);
    return el.matches(LIST) || !!el.querySelector(LIST);
}

function nodeOf(el) {
    return el === window ? (document.scrollingElement || document.documentElement) : el;
}

function scrollBox(node) {
    for (let el = node; el && el !== document.body; el = el.parentElement) {
        const oy = getComputedStyle(el).overflowY;
        if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 1) return el;
    }
    return window;
}

function targets() {
    const list = [];
    const add = el => {
        if (!el || list.includes(el)) return;
        if (el !== window && !el.isConnected) return;
        if (pos(el) > 1) list.push(el);
    };
    document.querySelectorAll(LIST).forEach(node => add(scrollBox(node)));
    add(active);
    add(window);
    return list;
}

function ensure() {
    if (btn) return btn;
    btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'to-top';
    btn.title = '回到顶部';
    btn.setAttribute('aria-label', '回到顶部');
    btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 20 20" fill="none"><path d="M10 15.5V5M5 9.5L10 4.5l5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    btn.addEventListener('click', go);
    document.body.appendChild(btn);
    return btn;
}

function syncBtn() {
    if (!btn) return;
    let show = false;
    const see = el => { if (hasList(el) && pos(el) > view(el)) show = true; };
    document.querySelectorAll(LIST).forEach(node => see(scrollBox(node)));
    see(window);
    btn.classList.toggle('show', show);
}

function place(tr, next) {
    const node = nodeOf(tr.el);
    if (!node || (tr.el !== window && !node.isConnected)) return false;
    if (node.scrollTop > next) node.scrollTop = next;
    return true;
}

function go() {
    const tracks = targets().map(el => ({ el, from: pos(el) }));
    if (!tracks.length) { syncBtn(); return; }
    const id = ++run;
    mark += 1;
    stick = false;
    pinned.clear();
    tracks.forEach(t => pinned.add(t.el));
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const far = Math.max(...tracks.map(t => t.from));
    const ms = reduce ? 0 : Math.max(320, Math.min(700, 200 + far / 8));
    const now0 = performance.now();
    // 松手后的惯性还会再推一会。这段时间里滚轮不取消，偏离顶部就拉回来。
    quietUntil = now0 + ms + 400;
    pinCap = now0 + ms + 2500;
    const frame = now => {
        if (id !== run) return;
        const t = ms ? Math.min(1, (now - now0) / ms) : 1;
        const k = 1 - (1 - t) ** 3;
        let live = false;
        for (const tr of tracks) {
            const next = t >= 1 ? 0 : Math.round(tr.from * (1 - k));
            if (place(tr, next)) live = true;
        }
        if (!live || t >= 1) {
            run = 0;
            mark += 1;
            stick = live;
            if (!live) pinned.clear();
            syncBtn();
            return;
        }
        requestAnimationFrame(frame);
    };
    if (ms) requestAnimationFrame(frame);
    else frame(now0);
}

function cancel() {
    if (!run) return;
    run = 0;
    mark += 1;
    stick = false;
    pinned.clear();
}

/** 惯性把列表推离顶部时拉回去，直到惯性停住或用户自己滚动。 */
function pinBack(el) {
    if (pinning || !stick || !pinned.has(el)) return;
    const now = performance.now();
    if (now >= pinCap || now >= quietUntil) { stick = false; return; }
    const node = nodeOf(el);
    if (!node || (el !== window && !node.isConnected) || node.scrollTop <= 0) return;
    pinning = true;
    node.scrollTop = 0;
    pinning = false;
    quietUntil = Math.min(pinCap, now + 250);
}

function onScroll(e) {
    const el = e.target === document || e.target === document.documentElement ? window : e.target;
    if (!(el === window || el instanceof Element)) return;
    pinBack(el);
    if (!hasList(el)) return;
    const show = pos(el) > view(el);
    if (show) active = el;
    else if (active && active !== el) return;
    ensure().classList.toggle('show', show);
}

function release() {
    stick = false;
    quietUntil = 0;
    pinned.clear();
    cancel();
}

function onKey(e) {
    if (!['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(e.key)) return;
    const tag = e.target && e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    release();
}

function onPointer(e) {
    if (e.target.closest && e.target.closest('.to-top')) return;
    release();
}

function onWheel() {
    const now = performance.now();
    if (run || now < quietUntil) {
        if (stick && now < pinCap) quietUntil = Math.min(pinCap, now + 250);
        return;
    }
    release();
}

document.addEventListener('scroll', onScroll, { capture: true, passive: true });
addEventListener('scroll', onScroll, { passive: true });
addEventListener('wheel', onWheel, { capture: true, passive: true });
addEventListener('touchmove', onWheel, { capture: true, passive: true });
addEventListener('keydown', onKey);
addEventListener('pointerdown', onPointer, true);
