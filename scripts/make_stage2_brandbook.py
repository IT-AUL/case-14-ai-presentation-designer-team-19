"""Синтетический брендбук второй ступени (ADR-033 §9, §11) →
examples/stage2/brandbook/.

Бренд «Северный ветер» выдуман. Основной цвет (оранжево-красный)
специально спорит с синим акцентом шаблонов VK: на нём видно, как
работают ``brand_priority`` и конфликты в паспорте. Четыре входа одного
бренда: токены W3C, экспорт переменных Figma, PDF-гайдлайн, логотип.

    uv run python scripts/make_stage2_brandbook.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "examples" / "stage2" / "brandbook"
NAME = "Северный ветер"
COLORS = {
    "primary": ("#E4572E", "Основной: закатный"),
    "secondary": ("#29335C", "Вторичный: глубокий синий"),
    "accent": ("#F3A712", "Акцентный: янтарь"),
    "text": ("#1B1B1E", "Текст"),
    "background": ("#FFFFFF", "Фон"),
}
FONTS = {"heading": "DejaVu Serif", "body": "Liberation Sans"}
FONT_FILE = str(Path(__file__).resolve().parents[1] / "assets/fonts/play/Play-Regular.ttf")


def tokens() -> dict:
    return {
        "name": NAME,
        "color": {
            role: {"$type": "color", "$value": hex_, "$description": label}
            for role, (hex_, label) in COLORS.items()
        },
        "font": {
            role: {"$type": "fontFamily", "$value": family} for role, family in FONTS.items()
        },
    }


def figma_variables() -> dict:
    def rgba(hex_: str) -> dict:
        h = hex_.lstrip("#")
        return {"r": int(h[0:2], 16) / 255, "g": int(h[2:4], 16) / 255,
                "b": int(h[4:6], 16) / 255, "a": 1}

    variables = [
        {"name": f"brand/{role}", "resolvedType": "COLOR",
         "valuesByMode": {"1:0": rgba(hex_)}}
        for role, (hex_, _label) in COLORS.items()
    ] + [
        {"name": f"font/{role}", "resolvedType": "STRING", "valuesByMode": {"1:0": family}}
        for role, family in FONTS.items()
    ]
    return {"collections": [{"name": NAME, "modes": [{"modeId": "1:0", "name": "Light"}],
                             "variables": variables}]}


def guideline_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(TTFont("GuideText", FONT_FILE))
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"Брендбук «{NAME}» (синтетический)")
    width, height = A4
    y = height - 72
    c.setFont("GuideText", 20)
    c.drawString(56, y, f"Брендбук «{NAME}»")
    y -= 28
    c.setFont("GuideText", 10)
    c.drawString(56, y, "Синтетический пример для ЛЦТ-2026. Фирменная палитра и шрифты.")
    y -= 40
    c.setFont("GuideText", 14)
    c.drawString(56, y, "Палитра")
    y -= 26
    c.setFont("GuideText", 11)
    for hex_, label in COLORS.values():
        h = hex_.lstrip("#")
        c.setFillColorRGB(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)
        c.rect(56, y - 4, 28, 16, fill=1, stroke=1)
        c.setFillColorRGB(0, 0, 0)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        c.drawString(96, y, f"{label} - HEX {hex_}, RGB {r}, {g}, {b}")
        y -= 24
    y -= 16
    c.setFont("GuideText", 14)
    c.drawString(56, y, "Шрифты")
    y -= 24
    c.setFont("GuideText", 11)
    c.drawString(56, y, f"Шрифт заголовков: {FONTS['heading']}")
    y -= 20
    c.drawString(56, y, f"Основной шрифт текста: {FONTS['body']}")
    y -= 36
    c.setFont("GuideText", 14)
    c.drawString(56, y, "Логотип")
    y -= 24
    c.setFont("GuideText", 11)
    c.drawString(56, y, "Минимальная ширина логотипа 20 мм, отступ вокруг логотипа не меньше 5 мм.")
    c.showPage()
    c.save()


def logo_png(path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (480, 160), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.polygon([(20, 140), (80, 20), (140, 140)], fill=COLORS["primary"][0])
    draw.polygon([(70, 140), (110, 60), (150, 140)], fill=COLORS["secondary"][0])
    draw.rectangle([180, 64, 460, 96], fill=COLORS["secondary"][0])
    img.save(path)


def main() -> None:
    global OUT, FONT_FILE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--font", type=Path, default=Path(FONT_FILE))
    args = parser.parse_args()
    OUT, FONT_FILE = args.out, str(args.font)
    if not args.font.is_file():
        parser.error(f"font not found: {args.font}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tokens.json").write_text(
        json.dumps(tokens(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "figma_variables.json").write_text(
        json.dumps(figma_variables(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    guideline_pdf(OUT / "guideline.pdf")
    logo_png(OUT / "logo.png")
    print(f"brandbook → {OUT}")


if __name__ == "__main__":
    main()
