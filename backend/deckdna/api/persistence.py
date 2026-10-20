"""Снапшот состояния API на диск (ADR-031 §8).

``_Store`` живёт в памяти процесса: перезапуск или падение контейнера во
время демо стирал проекты, шаблоны, паки и прогоны. Полноценная БД до
финала не нужна — достаточно снапшота после каждой мутирующей операции и
загрузки при старте.

* **Формат.** ``pickle`` словаря полей ``_Store`` (все записи — pydantic-
  модели и простые коллекции). Байты артефактов (PPTX, PDF, превью,
  исходные файлы) пишутся отдельно, по одному файлу на sha256 в
  ``<dir>/blobs/``: снапшот после каждой операции не переписывает сотни
  мегабайт уже сохранённых колод.
* **Атомарность.** Запись во временный файл рядом и ``os.replace``: при
  падении посреди записи остаётся предыдущий целый снапшот.
* **Секреты не сохраняются** (ADR-005, ephemeral secrets): токены
  provider-сессий и сами сессии (без токена сессия бесполезна) в снапшот не
  попадают — после перезапуска фронт открывает сессию заново.
* **Доверие.** Снапшот пишет и читает только сам сервер, в своём каталоге
  данных; ``pickle`` из чужого источника не загружается.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import tempfile
import threading
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1
# не переживают перезапуск: секреты (ADR-005) и сессии, без них бесполезные
_EPHEMERAL = frozenset({"provider_secrets", "provider_sessions"})
_WRITE_LOCK = threading.Lock()


def _blob_dir(path: Path) -> Path:
    return path.parent / "blobs"


def save_store(store: Any, path: Path, lock: AbstractContextManager | None = None) -> None:
    """Атомарно записать снапшот *store* в *path*.

    *lock* — тот же замок, под которым фоновые прогоны меняют ``STORE``:
    под ним снимаются поверхностные копии словарей (быстро), сериализация
    и запись идут уже без него."""
    with lock or nullcontext(), _WRITE_LOCK:
        _save_store_locked(store, path)


def _save_store_locked(store: Any, path: Path) -> None:
    fields = {
        name: (dict(value) if isinstance(value, dict) else value)
        for name, value in vars(store).items()
        if name not in _EPHEMERAL
    }
    artifacts = fields.get("artifacts") or {}
    blobs = _blob_dir(path)
    blobs.mkdir(parents=True, exist_ok=True)
    stripped = {}
    for key, artifact in artifacts.items():
        data = getattr(artifact, "data", b"")
        sha = getattr(artifact, "sha256", "")
        if data and sha:
            blob = blobs / sha
            if not blob.exists():
                _atomic_write(blob, data)
            artifact = artifact.model_copy(update={"data": b""})
        stripped[key] = artifact
    fields["artifacts"] = stripped
    payload = pickle.dumps({"version": SNAPSHOT_VERSION, "fields": fields})
    _atomic_write(path, payload)


def load_store(store: Any, path: Path, *, preserve_queued: bool = False,
               require_integrity: bool = False) -> bool:
    """Восстановить *store* из снапшота. False, если снапшота нет или он
    нечитаем (тогда сервер стартует с пустым состоянием, как раньше)."""
    if not path.exists():
        return False
    try:
        snapshot = pickle.loads(path.read_bytes())  # noqa: S301 — свой файл сервера
    except Exception as exc:  # noqa: BLE001 — битый снапшот не роняет старт
        if require_integrity:
            raise RuntimeError(
                'Durable state snapshot is unreadable; restore it before startup') from exc
        logger.warning("state snapshot %s unreadable (%s); starting empty", path, exc)
        return False
    if not isinstance(snapshot, dict) or snapshot.get("version") != SNAPSHOT_VERSION:
        if require_integrity:
            raise RuntimeError('Durable state snapshot has an unsupported version')
        logger.warning("state snapshot %s has an unknown version; starting empty", path)
        return False
    blobs = _blob_dir(path)
    for name, value in snapshot["fields"].items():
        if name in _EPHEMERAL or not hasattr(store, name):
            continue
        if name == "artifacts":
            restored = {}
            for key, artifact in value.items():
                blob = blobs / getattr(artifact, "sha256", "")
                if not artifact.data and blob.is_file():
                    artifact = artifact.model_copy(update={"data": blob.read_bytes()})
                if require_integrity and (
                    len(artifact.data) != artifact.size_bytes
                    or hashlib.sha256(artifact.data).hexdigest() != artifact.sha256
                ):
                    raise RuntimeError('Durable artifact is missing or corrupt; restore its blob')
                restored[key] = artifact
            value = restored
        setattr(store, name, value)
    _restore_model_defaults(vars(store), set())
    _recover_interrupted_jobs(store, preserve_queued=preserve_queued)
    return True


def _restore_model_defaults(value: Any, seen: set[int]) -> None:
    """Pickle bypasses model validation: fill additive defaults after an upgrade.

    Keep values and fields_set intact; an added default is not an author choice.
    Required fields are never invented and model graphs may share references.
    """
    if not isinstance(value, (BaseModel, dict, list, tuple)) or id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, BaseModel):
        for name, field in type(value).model_fields.items():
            if name not in value.__dict__ and not field.is_required():
                value.__dict__[name] = field.get_default(call_default_factory=True)
        children = value.__dict__.values()
    elif isinstance(value, dict):
        children = value.values()
    else:
        children = value
    for child in children:
        _restore_model_defaults(child, seen)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _recover_interrupted_jobs(store: Any, *, preserve_queued: bool = False) -> None:
    """Thread-pool work cannot survive a process restart; allow an honest retry.

    Use the restored model classes to avoid importing api.app during its own
    initialization. Completed artifacts and completed variants remain available.
    """
    now = datetime.now(UTC)
    for job_id, job in list(getattr(store, "jobs", {}).items()):
        if preserve_queued and job.state == "queued" and (
            job.kind == "generation" or job.stage == "slide_change"
        ):
            continue
        if job.state not in ("queued", "running"):
            continue
        raw = job.model_dump()
        raw.update(state="failed", finished_at=now, error={
            "code": "execution_interrupted", "stage": job.stage,
            "message": "Server restarted before this job completed; retry with saved inputs.",
        })
        recovered = type(job).model_validate(raw)
        store.jobs[job_id] = recovered
        run_id = (job.result_ids or {}).get("generation_id")
        run = getattr(store, "runs", {}).get(run_id)
        if run is not None and run.detail.state in ("queued", "running"):
            failed = recovered.state
            run.detail = run.detail.model_copy(update={
                "state": failed, "finished_at": now,
                "variants": [v.model_copy(update={
                    "status": failed, "stage": None, "finished_at": now,
                }) if v.status in ("queued", "running") else v for v in run.detail.variants],
            })
        events = store.job_events.setdefault(job_id, [])
        events.append({
            "sequence": len(events) + 1, "event": "job.failed", "timestamp": now.isoformat(),
            "job_id": job_id, "stage": job.stage, "progress": job.progress,
            "state": "failed", "error": recovered.error.model_dump(),
        })

    for run in getattr(store, "runs", {}).values():
        for items in getattr(run, "changes", {}).values():
            for item in items:
                if preserve_queued and item["state"] == "queued":
                    continue
                if item["state"] in {"queued", "running"}:
                    item.update(state="failed", error={"code": "execution_interrupted",
                                "message": "Server restarted; resubmit from committed revision."})
