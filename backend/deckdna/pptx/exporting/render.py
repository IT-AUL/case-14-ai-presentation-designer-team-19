"""Headless LibreOffice rendering (workstream T3, minimal slice).

pptx → PDF via ``soffice --headless`` → per-slide PNG via ``pdftoppm``.
Used today by the POC test to prove the generated deck renders; the full
renderer (montage, font-substitution capture, timeouts) lands with T3.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from deckdna.errors import DeckDNAError

_RENDER_TIMEOUT_S = 120
# Open (OFL) fonts that templates embed as MTX-compressed EOT, which
# LibreOffice cannot read (Play in the organizer templates; Carlito as the
# metric twin of Calibri). They go into the per-call profile's user/fonts:
# LibreOffice loads that directory on every OS, while on macOS it ignores
# fontconfig and even ~/Library/Fonts. Without them the PDF falls back to
# a serif face and no longer matches the PPTX.
_FONT_DIRS = (
    Path(__file__).resolve().parents[4] / "assets" / "fonts",
    Path("/usr/local/share/fonts/deckdna"),
)


def _install_profile_fonts(profile: str) -> None:
    target = Path(profile) / "user" / "fonts"
    for root in _FONT_DIRS:
        if not root.is_dir():
            continue
        for font in sorted(root.rglob("*")):
            if font.suffix.lower() in (".ttf", ".otf"):
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy(font, target / font.name)
        return


def _require(tool: str) -> str:
    exe = shutil.which(tool)
    if exe is None:
        raise DeckDNAError(
            code="render_failed",
            message=f"required renderer tool not found on PATH: {tool}",
            stage="exporting.render",
        )
    return exe


def _convert(pptx_path: Path, target: str, out_dir: Path, soffice: str) -> Path:
    """Run the headless soffice conversion to *target*; returns the file it produced.

    Each invocation uses a unique ``UserInstallation`` temp directory so
    that concurrent or sequential calls never fight over the default
    LibreOffice profile lock — the primary cause of ``DeploymentException``
    crashes on macOS (and sometimes Linux containers with shared /tmp).
    """
    with tempfile.TemporaryDirectory(prefix="deckdna-soffice-profile-") as profile:
        _install_profile_fonts(profile)
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                soffice,
                "--headless",
                "--nologo",
                "--nofirststartwizard",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to",
                target,
                "--outdir",
                str(out_dir),
                str(pptx_path),
            ],
            capture_output=True,
            text=True,
            timeout=_RENDER_TIMEOUT_S,
            check=False,
        )
    produced = out_dir / f"{pptx_path.stem}.{target}"
    if proc.returncode != 0 or not produced.exists():
        err = (proc.stderr or proc.stdout).strip()
        raise DeckDNAError(
            code="render_failed",
            message=f"soffice failed on {pptx_path.name}: {err}",
            stage="exporting.render",
        )
    return produced


def _convert_pdf(pptx_path: Path, pdf_dir: Path, soffice: str) -> Path:
    """Run the headless soffice conversion; returns the produced PDF path."""
    return _convert(pptx_path, "pdf", pdf_dir, soffice)


def render_pdf(pptx_path: str | Path, out_path: str | Path) -> Path:
    """Convert *pptx_path* to a PDF written at *out_path*; returns it."""
    pptx_path = Path(pptx_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    soffice = _require("soffice")

    with tempfile.TemporaryDirectory(prefix="deckdna-render-") as tmp:
        pdf = _convert_pdf(pptx_path, Path(tmp), soffice)
        shutil.copy(pdf, out_path)
    return out_path


def _slide_texts(pptx_path: Path) -> list[str]:
    """Plain text of each slide (caption material for the HTML viewer)."""
    from pptx import Presentation

    texts: list[str] = []
    for slide in Presentation(str(pptx_path)).slides:
        parts = [
            shape.text_frame.text.strip()
            for shape in slide.shapes
            if shape.has_text_frame and shape.text_frame.text.strip()
        ]
        texts.append("\n".join(parts))
    return texts


def render_html(pptx_path: str | Path, out_dir: str | Path) -> Path:
    """Render *pptx_path* to an HTML viewer directory; returns index.html.

    Slides go through the proven pdf→pdftoppm path into ``slide-*.png``
    assets; ``index.html`` references them plus per-slide text captions.
    Deliberately avoids ``soffice --convert-to html``: that filter emits a
    single document with every slide image base64-embedded, which hits
    libxml2 huge-text-node limits on real decks (OR-010 is a viewer, not
    an editable format — ARCHITECTURE §12).
    """
    import html as _html

    pptx_path = Path(pptx_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    slides = render_slides_png(pptx_path, out_dir)
    texts = _slide_texts(pptx_path)

    items = []
    for i, png in enumerate(slides):
        caption = texts[i] if i < len(texts) else ""
        items.append(
            '<div class="slide">\n'
            f'  <h2>Slide {i + 1}</h2>\n'
            f'  <img src="{_html.escape(png.name)}" alt="Slide {i + 1}">\n'
            + (f'  <p class="caption">{_html.escape(caption)}</p>\n' if caption else "")
            + "</div>"
        )
    index = out_dir / "index.html"
    index.write_text(
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8">'
        f"<title>{_html.escape(pptx_path.stem)}</title>\n"
        "<style>"
        "body{font-family:sans-serif;margin:2em;background:#f7f7f7}"
        ".slide{margin-bottom:2.5em}"
        "img{max-width:100%;border:1px solid #ccc;background:#fff}"
        ".caption{color:#555;font-size:.9em;white-space:pre-line;margin-top:.4em}"
        "</style></head><body>\n"
        f"<h1>{_html.escape(pptx_path.stem)}</h1>\n" + "\n".join(items) + "\n</body></html>\n",
        encoding="utf-8",
    )
    return index


def render_slides_png(
    pptx_path: str | Path,
    out_dir: str | Path,
    first: int = 1,
    last: int | None = None,
) -> list[Path]:
    """Render slides [first..last] of *pptx_path* to PNGs in *out_dir*.

    Returns the produced PNG paths (``slide_1.png``, ...). Page numbers are
    1-based; *last*=None renders through the final page.
    """
    pptx_path = Path(pptx_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    soffice = _require("soffice")
    pdftoppm = _require("pdftoppm")

    with tempfile.TemporaryDirectory(prefix="deckdna-render-") as tmp:
        pdf_path = _convert_pdf(pptx_path, Path(tmp), soffice)
        cmd = [pdftoppm, "-png", "-r", "96", "-f", str(first)]
        if last is not None:
            cmd += ["-l", str(last)]
        subprocess.run(  # noqa: S603 — fixed argv, no shell
            [*cmd, str(pdf_path), str(out_dir / "slide")],
            capture_output=True,
            text=True,
            timeout=_RENDER_TIMEOUT_S,
            check=True,
        )
    return sorted(out_dir.glob("slide-*.png"))
