# hand-maintained (ADR-033 §9): schemas/brandbook.schema.json
"""Брендбук (ADR-033 §9): палитра, шрифты, логотипы, правила, конфликты
с шаблоном. Схема — ``schemas/brandbook.schema.json``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ColorRole = Literal["primary", "secondary", "accent", "text", "background", "other"]
FontRole = Literal["heading", "body", "other"]
SourceKind = Literal["tokens", "figma", "pdf", "logo", "example", "unknown"]


class BrandColor(BaseModel):
    name: str
    hex: str = Field(pattern=r"^#[0-9A-F]{6}$")
    role: ColorRole = "other"
    source: str | None = None


class BrandFont(BaseModel):
    role: FontRole
    family: str
    source: str | None = None


class BrandLogo(BaseModel):
    file_name: str
    artifact_id: str | None = None
    media_type: str | None = None


class BrandRule(BaseModel):
    text: str
    source: str | None = None


class BrandSource(BaseModel):
    file_name: str
    kind: SourceKind
    parsed: bool
    note: str | None = None


class BrandConflict(BaseModel):
    """Расхождение шаблона и брендбука. ``resolved`` — чья сторона взята
    (``brand_priority``); ``kind = contrast`` — предупреждение о читаемости."""

    field: str
    kind: Literal["color", "font", "contrast"]
    template: str | None = None
    brandbook: str | None = None
    resolved: Literal["brandbook", "template"]
    note: str | None = None


class Brandbook(BaseModel):
    id: str
    project_id: str | None = None
    name: str | None = None
    colors: list[BrandColor] = Field(default_factory=list)
    fonts: list[BrandFont] = Field(default_factory=list)
    logos: list[BrandLogo] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    rules: list[BrandRule] = Field(default_factory=list)
    sources: list[BrandSource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def color(self, role: str) -> BrandColor | None:
        return next((c for c in self.colors if c.role == role), None)

    def font(self, role: str) -> BrandFont | None:
        return next((f for f in self.fonts if f.role == role), None)


__all__ = [
    "BrandColor",
    "BrandConflict",
    "BrandFont",
    "BrandLogo",
    "BrandRule",
    "BrandSource",
    "Brandbook",
]
