"""Background job runner.

Every slow operation (model download, synthesis, transcription, export, update
check) runs through :class:`JobRunner`, which keeps the GUI thread free and
reports progress with Qt signals — the signal/slot hop is what makes it safe to
touch widgets from the UI callbacks.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..app.logging_config import get_logger

log = get_logger("tixi.services.jobs")

JobFunction = Callable[..., Any]
ProgressCallback = Callable[[float, str], None]


class JobCancelled(RuntimeError):
    """Raised inside a job when the user cancels it."""


@dataclass
class JobInfo:
    """Public description of a running job."""

    job_id: str
    label: str
    detail: str = ""
    progress: float = 0.0
    started_at: float = field(default_factory=time.time)
    cancellable: bool = True
    kind: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_s(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def as_dict(self) -> dict[str, Any]:
        data = {
            "job_id": self.job_id,
            "label": self.label,
            "detail": self.detail,
            "progress": round(self.progress, 4),
            "elapsed_s": round(self.elapsed_s, 2),
            "kind": self.kind,
        }
        data.update(self.payload)
        return data


class _JobSignals(QObject):
    started = Signal(dict)
    progress = Signal(dict)
    finished = Signal(dict)
    failed = Signal(dict)
    cancelled = Signal(dict)


class _JobTask(QRunnable):
    """Runnable wrapper: the function gets ``progress`` and ``cancel`` hooks."""

    def __init__(
        self,
        info: JobInfo,
        function: JobFunction,
        signals: _JobSignals,
        cancel_event: threading.Event,
        *,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        pass_hooks: bool = True,
    ) -> None:
        super().__init__()
        self.info = info
        self.function = function
        self.signals = signals
        self.cancel_event = cancel_event
        self.args = args
        self.kwargs = kwargs or {}
        self.pass_hooks = pass_hooks
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self.cancel_event.set()

    def _progress(self, fraction: float, detail: str = "") -> None:
        self.info.progress = max(0.0, min(1.0, float(fraction)))
        if detail:
            self.info.detail = detail
        payload = self.info.as_dict()
        payload["detail"] = detail or self.info.detail
        self.signals.progress.emit(payload)
        if self.cancel_event.is_set():
            raise JobCancelled(self.info.label)

    def run(self) -> None:  # noqa: D102 - QRunnable
        self.signals.started.emit(self.info.as_dict())
        try:
            if self.pass_hooks:
                result = self.function(
                    *self.args, progress=self._progress, cancel=self.cancel_event, **self.kwargs
                )
            else:
                result = self.function(*self.args, **self.kwargs)
        except JobCancelled:
            self.signals.cancelled.emit(self.info.as_dict())
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            log.exception("job failed", extra={"event": "job_failed", "job": self.info.label})
            payload = self.info.as_dict()
            payload["error"] = f"{type(exc).__name__}: {exc}"
            payload["exception"] = exc
            self.signals.failed.emit(payload)
        else:
            payload = self.info.as_dict()
            payload["result"] = result
            self.signals.finished.emit(payload)


class JobRunner(QObject):
    """Tracks and runs background jobs, exposing Qt signals for the UI."""

    job_started = Signal(dict)
    job_progress = Signal(dict)
    job_finished = Signal(dict)
    job_failed = Signal(dict)
    job_cancelled = Signal(dict)
    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None, *, max_threads: int = 4) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, max_threads))
        self._tasks: dict[str, _JobTask] = {}
        self._lock = threading.RLock()

    # -- queries ------------------------------------------------------------
    @property
    def active_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [task.info.as_dict() for task in self._tasks.values()]

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return bool(self._tasks)

    def is_running(self, kind: str) -> bool:
        with self._lock:
            return any(task.info.kind == kind for task in self._tasks.values())

    # -- submission ---------------------------------------------------------
    def submit(
        self,
        label: str,
        function: JobFunction,
        *,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        kind: str = "",
        cancellable: bool = True,
        on_finished: Callable[[dict[str, Any]], None] | None = None,
        on_failed: Callable[[dict[str, Any]], None] | None = None,
        on_cancelled: Callable[[dict[str, Any]], None] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        payload: dict[str, Any] | None = None,
        exclusive: bool = False,
        pass_hooks: bool = True,
    ) -> str:
        """Run ``function`` in the thread pool and return its job id.

        ``function`` is called with ``progress(fraction, detail)`` and
        ``cancel`` (a :class:`threading.Event`) keyword arguments unless
        ``pass_hooks`` is false.
        """
        if exclusive and kind:
            with self._lock:
                existing = next((t for t in self._tasks.values() if t.info.kind == kind), None)
            if existing is not None:
                log.info("job rejected: identical job already running", extra={"event": "job_duplicate", "kind": kind})
                return existing.info.job_id
        job_id = uuid.uuid4().hex[:12]
        info = JobInfo(
            job_id=job_id,
            label=label,
            cancellable=cancellable,
            kind=kind or label,
            payload=payload or {},
        )
        signals = _JobSignals(self)
        cancel_event = threading.Event()
        task = _JobTask(
            info,
            function,
            signals,
            cancel_event,
            args=args,
            kwargs=kwargs,
            pass_hooks=pass_hooks,
        )
        if on_progress is not None:
            signals.progress.connect(on_progress)
        if on_finished is not None:
            signals.finished.connect(on_finished)
        if on_failed is not None:
            signals.failed.connect(on_failed)
        if on_cancelled is not None:
            signals.cancelled.connect(on_cancelled)

        signals.started.connect(self._on_started)
        signals.progress.connect(self._on_progress)
        signals.finished.connect(self._on_finished)
        signals.failed.connect(self._on_failed)
        signals.cancelled.connect(self._on_cancelled)

        with self._lock:
            self._tasks[job_id] = task
        self._pool.start(task)
        return job_id

    # -- control ------------------------------------------------------------
    def cancel(self, job_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(job_id)
        if task is None:
            return False
        task.cancel()
        return True

    def cancel_kind(self, kind: str) -> int:
        with self._lock:
            tasks = [t for t in self._tasks.values() if t.info.kind == kind]
        for task in tasks:
            task.cancel()
        return len(tasks)

    def cancel_all(self) -> int:
        with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        return len(tasks)

    def wait_for_all(self, timeout_ms: int = 5000) -> bool:
        return self._pool.waitForDone(timeout_ms)

    # -- signal handlers ----------------------------------------------------
    def _on_started(self, payload: dict[str, Any]) -> None:
        self.job_started.emit(payload)
        self.busy_changed.emit(True)

    def _on_progress(self, payload: dict[str, Any]) -> None:
        self.job_progress.emit(payload)

    def _forget(self, payload: dict[str, Any]) -> None:
        job_id = payload.get("job_id", "")
        with self._lock:
            self._tasks.pop(job_id, None)
            busy = bool(self._tasks)
        self.busy_changed.emit(busy)

    def _on_finished(self, payload: dict[str, Any]) -> None:
        self._forget(payload)
        self.job_finished.emit(payload)

    def _on_failed(self, payload: dict[str, Any]) -> None:
        self._forget(payload)
        self.job_failed.emit(payload)

    def _on_cancelled(self, payload: dict[str, Any]) -> None:
        self._forget(payload)
        self.job_cancelled.emit(payload)
