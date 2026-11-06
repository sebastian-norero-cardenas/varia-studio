"""F3.G Qt export workflows, atomic overwrite confirmation and lifecycle."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from csv_analyzer.data import DataError, Dataset, describe_columns
from csv_analyzer.exports import export_statistics
from csv_analyzer.plots import export_figure
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.exporting import export_with_confirmation
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule

APP = QApplication.instance() or QApplication(["varia-f3g-tests"])


def pump_until(predicate, timeout_ms=8000):
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


def dataset(frame: pd.DataFrame, name="export.csv") -> Dataset:
    return Dataset(frame=frame, path=Path(name), columns=describe_columns(frame))


def simple_figure(label: str) -> Figure:
    figure = Figure(figsize=(3, 2), dpi=100)
    axis = figure.add_subplot(111)
    axis.plot([0, 1], [0, 1])
    axis.set_title(label)
    return figure


class RecordingShell(QtShell):
    def __init__(self):
        super().__init__()
        self.errors = []

    def show_error(self, text: str) -> None:
        self.errors.append(text)


class QtExportTests(unittest.TestCase):
    def make(self, **kwargs):
        shell = RecordingShell()
        coordinator = QtCoordinator(shell, **kwargs)
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

    def commit(self, coordinator, frame, name="export.csv"):
        ticket = coordinator.state.begin_load()
        self.assertTrue(coordinator.state.commit_dataset(ticket, dataset(frame, name)))
        APP.processEvents()

    def compute_statistics(self, coordinator):
        coordinator.statistics_controller.request_compute()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        APP.processEvents()
        return coordinator.statistics_controller.current_result

    def test_figure_button_state_and_current_figure_replacement(self):
        _coordinator, shell = self.make()
        panel = shell.scatter_view.panel
        self.assertFalse(panel.export_button.isEnabled())

        figure_a = simple_figure("A")
        panel.show_figure(figure_a, "A")
        self.assertTrue(panel.export_button.isEnabled())

        figure_b = simple_figure("B")
        panel.show_figure(figure_b, "B")
        self.assertIs(panel.figure, figure_b)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "current.png"
            with (
                patch(
                    "csv_analyzer.qt_app.plot_panel.choose_save_path",
                    return_value=target,
                ),
                patch(
                    "csv_analyzer.qt_app.plot_panel.export_figure",
                    return_value=target,
                ) as writer,
            ):
                panel.export_current_figure()
            self.assertIs(writer.call_args.args[0], figure_b)

        panel.clear()
        self.assertFalse(panel.export_button.isEnabled())

    def test_figure_png_pdf_svg_and_dpi_values(self):
        _coordinator, shell = self.make()
        panel = shell.scatter_view.panel
        panel.show_figure(simple_figure("formats"), "formats")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix in (".png", ".pdf", ".svg"):
                target = root / f"figure{suffix}"
                with patch(
                    "csv_analyzer.qt_app.plot_panel.choose_save_path",
                    return_value=target,
                ):
                    panel.export_current_figure()
                self.assertTrue(target.exists())
                self.assertGreater(target.stat().st_size, 0)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dpi.png"
            for dpi in (150, 300, 600):
                panel.dpi.setCurrentText(f"{dpi} DPI")
                with (
                    patch(
                        "csv_analyzer.qt_app.plot_panel.choose_save_path",
                        return_value=target,
                    ),
                    patch(
                        "csv_analyzer.qt_app.plot_panel.export_figure",
                        return_value=target,
                    ) as writer,
                ):
                    panel.export_current_figure()
                self.assertEqual(writer.call_args.kwargs["dpi"], dpi)
        self.assertEqual(panel.dpi.itemData(1), 300)

    def test_scatter_histogram_independence_and_figure_failure_preserves_owner(self):
        _coordinator, shell = self.make()
        scatter = shell.scatter_view.panel
        histogram = shell.histogram_view.panel
        scatter_figure = simple_figure("scatter")
        histogram_figure = simple_figure("histogram")
        scatter.show_figure(scatter_figure, "scatter")
        histogram.show_figure(histogram_figure, "histogram")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = iter((root / "scatter.png", root / "histogram.png"))
            exported = []

            def record(figure, path, **_kwargs):
                exported.append(figure)
                return Path(path)

            with (
                patch(
                    "csv_analyzer.qt_app.plot_panel.choose_save_path",
                    side_effect=lambda **_kwargs: next(paths),
                ),
                patch("csv_analyzer.qt_app.plot_panel.export_figure", side_effect=record),
            ):
                scatter.export_current_figure()
                histogram.export_current_figure()

            self.assertEqual(exported, [scatter_figure, histogram_figure])

            target = root / "broken.png"
            with (
                patch(
                    "csv_analyzer.qt_app.plot_panel.choose_save_path",
                    return_value=target,
                ),
                patch(
                    "csv_analyzer.qt_app.plot_panel.export_figure",
                    side_effect=DataError("simulated figure failure"),
                ),
            ):
                scatter.export_current_figure()
            self.assertIs(scatter.figure, scatter_figure)
            self.assertTrue(scatter.export_button.isEnabled())
            self.assertTrue(shell.errors)
            self.assertIn("simulated figure failure", shell.errors[-1])

    def test_statistics_csv_full_precision_and_invalidation(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame(
            {
                "x": [1.234567890123456, 2.345678901234567, 3.456789012345678],
                "y": [2.0, 4.0, 6.0],
            }
        )
        self.commit(coordinator, frame)
        result = self.compute_statistics(coordinator)
        view = shell.statistics_view
        self.assertIsNotNone(result)
        self.assertTrue(view.export_button.isEnabled())

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "statistics.csv"
            with patch(
                "csv_analyzer.qt_app.statistics_controller.choose_save_path",
                return_value=target,
            ):
                coordinator.statistics_controller.request_export()
            actual = pd.read_csv(target)
            self.assertEqual(list(actual.columns), list(result.columns))
            self.assertIn("ddof", actual.columns)
            self.assertGreater(len(target.read_text(encoding="utf-8").split(".")[1]), 6)

        view.ddof.setCurrentIndex(1)
        self.assertFalse(view.export_button.isEnabled())
        self.compute_statistics(coordinator)
        self.assertTrue(view.export_button.isEnabled())

        snapshot = coordinator.state.snapshot()
        mask = np.array([True, True, False])
        self.assertTrue(
            coordinator.state.commit_filters(
                snapshot.revision,
                (FilterRule("x", upper=2.5),),
                mask,
            )
        )
        self.assertFalse(view.export_button.isEnabled())

        self.compute_statistics(coordinator)
        self.assertTrue(view.export_button.isEnabled())
        self.commit(coordinator, pd.DataFrame({"x": [1.0], "y": [2.0]}), "replacement.csv")
        self.assertFalse(view.export_button.isEnabled())

    def test_statistics_failure_preserves_result_and_failed_load_preserves_export(self):
        def failing_loader(*_args, **_kwargs):
            raise DataError("load failed")

        coordinator, shell = self.make(loader=failing_loader)
        self.commit(coordinator, pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 4.0]}))
        result = self.compute_statistics(coordinator)
        view = shell.statistics_view

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "broken.csv"
            with (
                patch(
                    "csv_analyzer.qt_app.statistics_controller.choose_save_path",
                    return_value=target,
                ),
                patch(
                    "csv_analyzer.qt_app.statistics_controller.export_statistics",
                    side_effect=DataError("simulated csv failure"),
                ),
            ):
                coordinator.statistics_controller.request_export()
        self.assertIs(coordinator.statistics_controller.current_result, result)
        self.assertTrue(view.export_button.isEnabled())
        self.assertIn("simulated csv failure", shell.errors[-1])

        shell.errors.clear()
        coordinator._present_error = lambda _error: None
        coordinator.load_path("missing.csv")
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIs(coordinator.statistics_controller.current_result, result)
        self.assertTrue(view.export_button.isEnabled())

    def test_empty_statistics_result_is_not_exportable(self):
        coordinator, shell = self.make()
        self.commit(coordinator, pd.DataFrame({"x": pd.Series(dtype="float64")}))
        result = self.compute_statistics(coordinator)
        self.assertIsNotNone(result)
        self.assertFalse(result.empty)
        self.assertTrue(shell.statistics_view.export_button.isEnabled())

        shell.statistics_view.mode.setCurrentIndex(2)
        self.assertFalse(shell.statistics_view.export_button.isEnabled())

    def test_existing_destination_decline_and_accept(self):
        frame = pd.DataFrame({"x": [1.25]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.csv"
            path.write_text("original", encoding="utf-8")
            parent = shell = QtShell()
            self.addCleanup(shell.deleteLater)

            with patch.object(
                QMessageBox,
                "exec",
                return_value=QMessageBox.StandardButton.No,
            ):
                result = export_with_confirmation(
                    parent,
                    path,
                    lambda overwrite: export_statistics(frame, path, overwrite=overwrite),
                )
            self.assertIsNone(result)
            self.assertEqual(path.read_text(encoding="utf-8"), "original")

            with patch.object(
                QMessageBox,
                "exec",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                result = export_with_confirmation(
                    parent,
                    path,
                    lambda overwrite: export_statistics(frame, path, overwrite=overwrite),
                )
            self.assertEqual(result, path.resolve())
            pd.testing.assert_frame_equal(pd.read_csv(path), frame)

    def test_concurrent_destination_race_decline_and_accept(self):
        frame = pd.DataFrame({"x": [7.5]})
        for answer, should_replace in (
            (QMessageBox.StandardButton.No, False),
            (QMessageBox.StandardButton.Yes, True),
        ):
            with self.subTest(answer=answer):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "race.csv"
                    calls = []
                    parent = QtShell()
                    self.addCleanup(parent.deleteLater)

                    def writer(overwrite):
                        calls.append(overwrite)
                        if not overwrite and not path.exists():
                            path.write_text("other writer", encoding="utf-8")
                        return export_statistics(frame, path, overwrite=overwrite)

                    with patch.object(QMessageBox, "exec", return_value=answer):
                        result = export_with_confirmation(parent, path, writer)

                    if should_replace:
                        self.assertEqual(calls, [False, True])
                        self.assertEqual(result, path.resolve())
                        pd.testing.assert_frame_equal(pd.read_csv(path), frame)
                    else:
                        self.assertEqual(calls, [False])
                        self.assertIsNone(result)
                        self.assertEqual(path.read_text(encoding="utf-8"), "other writer")

    def test_cancel_save_dialog_and_extension_errors(self):
        _coordinator, shell = self.make()
        panel = shell.scatter_view.panel
        panel.show_figure(simple_figure("cancel"), "cancel")
        with (
            patch("csv_analyzer.qt_app.plot_panel.choose_save_path", return_value=None),
            patch("csv_analyzer.qt_app.plot_panel.export_figure") as writer,
        ):
            panel.export_current_figure()
        writer.assert_not_called()
        self.assertIsNotNone(panel.figure)

        with tempfile.TemporaryDirectory() as directory:
            bad_figure = Path(directory) / "figure.txt"
            with patch(
                "csv_analyzer.qt_app.plot_panel.choose_save_path",
                return_value=bad_figure,
            ):
                panel.export_current_figure()
            self.assertIn(".png", shell.errors[-1])

            coordinator = _coordinator
            self.commit(coordinator, pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 4.0]}))
            self.compute_statistics(coordinator)
            bad_stats = Path(directory) / "stats.txt"
            with patch(
                "csv_analyzer.qt_app.statistics_controller.choose_save_path",
                return_value=bad_stats,
            ):
                coordinator.statistics_controller.request_export()
            self.assertIn(".csv", shell.errors[-1])

    def test_filter_invalidation_disables_both_plot_exports(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 6.0]}),
        )
        coordinator.scatter_controller.request_plot()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        coordinator.histogram_controller.request_plot()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertTrue(shell.scatter_view.panel.export_button.isEnabled())
        self.assertTrue(shell.histogram_view.panel.export_button.isEnabled())

        snapshot = coordinator.state.snapshot()
        self.assertTrue(
            coordinator.state.commit_filters(
                snapshot.revision,
                (FilterRule("x", upper=2.0),),
                np.array([True, True, False]),
            )
        )
        self.assertFalse(shell.scatter_view.panel.export_button.isEnabled())
        self.assertFalse(shell.histogram_view.panel.export_button.isEnabled())

    def test_export_does_not_mutate_app_state_or_owned_objects(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 4.0]})
        self.commit(coordinator, frame)
        coordinator.scatter_controller.request_plot()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        result = self.compute_statistics(coordinator)
        figure = shell.scatter_view.panel.figure
        before = coordinator.state.snapshot()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "csv_analyzer.qt_app.plot_panel.choose_save_path",
                return_value=root / "figure.png",
            ):
                shell.scatter_view.panel.export_current_figure()
            with patch(
                "csv_analyzer.qt_app.statistics_controller.choose_save_path",
                return_value=root / "statistics.csv",
            ):
                coordinator.statistics_controller.request_export()

        after = coordinator.state.snapshot()
        self.assertEqual(
            (after.revision, after.dataset_revision, after.selection_revision, after.filters),
            (before.revision, before.dataset_revision, before.selection_revision, before.filters),
        )
        self.assertIs(after.mask, before.mask)
        self.assertIs(after.dataset.frame, before.dataset.frame)
        self.assertIs(shell.scatter_view.panel.figure, figure)
        self.assertIs(coordinator.statistics_controller.current_result, result)

    def test_close_with_save_dialog_open_rejects_dialog_and_closes_cleanly(self):
        coordinator, shell = self.make()
        panel = shell.scatter_view.panel
        panel.show_figure(simple_figure("close"), "close")

        QTimer.singleShot(0, coordinator.begin_close)
        panel.export_current_figure()
        pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        self.assertIsNone(panel.figure)
        self.assertFalse(panel.export_button.isEnabled())

    def test_figure_export_runs_on_gui_thread(self):
        _coordinator, shell = self.make()
        panel = shell.scatter_view.panel
        panel.show_figure(simple_figure("thread"), "thread")
        gui_thread = threading.get_ident()
        seen = []

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "thread.png"

            def writer(figure, path, **kwargs):
                seen.append(threading.get_ident())
                return export_figure(figure, path, **kwargs)

            with (
                patch(
                    "csv_analyzer.qt_app.plot_panel.choose_save_path",
                    return_value=target,
                ),
                patch("csv_analyzer.qt_app.plot_panel.export_figure", side_effect=writer),
            ):
                panel.export_current_figure()
        self.assertEqual(seen, [gui_thread])


if __name__ == "__main__":
    unittest.main()
