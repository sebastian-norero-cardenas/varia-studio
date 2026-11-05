"""F3.C Qt filter editor, async commit and F3.B integration tests."""

from __future__ import annotations

import os
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtCore import QEventLoop, QItemSelectionModel, QTimer
from PySide6.QtWidgets import QApplication

from csv_analyzer.analysis import build_filter_mask as real_build_filter_mask
from csv_analyzer.data import CancelledError, DataError, Dataset, describe_columns
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.filter_dialog import FilterDialog, parse_filter_bound
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule
from csv_analyzer.table import SearchMode, SortDirection, TableScope

APP = QApplication.instance() or QApplication(["varia-f3c-tests"])


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


class QtFilterTests(unittest.TestCase):
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

    def apply_and_wait(self, coordinator, rules):
        self.assertTrue(coordinator.apply_filters(tuple(rules)))
        pump_until(lambda: coordinator.runner.activity.physical == 0)

    def test_filter_button_enablement_and_label(self):
        coordinator, shell = self.make()
        self.assertFalse(shell.table_view.filter_button.isEnabled())
        self.assertEqual(shell.table_view.filter_button.text(), "Filters · none")

        self.commit(coordinator, dataset(pd.DataFrame({"name": ["a", "b"]})))
        self.assertFalse(shell.table_view.filter_button.isEnabled())

        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2], "flag": [True, False]})))
        self.assertTrue(shell.table_view.filter_button.isEnabled())
        self.apply_and_wait(coordinator, (FilterRule("x", lower=2),))
        self.assertEqual(shell.table_view.filter_button.text(), "Filters · 1")

    def test_parse_bounds_preserves_integer_precision_and_rejects_nonfinite_float(self):
        self.assertEqual(
            parse_filter_bound("9007199254740993", "Minimum", exact=True),
            9007199254740993,
        )
        self.assertEqual(
            parse_filter_bound("9007199254740992.5", "Minimum", exact=True),
            Decimal("9007199254740992.5"),
        )
        self.assertEqual(
            parse_filter_bound(str(2**63 + 1), "Minimum", exact=True),
            2**63 + 1,
        )
        self.assertEqual(parse_filter_bound("1.25", "Minimum", exact=False), 1.25)
        for value in ("nan", "inf", "-inf", "bad"):
            with self.subTest(value=value), self.assertRaises(DataError):
                parse_filter_bound(value, "Minimum", exact=False)

    def test_editor_staging_add_update_remove_new_and_cancel(self):
        coordinator, shell = self.make()
        self.commit(
            coordinator,
            dataset(pd.DataFrame({"x": [1, 2, 3], "y": [3.0, 4.0, 5.0]})),
        )
        before = coordinator.state.snapshot()
        dialog = coordinator.open_filter_editor()
        self.assertIsInstance(dialog, FilterDialog)
        dialog.column_combo.setCurrentText("x")
        dialog.minimum_edit.setText("2")
        dialog.add_condition()
        self.assertEqual(dialog.staged_rules, (FilterRule("x", 2, None),))

        dialog.rules_table.selectRow(0)
        APP.processEvents()
        dialog.minimum_edit.setText("1")
        dialog.maximum_edit.setText("2")
        dialog.update_condition()
        self.assertEqual(dialog.staged_rules, (FilterRule("x", 1, 2),))

        dialog.rules_table.selectRow(0)
        dialog.remove_condition()
        self.assertEqual(dialog.staged_rules, ())
        dialog.column_combo.setCurrentText("y")
        dialog.maximum_edit.setText("4")
        dialog.add_condition()
        dialog.new_condition()
        self.assertEqual(dialog.minimum_edit.text(), "")
        self.assertEqual(dialog.maximum_edit.text(), "")
        dialog.reject()
        APP.processEvents()

        after = coordinator.state.snapshot()
        self.assertEqual(after.filters, before.filters)
        self.assertIs(after.mask, before.mask)
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(after.selection_revision, before.selection_revision)

    def test_clear_staged_then_cancel_keeps_committed_state(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2, 3]})))
        self.apply_and_wait(coordinator, (FilterRule("x", lower=2),))
        before = coordinator.state.snapshot()
        dialog = coordinator.open_filter_editor()
        self.assertEqual(dialog.staged_rules, before.filters)
        dialog.clear_conditions()
        self.assertEqual(dialog.staged_rules, ())
        dialog.reject()
        APP.processEvents()
        self.assertEqual(coordinator.state.filters, before.filters)
        self.assertIs(coordinator.state.mask, before.mask)
        self.assertEqual(coordinator.state.revision, before.revision)
        self.assertEqual(coordinator.state.selection_revision, before.selection_revision)

    def test_apply_rules_and_clear_commit_revisions_and_zero_selected(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2, 3], "y": [3, 2, 1]})))
        before = coordinator.state.snapshot()
        self.apply_and_wait(
            coordinator,
            (FilterRule("x", lower=2), FilterRule("y", upper=2)),
        )
        self.assertEqual(coordinator.state.mask.tolist(), [False, True, True])
        self.assertEqual(coordinator.state.revision, before.revision + 1)
        self.assertEqual(coordinator.state.selection_revision, before.selection_revision + 1)

        self.apply_and_wait(coordinator, (FilterRule("x", lower=99),))
        self.assertEqual(coordinator.state.selected_count, 0)
        self.assertEqual(coordinator.table_controller.model.rowCount(), 0)

        revision = coordinator.state.revision
        selection_revision = coordinator.state.selection_revision
        self.assertTrue(coordinator.apply_filters(()))
        self.assertEqual(coordinator.state.filters, ())
        self.assertIsNone(coordinator.state.mask)
        self.assertEqual(coordinator.state.revision, revision + 1)
        self.assertEqual(coordinator.state.selection_revision, selection_revision + 1)
        self.assertEqual(coordinator.table_controller.model.rowCount(), 3)

    def test_dialog_apply_rejects_stale_revision(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2, 3]})))
        dialog = coordinator.open_filter_editor()
        self.assertIsNotNone(dialog)
        captured = coordinator.state.snapshot()
        self.assertTrue(
            coordinator.state.commit_filters(
                captured.revision,
                (FilterRule("x", lower=2),),
                np.array([False, True, True], dtype=np.bool_),
            )
        )
        APP.processEvents()
        dialog.column_combo.setCurrentText("x")
        dialog.minimum_edit.setText("1")
        dialog.add_condition()
        dialog.apply_button.click()
        APP.processEvents()
        self.assertIn("changed", dialog.feedback_label.text())
        self.assertTrue(dialog.isVisible())
        self.assertEqual(coordinator.state.filters, (FilterRule("x", lower=2),))

    def test_exact_large_integer_and_uint64_apply(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame(
            {
                "i": pd.Series([2**53, 2**53 + 1], dtype="Int64"),
                "u": pd.Series([2**63, 2**63 + 1], dtype="UInt64"),
            }
        )
        self.commit(coordinator, dataset(frame))
        self.apply_and_wait(coordinator, (FilterRule("i", lower=2**53 + 1),))
        self.assertEqual(coordinator.state.mask.tolist(), [False, True])
        self.apply_and_wait(coordinator, (FilterRule("u", lower=2**63 + 1),))
        self.assertEqual(coordinator.state.mask.tolist(), [False, True])

    def test_dataset_change_discards_inflight_filter_result(self):
        coordinator, shell = self.make()
        first = dataset(pd.DataFrame({"x": [1, 2, 3]}), "a.csv")
        second = dataset(pd.DataFrame({"y": [7, 8]}), "b.csv")
        self.commit(coordinator, first)
        entered = threading.Event()
        release = threading.Event()

        def slow(frame, rules, cancel=None):
            entered.set()
            release.wait(timeout=5)
            return real_build_filter_mask(frame, rules, cancel)

        with patch("csv_analyzer.qt_app.coordinator.build_filter_mask", slow):
            self.assertTrue(coordinator.apply_filters((FilterRule("x", lower=2),)))
            self.assertTrue(entered.wait(timeout=2))
            self.commit(coordinator, second)
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertIs(coordinator.state.dataset, second)
        self.assertEqual(coordinator.state.filters, ())
        self.assertIsNone(coordinator.state.mask)

    def test_selection_revision_change_discards_inflight_filter_result(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2, 3]})))
        entered = threading.Event()
        release = threading.Event()

        def slow(frame, rules, cancel=None):
            entered.set()
            release.wait(timeout=5)
            return real_build_filter_mask(frame, rules, cancel)

        with patch("csv_analyzer.qt_app.coordinator.build_filter_mask", slow):
            self.assertTrue(coordinator.apply_filters((FilterRule("x", lower=2),)))
            self.assertTrue(entered.wait(timeout=2))
            snapshot = coordinator.state.snapshot()
            committed = (FilterRule("x", upper=2),)
            self.assertTrue(
                coordinator.state.commit_filters(
                    snapshot.revision,
                    committed,
                    np.array([True, True, False], dtype=np.bool_),
                )
            )
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertEqual(coordinator.state.filters, committed)
        self.assertEqual(coordinator.state.mask.tolist(), [True, True, False])

    def test_latest_request_wins(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2, 3, 4]})))
        entered = threading.Event()
        release = threading.Event()
        calls = {"count": 0}

        def controlled(frame, rules, cancel=None):
            calls["count"] += 1
            if calls["count"] == 1:
                entered.set()
                release.wait(timeout=5)
            return real_build_filter_mask(frame, rules, cancel)

        with patch("csv_analyzer.qt_app.coordinator.build_filter_mask", controlled):
            self.assertTrue(coordinator.apply_filters((FilterRule("x", lower=2),)))
            self.assertTrue(entered.wait(timeout=2))
            self.assertTrue(coordinator.apply_filters((FilterRule("x", lower=4),)))
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertEqual(coordinator.state.filters, (FilterRule("x", lower=4),))
        self.assertEqual(coordinator.state.mask.tolist(), [False, False, False, True])

    def test_clear_cancels_old_filter_and_old_result_cannot_revive(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2, 3]})))
        entered = threading.Event()
        release = threading.Event()

        def slow(frame, rules, cancel=None):
            entered.set()
            release.wait(timeout=5)
            return real_build_filter_mask(frame, rules, cancel)

        with patch("csv_analyzer.qt_app.coordinator.build_filter_mask", slow):
            self.assertTrue(coordinator.apply_filters((FilterRule("x", lower=2),)))
            self.assertTrue(entered.wait(timeout=2))
            self.assertTrue(coordinator.apply_filters(()))
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)

        self.assertEqual(coordinator.state.filters, ())
        self.assertIsNone(coordinator.state.mask)
        self.assertEqual(coordinator.state.selected_count, 3)

    def test_failure_and_cancel_preserve_previous_selection(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2, 3]})))
        self.apply_and_wait(coordinator, (FilterRule("x", lower=2),))
        before = coordinator.state.snapshot()

        with patch(
            "csv_analyzer.qt_app.coordinator.build_filter_mask",
            side_effect=DataError("filter failure"),
        ):
            self.assertTrue(coordinator.apply_filters((FilterRule("x", upper=2),)))
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(coordinator.state.filters, before.filters)
        self.assertIs(coordinator.state.mask, before.mask)
        self.assertTrue(shell.errors)

        entered = threading.Event()
        release = threading.Event()

        def slow(frame, rules, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return real_build_filter_mask(frame, rules, cancel)

        with patch("csv_analyzer.qt_app.coordinator.build_filter_mask", slow):
            self.assertTrue(coordinator.apply_filters((FilterRule("x", upper=2),)))
            self.assertTrue(entered.wait(timeout=2))
            coordinator.cancel_all()
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(coordinator.state.filters, before.filters)
        self.assertIs(coordinator.state.mask, before.mask)

    def test_selected_scope_preserves_sort_search_and_visible_selection(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame(
            {
                "x": [3, 1, 2, 4],
                "text": ["beta", "beta", "alpha", "beta"],
            }
        )
        self.commit(coordinator, dataset(frame))
        controller = coordinator.table_controller
        view = shell.table_view
        controller.set_sort(1, SortDirection.ASC)
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        controller.set_search("beta", SearchMode.EXACT)
        pump_until(lambda: controller.search_result is not None)
        self.assertTrue(view.select_source_id(controller.model, 1))
        selection_model = view.table.selectionModel()
        row2 = controller.model.visible_row_for_source_id(2)
        selection_model.select(
            controller.model.index(row2, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )

        self.apply_and_wait(coordinator, (FilterRule("x", lower=2),))
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIs(controller.scope, TableScope.SELECTED)
        self.assertEqual(
            [controller.model.source_row_id_at(row) for row in range(controller.model.rowCount())],
            [3, 1, 4],
        )
        self.assertEqual(view.capture_selection(controller.model)[0], (1,))
        self.assertEqual(controller.search_spec.text, "beta")
        self.assertEqual(controller._visible_match_rows(), [1, 2])

    def test_all_scope_stays_materialized_and_counts_selection(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [3, 1, 2, 4]})))
        controller = coordinator.table_controller
        controller.set_scope(TableScope.ALL)
        controller.set_sort(1, SortDirection.ASC)
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        row_map = controller.model.row_map

        self.apply_and_wait(coordinator, (FilterRule("x", lower=3),))
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIs(controller.scope, TableScope.ALL)
        self.assertIs(controller.model.row_map.scope, TableScope.ALL)
        self.assertIs(controller.model.row_map, row_map)
        self.assertEqual(controller.model.rowCount(), 4)
        self.assertEqual(
            [controller.model.source_row_id_at(row) for row in range(4)],
            [2, 3, 1, 4],
        )
        self.assertEqual(shell.table_view.count_label.text(), "All rows · 4 / 4")
        self.assertIn("2 / 4", shell.table_view.count_label.toolTip())

    def test_go_to_filtered_row_switches_explicitly_to_all(self):
        coordinator, shell = self.make()
        self.commit(coordinator, dataset(pd.DataFrame({"x": [1, 2, 3]})))
        self.apply_and_wait(coordinator, (FilterRule("x", lower=2),))
        controller = coordinator.table_controller
        self.assertIs(controller.scope, TableScope.SELECTED)
        controller.go_to_source_id("1")
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIs(controller.scope, TableScope.ALL)
        self.assertEqual(shell.table_view.capture_selection(controller.model)[1], 1)


if __name__ == "__main__":
    unittest.main()
