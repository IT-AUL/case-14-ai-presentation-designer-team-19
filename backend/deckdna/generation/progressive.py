"""Real draft renders at compiler slide boundaries (ADR-036).

Opt-in: no extra model calls. A single process-wide render slot bounds resource
use across concurrently generated variants. Final audit/render remains canonical.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deckdna.pptx.exporting.previews import pdf_page_pngs
from deckdna.pptx.exporting.render import render_pdf
from deckdna.pptx.opc.package import OpcPackage

logger = logging.getLogger(__name__)
_RENDER_SLOT = threading.BoundedSemaphore(1)
SlideEvent = Callable[[str, dict[str, Any]], None]


class ProgressiveRenderer:
    def __init__(
        self, directory: Path, on_event: SlideEvent,
        is_canceled: Callable[[], bool] | None = None,
    ) -> None:
        self.directory = directory
        self.on_event = on_event
        self.is_canceled = is_canceled or (lambda: False)

    def compiled(self, index: int, slide_id: str, package: OpcPackage) -> None:
        if self.is_canceled():
            return
        payload = {"index": index + 1, "slide_id": slide_id, "revision": 1, "status": "draft"}
        self.on_event("slide.compiled", payload)
        with _RENDER_SLOT:
            if self.is_canceled():
                return
            directory = self.directory / str(index + 1)
            directory.mkdir(parents=True, exist_ok=True)
            pptx = directory / "slide.pptx"
            try:
                package.save(pptx)
                pdf = render_pdf(pptx, directory / "slide.pdf")
                pngs = pdf_page_pngs(pdf)
                if len(pngs) != 1:
                    raise ValueError(f"single-slide render returned {len(pngs)} pages")
                if not self.is_canceled():
                    self.on_event("slide.draft_ready", {**payload, "png": pngs[0]})
            except Exception:  # noqa: BLE001 — final export can still succeed
                logger.exception("draft render failed for slide %s", slide_id)
                if not self.is_canceled():
                    self.on_event("slide.preview_failed", {
                        **payload, "code": "render_failed",
                    })
