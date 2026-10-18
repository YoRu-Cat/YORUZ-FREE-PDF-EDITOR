"""Coordinate helpers.

Two coordinate systems exist:
  * display space - what the user sees: the page after its /Rotate is applied,
    origin top-left, units = PDF points. The browser always talks in this space.
  * pdf space     - the unrotated page (PyMuPDF's native space for extraction,
    drawing, annotations and redactions).
All engine code works in pdf space; conversion happens only at the API edge.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

import pymupdf as fitz


@dataclass
class Frame:
    to_disp: fitz.Matrix
    to_pdf: fitz.Matrix
    rotation: int

    def pt_pdf(self, x: float, y: float) -> fitz.Point:
        return fitz.Point(x, y) * self.to_pdf

    def pt_disp(self, p) -> list[float]:
        q = fitz.Point(p) * self.to_disp
        return [round(q.x, 2), round(q.y, 2)]

    def rect_pdf(self, r: Sequence[float]) -> fitz.Rect:
        return (fitz.Rect(r) * self.to_pdf).normalize()

    def rect_disp(self, r) -> list[float]:
        q = (fitz.Rect(r) * self.to_disp).normalize()
        return [round(q.x0, 2), round(q.y0, 2), round(q.x1, 2), round(q.y1, 2)]

    def upright_angle(self) -> float:
        """Rotation (deg) for content that must look upright to the viewer."""
        v = fitz.Point(1, 0) * self.to_pdf - fitz.Point(0, 0) * self.to_pdf
        return math.degrees(math.atan2(v.y, v.x))


def frame_of(page: fitz.Page) -> Frame:
    return Frame(page.rotation_matrix, page.derotation_matrix, page.rotation)


@contextmanager
def unrotated(page: fitz.Page) -> Iterator[Frame]:
    """Temporarily set /Rotate 0 so every PyMuPDF call works in pdf space."""
    fr = frame_of(page)
    if fr.rotation:
        page.set_rotation(0)
    try:
        yield fr
    finally:
        if fr.rotation:
            page.set_rotation(fr.rotation)


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def rect_corners(r: Sequence[float]) -> Iterable[tuple[float, float]]:
    x0, y0, x1, y1 = r
    return ((x0, y0), (x1, y0), (x0, y1), (x1, y1))


def hex_to_rgb(h: str | None) -> tuple[float, float, float] | None:
    if not h:
        return None
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def int_to_hex(c: int) -> str:
    return f"#{c & 0xFFFFFF:06x}"


def int_to_rgb(c: int) -> tuple[float, float, float]:
    return ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255
