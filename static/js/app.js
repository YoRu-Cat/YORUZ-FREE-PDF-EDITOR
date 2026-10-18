// Application shell: state, toolbar, options bar, file handling, dialogs, shortcuts.
import { api, ApiError, onBusy } from './api.js';
import {
  confirmBox, debounce, el, field, hydrateIcons, icon, modal, parseRanges, prompt, toast,
} from './ui.js';
import { Tools } from './tools.js';
import { Viewer } from './viewer.js';

const $ = (id) => document.getElementById(id);
const LS_SID = 'pdfe.sid';
const LS_OPTS = 'pdfe.opts';
const LS_SIG = 'pdfe.signature';

const TOOLS = [
  { id: 'edit', icon: 'edit', label: 'Edit text', key: 'v' },
  { id: 'addtext', icon: 'addtext', label: 'Add text', key: 't' },
  { id: 'erase', icon: 'erase', label: 'Erase text', key: 'e' },
  { id: 'redact', icon: 'redact', label: 'Erase / redact area', key: 'r' },
  { id: 'whiteout', icon: 'whiteout', label: 'Whiteout (cover)', key: 'w' },
  '-',
  { id: 'markup', icon: 'markup', label: 'Highlight / underline / strike', key: 'h' },
  { id: 'draw', icon: 'draw', label: 'Draw', key: 'p' },
  { id: 'shape', icon: 'shape', label: 'Shapes', key: 's' },
  { id: 'image', icon: 'image', label: 'Insert image', key: 'i' },
  { id: 'sign', icon: 'sign', label: 'Signature', key: 'g' },
  { id: 'note', icon: 'note', label: 'Comment', key: 'n' },
  '-',
  { id: 'forms', icon: 'forms', label: 'Fill forms', key: 'f' },
  { id: 'objects', icon: 'objects', label: 'Images & annotations', key: 'o' },
];

const DEFAULT_OPTS = {
  editMode: 'line',
  text: { font: 'base:helvetica', size: 14, color: '#000000', bold: false, italic: false, align: 'left' },
  redact: { fill: '#ffffff', text: true, images: true, graphics: true },
  whiteout: { color: '#ffffff' },
  markup: { kind: 'highlight', color: '#ffd400' },
  draw: { color: '#e5484d', width: 2, opacity: 1 },
  shape: { kind: 'rect', stroke: '#e5484d', fill: '#ffffff', fillOn: false, width: 2, opacity: 1 },
  note: { color: '#ffd400' },
};

function loadOpts() {
  try {
    const saved = JSON.parse(localStorage.getItem(LS_OPTS) || '{}');
    const o = structuredClone(DEFAULT_OPTS);
    for (const k of Object.keys(o)) {
      if (saved[k] === undefined) continue;
      o[k] = typeof o[k] === 'object' ? { ...o[k], ...saved[k] } : saved[k];
    }
    return o;
  } catch { return structuredClone(DEFAULT_OPTS); }
}

class App {
  constructor() {
    this.state = { doc: null, tool: 'edit', fonts: [], opts: loadOpts() };
    this.queue = Promise.resolve();
    this.viewer = new Viewer(this);
    this.tools = new Tools(this);
    this.saveOpts = debounce(() => {
      try { localStorage.setItem(LS_OPTS, JSON.stringify(this.state.opts)); } catch { /* private mode */ }
    }, 300);
    this.find = { hits: [], index: -1, query: '' };
    hydrateIcons();
    this.buildRail();
    this.bind();
    onBusy((b) => { $('busy').hidden = !b; });
    api.fonts().then((f) => { this.state.fonts = f; this.renderOptbar(); }).catch(() => {});
    this.restore();
  }

  // ── session ────────────────────────────────────────
  async restore() {
    let sid = null;
    try { sid = localStorage.getItem(LS_SID); } catch { /* ignore */ }
    if (!sid) return;
    try {
      this.setDoc(await api.get(sid));
    } catch {
      try { localStorage.removeItem(LS_SID); } catch { /* ignore */ }
    }
  }

  setDoc(doc) {
    const first = !this.state.doc || this.state.doc.id !== doc.id;
    this.state.doc = doc;
    try { localStorage.setItem(LS_SID, doc.id); } catch { /* ignore */ }
    $('welcome').hidden = true;
    for (const id of ['workspace', 'optbar', 'docControls', 'docActions', 'docNameWrap']) $(id).hidden = false;
    $('docName').value = doc.name;
    document.title = `${doc.name} – PDF Editor`;
    this.updateUndo();
    if (first) {
      this.closeFind();
      this.viewer.build();
      requestAnimationFrame(() => {
        this.viewer.fitWidth();
        if (this.viewer.zoom > 1.6) this.viewer.setZoom(1.6);
        this.viewer.scroller.scrollTop = 0;
      });
      this.renderOptbar();
      if (doc.has_forms) toast('This PDF has form fields – use the "Fill forms" tool to fill them.');
    }
  }

  /** Apply a state returned by a mutation, refresh affected pages. */
  async applyState(doc) {
    this.state.doc = doc;
    $('docName').value = doc.name;
    this.updateUndo();
    if (this.find.hits.length) this.clearHits();
    await this.viewer.refresh(doc.changed);
  }

  updateUndo() {
    $('btnUndo').disabled = !this.state.doc?.can_undo;
    $('btnRedo').disabled = !this.state.doc?.can_redo;
  }

  updatePager() {
    const d = this.state.doc;
    if (!d) return;
    $('pageCount').textContent = d.page_count;
    if (document.activeElement !== $('pageInput')) $('pageInput').value = this.viewer.current + 1;
    this.viewer.markCurrentThumb();
  }

  updateZoomLabel() {
    $('zoomLabel').textContent = `${Math.round(this.viewer.zoom * 100)}%`;
  }

  handleError(e) {
    if (e instanceof ApiError && e.status === 404 && e.code === 'not_found') {
      toast(e.message, 'error', 5000);
      this.closeDoc();
      return;
    }
    toast(e.message || String(e), 'error', 5000);
    if (e instanceof ApiError && e.code === 'edit_failed' && this.state.doc) {
      // page content may have moved on: refresh its overlays
      this.viewer.refresh(this.viewer.pages.map((p) => p.i)).catch(() => {});
    }
  }

  /** Serialised, undoable edit. Resolves to the new state (or null on error). */
  mutate(path, body) {
    const run = async () => {
      try {
        const doc = await api.op(this.state.doc.id, path, body);
        await this.applyState(doc);
        return doc;
      } catch (e) {
        this.handleError(e);
        return null;
      }
    };
    const p = this.queue.then(run);
    this.queue = p.catch(() => {});
    return p;
  }

  mutateForm(path, fields) {
    const run = async () => {
      try {
        const doc = await api.form(this.state.doc.id, path, fields);
        await this.applyState(doc);
        return doc;
      } catch (e) {
        this.handleError(e);
        return null;
      }
    };
    const p = this.queue.then(run);
    this.queue = p.catch(() => {});
    return p;
  }

  pageOp(op, body) { return this.mutate(`pages/${op}`, body); }

  async deletePage(i) {
    if (this.state.doc.page_count <= 1) { toast('A document needs at least one page'); return; }
    if (await confirmBox('Delete page', `Delete page ${i + 1}? You can undo this.`, 'Delete', true)) {
      this.pageOp('delete', { pages: [i] });
    }
  }

  async undo() {
    if (!this.state.doc?.can_undo) return;
    this.tools.closeEditors(false);
    try { await this.applyState(await api.undo(this.state.doc.id)); } catch (e) { this.handleError(e); }
  }

  async redo() {
    if (!this.state.doc?.can_redo) return;
    this.tools.closeEditors(false);
    try { await this.applyState(await api.redo(this.state.doc.id)); } catch (e) { this.handleError(e); }
  }

  closeDoc() {
    this.state.doc = null;
    try { localStorage.removeItem(LS_SID); } catch { /* ignore */ }
    for (const id of ['workspace', 'optbar', 'docControls', 'docActions', 'docNameWrap']) $(id).hidden = true;
    $('welcome').hidden = false;
    document.title = 'PDF Editor';
  }

  // ── opening files ──────────────────────────────────
  async openFile(file, password = null) {
    if (!file) return;
    try {
      const doc = await api.upload(file, password);
      const old = this.state.doc?.id;
      this.state.doc = null;
      this.setDoc(doc);
      if (old && old !== doc.id) api.remove(old).catch(() => {});
    } catch (e) {
      if (e instanceof ApiError && e.code === 'password_required') {
        const pw = await prompt(password ? 'Wrong password – try again' : 'Password protected PDF',
          { label: `Enter the password for "${file.name}"`, type: 'password' });
        if (pw) this.openFile(file, pw);
        return;
      }
      this.handleError(e);
    }
  }

  async newBlank() {
    const size = el('select', {}, ...['a4', 'letter', 'legal', 'a3', 'a5'].map((s) => el('option', { value: s }, s.toUpperCase())));
    const land = el('input', { type: 'checkbox' });
    const ok = await modal({
      title: 'New blank document',
      body: el('div', {}, field('Page size', size), el('label.check', { style: { marginTop: '10px' } }, land, 'Landscape')),
      buttons: [{ label: 'Cancel', value: false }, { label: 'Create', value: true, primary: true }],
    });
    if (!ok) return;
    try {
      const old = this.state.doc?.id;
      this.state.doc = null;
      this.setDoc(await api.blank(size.value, land.checked));
      if (old) api.remove(old).catch(() => {});
    } catch (e) { this.handleError(e); }
  }

  async insertFile(file) {
    if (!file || !this.state.doc) return;
    const at = this.viewer.current + 1;
    const res = await this.mutateForm('pages/insert-file', { at, file });
    if (res) toast(`Inserted pages from ${file.name}`, 'ok');
  }

  chooseImage() { $('imageInput').value = ''; $('imageInput').click(); }

  async replaceImage(page, xref) {
    const input = el('input', { type: 'file', accept: 'image/*' });
    input.addEventListener('change', () => {
      const f = input.files[0];
      if (f) this.mutateForm('images/replace', { page, xref, file: f });
    });
    input.click();
  }

  // ── download / export ──────────────────────────────
  async download(body = {}, suffix = '') {
    if (!this.state.doc) return;
    this.tools.closeEditors(true);
    await this.queue;
    try {
      const blob = await api.exportPdf(this.state.doc.id, body);
      const base = this.state.doc.name.replace(/\.pdf$/i, '');
      const a = el('a', { href: URL.createObjectURL(blob), download: `${base}${suffix ? '-' + suffix : ''}.pdf` });
      document.body.append(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
    } catch (e) { this.handleError(e); }
  }

  async exportDialog() {
    const range = el('input', { placeholder: `All pages (e.g. 1-3, 5)` });
    const pw = el('input', { type: 'password', placeholder: 'No password', autocomplete: 'new-password' });
    const flat = el('input', { type: 'checkbox' });
    const body = el('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } },
      field('Pages', range),
      field('Protect with password (AES-256)', pw),
      el('label.check', {}, flat, 'Flatten annotations & form fields into the page'));
    let pages = null;
    const ok = await modal({
      title: 'Download options', body,
      buttons: [{ label: 'Cancel', value: false }, {
        label: 'Download', value: true, primary: true,
        validate: () => {
          if (!range.value.trim()) { pages = null; return true; }
          pages = parseRanges(range.value, this.state.doc.page_count);
          if (!pages || !pages.length) { toast('Invalid page range', 'error'); return false; }
          return true;
        },
      }],
    });
    if (ok) this.download({ pages, password: pw.value || null, flatten: flat.checked }, pages ? 'extract' : '');
  }

  async propertiesDialog() {
    const m = this.state.doc.metadata || {};
    const inputs = {};
    const body = el('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px' } });
    for (const k of ['title', 'author', 'subject', 'keywords']) {
      inputs[k] = el('input', { value: m[k] || '' });
      body.append(field(k[0].toUpperCase() + k.slice(1), inputs[k]));
    }
    body.append(el('p.muted.small', { style: { margin: 0 } },
      `${this.state.doc.page_count} page(s) · produced by ${m.producer || 'unknown'}`));
    const flat = el('button.btn.sm', { type: 'button' }, 'Flatten annotations & forms');
    flat.addEventListener('click', async () => {
      if (await confirmBox('Flatten', 'Burn all annotations and form fields into the page content? They will no longer be editable (you can undo).', 'Flatten')) {
        this.mutate('flatten');
      }
    });
    body.append(flat);
    const ok = await modal({
      title: 'Document properties', body,
      buttons: [{ label: 'Cancel', value: false }, { label: 'Save', value: true, primary: true }],
    });
    if (!ok) return;
    const changed = {};
    for (const [k, inp] of Object.entries(inputs)) if ((m[k] || '') !== inp.value) changed[k] = inp.value;
    if (Object.keys(changed).length) this.mutate('metadata', changed);
  }

  // ── signature pad ──────────────────────────────────
  async openSignature() {
    let saved = null;
    try { saved = localStorage.getItem(LS_SIG); } catch { /* ignore */ }
    const canvas = el('canvas.sig-canvas');
    const color = el('input', { type: 'color', value: '#1a2b8c', title: 'Ink colour' });
    const width = el('input', { type: 'range', min: 1, max: 8, step: 0.5, value: 2.5, title: 'Thickness' });
    const clear = el('button.btn.sm', { type: 'button' }, 'Clear');
    const upload = el('button.btn.sm', { type: 'button' }, 'Upload image…');
    const useSaved = el('button.btn.sm', { type: 'button', hidden: !saved }, 'Use saved signature');
    const remember = el('input', { type: 'checkbox', checked: true });
    let uploaded = null;
    let drawn = false;
    const body = el('div', {},
      el('p.muted.small', { style: { margin: '0 0 8px' } }, 'Draw your signature below, then click on the page to place it (drag to set its size).'),
      canvas,
      el('div.sig-tools', { style: { marginTop: '8px' } }, color, width, clear, upload, useSaved,
        el('label.check', { style: { marginLeft: 'auto' } }, remember, 'Remember')));

    const setup = () => {
      const dpr = window.devicePixelRatio || 1;
      const r = canvas.getBoundingClientRect();
      canvas.width = r.width * dpr;
      canvas.height = r.height * dpr;
      const ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      let last = null;
      canvas.addEventListener('pointerdown', (e) => {
        canvas.setPointerCapture(e.pointerId);
        last = [e.offsetX, e.offsetY];
        uploaded = null;
      });
      canvas.addEventListener('pointermove', (e) => {
        if (!last) return;
        const evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
        for (const ce of evs) {
          ctx.strokeStyle = color.value;
          ctx.lineWidth = +width.value * (0.75 + 0.5 * (ce.pressure || 0.5));
          ctx.beginPath();
          ctx.moveTo(...last);
          ctx.lineTo(ce.offsetX, ce.offsetY);
          ctx.stroke();
          last = [ce.offsetX, ce.offsetY];
        }
        drawn = true;
      });
      const stop = () => { last = null; };
      canvas.addEventListener('pointerup', stop);
      canvas.addEventListener('pointercancel', stop);
      clear.addEventListener('click', () => { ctx.clearRect(0, 0, canvas.width, canvas.height); drawn = false; uploaded = null; });
      upload.addEventListener('click', () => {
        const inp = el('input', { type: 'file', accept: 'image/*' });
        inp.addEventListener('change', () => {
          const f = inp.files[0];
          if (!f) return;
          uploaded = f;
          const img = new Image();
          img.onload = () => {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            const s = Math.min(r.width / img.width, r.height / img.height);
            ctx.drawImage(img, (r.width - img.width * s) / 2, (r.height - img.height * s) / 2, img.width * s, img.height * s);
            drawn = true;
          };
          img.src = URL.createObjectURL(f);
        });
        inp.click();
      });
      useSaved.addEventListener('click', () => {
        const img = new Image();
        img.onload = () => {
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          const s = Math.min(r.width / img.width, r.height / img.height, 1);
          ctx.drawImage(img, (r.width - img.width * s) / 2, (r.height - img.height * s) / 2, img.width * s, img.height * s);
          drawn = true;
        };
        img.src = saved;
      });
    };

    const ok = await modal({
      title: 'Signature', body, wide: true, onOpen: () => requestAnimationFrame(setup),
      buttons: [{ label: 'Cancel', value: false }, {
        label: 'Use signature', value: true, primary: true,
        validate: () => { if (!drawn) toast('Draw or upload a signature first'); return drawn; },
      }],
    });
    if (!ok) return;
    let blob = uploaded;
    if (!blob) {
      const trimmed = trimCanvas(canvas);
      blob = await new Promise((res) => trimmed.toBlob(res, 'image/png'));
      if (remember.checked) {
        try { localStorage.setItem(LS_SIG, trimmed.toDataURL('image/png')); } catch { /* quota */ }
      }
    }
    this.tools.setPendingImage(blob, { sign: true });
    this.setTool('sign', true);
    toast('Click on the page to place your signature');
  }

  // ── find & replace ─────────────────────────────────
  openFind() {
    $('findPanel').hidden = false;
    $('findInput').focus();
    $('findInput').select();
  }

  closeFind() {
    $('findPanel').hidden = true;
    this.clearHits();
  }

  clearHits() {
    this.find = { hits: [], index: -1, query: '' };
    this.viewer.setHits([]);
    $('findCount').textContent = '';
  }

  async runFind() {
    const q = $('findInput').value;
    if (!q || !this.state.doc) { this.clearHits(); return; }
    try {
      const hits = await api.search(this.state.doc.id, q, $('findCase').checked);
      this.find = { hits, index: hits.length ? 0 : -1, query: q };
      this.showHit();
    } catch (e) { this.handleError(e); }
  }

  showHit() {
    const { hits, index } = this.find;
    $('findCount').textContent = hits.length ? `${index + 1}/${hits.length}` : 'none';
    this.viewer.setHits(hits, index);
    if (index >= 0) this.viewer.scrollToPage(hits[index].page, hits[index].bbox);
  }

  stepHit(d) {
    const n = this.find.hits.length;
    if (!n) { this.runFind(); return; }
    this.find.index = (this.find.index + d + n) % n;
    this.showHit();
  }

  async replaceAll() {
    const find = $('findInput').value;
    if (!find) return;
    const res = await this.mutate('text/replace', { find, replace: $('replaceInput').value, match_case: $('findCase').checked });
    if (res) toast(res.replaced ? `Replaced ${res.replaced} occurrence(s)` : 'No matches found', res.replaced ? 'ok' : '');
  }

  // ── tools & options bar ────────────────────────────
  buildRail() {
    const rail = $('rail');
    for (const t of TOOLS) {
      if (t === '-') { rail.append(el('div.rail-sep')); continue; }
      const b = el('button', { type: 'button', dataset: { tool: t.id, tip: `${t.label} (${t.key.toUpperCase()})` }, html: icon(t.icon), 'aria-label': t.label });
      b.addEventListener('click', () => this.setTool(t.id));
      rail.append(b);
    }
    this.markRail();
  }

  markRail() {
    $('rail').querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.tool === this.state.tool));
  }

  setTool(id, keepPending = false) {
    if (this.state.tool === id && !keepPending) {
      if (id === 'image') this.chooseImage();
      if (id === 'sign') this.openSignature();
      return;
    }
    this.tools.closeEditors(true);
    if (!keepPending) this.tools.setPendingImage(null);
    this.state.tool = id;
    document.body.dataset.tool = id;
    this.markRail();
    this.renderOptbar();
    this.viewer.reloadVisible();
    if (!keepPending && id === 'image') this.chooseImage();
    if (!keepPending && id === 'sign') this.openSignature();
  }

  renderOptbar() {
    const bar = $('optbar');
    bar.innerHTML = '';
    const o = this.state.opts;
    const tool = TOOLS.find((t) => t.id === this.state.tool);
    bar.append(el('span.title', {}, tool?.label || ''));
    const hint = (t) => el('span.hint', {}, t);
    const save = () => this.saveOpts();
    const seg = (items, value, onPick) => {
      const s = el('div.seg');
      for (const [val, label, ic] of items) {
        const b = el('button', { type: 'button', html: (ic ? icon(ic) : '') + (label ? `<span>${label}</span>` : ''), title: label || val });
        b.classList.toggle('on', val === value);
        b.addEventListener('click', () => { onPick(val); s.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b)); save(); });
        s.append(b);
      }
      return s;
    };
    const color = (obj, key, title = 'Colour') => {
      const c = el('input', { type: 'color', value: obj[key], title });
      c.addEventListener('input', () => { obj[key] = c.value; save(); this.tools.newText?.restyle(); });
      return c;
    };
    const num = (obj, key, { min = 0.5, max = 200, step = 0.5, title = '' } = {}) => {
      const n = el('input', { type: 'number', min, max, step, value: obj[key], title });
      n.addEventListener('input', () => { if (+n.value > 0) { obj[key] = +n.value; save(); this.tools.newText?.restyle(); } });
      return n;
    };
    const check = (obj, key, label) => {
      const c = el('input', { type: 'checkbox', checked: !!obj[key] });
      c.addEventListener('change', () => { obj[key] = c.checked; save(); });
      return el('label', {}, c, label);
    };
    const range = (obj, key, min, max, step, label) => {
      const r = el('input', { type: 'range', min, max, step, value: obj[key] });
      r.addEventListener('input', () => { obj[key] = +r.value; save(); });
      return el('label', {}, label, r);
    };

    switch (this.state.tool) {
      case 'edit':
        bar.append(seg([['line', 'Line'], ['para', 'Paragraph']], o.editMode, (v) => { o.editMode = v; this.viewer.setEditMode(v); }),
          hint('Click any text to change it. The original glyphs are removed from the PDF and rewritten in a matching font. Delete all text to erase it.'));
        break;
      case 'addtext': {
        const t = o.text;
        const font = el('select', { title: 'Font' });
        const list = this.state.fonts.length ? this.state.fonts : [{ key: 'base:helvetica', name: 'Helvetica (standard)' }];
        for (const f of list) font.append(el('option', { value: f.key }, f.name));
        font.value = t.font;
        font.addEventListener('change', () => { t.font = font.value; save(); this.tools.newText?.restyle(); });
        const tog = (key, label, cls) => {
          const b = el(`button.toggle${cls}`, { type: 'button', title: key }, label);
          b.classList.toggle('on', t[key]);
          b.addEventListener('click', () => { t[key] = !t[key]; b.classList.toggle('on', t[key]); save(); this.tools.newText?.restyle(); });
          return b;
        };
        bar.append(font, num(t, 'size', { min: 1, max: 400, title: 'Size (pt)' }), color(t, 'color'), tog('bold', 'B', ''), tog('italic', 'I', '.i'),
          seg([['left', '', 'alignl'], ['center', '', 'alignc'], ['right', '', 'alignr']], t.align, (v) => { t.align = v; this.tools.newText?.restyle(); }),
          hint('Click on the page to start typing · Ctrl+Enter or click outside to place'));
        break;
      }
      case 'erase':
        bar.append(hint('Drag over text to permanently delete those characters from the PDF. Images and drawings stay untouched.'));
        break;
      case 'redact': {
        const r = o.redact;
        bar.append(el('label', {}, 'Fill', seg([['#ffffff', 'White'], ['#000000', 'Black'], ['none', 'None']], r.fill, (v) => { r.fill = v; })),
          check(r, 'text', 'Text'), check(r, 'images', 'Images'), check(r, 'graphics', 'Drawings'),
          hint('Drag a box: everything selected inside is removed from the file for good.'));
        break;
      }
      case 'whiteout':
        bar.append(el('label', {}, 'Colour', color(o.whiteout, 'color')), hint('Drag to cover an area with a solid box (content underneath is kept – use "Erase area" to delete it).'));
        break;
      case 'markup': {
        const m = o.markup;
        bar.append(seg([['highlight', 'Highlight'], ['underline', 'Underline'], ['strikeout', 'Strike'], ['squiggly', 'Squiggly']], m.kind, (v) => { m.kind = v; }),
          color(m, 'color'), hint('Drag over text'));
        break;
      }
      case 'draw': {
        const d = o.draw;
        bar.append(color(d, 'color'), range(d, 'width', 0.5, 20, 0.5, 'Width'), range(d, 'opacity', 0.1, 1, 0.05, 'Opacity'), hint('Draw freehand on the page'));
        break;
      }
      case 'shape': {
        const s = o.shape;
        const fillChk = el('input', { type: 'checkbox', checked: s.fillOn });
        fillChk.addEventListener('change', () => { s.fillOn = fillChk.checked; save(); });
        bar.append(seg([['rect', '', 'rect'], ['ellipse', '', 'ellipse'], ['line', '', 'line'], ['arrow', '', 'arrow']], s.kind, (v) => { s.kind = v; }),
          el('label', {}, 'Stroke', color(s, 'stroke')), el('label', {}, fillChk, 'Fill', color(s, 'fill')),
          range(s, 'width', 0.5, 20, 0.5, 'Width'), range(s, 'opacity', 0.1, 1, 0.05, 'Opacity'), hint('Shift = straight / square'));
        break;
      }
      case 'image': {
        const b = el('button.btn.sm', { type: 'button', html: `${icon('upload')}<span>Choose image…</span>` });
        b.addEventListener('click', () => this.chooseImage());
        bar.append(b, hint(this.tools.pendingImage ? 'Click to place, or drag to set the size' : 'Choose an image, then click or drag on the page'));
        break;
      }
      case 'sign': {
        const b = el('button.btn.sm', { type: 'button', html: `${icon('sign')}<span>Create signature…</span>` });
        b.addEventListener('click', () => this.openSignature());
        bar.append(b, hint(this.tools.pendingImage ? 'Click to place your signature (drag to size). Place as many as you need.' : 'Create a signature first'));
        break;
      }
      case 'note':
        bar.append(color(o.note, 'color'), hint('Click on the page to add a comment'));
        break;
      case 'forms': {
        const b = el('button.btn.sm', { type: 'button' }, 'Flatten forms…');
        b.addEventListener('click', async () => {
          if (await confirmBox('Flatten', 'Burn form fields and annotations into the page content? (Undo is available.)', 'Flatten')) this.mutate('flatten');
        });
        bar.append(hint(this.state.doc?.has_forms ? 'Fill in the highlighted fields – changes are saved into the PDF form.' : 'This document has no form fields.'), b);
        break;
      }
      case 'objects':
        bar.append(hint('Click an image to replace or delete it, or an annotation to remove it.'));
        break;
      default:
    }
  }

  // ── wiring ─────────────────────────────────────────
  bind() {
    const open = () => { $('fileInput').value = ''; $('fileInput').click(); };
    $('btnOpen').onclick = open;
    $('btnOpen2').onclick = open;
    $('btnNew').onclick = () => this.newBlank();
    $('btnNew2').onclick = () => this.newBlank();
    $('fileInput').onchange = (e) => this.openFile(e.target.files[0]);
    $('insertInput').onchange = (e) => this.insertFile(e.target.files[0]);
    $('imageInput').onchange = (e) => {
      const f = e.target.files[0];
      if (!f) return;
      this.tools.setPendingImage(f);
      this.setTool('image', true);
      toast('Click on the page to place the image (or drag to size it)');
    };
    $('btnUndo').onclick = () => this.undo();
    $('btnRedo').onclick = () => this.redo();
    $('btnZoomIn').onclick = () => this.viewer.setZoom(this.viewer.zoom * 1.2);
    $('btnZoomOut').onclick = () => this.viewer.setZoom(this.viewer.zoom / 1.2);
    $('zoomLabel').onclick = () => this.viewer.fitWidth();
    $('btnDownload').onclick = () => this.download();
    $('btnExportOpts').onclick = () => this.exportDialog();
    $('btnProps').onclick = () => this.propertiesDialog();
    $('btnFind').onclick = () => ($('findPanel').hidden ? this.openFind() : this.closeFind());
    $('btnAddBlank').onclick = () => this.pageOp('insert-blank', { at: this.viewer.current + 1 });
    $('btnInsertFile').onclick = () => { $('insertInput').value = ''; $('insertInput').click(); };
    $('docName').addEventListener('change', async () => {
      const name = $('docName').value.trim();
      if (!name) { $('docName').value = this.state.doc.name; return; }
      try { this.state.doc = await api.op(this.state.doc.id, 'metadata', { name }); $('docName').value = this.state.doc.name; } catch (e) { this.handleError(e); }
    });
    $('docName').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });
    $('pageInput').addEventListener('change', () => {
      const n = parseInt($('pageInput').value, 10);
      if (n >= 1 && n <= this.state.doc.page_count) this.viewer.scrollToPage(n - 1);
      else this.updatePager();
    });

    // find panel
    $('findInput').addEventListener('input', debounce(() => this.runFind(), 300));
    $('findInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); this.stepHit(e.shiftKey ? -1 : 1); }
      if (e.key === 'Escape') this.closeFind();
    });
    $('findCase').onchange = () => this.runFind();
    $('findNext').onclick = () => this.stepHit(1);
    $('findPrev').onclick = () => this.stepHit(-1);
    $('findClose').onclick = () => this.closeFind();
    $('replaceAll').onclick = () => this.replaceAll();

    // drag & drop files
    let depth = 0;
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
    window.addEventListener('dragenter', (e) => { if (hasFiles(e)) { depth++; $('dropOverlay').hidden = false; } });
    window.addEventListener('dragleave', (e) => { if (hasFiles(e) && --depth <= 0) { depth = 0; $('dropOverlay').hidden = true; } });
    window.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
    window.addEventListener('drop', async (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      $('dropOverlay').hidden = true;
      const f = e.dataTransfer.files[0];
      if (!f) return;
      if (this.state.doc && f.type.startsWith('image/') && ['image', 'edit', 'sign'].includes(this.state.tool)) {
        this.tools.setPendingImage(f);
        this.setTool('image', true);
        toast('Click on the page to place the image');
        return;
      }
      if (this.state.doc) {
        const choice = await modal({
          title: 'Dropped a file',
          body: el('p', { style: { margin: 0 } }, `What should happen with "${f.name}"?`),
          buttons: [{ label: 'Cancel', value: null }, { label: 'Insert pages', value: 'insert' }, { label: 'Open', value: 'open', primary: true }],
        });
        if (choice === 'insert') this.insertFile(f);
        else if (choice === 'open') this.openFile(f);
        return;
      }
      this.openFile(f);
    });

    // keyboard shortcuts
    window.addEventListener('keydown', (e) => {
      const typing = e.target.closest?.('input, textarea, select, [contenteditable="true"], [contenteditable="plaintext-only"]');
      const mod = e.ctrlKey || e.metaKey;
      const k = e.key.toLowerCase();
      if (mod && k === 'o') { e.preventDefault(); open(); return; }
      if (!this.state.doc) return;
      if (mod && k === 's') { e.preventDefault(); this.download(); return; }
      if (mod && k === 'f') { e.preventDefault(); this.openFind(); return; }
      if (mod && (k === '=' || k === '+')) { e.preventDefault(); this.viewer.setZoom(this.viewer.zoom * 1.2); return; }
      if (mod && k === '-') { e.preventDefault(); this.viewer.setZoom(this.viewer.zoom / 1.2); return; }
      if (mod && k === '0') { e.preventDefault(); this.viewer.fitWidth(); return; }
      if (typing) return;
      if (mod && k === 'z' && !e.shiftKey) { e.preventDefault(); this.undo(); return; }
      if (mod && (k === 'y' || (k === 'z' && e.shiftKey))) { e.preventDefault(); this.redo(); return; }
      if (mod || e.altKey) return;
      if (k === 'escape') { this.closeFind(); return; }
      if (k === 'pagedown') { this.viewer.scrollToPage(Math.min(this.viewer.current + 1, this.viewer.pages.length - 1)); e.preventDefault(); return; }
      if (k === 'pageup') { this.viewer.scrollToPage(Math.max(this.viewer.current - 1, 0)); e.preventDefault(); return; }
      const t = TOOLS.find((x) => x !== '-' && x.key === k);
      if (t) { e.preventDefault(); this.setTool(t.id); }
    });

    window.addEventListener('beforeunload', () => this.tools.closeEditors(true));
  }
}

/** Crop transparent margins of a signature canvas. */
function trimCanvas(src) {
  const ctx = src.getContext('2d');
  const { width: w, height: h } = src;
  const data = ctx.getImageData(0, 0, w, h).data;
  let x0 = w; let y0 = h; let x1 = -1; let y1 = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (data[(y * w + x) * 4 + 3] > 8) {
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
  }
  if (x1 < 0) return src;
  const pad = 6;
  x0 = Math.max(0, x0 - pad); y0 = Math.max(0, y0 - pad);
  x1 = Math.min(w - 1, x1 + pad); y1 = Math.min(h - 1, y1 + pad);
  const out = document.createElement('canvas');
  out.width = x1 - x0 + 1;
  out.height = y1 - y0 + 1;
  out.getContext('2d').drawImage(src, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
  return out;
}

window.pdfEditor = new App();
