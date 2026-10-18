// Page viewer: lazy page rendering, overlay layers, thumbnails, zoom.
import { api } from './api.js';
import { el, icon } from './ui.js';

const SVGNS = 'http://www.w3.org/2000/svg';
const THUMB_W = 132;
const pct = (v, total) => `${(v / total) * 100}%`;

export class Viewer {
  constructor(app) {
    this.app = app;
    this.scroller = document.getElementById('viewer');
    this.pagesEl = document.getElementById('pages');
    this.thumbsEl = document.getElementById('thumbs');
    this.pages = [];
    this.pver = [];          // per-page content version (cache key for images)
    this.zoom = 1;
    this.current = 0;
    this.hits = [];
    this.activeHit = -1;
    this.observer = new IntersectionObserver((entries) => this.onIntersect(entries),
      { root: this.scroller, rootMargin: '900px 0px' });
    let raf = 0;
    this.scroller.addEventListener('scroll', () => {
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; this.updateCurrent(); });
    }, { passive: true });
    this.scroller.addEventListener('wheel', (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      this.setZoom(this.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
    }, { passive: false });
    this.setupThumbDnD();
  }

  get doc() { return this.app.state.doc; }
  get sid() { return this.app.state.doc?.id; }

  // ── building ───────────────────────────────────────
  build() {
    const d = this.doc;
    this.observer.disconnect();
    this.pagesEl.innerHTML = '';
    this.pages = [];
    this.pver = d.pages.map(() => d.version);
    d.pages.forEach((pg, i) => {
      const root = el('div.page', { dataset: { pno: i } });
      const img = el('img.pimg', { alt: '', draggable: false, decoding: 'async' });
      const hit = el('div.layer.hit-layer');
      const text = el('div.layer.text-layer');
      const obj = el('div.layer.obj-layer');
      const form = el('div.layer.form-layer');
      const svg = document.createElementNS(SVGNS, 'svg');
      svg.classList.add('draw');
      svg.setAttribute('viewBox', `0 0 ${pg.w} ${pg.h}`);
      svg.setAttribute('preserveAspectRatio', 'none');
      root.append(img, hit, text, obj, form, svg, el('div.pno', {}, `${i + 1}`));
      this.pagesEl.append(root);
      this.pages.push({ i, root, img, hit, text, obj, form, svg, w: pg.w, h: pg.h,
        imgKey: null, textData: null, textVer: null, objData: null, objVer: null, visible: false });
      this.observer.observe(root);
    });
    this.layout();
    this.buildThumbs();
    this.renderHits();
    this.app.updatePager();
  }

  layout() {
    this.pagesEl.style.setProperty('--z', this.zoom);
    for (const p of this.pages) {
      p.root.style.width = `${p.w * this.zoom}px`;
      p.root.style.height = `${p.h * this.zoom}px`;
    }
    this.reloadVisible();
  }

  onIntersect(entries) {
    for (const e of entries) {
      const p = this.pages[+e.target.dataset.pno];
      if (!p) continue;
      p.visible = e.isIntersecting;
      if (p.visible) this.ensure(p.i);
    }
  }

  reloadVisible() {
    for (const p of this.pages) if (p.visible) this.ensure(p.i);
  }

  /** Make sure image + whatever overlay data the active tool needs are fresh. */
  ensure(i) {
    this.loadImage(i);
    const tool = this.app.state.tool;
    if (tool === 'edit') this.loadText(i);
    if (tool === 'objects' || tool === 'forms') this.loadObjects(i);
  }

  scale() {
    return Math.min(4, Math.max(0.5, this.zoom * (window.devicePixelRatio || 1)));
  }

  loadImage(i, force = false) {
    const p = this.pages[i];
    const scale = Math.round(this.scale() * 100) / 100;
    const key = `${this.pver[i]}@${scale}`;
    if (!force && p.imgKey === key) return p.imgPromise;
    p.imgKey = key;
    const url = api.imageUrl(this.sid, i, scale, this.pver[i]);
    p.root.classList.add('loading');
    const pre = new Image();
    pre.src = url;
    p.imgPromise = pre.decode().catch(() => {}).then(() => {
      if (p.imgKey === key) { p.img.src = url; p.root.classList.remove('loading'); }
    });
    return p.imgPromise;
  }

  async loadText(i) {
    const p = this.pages[i];
    if (p.textVer === this.pver[i]) return p.textData;
    const ver = this.pver[i];
    p.textVer = ver;
    try {
      const data = await api.text(this.sid, i);
      if (this.pver[i] !== ver) return null;
      p.textData = data;
      this.renderText(i);
      return data;
    } catch (e) {
      p.textVer = null;
      throw e;
    }
  }

  async loadObjects(i) {
    const p = this.pages[i];
    if (p.objVer === this.pver[i]) return p.objData;
    const ver = this.pver[i];
    p.objVer = ver;
    try {
      const data = await api.objects(this.sid, i);
      if (this.pver[i] !== ver) return null;
      p.objData = data;
      this.renderObjects(i);
      this.app.tools.renderForms(i);
      return data;
    } catch (e) {
      p.objVer = null;
      throw e;
    }
  }

  // ── overlays ───────────────────────────────────────
  boxStyle(p, b) {
    const pad = 1.5;
    return {
      left: pct(b[0] - pad, p.w), top: pct(b[1] - pad, p.h),
      width: pct(b[2] - b[0] + 2 * pad, p.w), height: pct(b[3] - b[1] + 2 * pad, p.h),
    };
  }

  renderText(i) {
    const p = this.pages[i];
    const d = p.textData;
    p.text.innerHTML = '';
    if (!d) return;
    const frag = document.createDocumentFragment();
    for (const l of d.lines) {
      frag.append(el('div.tbox.line', { style: this.boxStyle(p, l.bbox), dataset: { kind: 'line', id: l.id }, title: '' }));
    }
    for (const g of d.paragraphs) {
      frag.append(el('div.tbox.para', { style: this.boxStyle(p, g.bbox), dataset: { kind: 'para', id: g.id } }));
    }
    p.text.append(frag);
    p.text.classList.toggle('para-mode', this.app.state.opts.editMode === 'para');
  }

  setEditMode(mode) {
    for (const p of this.pages) p.text.classList.toggle('para-mode', mode === 'para');
  }

  renderObjects(i) {
    const p = this.pages[i];
    const d = p.objData;
    p.obj.innerHTML = '';
    if (!d) return;
    for (const im of d.images) {
      const b = el('div.obox', { style: this.boxStyle(p, im.bbox), dataset: { kind: 'image', xref: im.xref } });
      b.append(el('span.tag', {}, `Image ${im.w}×${im.h}`));
      p.obj.append(b);
    }
    for (const a of d.annots) {
      const b = el('div.obox', { style: this.boxStyle(p, a.bbox), dataset: { kind: 'annot', xref: a.xref } });
      b.append(el('span.tag', {}, a.type));
      p.obj.append(b);
    }
  }

  // ── search hits ────────────────────────────────────
  setHits(hits, active = -1) {
    this.hits = hits;
    this.activeHit = active;
    this.renderHits();
  }

  renderHits() {
    for (const p of this.pages) p.hit.innerHTML = '';
    this.hits.forEach((h, k) => {
      const p = this.pages[h.page];
      if (!p) return;
      const b = el('div.hit', { style: this.boxStyle(p, h.bbox) });
      if (k === this.activeHit) b.classList.add('active');
      p.hit.append(b);
    });
  }

  // ── refresh after edits ────────────────────────────
  /** changed: array of page indices, or null for "everything / structure changed". */
  async refresh(changed) {
    const d = this.doc;
    if (changed === null || changed === undefined || d.pages.length !== this.pages.length
        || d.pages.some((pg, i) => Math.abs(pg.w - this.pages[i].w) > 0.01 || Math.abs(pg.h - this.pages[i].h) > 0.01)) {
      const keep = this.current;
      const top = this.scroller.scrollTop;
      this.build();
      this.scroller.scrollTop = top;
      this.current = Math.min(keep, this.pages.length - 1);
      this.app.updatePager();
      return;
    }
    const waits = [];
    for (const i of changed) {
      if (!this.pages[i]) continue;
      this.pver[i] = d.version;
      const p = this.pages[i];
      p.textVer = p.objVer = null;
      if (p.visible) {
        waits.push(this.loadImage(i));
        const tool = this.app.state.tool;
        if (tool === 'edit') waits.push(this.loadText(i).catch(() => {}));
        if (tool === 'objects' || tool === 'forms') waits.push(this.loadObjects(i).catch(() => {}));
      }
      this.updateThumb(i);
    }
    await Promise.all(waits);
  }

  // ── geometry helpers ───────────────────────────────
  pageAt(target) {
    const root = target.closest?.('.page');
    return root ? this.pages[+root.dataset.pno] : null;
  }

  ptFromEvent(p, e) {
    const r = p.root.getBoundingClientRect();
    return [
      Math.max(0, Math.min(p.w, (e.clientX - r.left) / this.zoom)),
      Math.max(0, Math.min(p.h, (e.clientY - r.top) / this.zoom)),
    ];
  }

  // ── zoom & navigation ──────────────────────────────
  setZoom(z) {
    z = Math.max(0.25, Math.min(5, z));
    if (Math.abs(z - this.zoom) < 1e-3) return;
    const s = this.scroller;
    const fy = (s.scrollTop + s.clientHeight / 2) / Math.max(1, s.scrollHeight);
    const fx = (s.scrollLeft + s.clientWidth / 2) / Math.max(1, s.scrollWidth);
    this.app.tools.closeEditors(false);
    this.zoom = z;
    this.layout();
    s.scrollTop = fy * s.scrollHeight - s.clientHeight / 2;
    s.scrollLeft = fx * s.scrollWidth - s.clientWidth / 2;
    this.app.updateZoomLabel();
  }

  fitWidth() {
    if (!this.pages.length) return;
    const maxW = Math.max(...this.pages.map((p) => p.w));
    this.setZoom((this.scroller.clientWidth - 56) / maxW);
  }

  scrollToPage(i, bbox = null) {
    const p = this.pages[i];
    if (!p) return;
    let top = p.root.offsetTop - 16;
    if (bbox) top = p.root.offsetTop + bbox[1] * this.zoom - this.scroller.clientHeight / 3;
    this.scroller.scrollTo({ top, behavior: 'smooth' });
    this.current = i;
    this.app.updatePager();
  }

  updateCurrent() {
    const mid = this.scroller.getBoundingClientRect().top + this.scroller.clientHeight / 3;
    let best = this.current;
    let bestD = Infinity;
    for (const p of this.pages) {
      if (!p.visible) continue;
      const r = p.root.getBoundingClientRect();
      const d = r.top <= mid && r.bottom >= mid ? 0 : Math.min(Math.abs(r.top - mid), Math.abs(r.bottom - mid));
      if (d < bestD) { bestD = d; best = p.i; }
    }
    if (best !== this.current) {
      this.current = best;
      this.app.updatePager();
    }
  }

  // ── thumbnails ─────────────────────────────────────
  thumbUrl(i) {
    const p = this.pages[i];
    const scale = (THUMB_W / p.w) * Math.min(2, window.devicePixelRatio || 1);
    return api.imageUrl(this.sid, i, scale, this.pver[i]);
  }

  buildThumbs() {
    this.thumbsEl.innerHTML = '';
    this.pages.forEach((p, i) => {
      const h = Math.round((THUMB_W * p.h) / p.w);
      const img = el('img', { alt: `Page ${i + 1}`, loading: 'lazy', draggable: false, src: this.thumbUrl(i) });
      const actions = el('div.actions');
      const act = (name, title, fn) => {
        const b = el('button.icon-btn.sm', { type: 'button', title, html: icon(name) });
        b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
        actions.append(b);
      };
      act('rotl', 'Rotate left', () => this.app.pageOp('rotate', { pages: [i], delta: -90 }));
      act('rotr', 'Rotate right', () => this.app.pageOp('rotate', { pages: [i], delta: 90 }));
      act('copy', 'Duplicate page', () => this.app.pageOp('duplicate', { page: i }));
      act('fileplus', 'Insert blank page after', () => this.app.pageOp('insert-blank', { at: i + 1 }));
      act('download', 'Extract this page', () => this.app.download({ pages: [i] }, `page-${i + 1}`));
      act('trash', 'Delete page', () => this.app.deletePage(i));
      const t = el('div.thumb', { draggable: true, dataset: { pno: i } },
        el('div.frame', { style: { width: `${THUMB_W}px`, height: `${h}px` } }, img),
        el('span.num', {}, `${i + 1}`), actions);
      t.addEventListener('click', () => this.scrollToPage(i));
      this.thumbsEl.append(t);
    });
    this.markCurrentThumb();
  }

  updateThumb(i) {
    const img = this.thumbsEl.children[i]?.querySelector('img');
    if (img) img.src = this.thumbUrl(i);
  }

  markCurrentThumb() {
    [...this.thumbsEl.children].forEach((t, i) => t.classList.toggle('current', i === this.current));
    const cur = this.thumbsEl.children[this.current];
    if (cur) {
      const r = cur.getBoundingClientRect();
      const box = this.thumbsEl.getBoundingClientRect();
      if (r.top < box.top || r.bottom > box.bottom) cur.scrollIntoView({ block: 'nearest' });
    }
  }

  setupThumbDnD() {
    let from = null;
    const clear = () => this.thumbsEl.querySelectorAll('.drop-before,.drop-after,.dragging')
      .forEach((n) => n.classList.remove('drop-before', 'drop-after', 'dragging'));
    this.thumbsEl.addEventListener('dragstart', (e) => {
      const t = e.target.closest('.thumb');
      if (!t) return;
      from = +t.dataset.pno;
      t.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/x-page', String(from));
    });
    this.thumbsEl.addEventListener('dragover', (e) => {
      if (from === null) return;
      const t = e.target.closest('.thumb');
      if (!t) return;
      e.preventDefault();
      clear();
      this.thumbsEl.children[from]?.classList.add('dragging');
      const r = t.getBoundingClientRect();
      t.classList.add(e.clientY < r.top + r.height / 2 ? 'drop-before' : 'drop-after');
    });
    this.thumbsEl.addEventListener('drop', (e) => {
      if (from === null) return;
      e.preventDefault();
      const t = e.target.closest('.thumb');
      if (t) {
        const target = +t.dataset.pno;
        const r = t.getBoundingClientRect();
        let to = e.clientY < r.top + r.height / 2 ? target : target + 1;
        const order = this.pages.map((_, k) => k);
        order.splice(from, 1);
        if (to > from) to -= 1;
        order.splice(to, 0, from);
        if (order.some((v, k) => v !== k)) this.app.pageOp('reorder', { order });
      }
      clear();
      from = null;
    });
    this.thumbsEl.addEventListener('dragend', () => { clear(); from = null; });
  }
}
