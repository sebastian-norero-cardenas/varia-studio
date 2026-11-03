"""F3.B Qt table model, controller and integration tests."""

from __future__ import annotations

import csv
import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtCore import QEventLoop, QItemSelectionModel, Qt, QTimer
from PySide6.QtWidgets import QApplication

from csv_analyzer.data import CancelledError, Dataset, describe_columns
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.table import SearchMode, SortDirection, TableScope

APP = QApplication.instance() or QApplication(["varia-f3b-tests"])


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


def dataset(frame: pd.DataFrame, name="dataset.csv") -> Dataset:
    return Dataset(frame=frame, path=Path(name), columns=describe_columns(frame))


class RecordingShell(QtShell):
    def __init__(self):
        super().__init__()
        self.errors = []

    def show_error(self, text: str) -> None:
        self.errors.append(text)


class QtTableTests(unittest.TestCase):
    def make(self, *, loader=None):
        shell = RecordingShell()
        coordinator = QtCoordinator(shell, **({"loader": loader} if loader is not None else {}))
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

    def commit(self, coordinator, value: Dataset):
        ticket = coordinator.state.begin_load()
        self.assertTrue(coordinator.state.commit_dataset(ticket, value))
        APP.processEvents()

    def test_model_rows_columns_source_identity_headers_display_tooltip_and_flags(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame(
            {
                "big": pd.Series([9007199254740993, 2], dtype="int64"),
                "float": [np.inf, np.nan],
                "nullable": pd.Series([pd.NA, 4], dtype="Int64"),
            }
        )
        self.commit(coordinator, dataset(frame))
        model = coordinator.table_controller.model

        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.columnCount(), 4)
        self.assertEqual(model.headerData(0, Qt.Orientation.Horizontal), "Source Row ID")
        self.assertEqual(model.headerData(1, Qt.Orientation.Horizontal), "big")
        self.assertEqual(model.source_row_id_at(0), 1)
        self.assertEqual(model.dataset_position_at(1), 1)

        big = model.index(0, 1)
        self.assertEqual(model.data(big, Qt.ItemDataRole.DisplayRole), "9007199254740993")
        self.assertEqual(model.data(big, Qt.ItemDataRole.ToolTipRole), "9007199254740993")
        self.assertFalse(bool(model.flags(big) & Qt.ItemFlag.ItemIsEditable))
        self.assertIsNone(shell.table_view.table.indexWidget(big))
        self.assertEqual(model.data(model.index(0, 2)), "∞")
        self.assertEqual(model.data(model.index(1, 2)), "NaN")
        self.assertEqual(model.data(model.index(0, 3)), "∅")

    def test_visual_selection_and_sort_scope_preserve_source_ids_without_state_revision(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame({"value": [30, 10, 20], "text": ["c", "a", "b"]})
        self.commit(coordinator, dataset(frame))
        controller = coordinator.table_controller
        model = controller.model
        view = shell.table_view

        self.assertTrue(view.select_source_id(model, 2))
        before = (
            coordinator.state.revision,
            coordinator.state.dataset_revision,
            coordinator.state.selection_revision,
        )

        controller.set_sort(1, SortDirection.DESC)
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(model.row_map.positions.tolist(), [0, 2, 1])
        selected, current = view.capture_selection(model)
        self.assertEqual(selected, (2,))
        self.assertEqual(current, 2)

        controller.set_scope(TableScope.ALL)
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        selected, _ = view.capture_selection(model)
        self.assertEqual(selected, (2,))
        after = (
            coordinator.state.revision,
            coordinator.state.dataset_revision,
            coordinator.state.selection_revision,
        )
        self.assertEqual(after, before)

    def test_selected_scope_rebuilds_on_selection_revision_all_scope_does_not(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [3, 1, 2, 4]})))
        controller = coordinator.table_controller

        mask = np.array([True, False, True, False], dtype=np.bool_)
        revision = coordinator.state.revision
        self.assertTrue(coordinator.state.commit_filters(revision, (), mask))
        pump_until(lambda: controller.model.rowCount() == 2)
        self.assertEqual(controller.model.row_map.positions.tolist(), [0, 2])

        controller.set_scope(TableScope.ALL)
        row_map = controller.model.row_map
        revision = coordinator.state.revision
        new_mask = np.array([False, True, False, True], dtype=np.bool_)
        self.assertTrue(coordinator.state.commit_filters(revision, (), new_mask))
        APP.processEvents()
        self.assertIs(controller.model.row_map, row_map)
        self.assertEqual(controller.model.rowCount(), 4)

    def test_search_navigation_and_explicit_selected_to_all(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame({"text": ["alpha", "beta", "BETA", "delta"], "n": [1, 2, 3, 4]})
        self.commit(coordinator, dataset(frame))
        controller = coordinator.table_controller

        mask = np.array([True, False, False, True], dtype=np.bool_)
        self.assertTrue(coordinator.state.commit_filters(coordinator.state.revision, (), mask))
        pump_until(lambda: controller.model.rowCount() == 2)
        selection_revision = coordinator.state.selection_revision

        controller.set_search("beta", SearchMode.CONTAINS_CASE_INSENSITIVE)
        pump_until(lambda: controller.search_result is not None)
        self.assertEqual(controller.search_result.source_ids.tolist(), [2, 3])
        self.assertEqual(controller._visible_match_rows(), [])
        self.assertFalse(shell.table_view.show_all_button.isHidden())

        controller.show_matches_in_all()
        pump_until(lambda: controller.scope is TableScope.ALL)
        pump_until(lambda: shell.table_view.capture_selection(controller.model)[1] in (2, 3))
        self.assertEqual(coordinator.state.selection_revision, selection_revision)
        current = shell.table_view.capture_selection(controller.model)[1]
        controller.next_match()
        next_id = shell.table_view.capture_selection(controller.model)[1]
        self.assertNotEqual(next_id, current)
        controller.previous_match()
        self.assertEqual(shell.table_view.capture_selection(controller.model)[1], current)

    def test_go_to_source_id_switches_explicitly_and_invalid_input_keeps_scope(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [10, 20, 30]})))
        controller = coordinator.table_controller
        mask = np.array([True, False, True], dtype=np.bool_)
        self.assertTrue(coordinator.state.commit_filters(coordinator.state.revision, (), mask))
        pump_until(lambda: controller.model.rowCount() == 2)

        controller.go_to_source_id("2")
        self.assertIs(controller.scope, TableScope.ALL)
        self.assertEqual(shell.table_view.capture_selection(controller.model)[1], 2)

        controller.set_scope(TableScope.SELECTED)
        old_scope = controller.scope
        controller.go_to_source_id("999")
        self.assertIs(controller.scope, old_scope)
        self.assertIn("between 1 and 3", shell.table_view.feedback_label.text())

    def test_copy_uses_underlying_values_exactly_and_does_not_change_app_state(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame(
            {
                "big": pd.Series([9007199254740993], dtype="int64"),
                "float": [0.1],
                "special": ['a\tb\n"quoted"'],
            }
        )
        self.commit(coordinator, dataset(frame))
        controller = coordinator.table_controller
        self.assertTrue(shell.table_view.select_source_id(controller.model, 1))
        before = (
            coordinator.state.revision,
            coordinator.state.dataset_revision,
            coordinator.state.selection_revision,
        )
        text = controller.copy_selection()
        parsed = list(csv.reader(io.StringIO(text), delimiter="\t", quotechar='"'))
        self.assertEqual(parsed[0][0], "1")
        self.assertEqual(parsed[0][1], "9007199254740993")
        self.assertEqual(parsed[0][2], repr(0.1))
        self.assertEqual(parsed[0][3], 'a\tb\n"quoted"')
        self.assertEqual(
            (
                coordinator.state.revision,
                coordinator.state.dataset_revision,
                coordinator.state.selection_revision,
            ),
            before,
        )

    def test_dataset_replacement_resets_table_and_drops_old_dataset_reference(self):
        coordinator, shell = self.make()
        first = dataset(pd.DataFrame({"a": [2, 1]}), "a.csv")
        second = dataset(pd.DataFrame({"b": [7, 8, 9]}), "b.csv")
        self.commit(coordinator, first)
        controller = coordinator.table_controller
        controller.set_sort(1, SortDirection.ASC)
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        controller.set_search("2", SearchMode.EXACT)
        pump_until(lambda: controller.search_result is not None)
        shell.table_view.select_source_id(controller.model, 1)

        self.commit(coordinator, second)
        self.assertIs(controller.model.dataset, second)
        self.assertIsNot(controller.model.dataset, first)
        self.assertIs(controller.scope, TableScope.SELECTED)
        self.assertIsNone(controller.sort_spec)
        self.assertIsNone(controller.search_spec)
        self.assertEqual(controller.model.rowCount(), 3)
        self.assertEqual(shell.table_view.capture_selection(controller.model)[0], ())

    def test_stale_search_result_from_dataset_a_cannot_publish_after_b(self):
        coordinator, shell = self.make()
        first = dataset(pd.DataFrame({"text": ["needle", "x"]}), "a.csv")
        second = dataset(pd.DataFrame({"text": ["new"]}), "b.csv")
        self.commit(coordinator, first)
        controller = coordinator.table_controller
        entered = threading.Event()
        release = threading.Event()

        from csv_analyzer.table import search_frame as real_search

        def slow_search(frame, spec, *, dataset_revision, cancel=None):
            entered.set()
            release.wait(timeout=5)
            return real_search(
                frame,
                spec,
                dataset_revision=dataset_revision,
                cancel=None,
            )

        with patch("csv_analyzer.qt_app.table_controller.search_frame", slow_search):
            controller.set_search("needle", SearchMode.EXACT)
            self.assertTrue(entered.wait(timeout=2))
            self.commit(coordinator, second)
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertIs(controller.model.dataset, second)
        self.assertIsNone(controller.search_result)
        self.assertIsNone(controller.search_spec)

    def test_failed_and_cancelled_replacement_preserve_active_dataset_and_table(self):
        active = dataset(pd.DataFrame({"x": [1, 2]}), "a.csv")
        entered = threading.Event()

        def loader(path, options, *, cancel):
            if Path(path).name == "bad.csv":
                raise ValueError("bad replacement")
            if Path(path).name == "cancel.csv":
                entered.set()
                if not cancel.wait(timeout=5):
                    raise RuntimeError("cancel not received")
                raise CancelledError()
            return active

        coordinator, shell = self.make(loader=loader)
        self.commit(coordinator, active)
        controller = coordinator.table_controller

        coordinator.load_path("bad.csv")
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIs(coordinator.state.dataset, active)
        self.assertIs(controller.model.dataset, active)

        coordinator.load_path("cancel.csv")
        self.assertTrue(entered.wait(timeout=2))
        coordinator.cancel_all()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIs(coordinator.state.dataset, active)
        self.assertIs(controller.model.dataset, active)

    def test_zero_row_and_zero_selected_states(self):
        coordinator, shell = self.make()
        empty = dataset(pd.DataFrame({"x": pd.Series([], dtype="int64")}), "empty.csv")
        self.commit(coordinator, empty)
        self.assertEqual(coordinator.table_controller.model.rowCount(), 0)
        self.assertIn("0 rows", shell.table_view.feedback_label.text())

        nonempty = dataset(pd.DataFrame({"x": [1, 2]}), "nonempty.csv")
        self.commit(coordinator, nonempty)
        mask = np.array([False, False], dtype=np.bool_)
        self.assertTrue(coordinator.state.commit_filters(coordinator.state.revision, (), mask))
        pump_until(lambda: coordinator.table_controller.model.rowCount() == 0)
        self.assertIn("Selected rows contains 0 rows", shell.table_view.feedback_label.text())


if __name__ == "__main__":
    unittest.main()
