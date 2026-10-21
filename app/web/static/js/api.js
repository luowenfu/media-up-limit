/* 接口封装：统一处理未登录跳转和错误提示 */

export class ApiError extends Error {
    constructor(message, status, body) {
        super(message);
        this.status = status;
        this.body = body;
    }
}

async function request(method, url, body) {
    const opts = { method, headers: {}, credentials: 'same-origin' };
    if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    let res;
    try {
        res = await fetch(url, opts);
    } catch (e) {
        throw new ApiError('网络错误', 0, null);
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* 非 JSON */ }
    if (res.status === 401 || (data && data.auth_required)) {
        window.location.replace('/login');
        throw new ApiError('未登录', 401, data);
    }
    if (data && data.setup_required) {
        window.location.replace('/setup');
        throw new ApiError((data && data.error) || '请先设置账号密码', res.status, data);
    }
    if (!res.ok || (data && data.success === false)) {
        throw new ApiError((data && data.error) || `请求失败 (${res.status})`, res.status, data);
    }
    return data;
}

export const get = url => request('GET', url);
export const post = (url, body) => request('POST', url, body ?? {});
export const put = (url, body) => request('PUT', url, body ?? {});
export const del = url => request('DELETE', url);

export const q = params => {
    const s = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== '') s.set(k, v);
    });
    const t = s.toString();
    return t ? `?${t}` : '';
};

export const enc = encodeURIComponent;
