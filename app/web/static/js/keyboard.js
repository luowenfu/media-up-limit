/** 手机输入法弹出时，弹窗和抽屉让出键盘高度，输入框不被挡住。
 *  安卓 Chrome 认 viewport 的 interactive-widget，页面本身会变矮，这里算出来是 0；
 *  其他浏览器（iOS Safari、部分手机自带浏览器）页面不变矮，就用可见区域高度补上。 */
const root = document.documentElement;
const vv = window.visualViewport;

function sync() {
    if (!vv) return;
    const kb = Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop));
    root.style.setProperty('--kb', (kb > 80 ? kb : 0) + 'px');
}

if (vv) {
    vv.addEventListener('resize', sync);
    vv.addEventListener('scroll', sync);
    sync();
}

document.addEventListener('focusin', e => {
    const el = e.target;
    if (!el || !el.matches || !el.matches('input, textarea, select, [contenteditable="true"]')) return;
    if (!el.closest('.modal, .sheet')) return;
    setTimeout(() => { try { el.scrollIntoView({ block: 'nearest' }); } catch (err) { /* 老浏览器忽略 */ } }, 300);
});
