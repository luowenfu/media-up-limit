/* 数字与时间格式化：流量统一按二进制单位（1 GB = 1024 MB），保留三位有效数字 */

const UNITS = ['KB', 'MB', 'GB', 'TB'];

export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

function sig3(v) {
    const a = Math.abs(v);
    if (a >= 100) return v.toFixed(0);
    if (a >= 10) return v.toFixed(1);
    return v.toFixed(2);
}

/** 字节 → [数值文本, 单位]：最小单位 KB，零显示 0 KB。qB 用 1024，Emby 用 1000（与旧版一致） */
export function splitBytes(bytes, base = 1024) {
    const n = Math.max(0, Number(bytes) || 0);
    if (n <= 0) return ['0', 'KB'];
    for (let i = UNITS.length - 1; i >= 1; i--) {
        const v = n / (base ** (i + 1));
        if (v >= 1) return [sig3(v), UNITS[i]];
    }
    return [sig3(n / base), 'KB'];
}

export function fmtBytes(bytes, base = 1024) {
    const [n, u] = splitBytes(bytes, base);
    return `${n} ${u}`;
}

/** 每秒字节数 → [数值, 单位/s] */
export function splitSpeed(bytesPerSec, base = 1024) {
    const [n, u] = splitBytes(bytesPerSec, base);
    return [n, `${u}/s`];
}

/** 限速（KB/s）：0 表示无限速 */
export function fmtLimit(kbps) {
    const n = Number(kbps) || 0;
    return n > 0 ? `${n}KB/s` : '无限速';
}

export const fmtKbps = kbps => `${Number(kbps) || 0} KB/s`;

export function fmtGb(gb) {
    const v = Number(gb) || 0;
    return v >= 1024 ? `${sig3(v / 1024)} TB` : `${sig3(v)} GB`;
}

export function trimNum(v) {
    const n = Number(v);
    return Number.isInteger(n) ? String(n) : String(+n.toFixed(2));
}

const pad = n => String(n).padStart(2, '0');

/** "2026-10-09 16:23:25" → "10/09 16:23" */
export function shortStamp(text) {
    const m = String(text || '').match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    return m ? `${m[2]}/${m[3]} ${m[4]}:${m[5]}` : '';
}

/** 接口时间字符串（本地无时区，或带 Z 的 UTC）→ Date */
export function parseTime(text) {
    if (!text) return null;
    let s = String(text).trim();
    if (/^\d{4}-\d{2}-\d{2} \d/.test(s)) s = s.replace(' ', 'T');
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d;
}

export const hhmm = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
export const hhmmss = d => `${hhmm(d)}:${pad(d.getSeconds())}`;
export const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const mmdd = d => `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

/** 事件分组标题：今天 / 昨天 / 具体日期 */
export function dayTitle(d) {
    const today = new Date();
    const y = new Date(today.getTime() - 86400000);
    const same = (a, b) => ymd(a) === ymd(b);
    const date = `${d.getMonth() + 1} 月 ${d.getDate()} 日 · ${WEEK[d.getDay()]}`;
    if (same(d, today)) return ['今天', date];
    if (same(d, y)) return ['昨天', date];
    return [`${d.getMonth() + 1} 月 ${d.getDate()} 日`, WEEK[d.getDay()]];
}

export function fmtDuration(sec) {
    const s = Math.max(0, Math.round(Number(sec) || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return `${pad(h)}:${pad(m)}:${pad(r)}`;
}

export function fmtUptime(sec) {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d) return `${d} 天 ${h} 小时`;
    if (h) return `${h} 小时 ${m} 分`;
    return `${m} 分钟`;
}

export function fmtFileSize(bytes) {
    const n = Number(bytes) || 0;
    if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MB`;
    if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${n} B`;
}

/** 外网 IP 脱敏：保留首段和末段 */
export function maskIp(ip) {
    const s = String(ip || '');
    const m = s.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
    if (!m) return s;
    const [a, , , d] = m.slice(1);
    return `${a}.***.**.${d}`;
}

export function isLanIp(ip) {
    const s = String(ip || '');
    return /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.|169\.254\.)/.test(s) || s === '::1';
}
