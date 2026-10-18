"""Font discovery and matching.

When text inside a PDF is edited, the replacement glyphs should look like the
originals. We try, in order:
  1. the font that is embedded in the PDF (if it contains every glyph needed),
  2. an installed system font with the same name / family + style,
  3. the PDF base-14 fonts (Helvetica / Times / Courier),
  4. broad-coverage fallbacks (Arial, Segoe UI, Noto, DejaVu, built-in CJK).
Fallbacks are applied per character, so a single missing glyph in a subset
font does not force the whole line into a different typeface.
"""

from __future__ import annotations

import os
import re
import struct
import sys
import threading
from pathlib import Path

import pymupdf as fitz

STYLES = ("regular", "bold", "italic", "bolditalic")

BASE14 = {
    # family key -> style -> builtin name
    "helvetica": {"regular": "helv", "bold": "hebo", "italic": "heit", "bolditalic": "hebi"},
    "times": {"regular": "tiro", "bold": "tibo", "italic": "tiit", "bolditalic": "tibi"},
    "courier": {"regular": "cour", "bold": "cobo", "italic": "coit", "bolditalic": "cobi"},
}

# PDF standard names / common aliases -> preferred family keys (normalized)
ALIASES = {
    "helvetica": ["helvetica", "arial", "liberationsans", "nimbussans"],
    "arial": ["arial", "liberationsans", "helvetica"],
    "arialmt": ["arial", "liberationsans", "helvetica"],
    "times": ["timesnewroman", "liberationserif", "times"],
    "timesroman": ["timesnewroman", "liberationserif", "times"],
    "timesnewroman": ["timesnewroman", "liberationserif", "times"],
    "courier": ["couriernew", "liberationmono", "courier"],
    "couriernew": ["couriernew", "liberationmono", "courier"],
    # metric-compatible open replacements (installed in the Docker image)
    "calibri": ["calibri", "carlito"],
    "cambria": ["cambria", "caladea"],
    "verdana": ["verdana", "dejavusans"],
    "georgia": ["georgia", "gelasio", "liberationserif"],
}

FALLBACK_FAMILIES = ["arial", "segoeui", "notosans", "dejavusans", "liberationsans", "arialunicodems"]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def strip_subset(name: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", name or "")


def style_key(bold: bool, italic: bool) -> str:
    if bold and italic:
        return "bolditalic"
    if bold:
        return "bold"
    if italic:
        return "italic"
    return "regular"


def name_style(name: str) -> tuple[bool, bool]:
    low = (name or "").lower()
    bold = bool(re.search(r"bold|black|heavy|semibold|demibold|demi\b|extrabold|ultrabold|,bd|-bd", low))
    italic = bool(re.search(r"italic|oblique|ital\b|-it$|,it$|kursiv", low))
    return bold, italic


# --------------------------------------------------------------------------
# Minimal TrueType/OpenType "name" table reader (no external deps)
# --------------------------------------------------------------------------

def _read_names_at(f, offset: int) -> dict[int, str] | None:
    f.seek(offset)
    hdr = f.read(12)
    if len(hdr) < 12:
        return None
    num_tables = struct.unpack(">H", hdr[4:6])[0]
    name_off = None
    for _ in range(num_tables):
        rec = f.read(16)
        if len(rec) < 16:
            return None
        tag, _chk, toff, _tlen = struct.unpack(">4sIII", rec)
        if tag == b"name":
            name_off = toff
            break
    if name_off is None:
        return None
    f.seek(name_off)
    head = f.read(6)
    if len(head) < 6:
        return None
    _fmt, count, str_off = struct.unpack(">HHH", head)
    records = f.read(12 * count)
    best: dict[int, tuple[int, str]] = {}
    for i in range(count):
        pid, eid, lid, nid, length, off = struct.unpack(">HHHHHH", records[i * 12:(i + 1) * 12])
        if nid not in (1, 2, 4, 6, 16, 17):
            continue
        if pid == 3 or pid == 0:
            enc = "utf-16-be"
            score = 3 if (pid == 3 and lid == 0x409) else 2
        elif pid == 1 and eid == 0:
            enc = "mac_roman"
            score = 1
        else:
            continue
        if nid in best and best[nid][0] >= score:
            continue
        f.seek(name_off + str_off + off)
        raw = f.read(length)
        try:
            best[nid] = (score, raw.decode(enc, "ignore").strip("\x00 "))
        except Exception:
            pass
    return {k: v[1] for k, v in best.items()}


def read_font_names(path: str) -> list[dict[int, str]]:
    out = []
    try:
        with open(path, "rb") as f:
            tag = f.read(4)
            if tag == b"ttcf":
                f.seek(8)
                n = struct.unpack(">I", f.read(4))[0]
                offs = struct.unpack(">" + "I" * n, f.read(4 * n))
                # PyMuPDF's Font() always loads the first face of a collection
                names = _read_names_at(f, offs[0])
                if names:
                    out.append(names)
            elif tag in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
                names = _read_names_at(f, 0)
                if names:
                    out.append(names)
    except Exception:
        pass
    return out


def font_dirs() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform.startswith("win"):
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(Path(windir) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                 Path.home() / ".fonts", Path.home() / ".local/share/fonts"]
    return [d for d in dirs if d.is_dir()]


class FontIndex:
    def __init__(self):
        self.by_ps: dict[str, str] = {}
        self.by_full: dict[str, str] = {}
        self.families: dict[str, dict[str, str]] = {}
        self.display: dict[str, str] = {}  # family key -> human name
        self._ready = threading.Event()
        self._font_cache: dict[str, fitz.Font] = {}
        self._lock = threading.Lock()

    def build(self):
        try:
            for d in font_dirs():
                for root, _dirs, files in os.walk(d):
                    for fn in files:
                        if not fn.lower().endswith((".ttf", ".otf", ".ttc")):
                            continue
                        path = os.path.join(root, fn)
                        for names in read_font_names(path):
                            fam = names.get(1) or names.get(16) or ""
                            sub = names.get(2) or "Regular"
                            full = names.get(4) or ""
                            ps = names.get(6) or ""
                            if ps:
                                self.by_ps.setdefault(norm(ps), path)
                            if full:
                                self.by_full.setdefault(norm(full), path)
                            if fam:
                                fk = norm(fam)
                                b, i = name_style(sub)
                                if norm(sub) in ("regular", "normal", "roman", "book", "standard"):
                                    b, i = False, False
                                sk = style_key(b, i)
                                fam_map = self.families.setdefault(fk, {})
                                fam_map.setdefault(sk, path)
                                self.display.setdefault(fk, fam)
        finally:
            self._ready.set()

    def start(self):
        threading.Thread(target=self.build, daemon=True).start()

    def wait(self, timeout=30):
        self._ready.wait(timeout)

    # ---------------------------------------------------------------
    def load(self, key: str) -> fitz.Font | None:
        """key: 'builtin:<name>' or a font file path."""
        with self._lock:
            if key in self._font_cache:
                return self._font_cache[key]
            try:
                if key.startswith("builtin:"):
                    font = fitz.Font(key[8:])
                else:
                    font = fitz.Font(fontfile=key)
            except Exception:
                font = None
            self._font_cache[key] = font
            return font

    def family_lookup(self, fk: str, sk: str) -> str | None:
        fam = self.families.get(fk)
        if not fam:
            return None
        for cand in (sk, "bold" if "bold" in sk else None, "italic" if "italic" in sk else None, "regular"):
            if cand and cand in fam:
                return fam[cand]
        return next(iter(fam.values()))

    def system_match(self, pdf_font_name: str, bold: bool, italic: bool) -> str | None:
        self.wait()
        name = strip_subset(pdf_font_name)
        n = norm(name)
        if not n:
            return None
        if n in self.by_ps:
            return self.by_ps[n]
        if n in self.by_full:
            return self.by_full[n]
        base = re.split(r"[-,]", name)[0]
        fk = norm(base)
        sk = style_key(bold, italic)
        candidates = [fk]
        for suf in ("psmt", "mt", "ps", "std", "pro", "lt"):
            if fk.endswith(suf) and len(fk) > len(suf) + 2:
                candidates.append(fk[: -len(suf)])
        # also strip style words glued onto the family ("ArialBold")
        candidates.append(re.sub(r"(bold|italic|oblique|regular|semibold|light|medium|black)+$", "", fk))
        for c in list(candidates):
            candidates += ALIASES.get(c, [])
        for c in candidates:
            if c and (p := self.family_lookup(c, sk)):
                return p
        return None

    def base14_for(self, pdf_font_name: str, bold: bool, italic: bool, serif: bool, mono: bool) -> str:
        n = norm(strip_subset(pdf_font_name))
        if mono or "courier" in n or "mono" in n:
            fam = "courier"
        elif serif or "times" in n or "serif" in n and "sans" not in n or "roman" in n or "georgia" in n or "garamond" in n or "cambria" in n or "book" in n:
            fam = "times"
        else:
            fam = "helvetica"
        return "builtin:" + BASE14[fam][style_key(bold, italic)]

    def fallbacks(self, bold: bool, italic: bool) -> list[str]:
        self.wait()
        sk = style_key(bold, italic)
        out = []
        for fk in FALLBACK_FAMILIES:
            p = self.family_lookup(fk, sk)
            if p:
                out.append(p)
        out.append("builtin:cjk")
        return out

    def user_families(self) -> list[dict]:
        """Font families offered in the UI for new text."""
        self.wait()
        fams = [
            {"key": "base:helvetica", "name": "Helvetica (standard)"},
            {"key": "base:times", "name": "Times (standard)"},
            {"key": "base:courier", "name": "Courier (standard)"},
        ]
        for fk, styles in sorted(self.families.items(), key=lambda kv: self.display[kv[0]].lower()):
            if "regular" in styles or len(styles) > 0:
                fams.append({"key": "sys:" + fk, "name": self.display[fk]})
        return fams

    def resolve_user_font(self, key: str, bold: bool, italic: bool) -> str:
        sk = style_key(bold, italic)
        if key.startswith("base:"):
            fam = key[5:]
            if fam in BASE14:
                return "builtin:" + BASE14[fam][sk]
        if key.startswith("sys:"):
            p = self.family_lookup(key[4:], sk)
            if p:
                return p
        return "builtin:" + BASE14["helvetica"][sk]


INDEX = FontIndex()
