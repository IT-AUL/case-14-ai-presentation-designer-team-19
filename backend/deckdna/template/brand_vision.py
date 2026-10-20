"""Bounded, explicit transcription of written brand rules from raster pages."""
from __future__ import annotations

import asyncio
import base64
import io
import re
from typing import Literal

from pydantic import BaseModel, Field

from deckdna.contracts.brandbook import Brandbook, BrandColor, BrandFont


class ColorReading(BaseModel):
    image: int = Field(ge=1, le=3)
    quote: str = Field(min_length=1, max_length=300)
    role: Literal['primary', 'secondary', 'accent', 'text', 'background', 'other']
    hex: str = Field(pattern=r'^#[0-9A-Fa-f]{6}$')


class FontReading(BaseModel):
    image: int = Field(ge=1, le=3)
    quote: str = Field(min_length=1, max_length=300)
    role: Literal['heading', 'body', 'other']
    family: str = Field(min_length=1, max_length=100)


class BrandReadings(BaseModel):
    colors: list[ColorReading] = Field(default_factory=list, max_length=12)
    fonts: list[FontReading] = Field(default_factory=list, max_length=6)


def raster_pages(files: list[tuple[str, bytes]]) -> tuple[list[str], list[str]]:
    """Return at most 3 RGB PNGs, each bounded to 1600px; no file/network access."""
    from PIL import Image, ImageOps

    images, labels = [], []

    def append(image, label):
        image = ImageOps.exif_transpose(image).convert('RGB')
        image.thumbnail((1600, 1600))
        output = io.BytesIO()
        image.save(output, format='PNG')
        images.append('data:image/png;base64,' + base64.b64encode(output.getvalue()).decode())
        labels.append(label)

    for name, data in files:
        if len(images) >= 3:
            break
        ext = name.rsplit('.', 1)[-1].lower()
        if ext == 'pdf':
            import fitz

            with fitz.open(stream=data, filetype='pdf') as pdf:
                for index in range(min(len(pdf), 3 - len(images))):
                    page = pdf[index]
                    scale = min(2, 1600 / max(page.rect.width, page.rect.height, 1))
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    append(Image.open(io.BytesIO(pix.tobytes('png'))), f'{name}:page={index+1}')
        elif ext in {'png', 'jpg', 'jpeg', 'webp', 'gif'}:
            with Image.open(io.BytesIO(data)) as image:
                append(image, f'{name}:page=1')
    return images, labels


def merge_readings(book: Brandbook, result: BrandReadings, labels: list[str]) -> Brandbook:
    updated = book.model_copy(deep=True)
    for reading in result.colors:
        if reading.image > len(labels):
            continue
        # The model must transcribe an explicit code, never sample a swatch.
        if reading.hex.casefold() not in reading.quote.casefold():
            continue
        if any(c.role == reading.role for c in updated.colors):
            continue
        updated.colors.append(BrandColor(
            name=reading.quote, hex=reading.hex.upper(), role=reading.role,
            source=f'{labels[reading.image-1]}:vision: {reading.quote}',
        ))
    for reading in result.fonts:
        if reading.image > len(labels) or reading.family.casefold() not in reading.quote.casefold():
            continue
        if not re.search(r'font|typeface|шрифт|гарнитур', reading.quote, re.I):
            continue
        if any(f.role == reading.role for f in updated.fonts):
            continue
        updated.fonts.append(BrandFont(
            family=reading.family, role=reading.role,
            source=f'{labels[reading.image-1]}:vision: {reading.quote}',
        ))
    updated.warnings = [w for w in updated.warnings if not (
        (updated.colors and w == 'В брендбуке не нашлось цветов.')
        or (updated.fonts and w == 'В брендбуке не нашлось шрифтов.')
    )]
    updated.warnings.append(
        f'Vision-разбор: {len(labels)} стр./изобр.; прочитаны только явные подписи. '
        'Проверьте извлечённые значения; остальные страницы не анализировались.')
    return updated


async def analyze_brandbook(book: Brandbook, files, gateway) -> tuple[Brandbook, list[str]]:
    images, labels = await asyncio.to_thread(raster_pages, files)
    if not images:
        return book, []
    async with asyncio.timeout(30):
        result = await gateway.vision_json('brandbook_extract', images, {
            'pages': [{'image': i+1, 'source': label} for i, label in enumerate(labels)],
        }, BrandReadings)
    if not isinstance(result, BrandReadings):
        raise ValueError('invalid brandbook vision response')
    return merge_readings(book, result, labels), labels
