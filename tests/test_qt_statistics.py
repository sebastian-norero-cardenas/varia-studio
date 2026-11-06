"""F3.F Qt statistics controls, semantics, races and lifecycle."""

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

from csv_analyzer.analysis import (
    correlation_summary as real_correlation_summary,
    numeric_summary as real_numeric_summary,
)
from csv_analyzer.data import CancelledError, DataError, Dataset, describe_columns
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.qt_app.statistics_model import display_statistics_value
from csv_analyzer.state import FilterRule

APP = QApplication.instance() or QApplication(["varia-f3f-tests"])


def pump_until(predicate, timeout_ms=7000):
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


def make_dataset(frame, name="statistics.csv"):
    return Dataset(frame=frame, path=Path(name), columns=describe_columns(frame))


def base_frame():
    return pd.DataFrame(
        {
            "x": [1.0, 2.0, 3.0, 4.0, np.nan, np.inf],
            "y": [2.0, 4.0, 6.0, 8.0, 10.0, 12.0],
            "z": [8.0, 6.0, 4.0, 2.0, 0.0, -2.0],
            "constant": [5.0] * 6,
            "group": ["a", "b", "a", None, "b", "b"],
            "flag": [True, False, True, False, True, False],
            "date": pd.to_datetime(
                ["2026-01-03", "2026-01-01", None, "2026-01-04", "2026-01-02", None]
            ),
        }
    )


class StatisticsQtTests(unittest.TestCase):
    def make(self, **kwargs):
        shell = QtShell()
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

    def commit(self, coordinator, frame, name="statistics.csv"):
        ticket = coordinator.state.begin_load()
        self.assertTrue(coordinator.state.commit_dataset(ticket, make_dataset(frame, name)))
        APP.processEvents()

    def compute(self, coordinator):
        coordinator.statistics_controller.request_compute()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        APP.processEvents()
        return coordinator.statistics_controller.current_result

    def test_shell_defaults_and_result_model_formatting(self):
        coordinator, shell = self.make()
        self.assertIs(shell.tabs.widget(3), shell.statistics_view)
        self.commit(coordinator, pd.DataFrame({"x": [1.23456789, 1.23456789], "y": [1, 2]}))
        view = shell.statistics_view
        self.assertEqual(view.mode.currentData(), "numeric")
        self.assertEqual(view.ddof.currentData(), 1)
        self.assertEqual(view.correlation_columns, ("x", "y"))
        result = self.compute(coordinator)
        model = view.result_model
        mean_column = list(result.columns).index("Mean")
        self.assertIs(model.result, result)
        self.assertEqual(model.underlying_value(0, mean_column), result.iloc[0]["Mean"])
        self.assertEqual(model.data(model.index(0, mean_column)), "1.23457")
        self.assertEqual(display_statistics_value(np.nan), "—")
        self.assertEqual(display_statistics_value(np.inf), "∞")
        self.assertEqual(display_statistics_value(-np.inf), "−∞")
        self.assertEqual(display_statistics_value(7), "7")

    def test_numeric_semantics_sample_population_missing_infinite_huge_single(self):
        coordinator, shell = self.make()
        frame = base_frame().assign(huge=[1e308] * 6, single=[2.0] + [np.nan] * 5)
        self.commit(coordinator, frame)
        row = self.compute(coordinator).set_index("Column").loc["x"]
        self.assertEqual((row["Rows"], row["Valid"], row["Missing"], row["Infinite"]), (6, 4, 1, 1))
        self.assertAlmostEqual(row["Mean"], 2.5)
        self.assertAlmostEqual(row["Variance"], 5 / 3)
        self.assertEqual((row["Q1"], row["Median"], row["Q3"]), (1.75, 2.5, 3.25))
        result = coordinator.statistics_controller.current_result.set_index("Column")
        self.assertEqual(result.loc["huge", "Mean"], 1e308)
        self.assertTrue(np.isnan(result.loc["single", "Std"]))
        self.assertNotIn("flag", result.index)
        shell.statistics_view.ddof.setCurrentIndex(1)
        row = self.compute(coordinator).set_index("Column").loc["x"]
        self.assertEqual(row["ddof"], 0)
        self.assertAlmostEqual(row["Variance"], 1.25)

    def test_categorical_and_correlation_semantics(self):
        coordinator, shell = self.make()
        frame = base_frame().assign(sparse=[1.0, np.nan, np.nan, np.nan, np.nan, np.nan])
        self.commit(coordinator, frame)
        view = shell.statistics_view
        view.mode.setCurrentIndex(1)
        cat = self.compute(coordinator).set_index("Column")
        self.assertEqual(cat.loc["group", "Unique"], 2)
        self.assertEqual(cat.loc["group", "Most frequent"], "b")
        self.assertEqual(cat.loc["group", "Frequency"], 3)
        self.assertEqual(cat.loc["flag", "Tied modes"], 2)
        self.assertEqual(cat.loc["date", "Earliest"], "2026-01-01 00:00:00")
        self.assertEqual(cat.loc["date", "Latest"], "2026-01-04 00:00:00")

        view.mode.setCurrentIndex(2)
        view.set_correlation_columns(("x", "y", "z", "constant", "sparse"))
        corr = self.compute(coordinator)
        xy = corr[(corr.X == "x") & (corr.Y == "y")].iloc[0]
        xz = corr[(corr.X == "x") & (corr.Y == "z")].iloc[0]
        xc = corr[(corr.X == "x") & (corr.Y == "constant")].iloc[0]
        xs = corr[(corr.X == "x") & (corr.Y == "sparse")].iloc[0]
        self.assertEqual(xy["Paired N"], 4)
        self.assertAlmostEqual(xy["Pearson r"], 1)
        self.assertAlmostEqual(xz["Pearson r"], -1)
        self.assertEqual(xc["Note"], "Constant column")
        self.assertEqual(xs["Paired N"], 1)
        self.assertEqual(xs["Note"], "Fewer than two paired observations")

    def test_mask_is_authoritative_and_zero_selected_never_falls_back_to_all(self):
        coordinator, shell = self.make()
        self.commit(coordinator, base_frame())
        snap = coordinator.state.snapshot()
        mask = np.array([False, True, True, False, False, False])
        self.assertTrue(
            coordinator.state.commit_filters(
                snap.revision, (FilterRule("x", 2, 3),), mask
            )
        )
        numeric = self.compute(coordinator).set_index("Column")
        self.assertEqual(numeric.loc["x", "Rows"], 2)
        self.assertEqual(numeric.loc["x", "Mean"], 2.5)

        snap = coordinator.state.snapshot()
        self.assertTrue(
            coordinator.state.commit_filters(
                snap.revision,
                (FilterRule("x", lower=100),),
                np.zeros(6, dtype=bool),
            )
        )
        numeric = self.compute(coordinator).set_index("Column")
        self.assertEqual((numeric.loc["x", "Rows"], numeric.loc["x", "Valid"]), (0, 0))
        self.assertTrue(np.isnan(numeric.loc["x", "Mean"]))
        shell.statistics_view.mode.setCurrentIndex(1)
        cat = self.compute(coordinator).set_index("Column")
        self.assertEqual((cat.loc["group", "Rows"], cat.loc["group", "Valid"]), (0, 0))
        shell.statistics_view.mode.setCurrentIndex(2)
        corr = self.compute(coordinator).iloc[0]
        self.assertEqual(corr["Paired N"], 0)

    def test_settings_selection_and_failed_load_invalidation_contracts(self):
        def failing_loader(*_args, **_kwargs):
            raise DataError("injected load failure")

        coordinator, shell = self.make(loader=failing_loader)
        self.commit(coordinator, base_frame(), "A.csv")
        result = self.compute(coordinator)
        shell.statistics_view.ddof.setCurrentIndex(1)
        self.assertIsNone(coordinator.statistics_controller.current_result)
        self.assertIn("Settings changed", shell.statistics_view.local_status.text())
        result = self.compute(coordinator)
        settings = coordinator.statistics_controller.current_settings
        coordinator.statistics_controller._present_error = lambda _error: None
        coordinator.load_path("B.csv")
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIs(coordinator.statistics_controller.current_result, result)
        self.assertEqual(coordinator.statistics_controller.current_settings, settings)

        snap = coordinator.state.snapshot()
        coordinator.state.commit_filters(
            snap.revision,
            (FilterRule("x", upper=2),),
            np.array([True, True, False, False, False, False]),
        )
        self.assertIsNone(coordinator.statistics_controller.current_result)
        self.assertIn("Selection changed", shell.statistics_view.local_status.text())

    def test_latest_request_wins_for_ddof_mode_and_columns(self):
        coordinator, shell = self.make()
        self.commit(coordinator, base_frame())
        view = shell.statistics_view
        controller = coordinator.statistics_controller
        entered = threading.Event()
        release = threading.Event()

        def slow_numeric(frame, mask=None, *, ddof=1, cancel=None):
            if ddof == 1:
                entered.set()
                release.wait(timeout=5)
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
            return real_numeric_summary(frame, mask, ddof=ddof, cancel=cancel)

        with patch("csv_analyzer.qt_app.statistics_controller.numeric_summary", slow_numeric):
            controller.request_compute()
            self.assertTrue(entered.wait(timeout=2))
            view.ddof.setCurrentIndex(1)
            controller.request_compute()
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(set(controller.current_result["ddof"]), {0})

        entered.clear()
        release.clear()

        def slow_mode(frame, mask=None, *, ddof=1, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return real_numeric_summary(frame, mask, ddof=ddof, cancel=cancel)

        view.mode.setCurrentIndex(0)
        with patch("csv_analyzer.qt_app.statistics_controller.numeric_summary", slow_mode):
            controller.request_compute()
            self.assertTrue(entered.wait(timeout=2))
            view.mode.setCurrentIndex(1)
            controller.request_compute()
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(controller.current_settings.mode, "categorical")
        self.assertIn("Most frequent", controller.current_result.columns)

        entered.clear()
        release.clear()
        view.mode.setCurrentIndex(2)
        view.set_correlation_columns(("x", "y"))

        def slow_corr(frame, columns, mask=None, *, cancel=None):
            if tuple(columns) == ("x", "y"):
                entered.set()
                release.wait(timeout=5)
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
            return real_correlation_summary(frame, columns, mask, cancel=cancel)

        with patch("csv_analyzer.qt_app.statistics_controller.correlation_summary", slow_corr):
            controller.request_compute()
            self.assertTrue(entered.wait(timeout=2))
            view.set_correlation_columns(("x", "z"))
            controller.request_compute()
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(tuple(controller.current_result.iloc[0][["X", "Y"]]), ("x", "z"))

    def test_statistics_does_not_cancel_plot_group_and_compute_is_immutable(self):
        coordinator, _shell = self.make()
        self.commit(coordinator, base_frame())
        before = coordinator.state.snapshot()
        entered = threading.Event()
        release = threading.Event()
        cancelled = []

        def plot_work(cancel):
            entered.set()
            release.wait(timeout=5)
            cancelled.append(cancel.is_set())
            if cancel.is_set():
                raise CancelledError()
            return "plot"

        coordinator.submit_task(
            "scatter",
            plot_work,
            receiver=coordinator,
            exclusive_group="plots",
        )
        self.assertTrue(entered.wait(timeout=2))
        coordinator.statistics_controller.request_compute()
        pump_until(lambda: coordinator.statistics_controller.current_result is not None)
        self.assertEqual(coordinator.runner.current_generation("scatter"), 1)
        release.set()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(cancelled, [False])
        after = coordinator.state.snapshot()
        self.assertEqual(
            (after.revision, after.dataset_revision, after.selection_revision, after.filters),
            (before.revision, before.dataset_revision, before.selection_revision, before.filters),
        )
        self.assertIs(after.mask, before.mask)
        self.assertIs(after.dataset.frame, before.dataset.frame)

    def test_empty_modes_errors_model_failure_and_close_active_worker(self):
        coordinator, shell = self.make()
        view = shell.statistics_view
        self.assertFalse(view.compute_button.isEnabled())
        self.commit(coordinator, pd.DataFrame({"text": ["a", "b"]}))
        self.assertFalse(view.compute_button.isEnabled())
        view.mode.setCurrentIndex(1)
        self.assertTrue(view.compute_button.isEnabled())
        self.commit(coordinator, pd.DataFrame({"x": [1.0, 2.0]}))
        view.mode.setCurrentIndex(2)
        self.assertFalse(view.compute_button.isEnabled())

        self.commit(coordinator, base_frame())
        controller = coordinator.statistics_controller
        errors = []
        controller._present_error = errors.append
        view.mode.setCurrentIndex(2)
        view._correlation_columns = ("x",)
        controller.request_compute()
        self.assertTrue(errors)
        self.assertIsNone(controller.current_result)

        view.set_correlation_columns(("x", "y"))
        view.mode.setCurrentIndex(0)
        with patch.object(view, "show_result", side_effect=RuntimeError("install failure")):
            controller.request_compute()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertIsNone(controller.current_result)

        entered = threading.Event()
        release = threading.Event()

        def slow(frame, mask=None, *, ddof=1, cancel=None):
            entered.set()
            release.wait(timeout=5)
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            return real_numeric_summary(frame, mask, ddof=ddof, cancel=cancel)

        with patch("csv_analyzer.qt_app.statistics_controller.numeric_summary", slow):
            controller.request_compute()
            self.assertTrue(entered.wait(timeout=2))
            coordinator.begin_close()
            release.set()
            pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        self.assertIsNone(view.result_model.result)


if __name__ == "__main__":
    unittest.main()
