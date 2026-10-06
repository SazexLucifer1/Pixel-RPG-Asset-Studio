"""Sequential background job queue.

All heavy work (AI generation, Blender renders, processing) runs here, one job
at a time. Running stages sequentially is deliberate: it keeps VRAM usage
predictable on mid-range GPUs (RTX 3060 target) and makes logs easy to read.

The queue is UI-agnostic; the Qt layer subscribes via :meth:`JobQueue.add_listener`.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from pixel_rpg_studio.core.errors import ErrorReport, JobCancelledError

log = logging.getLogger(__name__)


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobContext:
    """Handed to the job function to report progress and check cancellation."""

    def __init__(self, job: "Job", notify: Callable[["Job"], None]) -> None:
        self._job = job
        self._notify = notify

    @property
    def cancelled(self) -> bool:
        return self._job.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelledError()

    def progress(self, fraction: float | None = None, message: str | None = None) -> None:
        if fraction is not None:
            self._job.progress = max(0.0, min(1.0, float(fraction)))
        if message is not None:
            self._job.message = message
            log.info("[job %s] %s", self._job.id, message)
        self._notify(self._job)

    def sub(self, start: float, end: float) -> "SubProgress":
        """Progress reporter mapping 0..1 onto ``start..end`` of this job."""
        return SubProgress(self, start, end)


class SubProgress:
    def __init__(self, ctx: JobContext, start: float, end: float) -> None:
        self.ctx, self.start, self.end = ctx, start, end

    @property
    def cancelled(self) -> bool:
        return self.ctx.cancelled

    def check_cancelled(self) -> None:
        self.ctx.check_cancelled()

    def progress(self, fraction: float | None = None, message: str | None = None) -> None:
        value = None if fraction is None else self.start + (self.end - self.start) * max(0.0, min(1.0, fraction))
        self.ctx.progress(value, message)

    def sub(self, start: float, end: float) -> "SubProgress":
        span = self.end - self.start
        return SubProgress(self.ctx, self.start + span * start, self.start + span * end)


@dataclass
class Job:
    id: int
    title: str
    func: Callable[[JobContext], Any]
    state: JobState = JobState.QUEUED
    progress: float = 0.0
    message: str = "Waiting..."
    result: Any = None
    error: ErrorReport | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    on_cancel: Callable[[], None] | None = field(default=None, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def cancel(self) -> None:
        self.cancel_event.set()
        if self.on_cancel is not None:
            try:
                self.on_cancel()
            except Exception:  # pragma: no cover - best effort
                log.exception("on_cancel hook failed")

    def wait(self, timeout: float | None = None) -> bool:
        return self.done_event.wait(timeout)


Listener = Callable[[Job], None]


class JobQueue:
    """Single-worker FIFO job queue."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[Job | None]" = queue.Queue()
        self._jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="studio-job-worker", daemon=True)
        self._started = False
        self.current: Job | None = None

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._worker.start()

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def submit(self, title: str, func: Callable[[JobContext], Any]) -> Job:
        self.start()
        job = Job(id=next(self._ids), title=title, func=func)
        with self._lock:
            self._jobs[job.id] = job
        self._notify(job)
        self._queue.put(job)
        return job

    def jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def pending_count(self) -> int:
        return sum(1 for j in self.jobs() if j.state in (JobState.QUEUED, JobState.RUNNING))

    def cancel(self, job_id: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job and job.state in (JobState.QUEUED, JobState.RUNNING):
            job.cancel()
            self._notify(job)

    def clear_finished(self) -> None:
        with self._lock:
            for jid in [j.id for j in self._jobs.values() if j.state not in (JobState.QUEUED, JobState.RUNNING)]:
                del self._jobs[jid]

    def shutdown(self, timeout: float = 2.0) -> None:
        for job in self.jobs():
            if job.state in (JobState.QUEUED, JobState.RUNNING):
                job.cancel()
        self._queue.put(None)
        if self._started:
            self._worker.join(timeout)

    # ----------------------------------------------------------------- internal
    def _notify(self, job: Job) -> None:
        for listener in list(self._listeners):
            try:
                listener(job)
            except Exception:  # pragma: no cover - listener bugs must not kill jobs
                log.exception("Job listener failed")

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            if job.cancel_event.is_set():
                job.state = JobState.CANCELLED
                job.message = "Cancelled before start"
                job.finished = time.time()
                job.done_event.set()
                self._notify(job)
                continue
            self.current = job
            job.state = JobState.RUNNING
            job.started = time.time()
            job.message = "Running..."
            self._notify(job)
            ctx = JobContext(job, self._notify)
            try:
                job.result = job.func(ctx)
                if job.cancel_event.is_set():
                    raise JobCancelledError()
                job.state = JobState.DONE
                job.progress = 1.0
                job.message = "Finished"
            except JobCancelledError:
                job.state = JobState.CANCELLED
                job.message = "Cancelled"
            except BaseException as exc:  # noqa: BLE001 - report everything
                log.exception("Job '%s' failed", job.title)
                job.state = JobState.FAILED
                job.error = ErrorReport.from_exception(exc)
                job.message = job.error.message
            finally:
                job.finished = time.time()
                self.current = None
                job.done_event.set()
                self._notify(job)
