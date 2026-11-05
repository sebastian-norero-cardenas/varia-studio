"""F3.D QtAgg scatter controller and figure lifecycle tests."""

from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from csv_analyzer.data import CancelledError, DataError, Dataset, describe_columns
from csv_analyzer.plots import prepare_scatter as real_prepare_scatter
from csv_analyzer.plots import render_scatter as real_render_scatter
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.plot_panel import FigureToolbar
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule

APP = QApplication.instance() or QApplication(["varia-f3d-tests"])


def pump_until(predicate, timeout_ms=6000):
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


def dataset(frame: pd.DataFrame, name="dataset.csv") -> Dataset:
    return Dataset(frame=frame, path=Path(name), columns=describe_columns(frame))


class RecordingShell(QtShell):
    def __init__(self):
        super().__init__()
        self.errors = []

    def show_error(self, text: str) -> None:
        self.errors.append(text)


class QtScatterTests(unittest.TestCase):
    def make(self, *, loader=None):
        shell = RecordingShell()
        kwargs = {} if loader is None else {"loader": loader}
        coordinator = QtCoordinator(shell, **kwargs)
        coordinator.start()
        self.addCleanup(self.cleanup, coordinator, shell)
        return coordinator, shell

    def cleanup(self, coordinator, shell):
        if coordinator.lifecycle not in (Lifecycle.CLOSING, Lifecycle.CLOSED):
            coordinator.begin_close()
        if coordinator.lifecycle is not Lifecycle.CLOSED:
            pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        shell.deleteLater()
        coordinator.deleteLater()
        APP.processEvents()

    def commit(self, coordinator, value):
        ticket = coordinator.state.begin_load()
        self.assertTrue(coordinator.state.commit_dataset(ticket, value))
        APP.processEvents()

    def plot_and_wait(self, coordinator):
        coordinator.scatter_controller.request_plot()
        pump_until(lambda: coordinator.runner.activity.physical == 0)

    def test_ui_states_and_toolbar_without_save(self):
        coordinator, shell = self.make()
        view = shell.scatter_view
        self.assertFalse(view.plot_button.isEnabled())
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2]})))
        self.assertFalse(view.plot_button.isEnabled())
        self.assertIn("at least two", view.local_status.text())
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2], "y": [3, 4]})))
        self.assertTrue(view.plot_button.isEnabled())
        self.plot_and_wait(coordinator)
        self.assertTrue(view.panel.has_figure)
        self.assertIsInstance(view.panel.canvas, FigureCanvasQTAgg)
        self.assertIsInstance(view.panel.toolbar, FigureToolbar)
        self.assertNotIn("Save", [item[0] for item in FigureToolbar.toolitems])

    def test_basic_color_filters_log_absolute_and_bounds(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame(
            {
                "x": [-2.0, -1.0, 1.0, 2.0],
                "y": [1.0, 2.0, 3.0, 4.0],
                "c": [1.0, 2.0, 3.0, 4.0],
            }
        )
        self.commit(coordinator, dataset(frame))
        view = shell.scatter_view
        view.x_axis.column.setCurrentText("x")
        view.y_axis.column.setCurrentText("y")
        view.x_axis.absolute.setChecked(True)
        view.x_axis.scale.setCurrentIndex(1)
        view.x_axis.minimum.setText("0.5")
        view.x_axis.maximum.setText("3")
        view.y_axis.scientific.setChecked(True)
        view.color.setCurrentText("c")
        view.color_scale.setCurrentIndex(1)
        view.color_absolute.setChecked(True)
        view.color_scientific.setChecked(True)

        snapshot = coordinator.state.snapshot()
        self.assertTrue(
            coordinator.state.commit_filters(
                snapshot.revision,
                (FilterRule("y", lower=3),),
                np.array([False, False, True, True], dtype=np.bool_),
            )
        )
        APP.processEvents()
        self.plot_and_wait(coordinator)

        controller = coordinator.scatter_controller
        self.assertEqual(controller.current_spec.x_axis.scale, "log")
        self.assertTrue(controller.current_spec.x_axis.absolute)
        self.assertEqual(controller.current_spec.x_axis.lower, 0.5)
        self.assertEqual(controller.current_spec.x_axis.upper, 3.0)
        self.assertEqual(controller.current_spec.color, "c")
        self.assertEqual(controller.current_spec.color_scale, "log")
        self.assertEqual(controller.current_counts[:4], (4, 2, 2, 2))
        self.assertGreater(len(view.panel.figure.axes), 1)

    def test_thread_boundary_prepare_render_canvas_and_draw(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 3.0]})),
        )
        gui = threading.get_ident()
        prepare_threads = []
        render_threads = []
        canvas_threads = []
        draw_threads = []
        panel = shell.scatter_view.panel
        original_canvas = panel.canvas_class

        class RecordingCanvas(original_canvas):
            def __init__(self, *args, **kwargs):
                canvas_threads.append(threading.get_ident())
                super().__init__(*args, **kwargs)

            def draw(self, *args, **kwargs):
                draw_threads.append(threading.get_ident())
                return super().draw(*args, **kwargs)

        def observed_prepare(*args, **kwargs):
            prepare_threads.append(threading.get_ident())
            return real_prepare_scatter(*args, **kwargs)

        def observed_render(*args, **kwargs):
            render_threads.append(threading.get_ident())
            return real_render_scatter(*args, **kwargs)

        panel.canvas_class = RecordingCanvas
        with (
            patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", observed_prepare),
            patch("csv_analyzer.qt_app.scatter_controller.render_scatter", observed_render),
        ):
            self.plot_and_wait(coordinator)

        self.assertTrue(prepare_threads)
        self.assertTrue(all(thread != gui for thread in prepare_threads))
        self.assertEqual(render_threads, [gui])
        self.assertEqual(canvas_threads, [gui])
        self.assertEqual(draw_threads, [gui])

    def test_latest_request_wins(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame({"x": [1.0, 2.0], "y": [3.0, 4.0], "z": [5.0, 6.0]})
        self.commit(coordinator, dataset(frame))
        view = shell.scatter_view
        entered = threading.Event()
        release = threading.Event()
        prepared = []

        def controlled(frame, spec, mask=None, *, cancel=None):
            prepared.append(spec.y)
            if spec.y == "y":
                entered.set()
                release.wait(timeout=5)
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
            return real_prepare_scatter(frame, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", controlled):
            view.y_axis.column.setCurrentText("y")
            coordinator.scatter_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            view.y_axis.column.setCurrentText("z")
            coordinator.scatter_controller.request_plot()
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertEqual(coordinator.scatter_controller.current_spec.y, "z")
        self.assertEqual(shell.scatter_view.panel.figure.axes[0].get_ylabel(), "z")
        self.assertIn("z", prepared)

    def test_dataset_and_selection_changes_invalidate_plot_but_failed_load_does_not(self):
        def failing_loader(*_args, **_kwargs):
            raise DataError("load failed")

        coordinator, shell = self.make(loader=failing_loader)
        first = dataset(pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 3.0]}), "a.csv")
        second = dataset(pd.DataFrame({"u": [1.0, 2.0], "v": [5.0, 6.0]}), "b.csv")
        self.commit(coordinator, first)
        self.plot_and_wait(coordinator)
        old_figure = shell.scatter_view.panel.figure

        coordinator.load_path("failed.csv")
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIs(shell.scatter_view.panel.figure, old_figure)

        snapshot = coordinator.state.snapshot()
        self.assertTrue(
            coordinator.state.commit_filters(
                snapshot.revision,
                (FilterRule("x", lower=2),),
                np.array([False, True], dtype=np.bool_),
            )
        )
        APP.processEvents()
        self.assertFalse(shell.scatter_view.panel.has_figure)
        self.assertIn("Selection changed", shell.scatter_view.panel.empty_label.text())

        self.plot_and_wait(coordinator)
        self.assertTrue(shell.scatter_view.panel.has_figure)
        self.commit(coordinator, second)
        self.assertFalse(shell.scatter_view.panel.has_figure)
        self.assertEqual(shell.scatter_view.x_axis.column.currentText(), "u")
        self.assertEqual(shell.scatter_view.y_axis.column.currentText(), "v")

    def test_inflight_stale_dataset_and_selection_do_not_publish(self):
        coordinator, shell = self.make()
        first = dataset(pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 3.0]}), "a.csv")
        second = dataset(pd.DataFrame({"u": [1.0, 2.0], "v": [5.0, 6.0]}), "b.csv")
        self.commit(coordinator, first)
        entered = threading.Event()
        release = threading.Event()

        def slow(frame, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return real_prepare_scatter(frame, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", slow):
            coordinator.scatter_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            self.commit(coordinator, second)
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertFalse(shell.scatter_view.panel.has_figure)

        self.commit(coordinator, first)
        entered.clear()
        release.clear()
        with patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", slow):
            coordinator.scatter_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            snap = coordinator.state.snapshot()
            self.assertTrue(
                coordinator.state.commit_filters(
                    snap.revision,
                    (FilterRule("x", lower=2),),
                    np.array([False, True], dtype=np.bool_),
                )
            )
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertFalse(shell.scatter_view.panel.has_figure)

    def test_prepare_render_and_draw_failures_preserve_previous_figure(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 3.0]})),
        )
        self.plot_and_wait(coordinator)
        panel = shell.scatter_view.panel
        original = panel.figure

        with patch(
            "csv_analyzer.qt_app.scatter_controller.prepare_scatter",
            side_effect=DataError("prepare failed"),
        ):
            self.plot_and_wait(coordinator)
        self.assertIs(panel.figure, original)

        with patch(
            "csv_analyzer.qt_app.scatter_controller.render_scatter",
            side_effect=DataError("render failed"),
        ):
            self.plot_and_wait(coordinator)
        self.assertIs(panel.figure, original)

        original_canvas_class = panel.canvas_class

        class DrawFailureCanvas(original_canvas_class):
            def draw(self):
                raise RuntimeError("draw failed")

        panel.canvas_class = DrawFailureCanvas
        self.plot_and_wait(coordinator)
        self.assertIs(panel.figure, original)
        panel.canvas_class = original_canvas_class

    def test_replacement_keeps_single_canvas_toolbar_and_figure(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 3.0], "z": [3.0, 4.0]})),
        )
        view = shell.scatter_view
        figures = []
        for y in ("y", "z", "y"):
            view.y_axis.column.setCurrentText(y)
            self.plot_and_wait(coordinator)
            figures.append(view.panel.figure)
            APP.processEvents()
            self.assertIsNotNone(view.panel.canvas)
            self.assertIsNotNone(view.panel.toolbar)
            self.assertEqual(
                sum(
                    isinstance(view.panel.host_layout.itemAt(i).widget(), FigureCanvasQTAgg)
                    for i in range(view.panel.host_layout.count())
                ),
                1,
            )
            self.assertEqual(
                sum(
                    isinstance(view.panel.host_layout.itemAt(i).widget(), FigureToolbar)
                    for i in range(view.panel.host_layout.count())
                ),
                1,
            )
        self.assertIsNot(figures[0], figures[1])
        self.assertIsNot(figures[1], figures[2])

    def test_invalid_empty_and_no_usable_cases_report_without_crash(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": [0.0, -1.0], "y": [1.0, np.nan]})),
        )
        view = shell.scatter_view
        view.x_axis.scale.setCurrentIndex(1)
        self.plot_and_wait(coordinator)
        self.assertTrue(shell.errors)
        self.assertFalse(view.panel.has_figure)

        view.x_axis.scale.setCurrentIndex(0)
        view.x_axis.minimum.setText("5")
        view.x_axis.maximum.setText("1")
        coordinator.scatter_controller.request_plot()
        APP.processEvents()
        self.assertTrue(shell.errors)

    def test_plot_does_not_mutate_app_state(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 3.0]})),
        )
        before = coordinator.state.snapshot()
        self.plot_and_wait(coordinator)
        after = coordinator.state.snapshot()
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(after.dataset_revision, before.dataset_revision)
        self.assertEqual(after.selection_revision, before.selection_revision)
        self.assertIs(after.mask, before.mask)
        self.assertEqual(after.filters, before.filters)

    def test_close_with_visible_figure(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 3.0]})),
        )
        self.plot_and_wait(coordinator)
        self.assertTrue(shell.scatter_view.panel.has_figure)
        coordinator.begin_close()
        pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        self.assertFalse(shell.scatter_view.panel.has_figure)

    def test_close_with_active_worker_never_publishes(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 3.0]})),
        )
        entered = threading.Event()
        release = threading.Event()

        def slow(frame, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return real_prepare_scatter(frame, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", slow):
            coordinator.scatter_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            coordinator.begin_close()
            release.set()
            pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        self.assertFalse(shell.scatter_view.panel.has_figure)


if __name__ == "__main__":
    unittest.main()
