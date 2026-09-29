# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

# LibreOffice + poppler + fonts for rendering pipeline
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress libreoffice-core poppler-utils \
    fonts-dejavu fonts-liberation fonts-noto \
    fonts-crosextra-carlito fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/*

# Open (OFL) fonts that templates commonly embed as MTX-compressed EOT,
# which LibreOffice cannot read: without them the PDF/PNG render falls
# back to a serif face and text metrics no longer match the PPTX.
COPY assets/fonts /usr/local/share/fonts/deckdna
RUN fc-cache -f
# render.py also copies them into each LibreOffice profile (user/fonts)

RUN pip install --no-cache-dir uv
# Repeatedly timing out on flaky/slow connections to pypi.org (different
# package each time: starlette, trove_classifiers, watchfiles) -- uv's
# default 30s per-request timeout is too tight for that. Not a fix for
# the network itself, but makes each retry attempt more likely to
# actually finish instead of a fast, wasted failure.
ENV UV_HTTP_TIMEOUT=120
WORKDIR /app
COPY pyproject.toml ./
COPY configs ./configs
COPY schemas ./schemas
COPY prompts ./prompts
COPY skill ./skill
COPY backend ./backend
RUN --mount=type=cache,target=/root/.cache/uv uv pip install --system -e .

RUN useradd -m deckdna && mkdir -p /data/artifacts && chown -R deckdna /data /app
USER deckdna

EXPOSE 8000
CMD ["uvicorn", "deckdna.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
