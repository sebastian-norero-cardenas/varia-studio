"""F5.A-UX1 productization polish tests."""

from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget

from csv_analyzer.data import Dataset, describe_columns
from csv_analyzer.plots import prepare_histogram, prepare_scatter
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule
from csv_analyzer.table import SearchMode, SortDirection, SortSpec, TableScope

APP = QApplication.instance() or QApplication(["varia-f5a-ux-tests"])


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


def make_dataset(value: pd.DataFrame, name="ux.csv") -> Dataset:
    return Dataset(frame=value, path=Path(name), columns=describe_columns(value))


def is_descendant(widget: QWidget, ancestor: QWidget) -> bool:
    current = widget.parentWidget()
    while current is not None:
        if current is ancestor:
            return True
        current = current.parentWidget()
    return False


class RecordingShell(QtShell):
    def __init__(self):
        super().__init__()
        self.errors: list[str] = []

    def show_error(self, text: str) -> None:
        self.errors.append(text)


class UxProductizationTests(unittest.TestCase):
    def make(self, *, max_workers=2):
        shell = RecordingShell()
        coordinator = QtCoordinator(shell, max_workers=max_workers)
        coordinator.start()
        shell.show()
        APP.processEvents()
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

    def commit(self, coordinator, value=None, name="ux.csv"):
        value = frame() if value is None else value
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

    def test_plot_footers_are_fixed_outside_scroll_and_reset_wording(self):
        _coordinator, shell = self.make()
        for view in (shell.scatter_view, shell.histogram_view):
            self.assertFalse(is_descendant(view.local_status, view.controls_scroll))
            self.assertFalse(is_descendant(view.plot_button, view.controls_scroll))
            self.assertFalse(is_descendant(view.reset_button, view.controls_scroll))
            self.assertEqual(view.reset_button.text(), "Reset all settings")

    def test_scatter_result_states_dirty_previous_current_reset_and_export_identity(self):
        coordinator, shell = self.make()
        self.commit(coordinator)
        view = shell.scatter_view

        self.plot_scatter(coordinator)
        figure_a = view.panel.figure
        self.assertEqual(view.panel.result_state.text(), "Current")
        self.assertEqual(view.plot_button.text(), "Update plot")

        index = view.x_axis.column.findText("z")
        view.x_axis.column.setCurrentIndex(index)
        view.x_axis.column.activated.emit(index)
        self.assertIs(view.panel.figure, figure_a)
        self.assertEqual(view.panel.result_state.text(), "Changes not applied")
        self.assertEqual(view.plot_button.text(), "Update plot")

        entered = threading.Event()
        release = threading.Event()

        def blocked_prepare(dataframe, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            return prepare_scatter(dataframe, spec, mask, cancel=cancel)

        view.x_axis.column.setCurrentText("x")
        with patch("csv_analyzer.qt_app.scatter_controller.prepare_scatter", blocked_prepare):
            coordinator.scatter_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            view.x_axis.column.setCurrentText("z")
            view.x_axis.column.activated.emit(view.x_axis.column.currentIndex())
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            APP.processEvents()

        figure_previous = view.panel.figure
        self.assertEqual(view.panel.result_state.text(), "Previous result")
        self.assertEqual(coordinator.scatter_controller.current_spec.x, "x")
        self.assertEqual(view.x_axis.column.currentText(), "z")

        exported = []
        with (
            patch(
                "csv_analyzer.qt_app.plot_panel.choose_save_path",
                return_value=Path("shown.png"),
            ),
            patch(
                "csv_analyzer.qt_app.plot_panel.export_figure",
                side_effect=lambda figure, path, **_kwargs: exported.append(figure) or Path(path),
            ),
        ):
            view.panel.export_current_figure()
        self.assertEqual(exported, [figure_previous])

        self.plot_scatter(coordinator)
        self.assertEqual(view.panel.result_state.text(), "Current")
        self.assertEqual(coordinator.scatter_controller.current_spec.x, "z")

        coordinator.scatter_controller.reset_settings()
        self.assertFalse(view.panel.has_figure)
        self.assertEqual(view.panel.result_state.text(), "No result")
        self.assertEqual(view.plot_button.text(), "Plot relationships")

    def test_histogram_result_states_dirty_previous_current_reset(self):
        coordinator, shell = self.make()
        self.commit(coordinator)
        view = shell.histogram_view

        self.plot_histogram(coordinator)
        figure_a = view.panel.figure
        self.assertEqual(view.panel.result_state.text(), "Current")

        view.title.setText("B")
        view.title.textEdited.emit("B")
        self.assertIs(view.panel.figure, figure_a)
        self.assertEqual(view.panel.result_state.text(), "Changes not applied")

        entered = threading.Event()
        release = threading.Event()
        view.title.setText("A")

        def blocked_prepare(dataframe, spec, mask=None, *, cancel=None):
            entered.set()
            release.wait(timeout=5)
            return prepare_histogram(dataframe, spec, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.histogram_controller.prepare_histogram", blocked_prepare):
            coordinator.histogram_controller.request_plot()
            self.assertTrue(entered.wait(timeout=2))
            view.title.setText("B")
            view.title.textEdited.emit("B")
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            APP.processEvents()

        self.assertEqual(view.panel.result_state.text(), "Previous result")
        self.assertEqual(coordinator.histogram_controller.current_spec.title, "A")
        self.assertEqual(view.title.text(), "B")

        self.plot_histogram(coordinator)
        self.assertEqual(view.panel.result_state.text(), "Current")
        self.assertEqual(coordinator.histogram_controller.current_spec.title, "B")

        coordinator.histogram_controller.reset_settings()
        self.assertEqual(view.panel.result_state.text(), "No result")
        self.assertEqual(view.plot_button.text(), "Plot distribution")

    def test_data_empty_state_and_commit_enablement(self):
        coordinator, shell = self.make()
        view = shell.table_view

        for widget in (
            view.scope_combo,
            view.reset_sort_button,
            view.search_edit,
            view.search_mode,
            view.go_edit,
            view.go_button,
            view.table,
        ):
            self.assertFalse(widget.isEnabled())
        self.assertFalse(view.filter_button.isEnabled())
        self.assertFalse(view.copy_action.isEnabled())
        self.assertEqual(view.feedback_label.text(), "Open a dataset to inspect rows.")

        self.commit(coordinator)
        for widget in (
            view.scope_combo,
            view.reset_sort_button,
            view.search_edit,
            view.search_mode,
            view.go_edit,
            view.go_button,
            view.table,
        ):
            self.assertTrue(widget.isEnabled())
        self.assertTrue(view.filter_button.isEnabled())
        self.assertTrue(view.copy_action.isEnabled())

    def test_data_sort_search_scope_pending_and_contextual_failures(self):
        coordinator, shell = self.make()
        self.commit(coordinator)
        controller = coordinator.table_controller
        view = shell.table_view

        sort_entered = threading.Event()
        sort_release = threading.Event()
        from csv_analyzer.qt_app import table_controller as table_module

        real_build = table_module.build_row_map

        def blocked_sort(*args, **kwargs):
            cancel = kwargs.get("cancel")
            if kwargs.get("sort_spec") is not None:
                sort_entered.set()
                sort_release.wait(timeout=5)
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("sort cancelled")
            return real_build(*args, **kwargs)

        with patch("csv_analyzer.qt_app.table_controller.build_row_map", blocked_sort):
            controller.set_sort(1, SortDirection.ASC)
            self.assertTrue(sort_entered.wait(timeout=2))
            self.assertEqual(view.feedback_label.text(), "Sorting…")
            sort_release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(view.feedback_label.text(), "Ready")

        search_entered = threading.Event()
        search_release = threading.Event()
        from csv_analyzer.qt_app import table_controller as table_module_2

        real_search = table_module_2.search_frame

        def blocked_search(*args, **kwargs):
            search_entered.set()
            search_release.wait(timeout=5)
            return real_search(*args, **kwargs)

        with patch("csv_analyzer.qt_app.table_controller.search_frame", blocked_search):
            controller.set_search("2", SearchMode.CONTAINS_CASE_INSENSITIVE)
            self.assertTrue(search_entered.wait(timeout=2))
            self.assertEqual(view.match_label.text(), "Searching…")
            self.assertEqual(view.feedback_label.text(), "Searching…")
            search_release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertNotEqual(view.match_label.text(), "Searching…")
        self.assertEqual(view.feedback_label.text(), "Ready")

        controller.sort_spec = SortSpec("x", SortDirection.ASC)
        with patch(
            "csv_analyzer.qt_app.table_controller.build_row_map",
            side_effect=RuntimeError("projection exploded"),
        ):
            controller.set_scope(TableScope.ALL)
            self.assertEqual(view.feedback_label.text(), "Updating view…")
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIn("Could not update the table view: projection exploded", view.feedback_label.text())
        self.assertEqual(view.scope_combo.currentData(), controller.model.row_map.scope)

        with patch(
            "csv_analyzer.qt_app.table_controller.build_row_map",
            side_effect=RuntimeError("sort exploded"),
        ):
            controller.set_sort(1, SortDirection.DESC)
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIn("Could not sort the table: sort exploded", view.feedback_label.text())

        with patch(
            "csv_analyzer.qt_app.table_controller.search_frame",
            side_effect=RuntimeError("search exploded"),
        ):
            controller.set_search("boom", SearchMode.CONTAINS_CASE_INSENSITIVE)
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(view.match_label.text(), "")
        self.assertIn("Could not search the dataset: search exploded", view.feedback_label.text())

    def test_data_stale_search_result_is_ignored(self):
        coordinator, shell = self.make(max_workers=2)
        self.commit(coordinator)
        controller = coordinator.table_controller
        view = shell.table_view
        first_entered = threading.Event()
        first_release = threading.Event()

        from csv_analyzer.qt_app import table_controller as table_module

        real_search = table_module.search_frame

        def controlled_search(dataframe, spec, **kwargs):
            if spec.text == "1":
                first_entered.set()
                first_release.wait(timeout=5)
            return real_search(dataframe, spec, **kwargs)

        with patch("csv_analyzer.qt_app.table_controller.search_frame", controlled_search):
            controller.set_search("1", SearchMode.CONTAINS_CASE_INSENSITIVE)
            self.assertTrue(first_entered.wait(timeout=2))
            controller.set_search("2", SearchMode.CONTAINS_CASE_INSENSITIVE)
            first_release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertEqual(controller.search_spec.text, "2")
        self.assertIsNotNone(controller.search_result)
        self.assertEqual(view.feedback_label.text(), "Ready")

    def test_statistics_controls_are_contextual(self):
        coordinator, shell = self.make()
        self.commit(coordinator)
        view = shell.statistics_view

        view.mode.setCurrentIndex(view.mode.findData("numeric"))
        APP.processEvents()
        self.assertFalse(view.ddof.isHidden())
        self.assertTrue(view.correlation_row.isHidden())

        view.mode.setCurrentIndex(view.mode.findData("categorical"))
        APP.processEvents()
        self.assertTrue(view.ddof.isHidden())
        self.assertTrue(view.correlation_row.isHidden())

        view.mode.setCurrentIndex(view.mode.findData("correlation"))
        APP.processEvents()
        self.assertTrue(view.ddof.isHidden())
        self.assertFalse(view.correlation_row.isHidden())
        self.assertIn("x", view.columns_label.text())

    def test_shell_ctrl_f_cancel_tooltips_notices_and_minimum_size(self):
        coordinator, shell = self.make()
        self.commit(coordinator)
        shell.statistics_view.setFocus()
        shell.tabs.setCurrentWidget(shell.statistics_view)
        shell.table_view.set_search_text("existing")

        shell.find_action.trigger()
        APP.processEvents()
        self.assertIs(shell.tabs.currentWidget(), shell.data_tab)
        self.assertTrue(shell.table_view.search_edit.hasFocus())
        self.assertEqual(shell.table_view.search_edit.selectedText(), "existing")

        self.assertEqual(shell.cancel_button.text(), "Cancel tasks")
        self.assertEqual(shell.cancel_button.toolTip(), "Cancels all running operations")
        self.assertLessEqual(shell.minimumWidth(), 1100)
        self.assertLessEqual(shell.minimumHeight(), 700)
        self.assertGreaterEqual(shell.minimumWidth(), 800)
        self.assertGreaterEqual(shell.minimumHeight(), 500)

        long_name = "very-" + "long-" * 30 + "dataset.csv"
        shell.set_dataset(long_name, 12, 4, missing=2, infinite=1, selected=10)
        self.assertEqual(shell.dataset_label.toolTip(), long_name)
        self.assertIn("12 rows", shell.dataset_metrics_label.text())

        long_status = "status " * 80
        shell.set_status(long_status)
        self.assertEqual(shell.status_label.toolTip(), long_status)

        shell.set_dataset("clean.csv", 12, 4, notices=())
        self.assertTrue(shell.notices_widget.isHidden())
        shell.set_dataset("notices.csv", 12, 4, notices=("A notice", "Another notice"))
        self.assertFalse(shell.notices_widget.isHidden())
        self.assertEqual(shell.notices_button.text(), "Import notices…")

    def test_column_header_tooltip_uses_dataset_metadata(self):
        coordinator, shell = self.make()
        value = pd.DataFrame(
            {
                "numeric": [1.0, np.nan, np.inf],
                "text": ["a", None, "c"],
            }
        )
        self.commit(coordinator, value)
        model = coordinator.table_controller.model

        numeric_tip = model.headerData(
            1,
            Qt.Orientation.Horizontal,
            Qt.ItemDataRole.ToolTipRole,
        )
        self.assertIn("numeric", numeric_tip)
        self.assertIn("Kind: numeric", numeric_tip)
        self.assertIn("Type:", numeric_tip)
        self.assertIn("Missing: 1", numeric_tip)
        self.assertIn("Infinite: 1", numeric_tip)

        text_tip = model.headerData(
            2,
            Qt.Orientation.Horizontal,
            Qt.ItemDataRole.ToolTipRole,
        )
        self.assertIn("Kind: text", text_tip)
        self.assertIn("Missing: 1", text_tip)
        self.assertNotIn("Infinite:", text_tip)

    def test_filter_editor_uses_clear_editor_wording(self):
        coordinator, shell = self.make()
        self.commit(coordinator)
        dialog = coordinator.open_filter_editor()
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.new_button.text(), "Clear editor")
        dialog.reject()


if __name__ == "__main__":
    unittest.main()
