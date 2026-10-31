"""Bounded PySide6 task execution with GUI-thread publication."""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from ..data import CancelledError
from .diagnostics import best_effort_exception_log, best_effort_log

logger = logging.getLogger(__name__)

TaskWork = Callable[[threading.Event], Any]


class TaskStatus(Enum):
    SUCCESS = auto()
    ERROR = auto()
    CANCELLED = auto()


class TaskRunnerClosed(RuntimeError):
    """New work was requested after the runner stopped accepting tasks."""


class TaskQueueFull(RuntimeError):
    """The bounded application queue cannot accept another distinct request."""


@dataclass(frozen=True)
class TaskToken:
    key: str
    generation: int
    application_epoch: int
    dataset_revision: int
    selection_revision: int
    load_ticket: int | None = None
    exclusive_group: str | None = None


@dataclass(frozen=True)
class TaskOutcome:
    token: TaskToken
    status: TaskStatus
    value: Any = None
    error: BaseException | None = None
    worker_thread_id: int | None = None
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class RunnerActivity:
    logical: int
    physical: int
    queued: int

    @property
    def busy(self) -> bool:
        return self.logical > 0 or self.physical > 0


@dataclass
class _Request:
    token: TaskToken
    work: TaskWork
    cancel: threading.Event


def _token_fields(token: TaskToken) -> dict[str, Any]:
    return {
        "key": token.key,
        "generation": token.generation,
        "application_epoch": token.application_epoch,
        "dataset_revision": token.dataset_revision,
        "selection_revision": token.selection_revision,
    }


class _Bridge(QObject):
    outcome = Signal(object)
    # R3 emitted this signal before QRunnable.run() returned. R4 retains the
    # checkpoint only for adversarial probes; it is not physical ownership truth.
    physical_finished = Signal(object)
    runnable_returned = Signal(object)


class _Runnable:
    def __init__(
        self,
        request: _Request,
        bridge: _Bridge,
    ) -> None:
        self.request = request
        self.bridge = bridge

    def run(self) -> None:
        status = TaskStatus.SUCCESS
        value = None
        error: BaseException | None = None
        worker_thread_id = threading.get_ident()
        started_ns = time.perf_counter_ns()
        try:
            if self.request.cancel.is_set():
                raise CancelledError()
            value = self.request.work(self.request.cancel)
            if self.request.cancel.is_set():
                status = TaskStatus.CANCELLED
        except CancelledError as exc:
            status = TaskStatus.CANCELLED
            error = exc
        except Exception as exc:
            status = TaskStatus.ERROR
            error = exc

        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
        if status is TaskStatus.ERROR and error is not None:
            best_effort_exception_log(
                logger,
                logging.ERROR,
                "task_worker_error",
                error,
                **_token_fields(self.request.token),
                elapsed_ms=f"{elapsed_ms:.3f}",
            )

        outcome = TaskOutcome(
            token=self.request.token,
            status=status,
            value=value,
            error=error,
            worker_thread_id=worker_thread_id,
            elapsed_ms=elapsed_ms,
        )

        # Outcome availability precedes physical ownership release. A direct
        # receiver may deliberately hold either this signal or the legacy
        # physical_finished checkpoint to extend the worker tail.
        self.bridge.outcome.emit(outcome)
        best_effort_log(
            logger,
            logging.DEBUG,
            "task_worker_tail_reached",
            **_token_fields(self.request.token),
            status=status.name,
            elapsed_ms=f"{elapsed_ms:.3f}",
        )
        self.bridge.physical_finished.emit(self.request.token)


class _TaskThread(QThread):
    """Qt-owned thread whose finished signal follows _Runnable.run() return."""

    def __init__(
        self,
        request: _Request,
        bridge: _Bridge,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.runnable = _Runnable(request, bridge)

    def run(self) -> None:
        self.runnable.run()


class QtTaskRunner(QObject):
    """Application-owned scheduler with at most two owned Qt worker threads.

    Runner bookkeeping is GUI-thread-only. Worker threads receive only their
    work callable and a cancellation Event, then emit a typed TaskOutcome.
    Physical ownership is released from QThread.finished, after the per-request
    run() method has returned.
    """

    outcome_ready = Signal(object)
    activity_changed = Signal(object)
    physical_idle = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        max_workers: int = 2,
        max_pending: int = 8,
    ) -> None:
        super().__init__(parent)
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if max_pending < 1:
            raise ValueError("max_pending must be positive")

        self.max_workers = max_workers
        self.max_pending = max_pending
        self._bridge = _Bridge(self)
        self._bridge.outcome.connect(self._on_worker_outcome)
        self._bridge.runnable_returned.connect(
            self._on_runnable_returned,
            Qt.ConnectionType.QueuedConnection,
        )

        self._generation: dict[str, int] = {}
        self._logical: dict[str, int] = {}
        self._queued: OrderedDict[str, _Request] = OrderedDict()
        self._active: dict[tuple[str, int], _Request] = {}
        self._threads: dict[tuple[str, int], _TaskThread] = {}
        self._accepting = True
        self._closing = False

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def activity(self) -> RunnerActivity:
        return RunnerActivity(
            logical=len(self._logical),
            physical=len(self._active),
            queued=len(self._queued),
        )

    def current_generation(self, key: str) -> int | None:
        return self._logical.get(key)

    def is_current(self, token: TaskToken) -> bool:
        return self._logical.get(token.key) == token.generation

    def submit(
        self,
        key: str,
        work: TaskWork,
        *,
        application_epoch: int,
        dataset_revision: int,
        selection_revision: int,
        load_ticket: int | None = None,
        exclusive_group: str | None = None,
    ) -> TaskToken:
        if not self._accepting:
            generation = self._generation.get(key, 0) + 1
            best_effort_log(
                logger,
                logging.DEBUG,
                "task_rejected",
                reason="runner_closed",
                key=key,
                generation=generation,
                application_epoch=application_epoch,
                dataset_revision=dataset_revision,
                selection_revision=selection_revision,
            )
            raise TaskRunnerClosed("Task runner is closing.")

        # Replacement invalidates publication immediately. A physically running
        # predecessor may continue until its cooperative cancellation point.
        self.cancel(key)
        generation = self._generation.get(key, 0) + 1
        self._generation[key] = generation
        token = TaskToken(
            key=key,
            generation=generation,
            application_epoch=application_epoch,
            dataset_revision=dataset_revision,
            selection_revision=selection_revision,
            load_ticket=load_ticket,
            exclusive_group=exclusive_group,
        )
        request = _Request(token=token, work=work, cancel=threading.Event())
        self._logical[key] = generation

        if self._can_start(request):
            best_effort_log(
                logger,
                logging.DEBUG,
                "task_admitted",
                admission="started",
                **_token_fields(token),
            )
            self._start(request)
        else:
            if len(self._queued) >= self.max_pending:
                self._logical.pop(key, None)
                best_effort_log(
                    logger,
                    logging.DEBUG,
                    "task_rejected",
                    reason="queue_full",
                    **_token_fields(token),
                    pending=len(self._queued),
                )
                raise TaskQueueFull(f"Task queue is full ({self.max_pending} pending requests).")
            self._queued[key] = request
            best_effort_log(
                logger,
                logging.DEBUG,
                "task_admitted",
                admission="queued",
                **_token_fields(token),
                pending=len(self._queued),
            )

        self._emit_activity()
        self._schedule()
        return token

    def cancel(self, key: str) -> None:
        generation = self._logical.pop(key, None)
        queued = self._queued.pop(key, None)
        active = self._active.get((key, generation)) if generation is not None else None

        request = active or queued
        if request is not None:
            best_effort_log(
                logger,
                logging.DEBUG,
                "task_cancel_requested",
                **_token_fields(request.token),
                physical_active=active is not None,
                queued=queued is not None,
            )

        if queued is not None:
            queued.cancel.set()
        if active is not None:
            active.cancel.set()

        self._emit_activity()

    def cancel_all(self) -> None:
        for key in tuple(self._logical):
            self.cancel(key)

    def begin_closing(self) -> None:
        if self._closing:
            return
        self._accepting = False
        self._closing = True
        self.cancel_all()
        for request in self._queued.values():
            request.cancel.set()
        self._queued.clear()
        self._emit_activity()
        if not self._active:
            self.physical_idle.emit()

    def finalize(self) -> None:
        """Finalize after all per-request worker threads have finished."""
        if self.activity.physical:
            raise RuntimeError("Cannot finalize while physical tasks are active.")
        if self._threads:
            raise RuntimeError("Worker thread ownership remained after physical idle.")
        self._accepting = False
        self._closing = True

    def _can_start(self, request: _Request) -> bool:
        if len(self._active) >= self.max_workers:
            return False
        group = request.token.exclusive_group
        if group is None:
            return True
        return not any(active.token.exclusive_group == group for active in self._active.values())

    def _start(self, request: _Request) -> None:
        key = (request.token.key, request.token.generation)
        self._active[key] = request
        best_effort_log(
            logger,
            logging.DEBUG,
            "task_started",
            **_token_fields(request.token),
            physical=len(self._active),
            queued=len(self._queued),
        )
        thread = _TaskThread(request, self._bridge, self)
        self._threads[key] = thread
        bridge = self._bridge
        token = request.token
        thread.finished.connect(
            lambda bridge=bridge, token=token: bridge.runnable_returned.emit(token),
            Qt.ConnectionType.DirectConnection,
        )
        thread.start()

    def _schedule(self) -> None:
        if self._closing:
            return
        made_progress = True
        while made_progress and len(self._active) < self.max_workers:
            made_progress = False
            for key, request in tuple(self._queued.items()):
                if self._can_start(request):
                    del self._queued[key]
                    self._start(request)
                    made_progress = True
                    break
        self._emit_activity()

    @Slot(object)
    def _on_worker_outcome(self, outcome: TaskOutcome) -> None:
        token = outcome.token
        common_fields = {
            **_token_fields(token),
            "status": outcome.status.name,
            "elapsed_ms": f"{outcome.elapsed_ms:.3f}",
        }
        if outcome.error is None:
            best_effort_log(
                logger,
                logging.DEBUG,
                "task_outcome_received",
                **common_fields,
                exception_class="None",
            )
        else:
            best_effort_exception_log(
                logger,
                logging.DEBUG,
                "task_outcome_received",
                outcome.error,
                **common_fields,
            )

        # Logical publication may complete before the worker run() returns.
        # _active remains authoritative until that QThread emits finished.
        self.outcome_ready.emit(outcome)
        if self._logical.get(token.key) == token.generation:
            self._logical.pop(token.key, None)
        self._emit_activity()

    @Slot(object)
    def _on_runnable_returned(self, token: TaskToken) -> None:
        key = (token.key, token.generation)
        request = self._active.pop(key, None)
        thread = self._threads.pop(key, None)
        if thread is not None:
            thread.deleteLater()
        if request is None:
            return
        best_effort_log(
            logger,
            logging.DEBUG,
            "task_physical_finished",
            **_token_fields(token),
        )
        self._schedule()
        self._emit_activity()
        if not self._active and self._closing:
            best_effort_log(
                logger,
                logging.DEBUG,
                "task_thread_scheduler_physical_idle",
                logical=len(self._logical),
                physical=0,
                queued=len(self._queued),
            )
            self.physical_idle.emit()

    def _emit_activity(self) -> None:
        self.activity_changed.emit(self.activity)
