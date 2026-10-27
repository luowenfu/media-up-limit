/* 深色 / 浅色主题。偏好存在本机，刷新后仍生效。 */

import { I } from './icons.js';

const KEY = 'qb-scheme';
const gearSvg = size => I.gear.replace('<svg ', `<svg class="gear" width="${size}" height="${size}" `);
const THEME_BTN = `<button type="button" class="nav-item" id="themeBtn" aria-pressed="false">
    <span class="theme-ico" aria-hidden="true">
        <svg class="ico-sun" width="18" height="18" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="3.1" stroke="currentColor" stroke-width="1.6"/><path d="M10 2.4v1.7M10 15.9v1.7M2.4 10h1.7M15.9 10h1.7M4.6 4.6l1.2 1.2M14.2 14.2l1.2 1.2M4.6 15.4l1.2-1.2M14.2 5.8l1.2-1.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        <svg class="ico-moon" width="18" height="18" viewBox="0 0 20 20" fill="none"><path d="M16.4 12.2A6.5 6.5 0 0 1 7.6 3.6 6.6 6.6 0 1 0 16.4 12.2Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
    </span>
    <span class="grow">主题切换</span>
    <span class="nav-badge" id="themeMark"></span>
</button>`;

export const isDark = () => document.documentElement.dataset.scheme === 'dark';

export function applyScheme(dark) {
    document.documentElement.dataset.scheme = dark ? 'dark' : 'light';
    try { localStorage.setItem(KEY, dark ? 'dark' : 'light'); } catch (e) { /* 隐私模式不影响当次显示 */ }
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = dark ? '#0c1220' : '#2563eb';
    const btn = document.getElementById('themeBtn');
    if (btn) {
        btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
        btn.setAttribute('aria-label', dark ? '主题切换，当前深色' : '主题切换，当前浅色');
    }
}

export function toggleScheme() {
    applyScheme(!isDark());
}

/* 点「主题切换」只展开选项，再点深色/浅色才生效，避免误触直接切换 */
function ensureSchemeMenu() {
    const btn = document.getElementById('themeBtn');
    if (!btn) return null;
    let menu = document.getElementById('schemeMenu');
    if (!menu) {
        btn.insertAdjacentHTML('afterend', `<div class="scheme-menu" id="schemeMenu" role="group" aria-label="选择主题" hidden>
    <button type="button" class="scheme-opt" data-scheme-pick="light">浅色</button>
    <button type="button" class="scheme-opt" data-scheme-pick="dark">深色</button>
</div>`);
        menu = document.getElementById('schemeMenu');
    }
    return menu;
}

export function setSchemeMenu(open) {
    const menu = ensureSchemeMenu();
    if (!menu) return;
    menu.hidden = !open;
    document.getElementById('themeBtn').setAttribute('aria-expanded', open ? 'true' : 'false');
    const cur = isDark() ? 'dark' : 'light';
    menu.querySelectorAll('[data-scheme-pick]').forEach(b => b.classList.toggle('on', b.dataset.schemePick === cur));
}

export function toggleSchemeMenu() {
    const menu = ensureSchemeMenu();
    setSchemeMenu(!!(menu && menu.hidden));
}

export function pickScheme(v) {
    applyScheme(v === 'dark');
    setSchemeMenu(false);
}
function ensureSchemeCss() {
    if (document.querySelector('link[href*="scheme.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/css/scheme.css';
    document.head.appendChild(link);
}

/** 旧页面没有新侧栏时补上标题、分割线、主题按钮和齿轮图标 */
export function ensureSidebar() {
    ensureSchemeCss();
    document.querySelectorAll('.sidebar .nav-label').forEach(el => {
        if (el.textContent.trim() === '设备') el.textContent = '设备管理';
        if (el.textContent.trim() === '系统') {
            const line = document.createElement('div');
            line.className = 'nav-line';
            el.replaceWith(line);
        }
    });
    const foot = document.querySelector('.sidebar .nav:not(.nav-tree)');
    if (foot && !document.querySelector('.sidebar .nav-line')) {
        const line = document.createElement('div');
        line.className = 'nav-line';
        foot.before(line);
    }
    const settings = document.querySelector('.sidebar [data-open="settings"]');
    const settingsIcon = settings && settings.querySelector('svg');
    if (settingsIcon) settingsIcon.outerHTML = gearSvg(18);
    const theme = document.getElementById('themeBtn');
    if (!theme && foot) foot.insertAdjacentHTML('afterbegin', THEME_BTN);
    else if (theme && foot && foot.firstElementChild !== theme) foot.prepend(theme);
    const oldMenu = document.getElementById('schemeMenu');
    if (oldMenu && theme && theme.nextElementSibling !== oldMenu) theme.after(oldMenu);
    ensureSchemeMenu();
    document.querySelectorAll('.bottom-bar [data-open="settings"] svg').forEach(svg => { svg.outerHTML = gearSvg(21); });
    if (!document.getElementById('sideBtn')) {
        const side = document.querySelector('.sidebar');
        if (side) side.insertAdjacentHTML('afterbegin', `<button type="button" class="side-toggle" id="sideBtn" aria-label="收起侧栏" aria-expanded="true"><svg width="18" height="18" viewBox="0 0 20 20" fill="none"><rect x="2.5" y="3" width="15" height="14" rx="2" stroke="currentColor" stroke-width="1.6"/><path d="M8 3v14" stroke="currentColor" stroke-width="1.6"/><path d="M13.2 7.5 10.2 10l3 2.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button>`);
    }
    let dark = isDark();
    try {
        const saved = localStorage.getItem(KEY);
        if (saved !== 'light' && saved !== 'dark') dark = true;
        else if (saved === 'dark') dark = true;
        else dark = false;
    } catch (e) { dark = true; }
    applyScheme(dark);
}

ensureSidebar();

document.addEventListener('click', e => {
    const t = e.target.closest && e.target.closest('#themeBtn, [data-scheme-pick], #schemeMenu');
    if (!t) { const m = document.getElementById('schemeMenu'); if (m && !m.hidden) setSchemeMenu(false); return; }
    if (t.id === 'themeBtn') { toggleSchemeMenu(); return; }
    if (t.dataset.schemePick) pickScheme(t.dataset.schemePick);
});
