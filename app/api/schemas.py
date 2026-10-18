"""Request bodies. All coordinates are display-space PDF points (page as shown)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Rect = list[float]  # [x0, y0, x1, y1]


class TextStyle(BaseModel):
    font: Optional[str] = None      # "base:helvetica" | "sys:<family>"; None = keep original
    size: Optional[float] = Field(None, gt=0, le=1000)
    color: Optional[str] = None     # "#rrggbb"
    bold: Optional[bool] = None
    italic: Optional[bool] = None

    def is_empty(self) -> bool:
        return not any(v is not None for v in self.model_dump().values())


class LineEdit(BaseModel):
    page: int
    line: int
    expect: Optional[str] = None
    text: str
    style: Optional[TextStyle] = None


class ParagraphEdit(BaseModel):
    page: int
    paragraph: int
    expect: Optional[str] = None
    text: str
    style: Optional[TextStyle] = None
    align: Optional[Literal["left", "center", "right", "justify"]] = None


class AddText(BaseModel):
    page: int
    x: float
    y: float
    text: str = Field(min_length=1)
    font: str = "base:helvetica"
    size: float = Field(12, gt=0, le=1000)
    color: str = "#000000"
    bold: bool = False
    italic: bool = False
    align: Literal["left", "center", "right"] = "left"
    line_height: float = Field(1.2, gt=0.5, le=5)


class AreaOp(BaseModel):
    page: int
    rect: Rect


class Replace(BaseModel):
    find: str = Field(min_length=1)
    replace: str = ""
    match_case: bool = False


class Draw(BaseModel):
    page: int
    kind: Literal["rect", "ellipse", "line", "arrow", "ink", "whiteout", "highlight"]
    points: list[list[float]] = Field(min_length=1)
    stroke: Optional[str] = "#000000"
    fill: Optional[str] = None
    width: float = Field(1.5, ge=0, le=100)
    opacity: float = Field(1, ge=0, le=1)


class Redact(BaseModel):
    page: int
    rect: Rect
    fill: Optional[str] = "#ffffff"
    text: bool = True
    images: bool = True
    graphics: bool = True


class Markup(BaseModel):
    page: int
    rect: Rect
    kind: Literal["highlight", "underline", "strikeout", "squiggly"]
    color: Optional[str] = None


class Note(BaseModel):
    page: int
    x: float
    y: float
    text: str
    color: Optional[str] = "#ffd400"


class XrefOp(BaseModel):
    page: int
    xref: int


class FieldSet(BaseModel):
    page: int
    xref: int
    value: str | bool | None = None


class Pages(BaseModel):
    pages: list[int] = Field(min_length=1)


class Rotate(Pages):
    delta: int = 90


class InsertBlank(BaseModel):
    at: int
    width: Optional[float] = None
    height: Optional[float] = None


class Reorder(BaseModel):
    order: list[int]


class PageRef(BaseModel):
    page: int


class Metadata(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    subject: Optional[str] = None
    keywords: Optional[str] = None
    name: Optional[str] = None  # download file name


class Export(BaseModel):
    pages: Optional[list[int]] = None
    password: Optional[str] = None
    flatten: bool = False


class Blank(BaseModel):
    size: Literal["a4", "letter", "legal", "a3", "a5"] = "a4"
    landscape: bool = False
