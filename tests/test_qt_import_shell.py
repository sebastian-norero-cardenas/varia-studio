"""F4.A Qt import options, Open/Reload and shell parity."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import shiboken6
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from csv_analyzer.data import (
    CancelledError,
    DataError,
    Dataset,
    ImportOptions,
    describe_columns,
)
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.import_dialog import ImportOptionsDialog
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule

APP = QApplication.instance() or QApplication(["varia-f4a-tests"])


def pump_until(predicate, timeout_ms=8000):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    timeout = QTimer()
    expired = {"value": False}
    poll.setInterval(2)
    timeout.setSingleShot(True)

    def check():
        if predicate():
            loop.quit()

    def expire():
        expired["value"] = True
        loop.quit()

    poll.timeout.connect(check)
    timeout.timeout.connect(expire)
    poll.start()
    timeout.start(timeout_ms)
    loop.exec()
    poll.stop()
    timeout.stop()
    if expired["value"] and not predicate():
        raise AssertionError("Qt condition did not become true before timeout")


def make_dataset(
    frame: pd.DataFrame,
    path: str | Path = "dataset.csv",
    *,
    notices: tuple[str, ...] = (),
) -> Dataset:
    return Dataset(
        frame=frame,
        path=Path(path),
        columns=describe_columns(frame),
        notices=notices,
    )


class RecordingShell(QtShell):
    def __init__(self):
        super().__init__()
        self.errors: list[str] = []

    def show_error(self, text: str) -> None:
        self.errors.append(text)


class QtImportShellTests(unittest.TestCase):
    def make(self, *, loader=None, **kwargs):
        shell = RecordingShell()
        if loader is not None:
            kwargs["loader"] = loader
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

    def commit(self, coordinator, dataset):
        ticket = coordinator.state.begin_load()
        self.assertTrue(coordinator.state.commit_dataset(ticket, dataset))
        APP.processEvents()

    def wait_idle(self, coordinator):
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        APP.processEvents()

    def test_dialog_maps_all_options_and_preserves_exact_date_lines(self):
        initial = ImportOptions(comment="#", date_columns=("existing",))
        dialog = ImportOptionsDialog(initial)
        self.addCleanup(dialog.deleteLater)

        for label, value in ImportOptionsDialog.DELIMITERS:
            with self.subTest(delimiter=label):
                dialog.delimiter.setCurrentIndex(dialog.delimiter.findData(value))
                self.assertEqual(dialog.staged_options().delimiter, value)

        for encoding in ImportOptionsDialog.ENCODINGS:
            with self.subTest(encoding=encoding):
                dialog.encoding.setCurrentText(encoding)
                self.assertEqual(dialog.staged_options().encoding, encoding)

        for label, value in ImportOptionsDialog.HEADERS:
            with self.subTest(header=label):
                dialog.header.setCurrentIndex(dialog.header.findData(value))
                self.assertEqual(dialog.staged_options().header, value)

        for decimal in ImportOptionsDialog.DECIMALS:
            with self.subTest(decimal=decimal):
                dialog.decimal.setCurrentText(decimal)
                self.assertEqual(dialog.staged_options().decimal, decimal)

        dialog.date_columns.setPlainText("date\n\n date with spaces \n   \nlast")
        staged = dialog.staged_options()
        self.assertEqual(
            staged.date_columns,
            ("date", " date with spaces ", "   ", "last"),
        )
        self.assertEqual(staged.comment, "#")

    def test_dialog_save_cancel_staging_and_options_edit_do_not_mutate_app_state(self):
        coordinator, shell = self.make()
        original = ImportOptions(comment="#")
        coordinator.import_options = original
        before = coordinator.state.snapshot()

        dialog = coordinator.open_import_options()
        self.assertIsNotNone(dialog)
        self.assertIs(dialog.parent(), shell)
        dialog.delimiter.setCurrentIndex(dialog.delimiter.findData("|"))
        dialog.encoding.setCurrentText("cp1252")
        dialog.header.setCurrentIndex(dialog.header.findData("no"))
        dialog.decimal.setCurrentText(",")
        dialog.date_columns.setPlainText("date\n exact name ")
        dialog._save()
        APP.processEvents()

        self.assertEqual(
            coordinator.import_options,
            ImportOptions(
                delimiter="|",
                encoding="cp1252",
                header="no",
                decimal=",",
                comment="#",
                date_columns=("date", " exact name "),
            ),
        )
        self.assertEqual(
            shell.status_label.text(),
            "Import options saved. Open or reload a file to apply them.",
        )
        after = coordinator.state.snapshot()
        self.assertEqual(
            (
                after.revision,
                after.dataset_revision,
                after.selection_revision,
                after.filters,
                after.mask,
            ),
            (
                before.revision,
                before.dataset_revision,
                before.selection_revision,
                before.filters,
                before.mask,
            ),
        )

        saved = coordinator.import_options
        dialog = coordinator.open_import_options()
        dialog.delimiter.setCurrentIndex(dialog.delimiter.findData(";"))
        dialog.reject()
        APP.processEvents()
        self.assertEqual(coordinator.import_options, saved)

    def test_shell_title_controls_shortcuts_reload_state_and_guide(self):
        shell = QtShell()
        self.addCleanup(shell.deleteLater)
        self.assertEqual(shell.windowTitle(), "Varia Studio")
        self.assertEqual(shell.open_button.text(), "Open…")
        self.assertEqual(shell.reload_button.text(), "Reload")
        self.assertEqual(shell.import_options_button.text(), "Import options…")
        self.assertEqual(shell.cancel_button.text(), "Cancel")
        self.assertEqual(shell.quit_button.text(), "Quit")
        self.assertEqual(shell.open_action.shortcut().toString(), "Ctrl+O")
        self.assertEqual(shell.reload_action.shortcut().toString(), "Ctrl+R")
        self.assertFalse(shell.reload_button.isEnabled())
        self.assertFalse(shell.reload_action.isEnabled())

        calls = []
        shell.open_requested.connect(lambda: calls.append("open"))
        shell.reload_requested.connect(lambda: calls.append("reload"))
        shell.open_action.trigger()
        shell.set_dataset("a.csv", 2, 1, selected=2)
        shell.reload_action.trigger()
        self.assertEqual(calls, ["open", "reload"])
        self.assertTrue(shell.reload_button.isEnabled())

        with patch.object(QMessageBox, "information") as info:
            shell.show_guide()
        guide = info.call_args.args[2]
        for phrase in (
            "Open & inspect",
            "Filters",
            "Relationships / Distributions",
            "Statistics",
            "Export",
            "Ctrl+O",
            "Ctrl+R",
        ):
            self.assertIn(phrase, guide)

    def test_open_uses_qfiledialog_and_current_options(self):
        calls = []

        def loader(path, options, *, cancel=None):
            calls.append((Path(path), options, cancel))
            return make_dataset(pd.DataFrame({"x": [1, 2]}), path)

        coordinator, shell = self.make(loader=loader)
        coordinator.import_options = ImportOptions(delimiter=";", encoding="latin-1", header="yes")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "open.csv"
            path.write_text("x\n1\n", encoding="utf-8")
            with patch.object(
                QFileDialog,
                "getOpenFileName",
                return_value=(str(path), "Data files"),
            ):
                coordinator.open_file()
            self.wait_idle(coordinator)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0].name, "open.csv")
        self.assertEqual(calls[0][1], coordinator.import_options)
        self.assertIsNotNone(calls[0][2])
        self.assertEqual(coordinator.state.dataset.path.name, "open.csv")
        self.assertEqual(shell.tabs.currentWidget(), shell.data_tab)

    def test_real_import_integration_formats_encodings_headers_decimal_and_dates(self):
        coordinator, shell = self.make()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = []

            bom = root / "bom.csv"
            bom.write_text("x,date\n1,2026-01-02\n", encoding="utf-8-sig")
            cases.append(
                (
                    bom,
                    ImportOptions(encoding="utf-8-sig", date_columns=("date",)),
                    ("x", "date"),
                    1,
                )
            )

            semicolon = root / "semicolon.csv"
            semicolon.write_bytes("x;name\n1,5;café\n".encode("cp1252"))
            cases.append(
                (
                    semicolon,
                    ImportOptions(
                        delimiter=";",
                        encoding="cp1252",
                        header="yes",
                        decimal=",",
                    ),
                    ("x", "name"),
                    1,
                )
            )

            tab = root / "tab.tsv"
            tab.write_text("x\ty\n1\t2\n", encoding="utf-8")
            cases.append(
                (
                    tab,
                    ImportOptions(delimiter="\t", encoding="utf-8", header="yes"),
                    ("x", "y"),
                    1,
                )
            )

            pipe = root / "pipe.txt"
            pipe.write_bytes("name|value\ncafé|2\n".encode("latin-1"))
            cases.append(
                (
                    pipe,
                    ImportOptions(delimiter="|", encoding="latin-1", header="yes"),
                    ("name", "value"),
                    1,
                )
            )

            utf16 = root / "utf16.csv"
            utf16.write_text("x,y\n3,4\n", encoding="utf-16")
            cases.append(
                (
                    utf16,
                    ImportOptions(delimiter=",", encoding="utf-16", header="yes"),
                    ("x", "y"),
                    1,
                )
            )

            no_header = root / "no_header.csv"
            no_header.write_text("1,2\n3,4\n", encoding="utf-8")
            cases.append(
                (
                    no_header,
                    ImportOptions(delimiter=",", encoding="utf-8", header="no"),
                    ("Column_1", "Column_2"),
                    2,
                )
            )

            dat = root / "table.dat"
            dat.write_text("# x y\n1 2\n3 4\n", encoding="utf-8")
            cases.append(
                (
                    dat,
                    ImportOptions(delimiter="whitespace", encoding="utf-8", header="auto"),
                    ("x", "y"),
                    2,
                )
            )

            for path, options, columns, rows in cases:
                with self.subTest(path=path.name):
                    coordinator.import_options = options
                    coordinator.load_path(path)
                    self.wait_idle(coordinator)
                    self.assertFalse(shell.errors, shell.errors)
                    active = coordinator.state.dataset
                    self.assertEqual(tuple(active.frame.columns), columns)
                    self.assertEqual(len(active.frame), rows)
                    self.assertEqual(active.encoding, options.encoding)

            self.assertEqual(coordinator.state.dataset.delimiter, "whitespace")

            coordinator.import_options = ImportOptions(
                delimiter=";", encoding="cp1252", header="yes", decimal=","
            )
            coordinator.load_path(semicolon)
            self.wait_idle(coordinator)
            self.assertAlmostEqual(float(coordinator.state.dataset.frame.loc[0, "x"]), 1.5)

            coordinator.import_options = ImportOptions(
                encoding="utf-8-sig",
                date_columns=("date",),
            )
            coordinator.load_path(bom)
            self.wait_idle(coordinator)
            self.assertEqual(coordinator.state.dataset.columns[1].kind, "datetime")

    def test_reload_uses_current_committed_path_and_current_options(self):
        calls = []

        def loader(path, options, *, cancel=None):
            calls.append((Path(path), options))
            return make_dataset(pd.DataFrame({"x": [10, 20]}), path)

        coordinator, shell = self.make(loader=loader)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active.csv"
            current = make_dataset(pd.DataFrame({"x": [1, 2]}), path)
            self.commit(coordinator, current)
            options = ImportOptions(delimiter="|", encoding="utf-16", header="no")
            coordinator.import_options = options

            token = coordinator.reload_file()
            self.assertIsNotNone(token)
            self.wait_idle(coordinator)

        self.assertEqual(calls, [(path, options)])
        self.assertEqual(coordinator.state.dataset.frame["x"].tolist(), [10, 20])
        self.assertTrue(shell.reload_button.isEnabled())

    def test_failed_reload_preserves_dataset_filters_and_derived_views(self):
        def failing_loader(*_args, **_kwargs):
            raise DataError("reload failed")

        coordinator, shell = self.make(loader=failing_loader)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active.csv"
            active = make_dataset(
                pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 6.0]}),
                path,
            )
            self.commit(coordinator, active)
            snapshot = coordinator.state.snapshot()
            mask = np.array([False, True, True], dtype=bool)
            self.assertTrue(
                coordinator.state.commit_filters(
                    snapshot.revision,
                    (FilterRule("x", lower=2),),
                    mask,
                )
            )

            coordinator.scatter_controller.request_plot()
            self.wait_idle(coordinator)
            coordinator.histogram_controller.request_plot()
            self.wait_idle(coordinator)
            coordinator.statistics_controller.request_compute()
            self.wait_idle(coordinator)

            scatter = shell.scatter_view.panel.figure
            histogram = shell.histogram_view.panel.figure
            statistics = coordinator.statistics_controller.current_result
            before = coordinator.state.snapshot()

            coordinator.reload_file()
            self.wait_idle(coordinator)

        after = coordinator.state.snapshot()
        self.assertIs(after.dataset, before.dataset)
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(after.dataset_revision, before.dataset_revision)
        self.assertEqual(after.selection_revision, before.selection_revision)
        self.assertEqual(after.filters, before.filters)
        np.testing.assert_array_equal(after.mask, before.mask)
        self.assertIs(shell.scatter_view.panel.figure, scatter)
        self.assertIs(shell.histogram_view.panel.figure, histogram)
        self.assertIs(coordinator.statistics_controller.current_result, statistics)
        self.assertIn("still active", shell.status_label.text())

    def test_cancelled_reload_preserves_committed_dataset_and_filters(self):
        entered = threading.Event()

        def cancellable_loader(path, options, *, cancel=None):
            entered.set()
            while not cancel.wait(0.002):
                pass
            raise CancelledError()

        coordinator, shell = self.make(loader=cancellable_loader)
        active = make_dataset(pd.DataFrame({"x": [1, 2, 3]}), "active.csv")
        self.commit(coordinator, active)
        snapshot = coordinator.state.snapshot()
        mask = np.array([True, False, False], dtype=bool)
        self.assertTrue(
            coordinator.state.commit_filters(
                snapshot.revision,
                (FilterRule("x", upper=1),),
                mask,
            )
        )
        before = coordinator.state.snapshot()

        coordinator.reload_file()
        self.assertTrue(entered.wait(timeout=2))
        coordinator.cancel_all()
        self.wait_idle(coordinator)

        after = coordinator.state.snapshot()
        self.assertIs(after.dataset, before.dataset)
        self.assertEqual(after.filters, before.filters)
        np.testing.assert_array_equal(after.mask, before.mask)
        self.assertEqual(after.dataset_revision, before.dataset_revision)
        self.assertEqual(after.selection_revision, before.selection_revision)
        self.assertIn("active dataset is unchanged", shell.status_label.text())

    def test_successful_reload_is_new_atomic_dataset_commit_and_invalidates_views(self):
        replacement = make_dataset(
            pd.DataFrame({"x": [10.0, 20.0], "y": [5.0, 6.0]}),
            "active.csv",
        )

        def loader(path, options, *, cancel=None):
            return Dataset(
                replacement.frame,
                Path(path),
                replacement.columns,
                replacement.notices,
            )

        coordinator, shell = self.make(loader=loader)
        active = make_dataset(
            pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 6.0]}),
            "active.csv",
        )
        self.commit(coordinator, active)
        snapshot = coordinator.state.snapshot()
        self.assertTrue(
            coordinator.state.commit_filters(
                snapshot.revision,
                (FilterRule("x", lower=2),),
                np.array([False, True, True], dtype=bool),
            )
        )
        coordinator.scatter_controller.request_plot()
        self.wait_idle(coordinator)
        coordinator.statistics_controller.request_compute()
        self.wait_idle(coordinator)
        before = coordinator.state.snapshot()

        coordinator.reload_file()
        self.wait_idle(coordinator)

        after = coordinator.state.snapshot()
        self.assertEqual(after.dataset.frame["x"].tolist(), [10.0, 20.0])
        self.assertEqual(after.dataset_revision, before.dataset_revision + 1)
        self.assertEqual(after.selection_revision, before.selection_revision + 1)
        self.assertEqual(after.filters, ())
        self.assertIsNone(after.mask)
        self.assertIsNone(shell.scatter_view.panel.figure)
        self.assertIsNone(coordinator.statistics_controller.current_result)

    def test_options_changed_while_load_pending_do_not_change_captured_request(self):
        entered = threading.Event()
        release = threading.Event()
        seen = []

        def loader(path, options, *, cancel=None):
            seen.append(options)
            entered.set()
            release.wait(timeout=5)
            if cancel.is_set():
                raise CancelledError()
            return make_dataset(pd.DataFrame({"x": [1]}), path)

        coordinator, _shell = self.make(loader=loader)
        old = ImportOptions(delimiter=",", encoding="utf-8")
        new = ImportOptions(delimiter="|", encoding="cp1252", header="no")
        coordinator.import_options = old
        before = coordinator.state.snapshot()

        coordinator.load_path("pending.csv")
        self.assertTrue(entered.wait(timeout=2))
        coordinator._save_import_options(new)
        during = coordinator.state.snapshot()
        self.assertEqual(
            (during.revision, during.dataset_revision, during.selection_revision),
            (before.revision, before.dataset_revision, before.selection_revision),
        )
        self.assertEqual(seen, [old])
        self.assertEqual(coordinator.import_options, new)

        release.set()
        self.wait_idle(coordinator)
        self.assertIsNotNone(coordinator.state.dataset)

    def test_reload_and_open_share_latest_load_semantics(self):
        first_entered = threading.Event()
        release_first = threading.Event()
        first_cancelled = []

        active = make_dataset(pd.DataFrame({"x": [1]}), "active.csv")
        replacement = make_dataset(pd.DataFrame({"x": [9]}), "new.csv")

        def loader(path, options, *, cancel=None):
            if Path(path).name == "active.csv":
                first_entered.set()
                release_first.wait(timeout=5)
                first_cancelled.append(cancel.is_set())
                if cancel.is_set():
                    raise CancelledError()
                return active
            return replacement

        coordinator, _shell = self.make(loader=loader, max_workers=2)
        self.commit(coordinator, active)
        coordinator.reload_file()
        self.assertTrue(first_entered.wait(timeout=2))
        coordinator.load_path("new.csv")
        release_first.set()
        self.wait_idle(coordinator)

        self.assertEqual(first_cancelled, [True])
        self.assertEqual(coordinator.state.dataset.frame["x"].tolist(), [9])
        self.assertEqual(coordinator.state.dataset.path.name, "new.csv")

    def test_dataset_summary_selected_count_and_notices_update_from_state(self):
        coordinator, shell = self.make()
        frame = pd.DataFrame(
            {
                "x": [1.0, np.nan, np.inf],
                "name": ["a", "b", "c"],
            }
        )
        active = make_dataset(
            frame,
            "summary.csv",
            notices=("Column 1 was renamed.", "Exact integers were preserved."),
        )
        self.commit(coordinator, active)

        summary = shell.dataset_label.text()
        for phrase in (
            "summary.csv",
            "3 rows",
            "2 columns",
            "1 missing",
            "1 infinite",
            "3 selected",
        ):
            self.assertIn(phrase, summary)
        self.assertTrue(shell.notices_button.isEnabled())
        self.assertIn("2 import notices", shell.notices_label.text())

        snapshot = coordinator.state.snapshot()
        self.assertTrue(
            coordinator.state.commit_filters(
                snapshot.revision,
                (FilterRule("x", upper=1),),
                np.array([True, False, False], dtype=bool),
            )
        )
        self.assertIn("1 selected", shell.dataset_label.text())

        with patch.object(QMessageBox, "information") as info:
            shell.show_import_notices()
        text = info.call_args.args[2]
        self.assertIn("Column 1 was renamed.", text)
        self.assertIn("Exact integers were preserved.", text)

    def test_import_dialog_survives_dataset_commit_and_application_close_rejects_it(self):
        coordinator, shell = self.make()
        original = coordinator.import_options
        dialog = coordinator.open_import_options()
        self.assertTrue(shiboken6.isValid(dialog))
        dialog.delimiter.setCurrentIndex(dialog.delimiter.findData("|"))

        self.commit(coordinator, make_dataset(pd.DataFrame({"x": [1]}), "new.csv"))
        self.assertIs(coordinator._import_dialog, dialog)
        self.assertTrue(shiboken6.isValid(dialog))
        self.assertEqual(coordinator.import_options, original)

        coordinator.begin_close()
        pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        self.assertIsNone(coordinator._import_dialog)
        self.assertEqual(coordinator.import_options, original)
        self.assertFalse(shell.isVisible())

    def test_reload_disabled_without_dataset_and_noop(self):
        coordinator, shell = self.make()
        self.assertFalse(shell.reload_button.isEnabled())
        self.assertFalse(shell.reload_action.isEnabled())
        self.assertIsNone(coordinator.reload_file())
        self.assertIsNone(coordinator.state.dataset)


if __name__ == "__main__":
    unittest.main()
