"""F4.C Qt plot configuration coherence and Distribution parity closure."""

from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from csv_analyzer.data import CancelledError, Dataset, describe_columns
from csv_analyzer.plots import (
    InsetSpec,
    Overlay,
    prepare_histogram,
    prepare_scatter,
)
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.scatter_style_dialog import ScatterStyle
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule

APP = QApplication.instance() or QApplication(["varia-f4c-tests"])


def pump_until(predicate, timeout_ms=10000):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    timeout = QTimer()
    expired = {"value": False}
    poll.setInterval(2)
    timeout.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)

    def expire():
        expired["value"] = True
        loop.quit()

    timeout.timeout.connect(expire)
    poll.start()
    timeout.start(timeout_ms)
    loop.exec()
    poll.stop()
    timeout.stop()
    if expired["value"] and not predicate():
        raise AssertionError("Qt condition did not become true before timeout")


def make_dataset(frame: pd.DataFrame, name="plots.csv") -> Dataset:
    return Dataset(frame=frame, path=Path(name), columns=describe_columns(frame))


def frame(rows=12):
    x = np.arange(1, rows + 1, dtype=float)
    return pd.DataFrame(
        {
            "x": x,
            "y": x * 2.0,
            "z": x * 3.0,
            "c": x + 0.5,
        }
    )


class RecordingShell(QtShell):
    def __init__(self):
        super().__init__()
        self.errors: list[str] = []

    def show_error(self, text: str) -> None:
        self.errors.append(text)


class PlotConfigurationTests(unittest.TestCase):
    def make(self, *, max_workers=2):
        shell = RecordingShell()
        coordinator = QtCoordinator(shell, max_workers=max_workers)
        coordinator.start()
        self.addCleanup(self.close, coordinator, shell)
        return coordinator, shell

    def close(self, coordinator, shell):
        if coordinator.lifecycle not in (Lifecycle.CLOSING, Lifecycle.CLOSED):
            coordinator.begin_close()
        if coordinator.lifecycle is not Lifecycle.CLOSED:
            pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        shell.deleteLater()
        coordinator.deleteLater()
        APP.processEvents()

    def commit(self, coordinator, value, name="plots.csv"):
        ticket = coordinator.state.begin_load()
        self.assertTrue(coordinator.state.commit_dataset(ticket, make_dataset(value, name)))
        APP.processEvents()

    def plot_scatter(self, coordinator):
        coordinator.scatter_controller.request_plot()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        APP.processEvents()

    def plot_histogram(self, coordinator):
        coordinator.histogram_controller.request_plot()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        APP.processEvents()

    @staticmethod
    def user_combo(combo, index):
        combo.setCurrentIndex(index)
        combo.activated.emit(index)

    @staticmethod
    def user_check(check, value):
        check.setChecked(value)
        check.clicked.emit()

    @staticmethod
    def user_text(edit, value):
        edit.setText(value)
        edit.textEdited.emit(value)

    def test_scatter_base_control_marks_dirty_and_preserves_existing_figure(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame())
        self.plot_scatter(coordinator)
        view = shell.scatter_view
        figure_a = view.panel.figure
        before = coordinator.state.snapshot()
        revision = view.plot_settings_revision

        self.user_combo(view.x_axis.column, view.x_axis.column.findText("z"))

        self.assertEqual(view.plot_settings_revision, revision + 1)
        self.assertIs(view.panel.figure, figure_a)
        self.assertEqual(view.x_axis.column.currentText(), "z")
        self.assertIn("Plot again", view.local_status.text())
        self.assertIn("Plot again", shell.status_label.text())
        after = coordinator.state.snapshot()
        self.assertEqual(
            (
                after.revision,
                after.dataset_revision,
                after.selection_revision,
                after.filters,
                after.selected_count,
            ),
            (
                before.revision,
                before.dataset_revision,
                before.selection_revision,
                before.filters,
                before.selected_count,
            ),
        )
        self.assertIs(after.mask, before.mask)
        self.assertIs(after.dataset.frame, before.dataset.frame)

    def test_scatter_pending_base_change_publishes_a_with_warning_then_b_clears_it(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame())
        view = shell.scatter_view
        entered = threading.Event()
        release = threading.Event()

        def blocked_prepare(dataframe, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            return prepare_scatter(dataframe, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", blocked_prepare):
            coordinator.scatter_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            spec_a = coordinator.scatter_controller.current_spec
            revision = view.plot_settings_revision

            self.user_combo(view.x_axis.column, view.x_axis.column.findText("z"))
            self.user_combo(view.x_axis.scale, view.x_axis.scale.findData("log"))
            self.assertGreater(view.plot_settings_revision, revision)

            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            APP.processEvents()

        self.assertIs(coordinator.scatter_controller.current_spec, spec_a)
        self.assertEqual(spec_a.x, "x")
        self.assertEqual(spec_a.x_axis.scale, "linear")
        self.assertEqual(view.x_axis.column.currentText(), "z")
        self.assertEqual(view.x_axis.scale.currentData(), "log")
        self.assertIn("Plot again", view.local_status.text())
        self.assertIn("Plot again", shell.status_label.text())

        self.plot_scatter(coordinator)
        spec_b = coordinator.scatter_controller.current_spec
        self.assertEqual(spec_b.x, "z")
        self.assertEqual(spec_b.x_axis.scale, "log")
        self.assertEqual(shell.status_label.text(), "Relationship figure updated.")
        self.assertNotIn("previous settings", view.local_status.text())

    def test_scatter_advanced_effective_apply_marks_unified_revision(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame())
        view = shell.scatter_view
        plot_revision = view.plot_settings_revision
        advanced_revision = view.advanced_revision

        view._apply_style(ScatterStyle(title="B"), view.settings_epoch)
        self.assertEqual(view.advanced_revision, advanced_revision + 1)
        self.assertEqual(view.plot_settings_revision, plot_revision + 1)

        plot_revision = view.plot_settings_revision
        view._apply_overlays((Overlay("Horizontal line", y=4),), view.settings_epoch)
        self.assertEqual(view.plot_settings_revision, plot_revision + 1)

        plot_revision = view.plot_settings_revision
        view._apply_inset(InsetSpec(1, 3, 2, 6), view.settings_epoch)
        self.assertEqual(view.plot_settings_revision, plot_revision + 1)

    def test_scatter_partial_invalid_text_marks_dirty_without_validation_until_plot(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame())
        self.plot_scatter(coordinator)
        view = shell.scatter_view
        valid_figure = view.panel.figure

        for value in ("-", "1e", "abc"):
            with self.subTest(value=value):
                revision = view.plot_settings_revision
                self.user_text(view.x_axis.minimum, value)
                self.assertEqual(view.plot_settings_revision, revision + 1)
                self.assertIs(view.panel.figure, valid_figure)
                self.assertIn("Plot again", view.local_status.text())

                before_generation = coordinator.runner.current_generation("scatter")
                coordinator.scatter_controller.request_plot()
                APP.processEvents()
                self.assertEqual(
                    coordinator.runner.current_generation("scatter"),
                    before_generation,
                )
                self.assertIs(view.panel.figure, valid_figure)
                self.assertTrue(shell.errors)
                shell.errors.clear()

    def test_histogram_title_maps_renders_and_export_owns_same_figure(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame())
        view = shell.histogram_view
        self.user_text(view.title, "Distribution A")
        spec = view.build_spec()
        self.assertEqual(spec.title, "Distribution A")

        self.plot_histogram(coordinator)
        figure = view.panel.figure
        self.assertEqual(figure.axes[0].get_title(loc="left"), "Distribution A")

        exported = []
        with (
            patch(
                "csv_analyzer.qt_app.plot_panel.export_figure",
                side_effect=lambda value, path, **_kwargs: exported.append((value, Path(path)))
                or Path(path),
            ),
            patch(
                "csv_analyzer.qt_app.plot_panel.choose_save_path",
                side_effect=(Path("distribution.png"), Path("distribution.svg")),
            ),
        ):
            view.panel.export_current_figure()
            view.panel.export_current_figure()

        self.assertEqual(len(exported), 2)
        self.assertIs(exported[0][0], figure)
        self.assertIs(exported[1][0], figure)
        self.assertEqual(exported[0][1].suffix, ".png")
        self.assertEqual(exported[1][1].suffix, ".svg")
        self.assertEqual(figure.axes[0].get_title(loc="left"), "Distribution A")

    def test_histogram_base_edit_preserves_figure_and_pending_a_keeps_warning(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame())
        view = shell.histogram_view
        self.user_text(view.title, "A")
        self.plot_histogram(coordinator)
        figure_a = view.panel.figure

        revision = view.plot_settings_revision
        self.user_text(view.title, "B")
        self.assertEqual(view.plot_settings_revision, revision + 1)
        self.assertIs(view.panel.figure, figure_a)
        self.assertIn("Plot again", view.local_status.text())
        self.assertIn("Plot again", shell.status_label.text())

        entered = threading.Event()
        release = threading.Event()
        self.user_text(view.title, "A")

        def blocked_prepare(dataframe, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            return prepare_histogram(dataframe, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.histogram_controller.prepare_histogram", blocked_prepare):
            coordinator.histogram_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            spec_a = coordinator.histogram_controller.current_spec

            self.user_text(view.title, "B")
            self.user_combo(view.bin_mode, view.bin_mode.findData("custom"))
            self.user_text(view.custom_bins, "7")
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            APP.processEvents()

        self.assertIs(coordinator.histogram_controller.current_spec, spec_a)
        self.assertEqual(spec_a.title, "A")
        self.assertEqual(spec_a.bins, "auto")
        self.assertEqual(view.title.text(), "B")
        self.assertEqual(view.custom_bins.text(), "7")
        self.assertIn("Plot again", view.local_status.text())
        self.assertIn("Plot again", shell.status_label.text())

        self.plot_histogram(coordinator)
        spec_b = coordinator.histogram_controller.current_spec
        self.assertEqual(spec_b.title, "B")
        self.assertEqual(spec_b.bins, 7)
        self.assertEqual(view.panel.figure.axes[0].get_title(loc="left"), "B")
        self.assertEqual(shell.status_label.text(), "Distribution figure updated.")

    def test_histogram_partial_invalid_text_marks_dirty_and_preserves_figure_until_plot(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame())
        self.plot_histogram(coordinator)
        view = shell.histogram_view
        valid_figure = view.panel.figure

        self.user_combo(view.bin_mode, view.bin_mode.findData("custom"))
        for value in ("-", "1e", "abc"):
            with self.subTest(value=value):
                revision = view.plot_settings_revision
                self.user_text(view.custom_bins, value)
                self.assertEqual(view.plot_settings_revision, revision + 1)
                self.assertIs(view.panel.figure, valid_figure)
                self.assertIn("Plot again", view.local_status.text())

                before_generation = coordinator.runner.current_generation("histogram")
                coordinator.histogram_controller.request_plot()
                APP.processEvents()
                self.assertEqual(
                    coordinator.runner.current_generation("histogram"),
                    before_generation,
                )
                self.assertIs(view.panel.figure, valid_figure)
                self.assertTrue(shell.errors)
                shell.errors.clear()

    def test_histogram_reset_restores_full_defaults_and_clears_figure(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame())
        view = shell.histogram_view

        view.column.setCurrentText("z")
        view.axis.scale.setCurrentIndex(view.axis.scale.findData("log"))
        view.axis.absolute.setChecked(True)
        view.axis.scientific.setChecked(True)
        view.axis.minimum.setText("1")
        view.axis.maximum.setText("10")
        view.bin_mode.setCurrentIndex(view.bin_mode.findData("custom"))
        view.custom_bins.setText("33")
        view.log_bins.setChecked(True)
        view.normalization.setCurrentIndex(1)
        view.log_y.setChecked(True)
        view.scientific_y.setChecked(True)
        view.title.setText("Changed")
        self.plot_histogram(coordinator)
        self.assertTrue(view.panel.has_figure)
        revision = view.plot_settings_revision

        coordinator.histogram_controller.reset_settings()

        self.assertGreater(view.plot_settings_revision, revision)
        self.assertFalse(view.panel.has_figure)
        self.assertEqual(view.column.currentText(), "x")
        self.assertEqual(view.axis.scale.currentData(), "linear")
        self.assertFalse(view.axis.absolute.isChecked())
        self.assertFalse(view.axis.scientific.isChecked())
        self.assertEqual(view.axis.minimum.text(), "")
        self.assertEqual(view.axis.maximum.text(), "")
        self.assertEqual(view.bin_mode.currentData(), "auto")
        self.assertEqual(view.custom_bins.text(), "20")
        self.assertFalse(view.log_bins.isChecked())
        self.assertFalse(bool(view.normalization.currentData()))
        self.assertFalse(view.log_y.isChecked())
        self.assertFalse(view.scientific_y.isChecked())
        self.assertEqual(view.title.text(), "")
        self.assertEqual(view.local_status.text(), "Settings reset. Plot again.")
        self.assertEqual(shell.status_label.text(), "Settings reset. Plot again.")

    def test_histogram_reset_cancels_pending_histogram_only(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame())
        view = shell.histogram_view
        entered = threading.Event()
        release = threading.Event()
        cancelled = []

        def blocked_prepare(dataframe, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            cancelled.append(cancel.is_set())
            if cancel.is_set():
                raise CancelledError()
            return prepare_histogram(dataframe, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.histogram_controller.prepare_histogram", blocked_prepare):
            coordinator.histogram_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            coordinator.histogram_controller.reset_settings()
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertEqual(cancelled, [True])
        self.assertIsNone(coordinator.histogram_controller.current_spec)
        self.assertFalse(view.panel.has_figure)
        self.assertEqual(view.local_status.text(), "Settings reset. Plot again.")

    def test_dataset_reset_clears_dirty_warning_and_selection_preserves_settings(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame(), "a.csv")
        scatter = shell.scatter_view
        histogram = shell.histogram_view

        self.user_combo(scatter.x_axis.column, scatter.x_axis.column.findText("z"))
        self.user_text(histogram.title, "Keep me")
        scatter_revision = scatter.plot_settings_revision
        histogram_revision = histogram.plot_settings_revision

        snapshot = coordinator.state.snapshot()
        self.assertTrue(
            coordinator.state.commit_filters(
                snapshot.revision,
                (FilterRule("x", lower=3),),
                np.array([False, False] + [True] * 10, dtype=bool),
            )
        )
        APP.processEvents()

        self.assertEqual(scatter.plot_settings_revision, scatter_revision)
        self.assertEqual(histogram.plot_settings_revision, histogram_revision)
        self.assertEqual(scatter.x_axis.column.currentText(), "z")
        self.assertEqual(histogram.title.text(), "Keep me")

        self.commit(coordinator, frame(6), "b.csv")
        self.assertGreater(scatter.plot_settings_revision, scatter_revision)
        self.assertGreater(histogram.plot_settings_revision, histogram_revision)
        self.assertEqual(scatter.x_axis.column.currentText(), "x")
        self.assertEqual(histogram.title.text(), "")
        self.assertNotIn("previous settings", scatter.local_status.text())
        self.assertNotIn("previous settings", histogram.local_status.text())
        self.assertFalse(scatter.panel.has_figure)
        self.assertFalse(histogram.panel.has_figure)

    def test_visual_setting_changes_do_not_mutate_app_state(self):
        coordinator, shell = self.make()
        self.commit(coordinator, frame())
        before = coordinator.state.snapshot()
        scatter = shell.scatter_view
        histogram = shell.histogram_view

        self.user_combo(scatter.x_axis.column, scatter.x_axis.column.findText("z"))
        self.user_check(scatter.show_excluded, False)
        scatter._apply_style(ScatterStyle(title="S"), scatter.settings_epoch)
        self.user_text(histogram.title, "H")
        self.user_combo(histogram.bin_mode, histogram.bin_mode.findData("custom"))
        self.user_text(histogram.custom_bins, "9")

        after = coordinator.state.snapshot()
        self.assertEqual(
            (
                after.revision,
                after.dataset_revision,
                after.selection_revision,
                after.filters,
                after.selected_count,
            ),
            (
                before.revision,
                before.dataset_revision,
                before.selection_revision,
                before.filters,
                before.selected_count,
            ),
        )
        self.assertIs(after.mask, before.mask)
        self.assertIs(after.dataset.frame, before.dataset.frame)

    def test_latest_scatter_and_histogram_requests_still_win(self):
        coordinator, shell = self.make(max_workers=2)
        self.commit(coordinator, frame())
        scatter = shell.scatter_view
        histogram = shell.histogram_view

        scatter_entered = threading.Event()
        scatter_release = threading.Event()

        def controlled_scatter(dataframe, spec, mask=None, *, cancel=None):
            if spec.x == "x":
                scatter_entered.set()
                scatter_release.wait(timeout=5)
                if cancel.is_set():
                    raise CancelledError()
            return prepare_scatter(dataframe, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", controlled_scatter):
            coordinator.scatter_controller.request_plot()
            self.assertTrue(scatter_entered.wait(timeout=2))
            scatter.x_axis.column.setCurrentText("z")
            coordinator.scatter_controller.request_plot()
            scatter_release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertEqual(coordinator.scatter_controller.current_spec.x, "z")

        histogram_entered = threading.Event()
        histogram_release = threading.Event()

        def controlled_histogram(dataframe, spec, mask=None, *, cancel=None):
            if spec.title == "":
                histogram_entered.set()
                histogram_release.wait(timeout=5)
                if cancel.is_set():
                    raise CancelledError()
            return prepare_histogram(dataframe, spec, mask, cancel=cancel)

        with patch(
            "csv_analyzer.qt_app.histogram_controller.prepare_histogram",
            controlled_histogram,
        ):
            coordinator.histogram_controller.request_plot()
            self.assertTrue(histogram_entered.wait(timeout=2))
            histogram.title.setText("B")
            coordinator.histogram_controller.request_plot()
            histogram_release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertEqual(coordinator.histogram_controller.current_spec.title, "B")


if __name__ == "__main__":
    unittest.main()
