// Thin client for the backend REST API.

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const BASE = (window.PDFE_API_BASE || '').replace(/\/$/, '');
let inflight = 0;
const busyListeners = new Set();
export const onBusy = (fn) => busyListeners.add(fn);
const setBusy = (d) => { inflight += d; busyListeners.forEach((fn) => fn(inflight > 0)); };

async function request(method, path, body, { raw = false } = {}) {
  setBusy(1);
  try {
    const opts = { method, headers: {} };
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(BASE + path, opts);
    } catch {
      throw new ApiError(0, 'network', 'Cannot reach the server. Is it running?');
    }
    if (!res.ok) {
      let j = {};
      try { j = await res.json(); } catch { /* not json */ }
      let msg = j.message || res.statusText;
      if (Array.isArray(j.detail)) msg = j.detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ');
      throw new ApiError(res.status, j.error || 'http_error', msg);
    }
    return raw ? res : res.json();
  } finally {
    setBusy(-1);
  }
}

const doc = (sid) => `/api/documents/${sid}`;

export const api = {
  fonts: () => request('GET', '/api/fonts'),
  upload(file, password) {
    const fd = new FormData();
    fd.append('file', file);
    if (password) fd.append('password', password);
    return request('POST', '/api/documents', fd);
  },
  blank: (size = 'a4', landscape = false) => request('POST', '/api/documents/blank', { size, landscape }),
  get: (sid) => request('GET', doc(sid)),
  remove: (sid) => request('DELETE', doc(sid)),
  undo: (sid) => request('POST', `${doc(sid)}/undo`),
  redo: (sid) => request('POST', `${doc(sid)}/redo`),
  text: (sid, p) => request('GET', `${doc(sid)}/pages/${p}/text`),
  objects: (sid, p) => request('GET', `${doc(sid)}/pages/${p}/objects`),
  search: (sid, q, matchCase) => request('GET', `${doc(sid)}/search?q=${encodeURIComponent(q)}&match_case=${!!matchCase}`),
  /** JSON POST to a document sub-resource, e.g. op(sid, 'text/line', {...}) */
  op: (sid, path, body = {}) => request('POST', `${doc(sid)}/${path}`, body),
  /** multipart POST to a document sub-resource */
  form(sid, path, fields) {
    const fd = new FormData();
    for (const [k, v] of Object.entries(fields)) if (v !== undefined && v !== null) fd.append(k, v);
    return request('POST', `${doc(sid)}/${path}`, fd);
  },
  async exportPdf(sid, body) {
    const res = await request('POST', `${doc(sid)}/export`, body, { raw: true });
    return res.blob();
  },
  imageUrl: (sid, p, scale, v) => `${BASE}${doc(sid)}/pages/${p}/image?scale=${scale.toFixed(3)}&v=${v}`,
};
