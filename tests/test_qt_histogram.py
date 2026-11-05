"""F3.E Qt histogram controls, async semantics and PlotPanel lifecycle."""

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
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from csv_analyzer.data import CancelledError, DataError, Dataset, describe_columns
from csv_analyzer.plots import HistogramSpec
from csv_analyzer.plots import prepare_histogram as real_prepare_histogram
from csv_analyzer.plots import prepare_scatter as real_prepare_scatter
from csv_analyzer.plots import render_histogram as real_render_histogram
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.plot_panel import FigureToolbar
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule
from csv_analyzer.table import SearchMode, SortDirection, TableScope

APP = QApplication.instance() or QApplication(["varia-f3e-tests"])


def pump_until(predicate, timeout_ms=7000):
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


def drain_qt_deletes():
    APP.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    APP.processEvents()


def dataset(frame: pd.DataFrame, name="dataset.csv") -> Dataset:
    return Dataset(frame=frame, path=Path(name), columns=describe_columns(frame))


class RecordingShell(QtShell):
    def __init__(self):
        super().__init__()
        self.errors = []

    def show_error(self, text: str) -> None:
        self.errors.append(text)


class QtHistogramTests(unittest.TestCase):
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
        drain_qt_deletes()

    def commit(self, coordinator, value):
        ticket = coordinator.state.begin_load()
        self.assertTrue(coordinator.state.commit_dataset(ticket, value))
        APP.processEvents()

    def plot_and_wait(self, coordinator):
        coordinator.histogram_controller.request_plot()
        pump_until(lambda: coordinator.runner.activity.physical == 0)

    def assert_single_graphics_tuple(self, panel):
        self.assertIsNotNone(panel.figure)
        self.assertIsNotNone(panel.canvas)
        self.assertIsNotNone(panel.toolbar)
        self.assertEqual(len(panel.host.findChildren(FigureCanvasQTAgg)), 1)
        self.assertEqual(len(panel.host.findChildren(FigureToolbar)), 1)
        self.assertIs(panel.toolbar.canvas, panel.canvas)
        self.assertIs(panel.canvas.toolbar, panel.toolbar)
        self.assertNotIn("Save", [item[0] for item in panel.toolbar.toolitems])

    def test_ui_states_tab_order_and_single_numeric_column(self):
        coordinator, shell = self.make()
        view = shell.histogram_view
        labels = [shell.tabs.tabText(index) for index in range(shell.tabs.count())]
        self.assertEqual(labels, ["Data", "Relationships", "Distributions", "Statistics"])
        self.assertFalse(view.plot_button.isEnabled())

        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": pd.Series([], dtype="float64")}), "empty.csv"),
        )
        self.assertFalse(view.plot_button.isEnabled())

        self.commit(coordinator, dataset(pd.DataFrame({"name": ["a", "b"], "flag": [True, False]})))
        self.assertFalse(view.plot_button.isEnabled())

        self.commit(coordinator, dataset(pd.DataFrame({"x": [1.0, 2.0], "flag": [True, False]})))
        self.assertTrue(view.plot_button.isEnabled())
        self.assertEqual([view.column.itemText(i) for i in range(view.column.count())], ["x"])
        self.plot_and_wait(coordinator)
        self.assert_single_graphics_tuple(view.panel)

    def test_spec_mapping_custom_bins_and_invalid_custom_preserves_figure_without_worker(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0]})))
        view = shell.histogram_view
        view.axis.scale.setCurrentIndex(1)
        view.axis.absolute.setChecked(True)
        view.axis.scientific.setChecked(True)
        view.axis.minimum.setText("1")
        view.axis.maximum.setText("4")
        view.bin_mode.setCurrentIndex(view.bin_mode.findData("custom"))
        view.custom_bins.setText("3")
        view.log_bins.setChecked(True)
        view.normalization.setCurrentIndex(1)
        view.log_y.setChecked(True)
        view.scientific_y.setChecked(True)
        spec = view.build_spec()
        self.assertEqual(spec.column, "x")
        self.assertEqual(spec.bins, 3)
        self.assertTrue(spec.log_bins)
        self.assertTrue(spec.density)
        self.assertTrue(spec.log_y)
        self.assertTrue(spec.scientific_y)
        self.assertEqual(spec.axis.scale, "log")
        self.assertTrue(spec.axis.absolute)
        self.assertEqual(spec.axis.lower, 1.0)
        self.assertEqual(spec.axis.upper, 4.0)
        self.assertEqual(spec.color, "#178F80")
        self.assertEqual(spec.opacity, 0.85)
        self.assertEqual(spec.title, "")

        view.axis.scale.setCurrentIndex(0)
        view.log_bins.setChecked(False)
        self.plot_and_wait(coordinator)
        active = view.panel.figure
        generation = coordinator.runner.current_generation("histogram")

        for bad in ("bad", "0", "4097"):
            with self.subTest(bad=bad):
                view.custom_bins.setText(bad)
                coordinator.histogram_controller.request_plot()
                APP.processEvents()
                self.assertIs(view.panel.figure, active)
                self.assertEqual(coordinator.runner.current_generation("histogram"), generation)
        self.assertTrue(shell.errors)

    def test_binning_algorithms_counts_edges_and_density_log_bins(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame({"x": np.geomspace(1.0, 1000.0, 120)})
        self.commit(coordinator, dataset(frame))
        view = shell.histogram_view

        for mode in ("auto", "fd", "sqrt", "sturges"):
            with self.subTest(mode=mode):
                view.bin_mode.setCurrentIndex(view.bin_mode.findData(mode))
                self.plot_and_wait(coordinator)
                data_counts = coordinator.histogram_controller.current_counts
                self.assertEqual(data_counts[1], 120)
                self.assertEqual(data_counts[2], 120)
                self.assertEqual(data_counts[3], 120)
                self.assertGreater(data_counts[4], 0)

        view.bin_mode.setCurrentIndex(view.bin_mode.findData("custom"))
        view.custom_bins.setText("6")
        view.log_bins.setChecked(True)
        view.normalization.setCurrentIndex(1)
        self.plot_and_wait(coordinator)
        spec = coordinator.histogram_controller.current_spec
        data = real_prepare_histogram(frame, spec)
        self.assertTrue((np.diff(data.edges) > 0).all())
        self.assertEqual(int(data.counts.sum()), data.in_range)
        figure = real_render_histogram(data, spec)
        heights = figure.axes[0].patches[0].get_data().values
        self.assertAlmostEqual(float(np.sum(heights * np.diff(data.edges))), 1.0, places=12)

    def test_log_bins_log_axis_absolute_bounds_and_dataset_immutability(self):
        coordinator, shell = self.make()
        original = np.array([-100.0, -10.0, -1.0, 0.0, 1.0, 10.0, 100.0])
        frame = pd.DataFrame({"x": original.copy()})
        self.commit(coordinator, dataset(frame))
        view = shell.histogram_view

        cases = (
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        )
        for log_bins, log_axis in cases:
            with self.subTest(log_bins=log_bins, log_axis=log_axis):
                view.log_bins.setChecked(log_bins)
                view.axis.scale.setCurrentIndex(1 if log_axis else 0)
                view.axis.absolute.setChecked(True)
                view.axis.minimum.setText("1")
                view.axis.maximum.setText("100")
                self.plot_and_wait(coordinator)
                self.assert_single_graphics_tuple(view.panel)

        np.testing.assert_array_equal(frame["x"].to_numpy(), original)
        self.assertEqual(coordinator.histogram_controller.current_counts[3], 6)

    def test_mask_apply_clear_zero_selected_and_no_silent_all(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0]})
        self.commit(coordinator, dataset(frame))
        self.plot_and_wait(coordinator)
        self.assertEqual(coordinator.histogram_controller.current_counts[1], 4)

        self.assertTrue(coordinator.apply_filters((FilterRule("x", lower=3),)))
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertFalse(shell.histogram_view.panel.has_figure)
        self.assertIn("Selection changed", shell.histogram_view.local_status.text())
        self.plot_and_wait(coordinator)
        self.assertEqual(coordinator.histogram_controller.current_counts[1], 2)
        self.assertEqual(coordinator.histogram_controller.current_counts[3], 2)

        self.assertTrue(coordinator.apply_filters((FilterRule("x", lower=99),)))
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertFalse(shell.histogram_view.panel.has_figure)
        self.plot_and_wait(coordinator)
        self.assertFalse(shell.histogram_view.panel.has_figure)
        self.assertTrue(shell.errors)
        self.assertEqual(coordinator.state.selected_count, 0)

        self.assertTrue(coordinator.apply_filters(()))
        self.assertFalse(shell.histogram_view.panel.has_figure)
        self.plot_and_wait(coordinator)
        self.assertEqual(coordinator.histogram_controller.current_counts[1], 4)

    def test_thread_boundary_prepare_render_canvas_and_draw(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0]})))
        gui = threading.get_ident()
        prepare_threads = []
        render_threads = []
        canvas_threads = []
        draw_threads = []
        panel = shell.histogram_view.panel
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
            return real_prepare_histogram(*args, **kwargs)

        def observed_render(*args, **kwargs):
            render_threads.append(threading.get_ident())
            return real_render_histogram(*args, **kwargs)

        panel.canvas_class = RecordingCanvas
        with (
            patch("csv_analyzer.qt_app.histogram_controller.prepare_histogram", observed_prepare),
            patch("csv_analyzer.qt_app.histogram_controller.render_histogram", observed_render),
        ):
            self.plot_and_wait(coordinator)

        self.assertTrue(prepare_threads)
        self.assertTrue(all(thread != gui for thread in prepare_threads))
        self.assertEqual(render_threads, [gui])
        self.assertEqual(canvas_threads, [gui])
        self.assertEqual(draw_threads, [gui])
        panel.canvas_class = original_canvas

    def test_latest_histogram_request_wins(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": np.arange(1.0, 41.0)})))
        view = shell.histogram_view
        entered = threading.Event()
        release = threading.Event()
        prepared = []

        def controlled(frame, spec, mask=None, *, cancel=None):
            prepared.append(spec.bins)
            if spec.bins == 2:
                entered.set()
                release.wait(timeout=5)
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
            return real_prepare_histogram(frame, spec, mask, cancel=cancel)

        view.bin_mode.setCurrentIndex(view.bin_mode.findData("custom"))
        with patch("csv_analyzer.qt_app.histogram_controller.prepare_histogram", controlled):
            view.custom_bins.setText("2")
            coordinator.histogram_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            view.custom_bins.setText("5")
            coordinator.histogram_controller.request_plot()
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertEqual(coordinator.histogram_controller.current_spec.bins, 5)
        self.assertEqual(coordinator.histogram_controller.current_counts[4], 5)
        self.assertIn(5, prepared)

    def test_cross_plot_group_scatter_pending_then_histogram(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 6.0]})),
        )
        coordinator.scatter_controller.request_plot()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        old_scatter = shell.scatter_view.panel.figure
        entered = threading.Event()
        release = threading.Event()

        def slow_scatter(frame, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return real_prepare_scatter(frame, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", slow_scatter):
            coordinator.scatter_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            coordinator.histogram_controller.request_plot()
            self.assertIs(shell.scatter_view.panel.figure, old_scatter)
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertIs(shell.scatter_view.panel.figure, old_scatter)
        self.assertTrue(shell.histogram_view.panel.has_figure)

    def test_cross_plot_group_histogram_pending_then_scatter(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 6.0]})),
        )
        self.plot_and_wait(coordinator)
        old_histogram = shell.histogram_view.panel.figure
        entered = threading.Event()
        release = threading.Event()

        def slow_histogram(frame, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return real_prepare_histogram(frame, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.histogram_controller.prepare_histogram", slow_histogram):
            coordinator.histogram_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            coordinator.scatter_controller.request_plot()
            self.assertIs(shell.histogram_view.panel.figure, old_histogram)
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertIs(shell.histogram_view.panel.figure, old_histogram)
        self.assertTrue(shell.scatter_view.panel.has_figure)

    def test_dataset_stale_failed_and_cancelled_load_lifecycle(self):
        def failing_loader(*_args, **_kwargs):
            raise DataError("load failed")

        coordinator, shell = self.make(loader=failing_loader)
        first = dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0]}), "a.csv")
        second = dataset(pd.DataFrame({"u": [4.0, 5.0]}), "b.csv")
        self.commit(coordinator, first)
        self.plot_and_wait(coordinator)
        old_figure = shell.histogram_view.panel.figure
        old_column = shell.histogram_view.column.currentText()

        coordinator.load_path("failed.csv")
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIs(shell.histogram_view.panel.figure, old_figure)
        self.assertEqual(shell.histogram_view.column.currentText(), old_column)

        entered = threading.Event()
        release = threading.Event()

        def slow(frame, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return real_prepare_histogram(frame, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.histogram_controller.prepare_histogram", slow):
            coordinator.histogram_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            self.commit(coordinator, second)
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertFalse(shell.histogram_view.panel.has_figure)
        self.assertEqual(shell.histogram_view.column.currentText(), "u")

    def test_cancelled_load_preserves_histogram_and_controls(self):
        entered = threading.Event()
        release = threading.Event()

        def cancellable_loader(path, options, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return dataset(pd.DataFrame({"u": [10.0, 20.0]}), "replacement.csv")

        coordinator, shell = self.make(loader=cancellable_loader)
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0]}), "active.csv"))
        self.plot_and_wait(coordinator)
        old_figure = shell.histogram_view.panel.figure
        old_column = shell.histogram_view.column.currentText()

        coordinator.load_path("replacement.csv")
        self.assertTrue(entered.wait(timeout=2))
        coordinator.cancel_all()
        release.set()
        pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertIs(shell.histogram_view.panel.figure, old_figure)
        self.assertEqual(shell.histogram_view.column.currentText(), old_column)
        self.assertEqual(coordinator.state.dataset.path.name, "active.csv")

    def test_pending_histogram_is_invalidated_by_selection_revision_change(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0]})))
        entered = threading.Event()
        release = threading.Event()

        def slow(frame, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return real_prepare_histogram(frame, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.histogram_controller.prepare_histogram", slow):
            coordinator.histogram_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            self.assertTrue(coordinator.apply_filters((FilterRule("x", lower=3),)))
            pump_until(lambda: coordinator.state.selected_count == 2)
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertFalse(shell.histogram_view.panel.has_figure)
        self.assertIsNone(coordinator.histogram_controller.current_counts)
        self.assertIn("Selection changed", shell.histogram_view.local_status.text())

    def test_table_scope_sort_and_search_do_not_change_histogram_selection(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame(
            {
                "x": [1.0, 2.0, 3.0, 4.0],
                "label": ["alpha", "beta", "alpha", "beta"],
            }
        )
        self.commit(coordinator, dataset(frame))
        self.assertTrue(coordinator.apply_filters((FilterRule("x", lower=3),)))
        pump_until(lambda: coordinator.runner.activity.physical == 0)

        table = coordinator.table_controller
        table.set_scope(TableScope.ALL)
        table.set_sort(1, SortDirection.DESC)
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        table.set_search("beta", SearchMode.EXACT)
        pump_until(lambda: table.search_result is not None)

        self.plot_and_wait(coordinator)
        self.assertEqual(coordinator.state.selected_count, 2)
        self.assertEqual(coordinator.histogram_controller.current_counts[1], 2)
        self.assertEqual(coordinator.histogram_controller.current_counts[3], 2)

    def test_prepare_render_draw_failures_preserve_valid_figure(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0]})))
        self.plot_and_wait(coordinator)
        panel = shell.histogram_view.panel
        active = (panel.figure, panel.canvas, panel.toolbar)

        with patch(
            "csv_analyzer.qt_app.histogram_controller.prepare_histogram",
            side_effect=DataError("prepare failed"),
        ):
            self.plot_and_wait(coordinator)
        self.assertIs(panel.figure, active[0])

        with patch(
            "csv_analyzer.qt_app.histogram_controller.render_histogram",
            side_effect=DataError("render failed"),
        ):
            self.plot_and_wait(coordinator)
        self.assertIs(panel.figure, active[0])

        original_canvas = panel.canvas_class

        class DrawFailureCanvas(original_canvas):
            def draw(self):
                raise RuntimeError("draw failed")

        panel.canvas_class = DrawFailureCanvas
        self.plot_and_wait(coordinator)
        panel.canvas_class = original_canvas
        drain_qt_deletes()
        self.assertIs(panel.figure, active[0])
        self.assertIs(panel.canvas, active[1])
        self.assertIs(panel.toolbar, active[2])

    def test_repeated_histogram_replacement_and_draw_idle_replot(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame({"x": np.arange(1.0, 101.0)})
        self.commit(coordinator, dataset(frame))
        view = shell.histogram_view
        view.bin_mode.setCurrentIndex(view.bin_mode.findData("custom"))

        for bins in ("5", "7", "9"):
            view.custom_bins.setText(bins)
            self.plot_and_wait(coordinator)
            self.assert_single_graphics_tuple(view.panel)

        late_draws = []
        for index in range(10):
            retired_canvas = view.panel.canvas
            retired_canvas.draw = lambda i=index: late_draws.append(i)
            retired_canvas.draw_idle()
            self.assertTrue(getattr(retired_canvas, "_draw_pending", False))
            late_callback = retired_canvas._draw_idle
            data = real_prepare_histogram(frame, HistogramSpec("x", bins=index + 2))
            figure = real_render_histogram(data, HistogramSpec("x", bins=index + 2))
            view.panel.show_figure(figure, data.description)
            self.assertFalse(getattr(retired_canvas, "_draw_pending", False))
            late_callback()
            drain_qt_deletes()
            self.assertEqual(late_draws, [])
            self.assert_single_graphics_tuple(view.panel)

    def test_empty_invalid_and_constant_cases_do_not_crash(self):
        coordinator, shell = self.make()
        cases = (
            pd.DataFrame({"x": [np.nan, np.nan]}),
            pd.DataFrame({"x": [np.inf, -np.inf]}),
            pd.DataFrame({"x": [0.0, 0.0, 0.0]}),
            pd.DataFrame({"x": [5.0, 5.0, 5.0]}),
        )
        for index, frame in enumerate(cases):
            with self.subTest(index=index):
                self.commit(coordinator, dataset(frame, f"{index}.csv"))
                view = shell.histogram_view
                if index == 2:
                    view.axis.scale.setCurrentIndex(0)
                    view.log_bins.setChecked(False)
                self.plot_and_wait(coordinator)
                if index == 2:
                    active_zero = view.panel.figure
                    view.axis.scale.setCurrentIndex(1)
                    self.plot_and_wait(coordinator)
                    self.assertIs(view.panel.figure, active_zero)

        self.commit(coordinator, dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0]}), "bounds.csv"))
        view = shell.histogram_view
        self.plot_and_wait(coordinator)
        active = view.panel.figure
        for lower, upper in (("2", "2"), ("3", "1"), ("0", "2")):
            with self.subTest(lower=lower, upper=upper):
                view.axis.scale.setCurrentIndex(1 if lower == "0" else 0)
                view.axis.minimum.setText(lower)
                view.axis.maximum.setText(upper)
                coordinator.histogram_controller.request_plot()
                pump_until(lambda: coordinator.runner.activity.physical == 0)
                self.assertIs(view.panel.figure, active)

    def test_plot_does_not_mutate_app_state(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0]})))
        before = coordinator.state.snapshot()
        self.plot_and_wait(coordinator)
        after = coordinator.state.snapshot()
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(after.dataset_revision, before.dataset_revision)
        self.assertEqual(after.selection_revision, before.selection_revision)
        self.assertIs(after.mask, before.mask)
        self.assertEqual(after.filters, before.filters)

    def test_close_visible_figure_and_active_worker(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1.0, 2.0, 3.0]})))
        self.plot_and_wait(coordinator)
        self.assertTrue(shell.histogram_view.panel.has_figure)

        entered = threading.Event()
        release = threading.Event()

        def slow(frame, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return real_prepare_histogram(frame, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.histogram_controller.prepare_histogram", slow):
            coordinator.histogram_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            coordinator.begin_close()
            release.set()
            pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        self.assertFalse(shell.histogram_view.panel.has_figure)


if __name__ == "__main__":
    unittest.main()
