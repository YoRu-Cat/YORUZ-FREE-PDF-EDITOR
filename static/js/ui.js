// Small UI toolkit: icons, DOM helper, toasts, modals, popover menus.

const P = {
  open: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  file: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5',
  fileplus: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5 M12 11v6 M9 14h6',
  merge: 'M8 7V5a2 2 0 0 1 2-2h7l3 3v11a2 2 0 0 1-2 2h-2 M4 9a2 2 0 0 1 2-2h6l3 3v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z',
  undo: 'M9 14L4 9l5-5 M4 9h10a6 6 0 0 1 0 12h-3',
  redo: 'M15 14l5-5-5-5 M20 9H10a6 6 0 0 0 0 12h3',
  minus: 'M5 12h14',
  plus: 'M12 5v14 M5 12h14',
  search: 'M11 4a7 7 0 1 1 0 14a7 7 0 0 1 0-14z M21 21l-5-5',
  info: 'M12 3a9 9 0 1 1 0 18a9 9 0 0 1 0-18z M12 11v6 M12 7.5v.5',
  download: 'M12 4v12 M7 11l5 5 5-5 M5 20h14',
  more: 'M5 12h.01 M12 12h.01 M19 12h.01',
  up: 'M6 15l6-6 6 6',
  down: 'M6 9l6 6 6-6',
  x: 'M6 6l12 12 M18 6L6 18',
  edit: 'M4 20h4L19 9l-4-4L4 16z M13 7l4 4',
  addtext: 'M4 7V5h12v2 M10 5v14 M7 19h6 M19 13v7 M15.5 16.5h7',
  erase: 'M7 20h13 M4.6 15.4l9-9a2 2 0 0 1 2.8 0l2.2 2.2a2 2 0 0 1 0 2.8L11 19H8.2z M9 11l5 5',
  redact: 'M3 5h18v14H3z M7 10h10 M7 14h6',
  whiteout: 'M4 5h16v14H4z M4 12l7-7 M4 19L18 5 M11 19l9-9',
  markup: 'M15 4l5 5L10 19H5v-5z M3 22h18',
  draw: 'M3 17c3-5 5-10 8-10s1 9 4 9 3-4 6-6',
  shape: 'M3 3h9v9H3z M17 12a4 4 0 1 1 0 8a4 4 0 0 1 0-8z M6 15l-3 6h6z',
  image: 'M3 5h18v14H3z M3 16l5-5 4 4 3-3 6 6 M15.5 8.5h.01',
  sign: 'M3 17c2 0 3-6 5-6s1 6 3 6 2-3 4-3 2 3 6 3 M3 21h18',
  note: 'M4 4h16v12H9l-5 4z M8 8h8 M8 12h5',
  forms: 'M3 4h18v6H3z M3 14h18v6H3z M6 7h5 M6 17h7',
  objects: 'M5 3l14 8-6 2-2 6z',
  rotl: 'M3 4v5h5 M3.5 9a8.5 8.5 0 1 1 2 8',
  rotr: 'M21 4v5h-5 M20.5 9a8.5 8.5 0 1 0-2 8',
  copy: 'M8 8h12v12H8z M4 16V4h12',
  trash: 'M4 7h16 M10 11v6 M14 11v6 M6 7l1 13h10l1-13 M9 7V4h6v3',
  check: 'M5 12l5 5L20 7',
  alignl: 'M4 6h16 M4 10h10 M4 14h16 M4 18h10',
  alignc: 'M4 6h16 M7 10h10 M4 14h16 M7 18h10',
  alignr: 'M4 6h16 M10 10h10 M4 14h16 M10 18h10',
  justify: 'M4 6h16 M4 10h16 M4 14h16 M4 18h16',
  replace: 'M4 7h12l-3-3 M20 17H8l3 3',
  lock: 'M5 11h14v10H5z M8 11V7a4 4 0 0 1 8 0v4',
  rect: 'M4 5h16v14H4z',
  ellipse: 'M12 5c4.4 0 8 3.1 8 7s-3.6 7-8 7-8-3.1-8-7 3.6-7 8-7z',
  line: 'M5 19L19 5',
  arrow: 'M5 19L19 5 M10 5h9v9',
  upload: 'M12 16V4 M7 9l5-5 5 5 M5 20h14',
  layers: 'M12 3l9 5-9 5-9-5z M3 13l9 5 9-5',
};

export function icon(name, size) {
  const d = P[name] || '';
  const s = size ? ` width="${size}" height="${size}"` : '';
  const sw = name === 'more' ? 3.2 : 1.8;
  return `<svg viewBox="0 0 24 24"${s} fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
}

/** Inject icons into every element with data-icon (keeps existing label text). */
export function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((el) => {
    if (el.dataset.iconDone) return;
    el.dataset.iconDone = '1';
    const label = el.textContent.trim();
    el.innerHTML = icon(el.dataset.icon) + (label ? `<span>${escapeHtml(label)}</span>` : '');
  });
}

export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** el('div.cls#id', {attrs/props/on*}, children...) */
export function el(spec, props = {}, ...children) {
  const m = spec.match(/^([a-z0-9-]+)?((?:[.#][\w-]+)*)$/i);
  const node = document.createElement((m && m[1]) || 'div');
  if (m && m[2]) {
    for (const part of m[2].match(/[.#][\w-]+/g)) {
      if (part[0] === '.') node.classList.add(part.slice(1));
      else node.id = part.slice(1);
    }
  }
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k in node && typeof v !== 'string') node[k] = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// ── toasts ─────────────────────────────────────────────
export function toast(message, kind = '', ms = 3200) {
  const box = document.getElementById('toasts');
  const t = el('div.toast', {}, message);
  if (kind) t.classList.add(kind);
  box.append(t);
  setTimeout(() => t.remove(), ms);
}

// ── modal ──────────────────────────────────────────────
/**
 * modal({title, body: Node|string, wide, buttons:[{label, value, primary, danger}]})
 * Resolves with the clicked button's value (or null when dismissed).
 * A button with `validate()` returning false keeps the modal open.
 */
export function modal({ title, body, buttons = [{ label: 'OK', value: true, primary: true }], wide = false, onOpen }) {
  return new Promise((resolve) => {
    const back = el('div.modal-back');
    const box = el('div.modal');
    if (wide) box.classList.add('wide');
    const content = typeof body === 'string' ? el('div', { html: body }) : body;
    const footer = el('footer');
    const close = (v) => { back.remove(); document.removeEventListener('keydown', onKey, true); resolve(v); };
    for (const b of buttons) {
      const btn = el('button.btn', { type: 'button' }, b.label);
      if (b.primary) btn.classList.add('primary');
      if (b.danger) btn.classList.add('danger');
      btn.addEventListener('click', async () => {
        if (b.validate && !(await b.validate())) return;
        close(b.value);
      });
      footer.append(btn);
    }
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); close(null); }
      if (e.key === 'Enter' && !(e.target instanceof HTMLTextAreaElement) && e.target.tagName !== 'BUTTON') {
        const primary = buttons.findIndex((b) => b.primary);
        if (primary >= 0) { e.preventDefault(); footer.children[primary].click(); }
      }
    };
    document.addEventListener('keydown', onKey, true);
    back.addEventListener('pointerdown', (e) => { if (e.target === back) close(null); });
    box.append(el('header', {}, title), el('div.body', {}, content), footer);
    back.append(box);
    document.body.append(back);
    hydrateIcons(box);
    const first = box.querySelector('input, textarea, select');
    (first || footer.querySelector('.primary'))?.focus();
    onOpen?.(box);
  });
}

export function field(label, input) {
  return el('label.field', {}, el('span', {}, label), input);
}

export async function prompt(title, { label = '', value = '', placeholder = '', type = 'text', multiline = false } = {}) {
  const input = multiline
    ? el('textarea', { rows: 4, placeholder }, value)
    : el('input', { type, value, placeholder });
  const ok = await modal({
    title, body: field(label, input),
    buttons: [{ label: 'Cancel', value: false }, { label: 'OK', value: true, primary: true }],
  });
  return ok ? input.value : null;
}

export function confirmBox(title, message, okLabel = 'OK', danger = false) {
  return modal({
    title, body: el('p', { style: { margin: 0 } }, message),
    buttons: [{ label: 'Cancel', value: false }, { label: okLabel, value: true, primary: !danger, danger }],
  });
}

// ── popover menu ───────────────────────────────────────
let openMenu = null;
export function closeMenu() { openMenu?.remove(); openMenu = null; }
export function menu(x, y, title, items) {
  closeMenu();
  const m = el('div.menu');
  if (title) m.append(el('div.menu-title', { title }, title));
  for (const it of items) {
    const b = el('button', { type: 'button', html: (it.icon ? icon(it.icon) : '') + `<span>${escapeHtml(it.label)}</span>` });
    if (it.danger) b.classList.add('danger');
    b.addEventListener('click', () => { closeMenu(); it.action(); });
    m.append(b);
  }
  document.body.append(m);
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 8) + 'px';
  m.style.top = Math.min(y, innerHeight - r.height - 8) + 'px';
  openMenu = m;
  setTimeout(() => document.addEventListener('pointerdown', function off(e) {
    if (!m.contains(e.target)) { closeMenu(); document.removeEventListener('pointerdown', off, true); }
  }, true));
}

export function parseRanges(text, count) {
  // "1-3, 5, 8-" -> zero-based page indices
  const out = new Set();
  for (const part of String(text).split(',').map((s) => s.trim()).filter(Boolean)) {
    const m = part.match(/^(\d*)\s*-\s*(\d*)$/);
    if (m) {
      const a = m[1] ? +m[1] : 1;
      const b = m[2] ? +m[2] : count;
      for (let i = Math.max(1, a); i <= Math.min(count, b); i++) out.add(i - 1);
    } else if (/^\d+$/.test(part) && +part >= 1 && +part <= count) out.add(+part - 1);
    else return null;
  }
  return [...out].sort((a, b) => a - b);
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
