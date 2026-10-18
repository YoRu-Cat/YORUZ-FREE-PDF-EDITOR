// Page interaction: text editors, drag tools, drawing, forms, objects.
import { el, icon, menu, prompt, toast } from './ui.js';

const SVGNS = 'http://www.w3.org/2000/svg';

/** CSS font-family guess for a PDF font name, so the in-place editor looks close. */
export function cssFamily(info) {
  const raw = String(info.font || '').replace(/^[A-Z]{6}\+/, '');
  const base = raw.split(/[-,]/)[0].replace(/(PSMT|PS|MT|Std|Pro|LT)$/,'');
  const spaced = base.replace(/([a-z])([A-Z])/g, '$1 $2');
  const generic = info.mono ? '"Courier New", monospace'
    : info.serif || /times|serif|roman|georgia|garamond|cambria|book/i.test(raw) ? '"Times New Roman", Times, serif'
      : 'Arial, Helvetica, sans-serif';
  return `"${base}", "${spaced}", ${generic}`;
}

export function userFamily(key, fonts) {
  if (!key) return null;
  if (key === 'base:helvetica') return 'Helvetica, Arial, sans-serif';
  if (key === 'base:times') return '"Times New Roman", Times, serif';
  if (key === 'base:courier') return '"Courier New", Courier, monospace';
  const f = fonts.find((x) => x.key === key);
  return f ? `"${f.name}", sans-serif` : null;
}

function svgEl(name, attrs) {
  const n = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

export class Tools {
  constructor(app) {
    this.app = app;
    this.editor = null;      // active text editor {kind, commit(), cancel()}
    this.pendingImage = null; // {blob, w, h, url, sign}
    const pagesEl = document.getElementById('pages');
    pagesEl.addEventListener('pointerdown', (e) => this.onPointerDown(e));
    pagesEl.addEventListener('dblclick', (e) => e.preventDefault());
    document.addEventListener('pointerdown', (e) => {
      // clicking outside an open editor commits it
      if (!this.editor) return;
      if (this.editor.contains(e.target)) return;
      if (e.target.closest('.optbar, .modal-back, .menu')) return;
      this.suppressNewText = true;
      this.editor.commit();
    }, true);
  }

  get viewer() { return this.app.viewer; }
  get state() { return this.app.state; }
  get opts() { return this.app.state.opts; }

  // ── dispatch ───────────────────────────────────────
  onPointerDown(e) {
    const suppress = this.suppressNewText;
    this.suppressNewText = false;
    if (e.button !== 0) return;
    const p = this.viewer.pageAt(e.target);
    if (!p) return;
    if (e.target.closest('.line-editor, .para-editor, .new-text, .float-bar, .ffield')) return;
    const pt = this.viewer.ptFromEvent(p, e);
    const tool = this.state.tool;
    switch (tool) {
      case 'edit': {
        const box = e.target.closest('.tbox');
        if (!box) return;
        e.preventDefault();
        const data = p.textData;
        if (!data) return;
        if (box.dataset.kind === 'line') this.openLineEditor(p, data.lines[+box.dataset.id], e);
        else this.openParaEditor(p, data.paragraphs[+box.dataset.id]);
        return;
      }
      case 'addtext':
        e.preventDefault();
        if (this.editor) { this.editor.commit(); return; }
        if (suppress) return;
        this.openNewText(p, pt);
        return;
      case 'erase':
        return this.dragRect(p, e, pt, 'svg-rect-erase', (r) => r && this.app.mutate('text/erase', { page: p.i, rect: r })
          .then((s) => s && toast(s.erased ? `Removed ${s.erased} characters` : 'No text in that area')));
      case 'redact': {
        const o = this.opts.redact;
        return this.dragRect(p, e, pt, 'svg-rect-redact', (r) => r && this.app.mutate('redact', {
          page: p.i, rect: r, fill: o.fill === 'none' ? null : o.fill, text: o.text, images: o.images, graphics: o.graphics,
        }), { fill: o.fill === 'none' ? 'rgba(0,0,0,.08)' : o.fill });
      }
      case 'whiteout':
        return this.dragRect(p, e, pt, 'svg-rect-whiteout', (r) => r && this.app.mutate('draw', {
          page: p.i, kind: 'whiteout', points: [[r[0], r[1]], [r[2], r[3]]], stroke: null, fill: this.opts.whiteout.color, width: 0, opacity: 1,
        }), { fill: this.opts.whiteout.color });
      case 'markup': {
        const o = this.opts.markup;
        return this.dragRect(p, e, pt, 'svg-rect-markup', (r) => r && this.app.mutate('annotations/markup', {
          page: p.i, rect: r, kind: o.kind, color: o.color,
        }).then((s) => s && !s.marked && toast('No text found in the selection')));
      }
      case 'shape':
        return this.dragShape(p, e, pt);
      case 'draw':
        return this.dragInk(p, e, pt);
      case 'image':
      case 'sign':
        if (!this.pendingImage) {
          if (tool === 'sign') this.app.openSignature();
          else this.app.chooseImage();
          return;
        }
        return this.dragRect(p, e, pt, 'svg-rect-place', (r, click) => this.placeImage(p, r, click));
      case 'note':
        e.preventDefault();
        prompt('Add comment', { label: 'Comment text', multiline: true }).then((text) => {
          if (text) this.app.mutate('annotations/note', { page: p.i, x: pt[0], y: pt[1], text, color: this.opts.note.color });
        });
        return;
      case 'objects': {
        const box = e.target.closest('.obox');
        if (box) this.objectMenu(p, box, e);
        return;
      }
      default:
    }
  }

  // ── generic drag rectangle ─────────────────────────
  dragRect(p, e, start, cls, done, { fill } = {}) {
    e.preventDefault();
    const rect = svgEl('rect', { class: cls, x: start[0], y: start[1], width: 0, height: 0, 'vector-effect': 'non-scaling-stroke' });
    if (fill) rect.style.fill = fill;
    if (cls === 'svg-rect-place' && this.pendingImage) {
      // preview the image being placed
      const im = svgEl('image', { href: this.pendingImage.url, preserveAspectRatio: 'none', opacity: 0.8 });
      p.svg.append(im);
      rect._img = im;
    }
    p.svg.append(rect);
    let cur = start;
    const upd = () => {
      const x = Math.min(start[0], cur[0]);
      const y = Math.min(start[1], cur[1]);
      const w = Math.abs(cur[0] - start[0]);
      const h = Math.abs(cur[1] - start[1]);
      for (const n of [rect, rect._img].filter(Boolean)) {
        n.setAttribute('x', x); n.setAttribute('y', y); n.setAttribute('width', w); n.setAttribute('height', h);
      }
    };
    const move = (ev) => { cur = this.viewer.ptFromEvent(p, ev); upd(); };
    const up = (ev) => {
      p.root.removeEventListener('pointermove', move);
      p.root.removeEventListener('pointerup', up);
      p.root.removeEventListener('pointercancel', up);
      cur = this.viewer.ptFromEvent(p, ev);
      rect.remove();
      rect._img?.remove();
      const r = [Math.min(start[0], cur[0]), Math.min(start[1], cur[1]), Math.max(start[0], cur[0]), Math.max(start[1], cur[1])];
      const isClick = r[2] - r[0] < 3 && r[3] - r[1] < 3;
      done(isClick ? null : r, isClick ? start : null);
    };
    p.root.setPointerCapture(e.pointerId);
    p.root.addEventListener('pointermove', move);
    p.root.addEventListener('pointerup', up);
    p.root.addEventListener('pointercancel', up);
  }

  dragShape(p, e, start) {
    e.preventDefault();
    const o = this.opts.shape;
    const common = { stroke: o.stroke, 'stroke-width': o.width, fill: o.fillOn ? o.fill : 'none', opacity: o.opacity };
    let node;
    if (o.kind === 'rect') node = svgEl('rect', common);
    else if (o.kind === 'ellipse') node = svgEl('ellipse', common);
    else node = svgEl('path', { ...common, fill: 'none', 'stroke-linecap': 'round' });
    p.svg.append(node);
    let cur = start;
    const upd = () => {
      const [x0, y0] = start;
      const [x1, y1] = cur;
      if (o.kind === 'rect') {
        node.setAttribute('x', Math.min(x0, x1)); node.setAttribute('y', Math.min(y0, y1));
        node.setAttribute('width', Math.abs(x1 - x0)); node.setAttribute('height', Math.abs(y1 - y0));
      } else if (o.kind === 'ellipse') {
        node.setAttribute('cx', (x0 + x1) / 2); node.setAttribute('cy', (y0 + y1) / 2);
        node.setAttribute('rx', Math.abs(x1 - x0) / 2); node.setAttribute('ry', Math.abs(y1 - y0) / 2);
      } else {
        let d = `M${x0} ${y0}L${x1} ${y1}`;
        if (o.kind === 'arrow') {
          const a = Math.atan2(y1 - y0, x1 - x0);
          const h = Math.max(6, o.width * 4);
          for (const s of [1, -1]) {
            const t = a + Math.PI - s * (28 * Math.PI / 180);
            d += `M${x1} ${y1}L${x1 + h * Math.cos(t)} ${y1 + h * Math.sin(t)}`;
          }
        }
        node.setAttribute('d', d);
      }
    };
    const move = (ev) => {
      cur = this.viewer.ptFromEvent(p, ev);
      if (ev.shiftKey && (o.kind === 'line' || o.kind === 'arrow')) {
        const dx = cur[0] - start[0];
        const dy = cur[1] - start[1];
        const a = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
        const len = Math.hypot(dx, dy);
        cur = [start[0] + len * Math.cos(a), start[1] + len * Math.sin(a)];
      } else if (ev.shiftKey) {
        const s = Math.max(Math.abs(cur[0] - start[0]), Math.abs(cur[1] - start[1]));
        cur = [start[0] + Math.sign(cur[0] - start[0] || 1) * s, start[1] + Math.sign(cur[1] - start[1] || 1) * s];
      }
      upd();
    };
    const up = () => {
      p.root.removeEventListener('pointermove', move);
      p.root.removeEventListener('pointerup', up);
      node.remove();
      if (Math.hypot(cur[0] - start[0], cur[1] - start[1]) < 2) return;
      this.app.mutate('draw', {
        page: p.i, kind: o.kind, points: [start, cur], stroke: o.stroke,
        fill: o.fillOn && (o.kind === 'rect' || o.kind === 'ellipse') ? o.fill : null, width: o.width, opacity: o.opacity,
      });
    };
    p.root.setPointerCapture(e.pointerId);
    p.root.addEventListener('pointermove', move);
    p.root.addEventListener('pointerup', up);
  }

  dragInk(p, e, start) {
    e.preventDefault();
    const o = this.opts.draw;
    const pts = [start];
    const path = svgEl('polyline', {
      fill: 'none', stroke: o.color, 'stroke-width': o.width, opacity: o.opacity,
      'stroke-linecap': 'round', 'stroke-linejoin': 'round', points: `${start[0]},${start[1]}`,
    });
    p.svg.append(path);
    const move = (ev) => {
      const evs = ev.getCoalescedEvents ? ev.getCoalescedEvents() : [ev];
      for (const ce of evs) {
        const q = this.viewer.ptFromEvent(p, ce);
        const last = pts[pts.length - 1];
        if (Math.hypot(q[0] - last[0], q[1] - last[1]) >= 0.7) pts.push(q);
      }
      path.setAttribute('points', pts.map((q) => q.join(',')).join(' '));
    };
    const up = () => {
      p.root.removeEventListener('pointermove', move);
      p.root.removeEventListener('pointerup', up);
      this.app.mutate('draw', { page: p.i, kind: 'ink', points: pts, stroke: o.color, fill: null, width: o.width, opacity: o.opacity })
        .finally(() => path.remove());
    };
    p.root.setPointerCapture(e.pointerId);
    p.root.addEventListener('pointermove', move);
    p.root.addEventListener('pointerup', up);
  }

  // ── images & signatures ────────────────────────────
  setPendingImage(blob, { sign = false } = {}) {
    if (this.pendingImage?.url) URL.revokeObjectURL(this.pendingImage.url);
    if (!blob) { this.pendingImage = null; return; }
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => { if (this.pendingImage) { this.pendingImage.w = img.naturalWidth; this.pendingImage.h = img.naturalHeight; } };
    img.src = url;
    this.pendingImage = { blob, url, w: 300, h: 150, sign };
  }

  async placeImage(p, rect, click) {
    const im = this.pendingImage;
    if (!im) return;
    if (!rect) {
      // a click: natural aspect ratio, sensible default width, centred on the click
      const w = Math.min(im.sign ? 160 : 220, p.w * 0.6);
      const h = (w * im.h) / Math.max(1, im.w);
      rect = [click[0] - w / 2, click[1] - h / 2, click[0] + w / 2, click[1] + h / 2];
    }
    const res = await this.app.mutateForm('images', { page: p.i, rect: JSON.stringify(rect), file: im.blob });
    if (res && !im.sign) {
      this.setPendingImage(null);
      this.app.renderOptbar();
    }
  }

  // ── objects (existing images / annotations) ───────
  objectMenu(p, box, e) {
    const xref = +box.dataset.xref;
    if (box.dataset.kind === 'image') {
      menu(e.clientX, e.clientY, 'Image', [
        { label: 'Replace image…', icon: 'image', action: () => this.app.replaceImage(p.i, xref) },
        { label: 'Delete image', icon: 'trash', danger: true, action: () => this.app.mutate('images/delete', { page: p.i, xref }) },
      ]);
    } else {
      const a = p.objData?.annots.find((x) => x.xref === xref);
      menu(e.clientX, e.clientY, `${a?.type || 'Annotation'}${a?.content ? ': ' + a.content : ''}`, [
        { label: 'Delete annotation', icon: 'trash', danger: true, action: () => this.app.mutate('annotations/delete', { page: p.i, xref }) },
      ]);
    }
  }

  // ── forms ──────────────────────────────────────────
  renderForms(i) {
    const p = this.viewer.pages[i];
    p.form.innerHTML = '';
    const fields = p.objData?.fields || [];
    for (const f of fields) {
      if (f.type === 'Button' || f.type === 'Signature') continue;
      const b = f.bbox;
      const style = {
        left: `${(b[0] / p.w) * 100}%`, top: `${(b[1] / p.h) * 100}%`,
        width: `${((b[2] - b[0]) / p.w) * 100}%`, height: `${((b[3] - b[1]) / p.h) * 100}%`,
        fontSize: `calc(var(--z) * ${Math.max(7, Math.min(14, (b[3] - b[1]) * 0.62)).toFixed(1)}px)`,
      };
      let input;
      const send = (value) => this.app.mutate('forms/set', { page: i, xref: f.xref, value });
      if (f.type === 'CheckBox' || f.type === 'RadioButton') {
        input = el('input.ffield', { type: 'checkbox', title: f.name, style });
        input.checked = !!f.value && f.value !== 'Off' && f.value !== false;
        input.addEventListener('change', () => send(input.checked));
      } else if (f.type === 'ComboBox' || f.type === 'ListBox') {
        input = el('select.ffield', { title: f.name, style });
        for (const c of f.choices) {
          const [val, label] = Array.isArray(c) ? c : [c, c];
          input.append(el('option', { value: val }, label));
        }
        input.value = f.value || '';
        input.addEventListener('change', () => send(input.value));
      } else {
        input = f.multiline ? el('textarea.ffield', { title: f.name, style }) : el('input.ffield', { title: f.name, style });
        input.value = f.value || '';
        input.addEventListener('change', () => send(input.value));
      }
      if (f.readonly) input.disabled = true;
      p.form.append(input);
    }
  }

  // ── text editors ───────────────────────────────────
  closeEditors(commit = true) {
    if (!this.editor) return;
    if (commit) this.editor.commit();
    else this.editor.cancel();
  }

  makeFloatBar(p, anchor, initial, { para = false, onDelete, onApply, onCancel, onChange }) {
    const fonts = this.state.fonts;
    const bar = el('div.float-bar');
    const fontSel = el('select', { title: 'Font' }, el('option', { value: '' }, initial.fontLabel || 'Original font'));
    for (const f of fonts) fontSel.append(el('option', { value: f.key }, f.name));
    fontSel.value = initial.font || '';
    const size = el('input', { type: 'number', min: 1, max: 400, step: 0.5, value: initial.size, title: 'Font size (pt)' });
    const color = el('input', { type: 'color', value: initial.color, title: 'Text colour' });
    const bold = el('button.toggle', { type: 'button', title: 'Bold' }, 'B');
    const ital = el('button.toggle.i', { type: 'button', title: 'Italic' }, 'I');
    bold.classList.toggle('on', !!initial.bold);
    ital.classList.toggle('on', !!initial.italic);
    const st = { font: initial.font || '', size: +initial.size, color: initial.color, bold: !!initial.bold, italic: !!initial.italic, align: null };
    const changed = () => onChange?.(st);
    fontSel.addEventListener('change', () => { st.font = fontSel.value; changed(); });
    size.addEventListener('input', () => { if (+size.value > 0) { st.size = +size.value; changed(); } });
    color.addEventListener('input', () => { st.color = color.value; changed(); });
    bold.addEventListener('click', () => { st.bold = !st.bold; bold.classList.toggle('on', st.bold); changed(); });
    ital.addEventListener('click', () => { st.italic = !st.italic; ital.classList.toggle('on', st.italic); changed(); });
    bar.append(fontSel, size, color, bold, ital);
    if (para) {
      const seg = el('div.seg');
      for (const [val, ic, title] of [[null, null, 'Auto'], ['left', 'alignl', 'Left'], ['center', 'alignc', 'Centre'], ['right', 'alignr', 'Right'], ['justify', 'justify', 'Justify']]) {
        const b = el('button', { type: 'button', title: `Align: ${title}`, html: ic ? icon(ic) : 'Auto' });
        if (val === null) b.classList.add('on');
        b.addEventListener('click', () => {
          st.align = val;
          seg.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
          changed();
        });
        seg.append(b);
      }
      bar.append(el('span.sep'), seg);
    }
    const mk = (ic, title, fn, cls = '') => {
      const b = el(`button.icon-btn.sm${cls}`, { type: 'button', title, html: icon(ic) });
      b.addEventListener('click', fn);
      return b;
    };
    bar.append(el('span.sep'),
      mk('trash', para ? 'Delete paragraph' : 'Delete line', onDelete),
      mk('x', 'Cancel (Esc)', onCancel),
      mk('check', para ? 'Apply (Ctrl+Enter)' : 'Apply (Enter)', onApply));
    bar.addEventListener('pointerdown', (e) => e.stopPropagation());
    p.root.append(bar);
    // position above the anchor, or below if no room
    const z = this.viewer.zoom;
    const top = anchor[1] * z - 46;
    bar.style.left = `${Math.max(0, Math.min(anchor[0] * z, p.w * z - bar.offsetWidth))}px`;
    bar.style.top = `${top < 0 ? anchor[3] * z + 8 : top}px`;
    return { bar, st };
  }

  /** Style fields that differ from the original (sent as override). */
  diffStyle(st, orig) {
    const o = {};
    if (st.font) o.font = st.font;
    if (Math.abs(st.size - orig.size) > 0.01) o.size = st.size;
    if (st.color.toLowerCase() !== orig.color.toLowerCase()) o.color = st.color;
    if (st.bold !== !!orig.bold) o.bold = st.bold;
    if (st.italic !== !!orig.italic) o.italic = st.italic;
    return Object.keys(o).length ? o : null;
  }

  applyEditorStyle(node, st, orig) {
    const z = this.viewer.zoom;
    node.style.fontSize = `${st.size * z}px`;
    node.style.color = st.color;
    node.style.fontWeight = st.bold ? '700' : '400';
    node.style.fontStyle = st.italic ? 'italic' : 'normal';
    node.style.fontFamily = (st.font && userFamily(st.font, this.state.fonts)) || cssFamily(orig);
  }

  async openLineEditor(p, line, e) {
    this.closeEditors(true);
    if (!line) return;
    const z = this.viewer.zoom;
    if (Math.abs(line.angle) > 1) {
      // rotated / vertical text: edit in a dialog
      const t = await prompt('Edit text', { label: `Rotated text (${line.angle}°)`, value: line.text });
      if (t !== null && t !== line.text) this.app.mutate('text/line', { page: p.i, line: line.id, expect: line.text, text: t });
      return;
    }
    const [x0, y0, x1, y1] = line.bbox;
    const ed = el('div.line-editor', { spellcheck: false });
    try { ed.contentEditable = 'plaintext-only'; } catch { ed.contentEditable = 'true'; }
    if (ed.contentEditable !== 'plaintext-only') ed.contentEditable = 'true';
    ed.textContent = line.text;
    const h = (y1 - y0) * z;
    Object.assign(ed.style, {
      left: `${x0 * z - 2}px`, top: `${y0 * z}px`, minWidth: `${(x1 - x0) * z + 4}px`,
      height: `${h}px`, lineHeight: `${h}px`, paddingLeft: '2px', paddingRight: '2px',
    });
    this.applyEditorStyle(ed, { size: line.size, color: line.color, bold: line.bold, italic: line.italic, font: '' }, line);
    p.root.append(ed);

    let done = false;
    const finish = () => { done = true; ed.remove(); fb.bar.remove(); if (this.editor === api) this.editor = null; };
    const commit = async (textOverride) => {
      if (done) return;
      const text = textOverride ?? ed.textContent.replace(/\r?\n/g, ' ');
      const style = this.diffStyle(fb.st, line);
      if (text === line.text && !style) { finish(); return; }
      done = true;
      ed.contentEditable = 'false';
      if (this.editor === api) this.editor = null;
      fb.bar.remove();
      await this.app.mutate('text/line', { page: p.i, line: line.id, expect: line.text, text, style });
      ed.remove();
    };
    const fb = this.makeFloatBar(p, line.bbox, { size: line.size, color: line.color, bold: line.bold, italic: line.italic, fontLabel: `Original (${line.font || 'font'})` }, {
      onDelete: () => commit(''), onApply: () => commit(), onCancel: () => finish(),
      onChange: (st) => this.applyEditorStyle(ed, st, line),
    });
    const api = { contains: (n) => ed.contains(n) || fb.bar.contains(n), commit: () => commit(), cancel: finish };
    this.editor = api;
    ed.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
      if (ev.key === 'Escape') { ev.preventDefault(); finish(); }
      ev.stopPropagation();
    });
    ed.addEventListener('paste', (ev) => {
      ev.preventDefault();
      const t = (ev.clipboardData?.getData('text/plain') || '').replace(/\r?\n/g, ' ');
      document.execCommand('insertText', false, t);
    });
    ed.focus();
    // caret where the user clicked
    const sel = getSelection();
    let range = null;
    if (document.caretRangeFromPoint) range = document.caretRangeFromPoint(e.clientX, e.clientY);
    else if (document.caretPositionFromPoint) {
      const pos = document.caretPositionFromPoint(e.clientX, e.clientY);
      if (pos) { range = document.createRange(); range.setStart(pos.offsetNode, pos.offset); }
    }
    if (range && ed.contains(range.startContainer)) { sel.removeAllRanges(); sel.addRange(range); }
    else { const r = document.createRange(); r.selectNodeContents(ed); r.collapse(false); sel.removeAllRanges(); sel.addRange(r); }
  }

  openParaEditor(p, para) {
    this.closeEditors(true);
    if (!para) return;
    const z = this.viewer.zoom;
    const [x0, y0, x1, y1] = para.bbox;
    const n = Math.max(1, para.lines.length);
    const lh = ((y1 - y0) / n) * z;
    const ta = el('textarea.para-editor', { spellcheck: false });
    ta.value = para.text;
    Object.assign(ta.style, {
      left: `${x0 * z - 3}px`, top: `${y0 * z - 2}px`, width: `${(x1 - x0) * z + 8}px`,
      lineHeight: `${Math.max(lh, para.size * z)}px`, padding: '1px 2px',
    });
    this.applyEditorStyle(ta, { size: para.size, color: para.color, bold: para.bold, italic: para.italic, font: '' }, para);
    p.root.append(ta);
    const grow = () => { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight + 2}px`; };
    let done = false;
    const finish = () => { done = true; ta.remove(); fb.bar.remove(); if (this.editor === api) this.editor = null; };
    const commit = async (textOverride) => {
      if (done) return;
      const text = textOverride ?? ta.value;
      const style = this.diffStyle(fb.st, para);
      if (text === para.text && !style && !fb.st.align) { finish(); return; }
      done = true;
      ta.readOnly = true;
      if (this.editor === api) this.editor = null;
      fb.bar.remove();
      await this.app.mutate('text/paragraph', { page: p.i, paragraph: para.id, expect: para.text, text, style, align: fb.st.align });
      ta.remove();
    };
    const fb = this.makeFloatBar(p, para.bbox, { size: para.size, color: para.color, bold: para.bold, italic: para.italic, fontLabel: `Original (${para.font || 'font'})` }, {
      para: true, onDelete: () => commit(''), onApply: () => commit(), onCancel: () => finish(),
      onChange: (st) => { this.applyEditorStyle(ta, st, para); if (st.align && st.align !== 'justify') ta.style.textAlign = st.align; grow(); },
    });
    const api = { contains: (nd) => ta.contains(nd) || fb.bar.contains(nd), commit: () => commit(), cancel: finish };
    this.editor = api;
    ta.addEventListener('input', grow);
    ta.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); commit(); }
      if (ev.key === 'Escape') { ev.preventDefault(); finish(); }
      ev.stopPropagation();
    });
    grow();
    ta.focus();
  }

  openNewText(p, pt) {
    const o = this.opts.text;
    const z = this.viewer.zoom;
    const ta = el('textarea.new-text', { spellcheck: false, wrap: 'off', rows: 1, placeholder: 'Type here' });
    Object.assign(ta.style, { left: `${pt[0] * z}px`, top: `${pt[1] * z}px`, lineHeight: '1.2', textAlign: o.align });
    const style = () => {
      this.applyEditorStyle(ta, { size: o.size, color: o.color, bold: o.bold, italic: o.italic, font: o.font }, { font: '', serif: false });
      ta.style.textAlign = o.align;
      ta.style.lineHeight = '1.2';
      grow();
    };
    const grow = () => {
      ta.style.height = 'auto'; ta.style.width = '0px';
      ta.style.width = `${Math.max(60, ta.scrollWidth + 6)}px`;
      ta.style.height = `${ta.scrollHeight}px`;
    };
    p.root.append(ta);
    let done = false;
    const finish = () => { done = true; ta.remove(); if (this.editor === api) this.editor = null; this.newText = null; };
    const commit = async () => {
      if (done) return;
      const text = ta.value.replace(/\s+$/, '');
      if (!text.trim()) { finish(); return; }
      done = true;
      ta.readOnly = true;
      if (this.editor === api) this.editor = null;
      this.newText = null;
      await this.app.mutate('text/add', {
        page: p.i, x: pt[0], y: pt[1], text, font: o.font, size: o.size, color: o.color,
        bold: o.bold, italic: o.italic, align: o.align,
      });
      ta.remove();
    };
    const api = { contains: (n) => ta.contains(n), commit, cancel: finish, restyle: style };
    this.editor = api;
    this.newText = api;
    ta.addEventListener('input', grow);
    ta.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); commit(); }
      if (ev.key === 'Escape') { ev.preventDefault(); finish(); }
      ev.stopPropagation();
    });
    style();
    setTimeout(() => ta.focus());
  }
}
