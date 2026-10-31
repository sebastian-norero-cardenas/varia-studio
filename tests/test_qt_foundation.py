"""F3.A Qt foundation contracts and deterministic concurrency tests."""

from __future__ import annotations

import gc
import io
import logging
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import shiboken6
from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel

from csv_analyzer.data import CancelledError, load_dataset
from csv_analyzer.qt_app.bootstrap import create_application
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.qt_app.tasks import QtTaskRunner, TaskQueueFull, TaskStatus
from csv_analyzer.state import AppState, FilterRule

ROOT = Path(__file__).resolve().parents[1]
APP = QApplication.instance() or QApplication(["varia-f3a-tests"])


def pump_until(predicate, timeout_ms=5000):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(2)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timed_out = {"value": False}

    def check():
        if predicate():
            loop.quit()

    def expire():
        timed_out["value"] = True
        loop.quit()

    poll.timeout.connect(check)
    timeout.timeout.connect(expire)
    poll.start()
    timeout.start(timeout_ms)
    loop.exec()
    poll.stop()
    timeout.stop()
    if timed_out["value"] and not predicate():
        raise AssertionError("Qt condition did not become true before timeout")


class DiagnosticReadError(ValueError):
    def __getattribute__(self, name):
        if name == "__cause__":
            raise RuntimeError("diagnostic-read-failed")
        return super().__getattribute__(name)


class BrokenLoggingHandler(logging.Handler):
    def emit(self, record):
        raise RuntimeError("qa-broken-log-sink")


@contextmanager
def broken_qt_logging():
    target = logging.getLogger("csv_analyzer.qt_app")
    old_handlers = list(target.handlers)
    old_level = target.level
    old_propagate = target.propagate
    target.handlers = [BrokenLoggingHandler()]
    target.setLevel(logging.DEBUG)
    target.propagate = False
    try:
        yield
    finally:
        target.handlers = old_handlers
        target.setLevel(old_level)
        target.propagate = old_propagate


@contextmanager
def captured_qt_logging():
    target = logging.getLogger("csv_analyzer.qt_app")
    old_handlers = list(target.handlers)
    old_level = target.level
    old_propagate = target.propagate
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    target.handlers = [handler]
    target.setLevel(logging.DEBUG)
    target.propagate = False
    try:
        yield stream
    finally:
        target.handlers = old_handlers
        target.setLevel(old_level)
        target.propagate = old_propagate


class RecordingShell(QtShell):
    def __init__(self):
        super().__init__()
        self.errors = []

    def show_error(self, text):
        self.errors.append(text)


class ExplodingPresenterShell(RecordingShell):
    def __init__(self):
        super().__init__()
        self.presenter_calls = 0

    def show_error(self, text):
        self.presenter_calls += 1
        raise RuntimeError("qa-presenter-boom")


class StateContractTests(unittest.TestCase):
    def test_revision_domains_and_snapshot(self):
        state = AppState()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.csv"
            path.write_text("x,y\n1,2\n3,4\n", encoding="utf-8")
            dataset = load_dataset(path)

            initial = state.snapshot()
            self.assertEqual(
                (initial.revision, initial.dataset_revision, initial.selection_revision),
                (0, 0, 0),
            )
            ticket = state.begin_load()
            self.assertTrue(state.commit_dataset(ticket, dataset))
            self.assertEqual(
                (state.revision, state.dataset_revision, state.selection_revision),
                (1, 1, 1),
            )

            rules = (FilterRule("x", lower=2),)
            self.assertTrue(state.commit_filters(state.revision, rules, np.array([False, True])))
            self.assertEqual(
                (state.revision, state.dataset_revision, state.selection_revision),
                (2, 1, 2),
            )
            before = state.snapshot()
            state.cancel_load()
            self.assertEqual(
                (
                    state.revision,
                    state.dataset_revision,
                    state.selection_revision,
                    state.dataset,
                    state.mask,
                ),
                (
                    before.revision,
                    before.dataset_revision,
                    before.selection_revision,
                    before.dataset,
                    before.mask,
                ),
            )


class RunnerContractTests(unittest.TestCase):
    def make_runner(self, **kwargs):
        runner = QtTaskRunner(**kwargs)
        self.addCleanup(self._cleanup_runner, runner)
        return runner

    def _cleanup_runner(self, runner):
        if not runner.closing:
            runner.begin_closing()
        if runner.activity.physical:
            pump_until(lambda: runner.activity.physical == 0)
        runner.finalize()

    def test_success_and_error_outcomes(self):
        runner = self.make_runner()
        outcomes = []
        runner.outcome_ready.connect(outcomes.append)

        runner.submit(
            "ok",
            lambda cancel: "value",
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        pump_until(lambda: len(outcomes) == 1)
        self.assertEqual(outcomes[0].status, TaskStatus.SUCCESS)
        self.assertEqual(outcomes[0].value, "value")

        def fail(cancel):
            raise ValueError("worker boom")

        runner.submit(
            "error",
            fail,
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        pump_until(lambda: len(outcomes) == 2)
        self.assertEqual(outcomes[1].status, TaskStatus.ERROR)
        self.assertIsInstance(outcomes[1].error, ValueError)

    def test_task_observability_records_identity_admission_and_elapsed(self):
        runner = self.make_runner()
        outcomes = []
        runner.outcome_ready.connect(outcomes.append)

        with self.assertLogs("csv_analyzer.qt_app.tasks", level="DEBUG") as captured:
            runner.submit(
                "observed",
                lambda cancel: "value",
                application_epoch=7,
                dataset_revision=11,
                selection_revision=13,
            )
            pump_until(lambda: len(outcomes) == 1)
            pump_until(lambda: runner.activity.physical == 0)

        log_text = "\n".join(captured.output)
        identity = (
            "key=observed generation=1 application_epoch=7 "
            "dataset_revision=11 selection_revision=13"
        )
        self.assertIn("event=task_admitted admission=started " + identity, log_text)
        self.assertIn("event=task_started " + identity, log_text)
        self.assertIn("event=task_physical_finished " + identity, log_text)
        self.assertIn("event=task_outcome_received " + identity, log_text)
        self.assertIn("elapsed_ms=", log_text)

    def test_broken_logging_cannot_change_success_error_or_cancel_semantics(self):
        runner = self.make_runner(max_workers=1)
        outcomes = []
        runner.outcome_ready.connect(outcomes.append)

        with broken_qt_logging():
            runner.submit(
                "success-broken-log",
                lambda cancel: "ok",
                application_epoch=1,
                dataset_revision=2,
                selection_revision=3,
            )
            pump_until(lambda: runner.activity.physical == 0)
        self.assertEqual(outcomes[-1].status, TaskStatus.SUCCESS)
        self.assertEqual(outcomes[-1].value, "ok")
        self.assertEqual(runner._active, {})
        self.assertEqual(runner.activity.logical, 0)

        def fail(cancel):
            raise ValueError("private-worker-message")

        with broken_qt_logging():
            runner.submit(
                "error-broken-log",
                fail,
                application_epoch=1,
                dataset_revision=2,
                selection_revision=3,
            )
            pump_until(lambda: runner.activity.physical == 0)
        self.assertEqual(outcomes[-1].status, TaskStatus.ERROR)
        self.assertIsInstance(outcomes[-1].error, ValueError)
        self.assertEqual(str(outcomes[-1].error), "private-worker-message")
        self.assertEqual(runner._active, {})
        self.assertEqual(runner.activity.logical, 0)

        entered = threading.Event()
        release = threading.Event()

        def cancellable(cancel):
            entered.set()
            release.wait(timeout=5)
            return "late"

        with broken_qt_logging():
            runner.submit(
                "cancel-broken-log",
                cancellable,
                application_epoch=1,
                dataset_revision=2,
                selection_revision=3,
            )
            self.assertTrue(entered.wait(timeout=2))
            runner.cancel("cancel-broken-log")
            release.set()
            pump_until(lambda: runner.activity.physical == 0)
        self.assertEqual(outcomes[-1].status, TaskStatus.CANCELLED)
        self.assertEqual(runner._active, {})
        self.assertEqual(runner.activity.logical, 0)
        self.assertEqual(runner.activity.physical, 0)

    def test_diagnostic_metadata_failure_preserves_outcome_cleanup_and_following_work(self):
        runner = self.make_runner(max_workers=1)
        outcomes = []
        runner.outcome_ready.connect(outcomes.append)
        original = DiagnosticReadError("ORIGINAL_FUNCTIONAL_MESSAGE")

        def fail(cancel):
            raise original

        runner.submit(
            "diagnostic-failure",
            fail,
            application_epoch=1,
            dataset_revision=2,
            selection_revision=3,
        )
        pump_until(lambda: len(outcomes) == 1)
        pump_until(lambda: runner.activity.physical == 0)

        self.assertEqual(outcomes[0].status, TaskStatus.ERROR)
        self.assertIs(outcomes[0].error, original)
        self.assertEqual(str(outcomes[0].error), "ORIGINAL_FUNCTIONAL_MESSAGE")
        self.assertEqual(runner._active, {})
        self.assertEqual(runner.activity.logical, 0)
        self.assertEqual(runner.activity.physical, 0)

        seen = []
        runner.submit(
            "after-diagnostic-failure",
            lambda cancel: "next",
            application_epoch=1,
            dataset_revision=2,
            selection_revision=3,
        )
        runner.outcome_ready.connect(
            lambda outcome: seen.append(outcome.value)
            if outcome.token.key == "after-diagnostic-failure"
            else None
        )
        pump_until(lambda: seen == ["next"])
        pump_until(lambda: runner.activity.physical == 0)

    def test_safe_formatter_omits_private_exception_payloads(self):
        runner = self.make_runner()
        outcomes = []
        runner.outcome_ready.connect(outcomes.append)

        private_markers = (
            "/home/private-user/secret/input.csv",
            "input.csv",
            "CELL_SECRET_9381",
            "HEADER_PRIVATE_X",
            "EXCEPTION_PRIVATE_MESSAGE",
            "CAUSE_PRIVATE_MESSAGE",
        )

        def fail(cancel):
            try:
                raise KeyError("CAUSE_PRIVATE_MESSAGE CELL_SECRET_9381")
            except KeyError as cause:
                raise ValueError(
                    "/home/private-user/secret/input.csv input.csv "
                    "HEADER_PRIVATE_X EXCEPTION_PRIVATE_MESSAGE"
                ) from cause

        with captured_qt_logging() as stream:
            runner.submit(
                "privacy-check",
                fail,
                application_epoch=4,
                dataset_revision=5,
                selection_revision=6,
            )
            pump_until(lambda: runner.activity.physical == 0)

        formatted = stream.getvalue()
        for marker in private_markers:
            self.assertNotIn(marker, formatted)
        self.assertIn("event=task_worker_error", formatted)
        self.assertIn("exception_class=ValueError", formatted)
        self.assertIn("cause_class=KeyError", formatted)
        self.assertIn("key=privacy-check", formatted)
        self.assertIn("generation=1", formatted)
        self.assertEqual(outcomes[-1].status, TaskStatus.ERROR)
        self.assertIn("EXCEPTION_PRIVATE_MESSAGE", str(outcomes[-1].error))

    def test_exclusive_load_waits_for_post_outcome_worker_tail(self):
        runner = self.make_runner(max_workers=2)
        outcomes = []
        runner.outcome_ready.connect(outcomes.append)
        tail_entered = threading.Event()
        tail_release = threading.Event()
        second_started = threading.Event()

        def hold_first_tail(outcome):
            if outcome.token.key == "load" and outcome.token.generation == 1:
                tail_entered.set()
                tail_release.wait(timeout=5)

        runner._bridge.outcome.connect(
            hold_first_tail,
            Qt.ConnectionType.DirectConnection,
        )

        first = runner.submit(
            "load",
            lambda cancel: "first",
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
            exclusive_group="load",
        )
        self.assertEqual(first.generation, 1)
        self.assertTrue(tail_entered.wait(timeout=2))
        pump_until(lambda: len(outcomes) == 1)

        def second_work(cancel):
            second_started.set()
            return "second"

        second = runner.submit(
            "load",
            second_work,
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
            exclusive_group="load",
        )
        self.assertEqual(second.generation, 2)
        APP.processEvents()
        self.assertFalse(second_started.is_set())
        self.assertEqual(runner.activity.queued, 1)
        self.assertEqual(runner.activity.physical, 1)

        tail_release.set()
        pump_until(lambda: second_started.is_set())
        pump_until(lambda: runner.activity.physical == 0)
        self.assertEqual([item.token.generation for item in outcomes], [1, 2])
        self.assertEqual([item.value for item in outcomes], ["first", "second"])

    def test_exclusive_load_waits_for_post_physical_finished_tail_and_preserves_unrelated_capacity(
        self,
    ):
        runner = self.make_runner(max_workers=2)
        tail_entered = threading.Event()
        tail_release = threading.Event()
        second_started = threading.Event()
        unrelated_entered = threading.Event()
        unrelated_release = threading.Event()

        def hold_first_physical_tail(token):
            if token.key == "load" and token.generation == 1:
                tail_entered.set()
                tail_release.wait(timeout=5)

        runner._bridge.physical_finished.connect(
            hold_first_physical_tail,
            Qt.ConnectionType.DirectConnection,
        )

        runner.submit(
            "load",
            lambda cancel: "first",
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
            exclusive_group="load",
        )
        self.assertTrue(tail_entered.wait(timeout=2))

        # R3 processed physical_finished here and released scheduler ownership
        # even though the emitting runnable was still blocked in this tail.
        APP.processEvents()
        APP.processEvents()

        runner.submit(
            "load",
            lambda cancel: second_started.set() or "second",
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
            exclusive_group="load",
        )

        def unrelated(cancel):
            unrelated_entered.set()
            unrelated_release.wait(timeout=5)
            return "unrelated"

        runner.submit(
            "unrelated",
            unrelated,
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )

        unrelated_before_release = unrelated_entered.wait(timeout=0.5)
        first_still_owned = ("load", 1) in runner._active
        second_waited = not second_started.is_set()

        unrelated_release.set()
        tail_release.set()
        pump_until(lambda: runner.activity.physical == 0)

        self.assertTrue(first_still_owned)
        self.assertTrue(second_waited)
        self.assertTrue(unrelated_before_release)
        self.assertTrue(second_started.is_set())
        self.assertEqual(runner._active, {})
        self.assertEqual(runner.activity.physical, 0)

    def test_error_and_cancel_keep_physical_ownership_through_physical_finished_tail(self):
        for mode in ("error", "cancel"):
            with self.subTest(mode=mode):
                runner = self.make_runner(max_workers=1)
                outcomes = []
                runner.outcome_ready.connect(outcomes.append)
                entered = threading.Event()
                work_release = threading.Event()
                tail_entered = threading.Event()
                tail_release = threading.Event()

                def hold_tail(token):
                    if token.key == mode:
                        tail_entered.set()
                        tail_release.wait(timeout=5)

                runner._bridge.physical_finished.connect(
                    hold_tail,
                    Qt.ConnectionType.DirectConnection,
                )

                if mode == "error":

                    def work(cancel):
                        raise ValueError("expected")

                else:

                    def work(cancel):
                        entered.set()
                        work_release.wait(timeout=5)
                        return "late"

                runner.submit(
                    mode,
                    work,
                    application_epoch=0,
                    dataset_revision=0,
                    selection_revision=0,
                )
                if mode == "cancel":
                    self.assertTrue(entered.wait(timeout=2))
                    runner.cancel(mode)
                    work_release.set()

                self.assertTrue(tail_entered.wait(timeout=2))
                APP.processEvents()
                APP.processEvents()
                physical_while_tail = runner.activity.physical

                tail_release.set()
                pump_until(lambda: runner.activity.physical == 0)
                pump_until(lambda: len(outcomes) == 1)

                self.assertEqual(physical_while_tail, 1)
                expected = TaskStatus.ERROR if mode == "error" else TaskStatus.CANCELLED
                self.assertEqual(outcomes[0].status, expected)

    def test_closing_waits_for_post_outcome_worker_tail(self):
        runner = self.make_runner(max_workers=2)
        tail_entered = threading.Event()
        tail_release = threading.Event()
        idle = []
        runner.physical_idle.connect(lambda: idle.append(True))

        def hold_tail(outcome):
            tail_entered.set()
            tail_release.wait(timeout=5)

        runner._bridge.outcome.connect(
            hold_tail,
            Qt.ConnectionType.DirectConnection,
        )
        runner.submit(
            "tail-close",
            lambda cancel: "done",
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        self.assertTrue(tail_entered.wait(timeout=2))
        APP.processEvents()
        runner.begin_closing()
        self.assertEqual(runner.activity.logical, 0)
        self.assertEqual(runner.activity.physical, 1)
        self.assertEqual(idle, [])

        tail_release.set()
        pump_until(lambda: runner.activity.physical == 0)
        pump_until(lambda: idle == [True])

    def test_cancel_is_logical_before_physical_completion(self):
        runner = self.make_runner(max_workers=1)
        entered = threading.Event()
        release = threading.Event()
        outcomes = []
        runner.outcome_ready.connect(outcomes.append)

        def work(cancel):
            entered.set()
            release.wait(timeout=5)
            return "late"

        runner.submit(
            "slow",
            work,
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        self.assertTrue(entered.wait(timeout=2))
        with self.assertLogs("csv_analyzer.qt_app.tasks", level="DEBUG") as captured:
            runner.cancel("slow")
        self.assertIn("event=task_cancel_requested", "\n".join(captured.output))
        self.assertIn("physical_active=True", "\n".join(captured.output))
        self.assertEqual(runner.activity.logical, 0)
        self.assertEqual(runner.activity.physical, 1)
        release.set()
        pump_until(lambda: runner.activity.physical == 0)
        self.assertEqual(outcomes[-1].status, TaskStatus.CANCELLED)

    def test_burst_replacement_and_bounded_pending_queue(self):
        runner = self.make_runner(max_workers=1, max_pending=8)
        entered = threading.Event()
        release = threading.Event()

        def hold(cancel):
            entered.set()
            release.wait(timeout=5)
            return None

        runner.submit(
            "active",
            hold,
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        self.assertTrue(entered.wait(timeout=2))

        for index in range(8):
            runner.submit(
                f"q{index}",
                lambda cancel, value=index: value,
                application_epoch=0,
                dataset_revision=0,
                selection_revision=0,
            )
        self.assertEqual(runner.activity.queued, 8)
        with self.assertLogs("csv_analyzer.qt_app.tasks", level="DEBUG") as captured:
            with self.assertRaises(TaskQueueFull):
                runner.submit(
                    "overflow",
                    lambda cancel: None,
                    application_epoch=0,
                    dataset_revision=0,
                    selection_revision=0,
                )
        rejection_log = "\n".join(captured.output)
        self.assertIn("event=task_rejected reason=queue_full", rejection_log)
        self.assertIn("key=overflow", rejection_log)

        old_generation = runner.current_generation("q0")
        replacement = runner.submit(
            "q0",
            lambda cancel: "replacement",
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        self.assertGreater(replacement.generation, old_generation)
        self.assertEqual(runner.activity.queued, 8)

        runner.cancel_all()
        release.set()
        pump_until(lambda: runner.activity.physical == 0)


class CoordinatorContractTests(unittest.TestCase):
    def make(self, **kwargs):
        shell = RecordingShell()
        coordinator = QtCoordinator(shell, **kwargs)
        coordinator.start()
        self.addCleanup(self.cleanup_coordinator, coordinator, shell)
        return coordinator, shell

    def cleanup_coordinator(self, coordinator, shell):
        if coordinator.lifecycle not in (Lifecycle.CLOSING, Lifecycle.CLOSED):
            coordinator.begin_close()
        if coordinator.lifecycle is not Lifecycle.CLOSED:
            pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        shell.deleteLater()
        coordinator.deleteLater()
        APP.processEvents()

    def write(self, directory, name, text):
        path = Path(directory) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_bootstrap_headless_and_clean_close(self):
        app, coordinator, shell = create_application()
        self.assertIs(app, APP)
        self.assertEqual(coordinator.lifecycle, Lifecycle.STARTING)
        coordinator.start()
        self.assertEqual(coordinator.lifecycle, Lifecycle.RUNNING)
        self.assertEqual(shell.dataset_label.text(), "No dataset loaded")

        shell.show()
        APP.processEvents()
        shell.close()
        pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED and not shell.isVisible())

        shell.deleteLater()
        coordinator.deleteLater()
        APP.processEvents()

    def test_callback_and_dataset_commit_run_on_gui_thread(self):
        coordinator, shell = self.make()
        gui_thread = threading.get_ident()
        callback_threads = []
        state_threads = []
        coordinator.state.subscribe(lambda reason: state_threads.append(threading.get_ident()))

        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "a.csv", "x,y\n1,2\n3,4\n")
            token = coordinator.load_path(path)
            self.assertIsNotNone(token)
            pump_until(lambda: coordinator.state.dataset is not None)
            self.assertEqual(state_threads, [gui_thread])

        coordinator.submit_task(
            "thread-check",
            lambda cancel: threading.get_ident(),
            success=lambda worker_thread: callback_threads.append(
                (worker_thread, threading.get_ident())
            ),
        )
        pump_until(lambda: bool(callback_threads))
        self.assertNotEqual(callback_threads[0][0], gui_thread)
        self.assertEqual(callback_threads[0][1], gui_thread)

    def test_worker_error_and_callback_exception_do_not_break_following_work(self):
        coordinator, shell = self.make()
        seen = []

        def fail(cancel):
            raise ValueError("expected worker error")

        coordinator.submit_task("failure", fail)
        pump_until(lambda: bool(shell.errors))
        self.assertIn("expected worker error", shell.errors[-1])

        def broken(value):
            raise RuntimeError("callback exploded")

        coordinator.submit_task("callback", lambda cancel: 1, success=broken)
        pump_until(lambda: any("callback exploded" in item for item in shell.errors))

        coordinator.submit_task("after", lambda cancel: 2, success=seen.append)
        pump_until(lambda: seen == [2])

    def test_presenter_failure_is_logged_and_does_not_break_loop(self):
        shell = ExplodingPresenterShell()
        coordinator = QtCoordinator(shell)
        coordinator.start()
        shell.show()
        self.addCleanup(self.cleanup_coordinator, coordinator, shell)

        def fail(cancel):
            raise ValueError("qa-worker-boom")

        with self.assertLogs("csv_analyzer.qt_app", level="ERROR") as captured:
            coordinator.submit_task("presenter-failure", fail)
            pump_until(lambda: shell.presenter_calls == 1)

        messages = "\n".join(record.getMessage() for record in captured.records)
        self.assertIn("event=task_worker_error", messages)
        self.assertIn("exception_class=ValueError", messages)
        self.assertIn("event=error_presenter_failed", messages)
        self.assertIn("presenter_exception_class=RuntimeError", messages)
        self.assertTrue(all(record.exc_info is None for record in captured.records))
        self.assertNotIn("qa-presenter-boom", messages)
        self.assertEqual(shell.presenter_calls, 1)

        seen = []
        coordinator.submit_task("after-presenter", lambda cancel: 2, success=seen.append)
        pump_until(lambda: seen == [2])

        shell.close()
        pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED and not shell.isVisible())

    def test_broken_gui_logging_does_not_break_stale_callback_or_close(self):
        coordinator, shell = self.make(max_workers=2)
        entered = threading.Event()
        release = threading.Event()
        seen = []

        def old(cancel):
            entered.set()
            release.wait(timeout=5)
            return "old"

        def broken_callback(value):
            raise RuntimeError("CALLBACK_PRIVATE_MARKER")

        with broken_qt_logging():
            coordinator.submit_task("same-broken-log", old, success=seen.append)
            self.assertTrue(entered.wait(timeout=2))
            coordinator.submit_task(
                "same-broken-log",
                lambda cancel: "new",
                success=seen.append,
            )
            pump_until(lambda: seen == ["new"])
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            self.assertEqual(seen, ["new"])

            coordinator.submit_task(
                "callback-broken-log",
                lambda cancel: 1,
                success=broken_callback,
            )
            pump_until(lambda: any("CALLBACK_PRIVATE_MARKER" in item for item in shell.errors))

            shell.show()
            shell.close()
            pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED and not shell.isVisible())

    def test_bound_qobject_callback_is_inferred_and_discarded_if_destroyed(self):
        coordinator, shell = self.make(max_workers=1)
        label = QLabel("before")
        entered = threading.Event()
        release = threading.Event()

        def work(cancel):
            entered.set()
            release.wait(timeout=5)
            return "after"

        coordinator.submit_task(
            "bound-destroyed",
            work,
            success=label.setText,
        )
        self.assertTrue(entered.wait(timeout=2))
        shiboken6.delete(label)
        self.assertFalse(shiboken6.isValid(label))
        release.set()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(shell.errors, [])

    def test_bound_qobject_live_and_plain_python_callbacks_still_work(self):
        coordinator, shell = self.make()
        label = QLabel("before")
        seen = []

        coordinator.submit_task(
            "bound-live",
            lambda cancel: "after",
            success=label.setText,
        )
        pump_until(lambda: label.text() == "after")

        coordinator.submit_task(
            "plain-python",
            lambda cancel: 7,
            success=seen.append,
        )
        pump_until(lambda: seen == [7])

    def test_explicit_receiver_adds_gate_without_replacing_bound_qobject_owner(self):
        coordinator, shell = self.make(max_workers=1)

        for index, (owner_alive, receiver_alive) in enumerate(
            ((True, True), (False, True), (True, False), (False, False))
        ):
            with self.subTest(owner_alive=owner_alive, receiver_alive=receiver_alive):
                owner = QLabel("before")
                receiver = QObject()
                entered = threading.Event()
                release = threading.Event()

                def work(cancel):
                    entered.set()
                    release.wait(timeout=5)
                    return "after"

                coordinator.submit_task(
                    f"owner-receiver-{index}",
                    work,
                    success=owner.setText,
                    receiver=receiver,
                )
                self.assertTrue(entered.wait(timeout=2))

                if not owner_alive:
                    shiboken6.delete(owner)
                    self.assertFalse(shiboken6.isValid(owner))
                if not receiver_alive:
                    shiboken6.delete(receiver)
                    self.assertFalse(shiboken6.isValid(receiver))

                before_errors = list(shell.errors)
                release.set()
                pump_until(lambda: coordinator.runner.activity.physical == 0)

                self.assertEqual(shell.errors, before_errors)
                if owner_alive and receiver_alive:
                    self.assertEqual(owner.text(), "after")
                elif owner_alive:
                    self.assertEqual(owner.text(), "before")

    def test_explicit_receiver_keeps_selected_success_and_failure_owner_independent(self):
        coordinator, shell = self.make()
        explicit_receiver = QObject()

        class Owner(QObject):
            def __init__(self):
                super().__init__()
                self.values = []

            def handle(self, value):
                self.values.append(value)

        success_owner = Owner()
        dead_failure_owner = Owner()
        coordinator.submit_task(
            "selected-success-owner",
            lambda cancel: "ok",
            success=success_owner.handle,
            failure=dead_failure_owner.handle,
            receiver=explicit_receiver,
        )
        shiboken6.delete(dead_failure_owner)
        pump_until(lambda: success_owner.values == ["ok"])

        dead_success_owner = Owner()
        failure_owner = Owner()

        def fail(cancel):
            raise ValueError("expected")

        coordinator.submit_task(
            "selected-failure-owner",
            fail,
            success=dead_success_owner.handle,
            failure=failure_owner.handle,
            receiver=explicit_receiver,
        )
        shiboken6.delete(dead_success_owner)
        pump_until(lambda: len(failure_owner.values) == 1)
        self.assertIsInstance(failure_owner.values[0], ValueError)
        self.assertEqual(shell.errors, [])

    def test_bound_python_method_without_qobject_still_works(self):
        coordinator, shell = self.make()

        class PythonOwner:
            def __init__(self):
                self.values = []

            def handle(self, value):
                self.values.append(value)

        owner = PythonOwner()
        coordinator.submit_task(
            "python-bound-method",
            lambda cancel: 9,
            success=owner.handle,
        )
        pump_until(lambda: owner.values == [9])
        self.assertEqual(shell.errors, [])

    def test_success_and_failure_qobjects_have_independent_inferred_receivers(self):
        coordinator, shell = self.make()
        success_label = QLabel("before")
        failure_label = QLabel("unused")

        coordinator.submit_task(
            "independent-receivers",
            lambda cancel: "success",
            success=success_label.setText,
            failure=failure_label.setText,
        )
        shiboken6.delete(failure_label)
        pump_until(lambda: success_label.text() == "success")

    def test_cancel_and_late_result_never_publish(self):
        coordinator, shell = self.make(max_workers=1)
        entered = threading.Event()
        release = threading.Event()
        seen = []

        def slow(cancel):
            entered.set()
            release.wait(timeout=5)
            return "late"

        coordinator.submit_task("slow", slow, success=seen.append)
        self.assertTrue(entered.wait(timeout=2))
        coordinator.cancel_all()
        self.assertEqual(coordinator.runner.activity.logical, 0)
        self.assertEqual(coordinator.runner.activity.physical, 1)
        release.set()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(seen, [])

    def test_old_generation_is_discarded(self):
        coordinator, shell = self.make(max_workers=2)
        entered = threading.Event()
        release = threading.Event()
        seen = []

        def old(cancel):
            entered.set()
            release.wait(timeout=5)
            return "old"

        coordinator.submit_task("same", old, success=seen.append)
        self.assertTrue(entered.wait(timeout=2))
        coordinator.submit_task("same", lambda cancel: "new", success=seen.append)
        pump_until(lambda: "new" in seen)
        with self.assertLogs("csv_analyzer.qt_app.coordinator", level="DEBUG") as captured:
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(seen, ["new"])
        stale_log = "\n".join(captured.output)
        self.assertIn("event=task_outcome_discarded", stale_log)
        self.assertIn("key=same", stale_log)

    def test_dataset_change_invalidates_task_publication(self):
        coordinator, shell = self.make()
        entered = threading.Event()
        release = threading.Event()
        seen = []

        with tempfile.TemporaryDirectory() as directory:
            a = load_dataset(self.write(directory, "a.csv", "x\n1\n"))
            b = load_dataset(self.write(directory, "b.csv", "x\n2\n"))
            self.assertTrue(coordinator.state.commit_dataset(coordinator.state.begin_load(), a))

            def derived(cancel):
                entered.set()
                release.wait(timeout=5)
                return "stale"

            coordinator.submit_task("derived", derived, success=seen.append)
            self.assertTrue(entered.wait(timeout=2))
            self.assertTrue(coordinator.state.commit_dataset(coordinator.state.begin_load(), b))
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            self.assertEqual(seen, [])

    def test_failed_b_from_filtered_a_preserves_state_and_revisions(self):
        coordinator, shell = self.make()
        with tempfile.TemporaryDirectory() as directory:
            a_path = self.write(directory, "a.csv", "x,y\n1,2\n3,4\n")
            bad_path = self.write(directory, "bad.csv", "x,y\n1,2,3\n")
            a = load_dataset(a_path)
            self.assertTrue(coordinator.state.commit_dataset(coordinator.state.begin_load(), a))
            self.assertTrue(
                coordinator.state.commit_filters(
                    coordinator.state.revision,
                    (FilterRule("x", lower=2),),
                    np.array([False, True]),
                )
            )
            before = coordinator.state.snapshot()
            coordinator.load_path(bad_path)
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            after = coordinator.state.snapshot()
            self.assertIs(after.dataset, before.dataset)
            self.assertIs(after.mask, before.mask)
            self.assertEqual(after.filters, before.filters)
            self.assertEqual(
                (after.revision, after.dataset_revision, after.selection_revision),
                (before.revision, before.dataset_revision, before.selection_revision),
            )
            self.assertTrue(shell.errors)

    def test_load_replacement_waits_for_physical_predecessor(self):
        entered_b = threading.Event()
        release_b = threading.Event()
        started_c = threading.Event()

        def loader(path, options, *, cancel=None):
            path = Path(path)
            if path.name == "b.csv":
                entered_b.set()
                release_b.wait(timeout=5)
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
            if path.name == "c.csv":
                started_c.set()
            return load_dataset(path, options, cancel=cancel)

        coordinator, shell = self.make(loader=loader)
        with tempfile.TemporaryDirectory() as directory:
            a = self.write(directory, "a.csv", "x\n1\n")
            b = self.write(directory, "b.csv", "x\n2\n")
            c = self.write(directory, "c.csv", "x\n3\n")

            coordinator.load_path(a)
            pump_until(
                lambda: (
                    coordinator.state.dataset is not None
                    and coordinator.state.dataset.path.name == "a.csv"
                )
            )
            coordinator.load_path(b)
            pump_until(lambda: entered_b.is_set(), timeout_ms=2000)
            coordinator.load_path(c)
            self.assertFalse(started_c.is_set())
            self.assertEqual(coordinator.runner.activity.physical, 1)
            release_b.set()
            pump_until(lambda: started_c.is_set())
            pump_until(
                lambda: (
                    coordinator.state.dataset is not None
                    and coordinator.state.dataset.path.name == "c.csv"
                )
            )
            self.assertEqual(coordinator.state.dataset.frame.iloc[0, 0], 3)

    def test_destroyed_receiver_discards_result(self):
        coordinator, shell = self.make(max_workers=1)
        entered = threading.Event()
        release = threading.Event()
        seen = []

        class Receiver(QObject):
            def handle(self, value):
                seen.append(value)

        receiver = Receiver()

        def work(cancel):
            entered.set()
            release.wait(timeout=5)
            return 1

        coordinator.submit_task(
            "receiver",
            work,
            success=receiver.handle,
            receiver=receiver,
        )
        self.assertTrue(entered.wait(timeout=2))
        del receiver
        gc.collect()
        release.set()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(seen, [])

    def test_close_with_active_temporarily_noncancelable_worker(self):
        coordinator, shell = self.make(max_workers=1)
        entered = threading.Event()
        release = threading.Event()
        published = []

        def noncancelable(cancel):
            entered.set()
            release.wait(timeout=5)
            return "finished"

        coordinator.submit_task("physical", noncancelable, success=published.append)
        self.assertTrue(entered.wait(timeout=2))
        coordinator.begin_close()
        self.assertEqual(coordinator.lifecycle, Lifecycle.CLOSING)
        self.assertEqual(coordinator.runner.activity.logical, 0)
        self.assertEqual(coordinator.runner.activity.physical, 1)
        self.assertIsNone(
            coordinator.submit_task("rejected", lambda cancel: None, success=published.append)
        )
        release.set()
        pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        self.assertEqual(published, [])


class ShellCloseProbeTests(unittest.TestCase):
    def run_probe(self, action, worker, *, broken_logging=False, timeout=10):
        command = [
            sys.executable,
            str(ROOT / "tools" / "f3a_qt_close_probe.py"),
            "--action",
            action,
            "--worker",
            worker,
        ]
        if broken_logging:
            command.append("--broken-logging")
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("lifecycle=CLOSED", result.stdout)
        self.assertIn("window_closed=true", result.stdout)
        self.assertIn("late_publications=0", result.stdout)
        self.assertIn("event_loop_exit_code=0", result.stdout)
        self.assertIn(
            f"broken_logging={str(broken_logging).lower()}",
            result.stdout,
        )

    def test_diagnostic_metadata_failure_cannot_block_real_quit_or_window_close(self):
        for action in ("quit", "window"):
            with self.subTest(action=action):
                self.run_probe(action, "diagnostic", timeout=3)

    def test_broken_logging_cannot_block_real_quit_or_window_close(self):
        for action in ("quit", "window"):
            for worker in ("none", "noncancelable"):
                with self.subTest(action=action, worker=worker):
                    self.run_probe(action, worker, broken_logging=True)

    def test_quit_button_exits_real_event_loop(self):
        for worker in ("none", "cooperative", "noncancelable"):
            with self.subTest(worker=worker):
                self.run_probe("quit", worker)

    def test_window_close_exits_real_event_loop(self):
        for worker in ("none", "cooperative", "noncancelable"):
            with self.subTest(worker=worker):
                self.run_probe("window", worker)


class SeparationGateTests(unittest.TestCase):
    def run_guarded_import(self, forbidden, statement):
        code = f"""
import builtins
real_import = builtins.__import__
forbidden = {forbidden!r}
def guarded(name, *args, **kwargs):
    if any(name == item or name.startswith(item + '.') for item in forbidden):
        raise RuntimeError('forbidden import: ' + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
{statement}
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_core_imports_without_pyside6(self):
        self.run_guarded_import(
            ["PySide6", "shiboken6"],
            "import csv_analyzer.data, csv_analyzer.analysis, csv_analyzer.state, "
            "csv_analyzer.plots, csv_analyzer.exports",
        )

    def test_metadata_keeps_pyside6_in_qt_extra_only(self):
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib

        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        base_dependencies = metadata["project"]["dependencies"]
        extras = metadata["project"]["optional-dependencies"]

        self.assertFalse(any(item.lower().startswith("pyside6") for item in base_dependencies))
        self.assertTrue(any(item.startswith("PySide6>=6.11.3,<6.13") for item in extras["qt"]))

    def test_qt_package_and_bootstrap_import_without_pyside6(self):
        self.run_guarded_import(
            ["PySide6", "shiboken6"],
            "import csv_analyzer.qt_app, csv_analyzer.qt_app.bootstrap",
        )

    def test_missing_qt_runtime_fails_cleanly(self):
        code = """
import builtins
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'PySide6' or name.startswith('PySide6.') or name == 'shiboken6':
        raise ModuleNotFoundError("No module named 'PySide6'", name='PySide6')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
from csv_analyzer.qt_app import main
raise SystemExit(main([]))
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Install Varia Studio with the 'qt' extra.", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_qt_does_not_import_tk_and_ctk_does_not_import_qt(self):
        self.run_guarded_import(
            ["tkinter", "customtkinter"],
            "import csv_analyzer.qt_app.bootstrap",
        )
        self.run_guarded_import(
            ["PySide6", "shiboken6"],
            "import csv_analyzer.app",
        )

    def test_packaging_keeps_both_ctk_entrypoints_and_adds_qt(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('varia-studio = "csv_analyzer.app:main"', text)
        self.assertIn('csv-analyzer = "csv_analyzer.app:main"', text)
        self.assertIn('varia-studio-qt = "csv_analyzer.qt_app:main"', text)


if __name__ == "__main__":
    unittest.main()
