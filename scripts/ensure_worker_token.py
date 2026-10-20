"""Provision Compose dispatcher auth in ignored .env without printing secrets."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import dotenv_values


def main() -> None:
    path = Path(__file__).resolve().parents[1] / '.env'
    if not path.is_file():
        raise SystemExit('Create .env from .env.example first; existing settings are preserved.')
    value = (os.environ.get('DECKDNA_WORKER_TOKEN')
             or dotenv_values(path).get('DECKDNA_WORKER_TOKEN'))
    if value:
        if len(value) < 16:
            raise SystemExit('Configured worker token is shorter than the required 16 characters.')
        print('Worker token already configured; no changes.')
        return
    text = path.read_text(encoding='utf-8')
    prefix = '\n' if text and not text.endswith('\n') else ''
    with path.open('a', encoding='utf-8') as stream:
        stream.write(prefix + 'DECKDNA_WORKER_TOKEN=' + secrets.token_hex(32) + '\n')
    print('Worker token added to ignored .env; existing settings preserved.')


if __name__ == '__main__':
    main()
