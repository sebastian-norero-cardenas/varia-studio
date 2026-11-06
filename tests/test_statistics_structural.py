"""S-200K-STATISTICS structural worker, mask and latest-request evidence."""

from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from csv_analyzer.analysis import (
    categorical_summary,
    correlation_summary,
    numeric_summary,
)
from csv_analyzer.data import CancelledError, Dataset, describe_columns
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule

APP = QApplication.instance() or QApplication(["varia-f3f-structural"])


def pump_until(predicate, timeout_ms=12000):
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


class StatisticsStructuralTests(unittest.TestCase):
    def setUp(self):
        rows = 200_000
        x = np.linspace(1.0, 1000.0, rows, dtype=np.float64)
        y = x * 2.0 + 3.0
        z = -x
        x[::997] = np.nan
        y[::1201] = np.inf
        groups = np.where(np.arange(rows) % 3 == 0, "a", "b")
        self.frame = pd.DataFrame(
            {
                "x": x,
                "y": y,
                "z": z,
                "group": groups,
                "filter": np.arange(rows, dtype=np.int64),
            }
        )
        self.original = self.frame.copy(deep=True)
        self.dataset = Dataset(
            frame=self.frame,
            path=Path("S-200K-STATISTICS.csv"),
            columns=describe_columns(self.frame),
        )
        self.shell = QtShell()
        self.coordinator = QtCoordinator(self.shell)
        self.coordinator.start()
        ticket = self.coordinator.state.begin_load()
        self.assertTrue(self.coordinator.state.commit_dataset(ticket, self.dataset))
        APP.processEvents()

    def tearDown(self):
        if self.coordinator.lifecycle not in (Lifecycle.CLOSING, Lifecycle.CLOSED):
            self.coordinator.begin_close()
        if self.coordinator.lifecycle is not Lifecycle.CLOSED:
            pump_until(lambda: self.coordinator.lifecycle is Lifecycle.CLOSED)
        self.shell.deleteLater()
        self.coordinator.deleteLater()
        APP.processEvents()

    def test_s_200k_modes_run_in_worker_respect_mask_and_do_not_mutate_source(self):
        snap = self.coordinator.state.snapshot()
        mask = np.arange(len(self.frame)) >= 100_000
        self.assertTrue(
            self.coordinator.state.commit_filters(
                snap.revision,
                (FilterRule("filter", lower=100_000),),
                mask,
            )
        )
        snapshot = self.coordinator.state.snapshot()
        gui = threading.get_ident()
        worker_ids = []
        results = {}

        jobs = {
            "numeric": lambda cancel: numeric_summary(
                snapshot.dataset.frame, snapshot.mask, ddof=1, cancel=cancel
            ),
            "categorical": lambda cancel: categorical_summary(
                snapshot.dataset.frame, snapshot.mask, cancel=cancel
            ),
            "correlation": lambda cancel: correlation_summary(
                snapshot.dataset.frame,
                ("x", "y", "z"),
                snapshot.mask,
                cancel=cancel,
            ),
        }
        for name, work in jobs.items():

            def observed(cancel, *, work=work):
                worker_ids.append(threading.get_ident())
                return work(cancel)

            self.coordinator.submit_task(
                f"statistics-struct-{name}",
                observed,
                success=lambda result, name=name: results.setdefault(name, result),
                receiver=self.coordinator,
            )
            pump_until(lambda: self.coordinator.runner.activity.physical == 0)

        self.assertTrue(worker_ids)
        self.assertTrue(all(worker != gui for worker in worker_ids))
        numeric = results["numeric"].set_index("Column")
        categorical = results["categorical"].set_index("Column")
        correlation = results["correlation"]
        self.assertEqual(numeric.loc["filter", "Rows"], 100_000)
        self.assertEqual(categorical.loc["group", "Rows"], 100_000)
        self.assertEqual(len(correlation), 3)
        self.assertTrue((correlation["Paired N"] <= 100_000).all())
        self.assertIs(self.coordinator.state.dataset.frame, self.frame)
        pd.testing.assert_frame_equal(self.frame, self.original)

    def test_s_200k_latest_request_cancels_predecessor(self):
        snapshot = self.coordinator.state.snapshot()
        entered = threading.Event()
        release = threading.Event()
        published = []

        def first(cancel):
            entered.set()
            release.wait(timeout=10)
            if cancel.is_set():
                raise CancelledError()
            return numeric_summary(snapshot.dataset.frame, snapshot.mask, cancel=cancel)

        self.coordinator.submit_task(
            "statistics",
            first,
            success=lambda result: published.append(("first", result)),
            receiver=self.coordinator,
        )
        self.assertTrue(entered.wait(timeout=2))
        self.coordinator.submit_task(
            "statistics",
            lambda cancel: numeric_summary(
                snapshot.dataset.frame, snapshot.mask, ddof=0, cancel=cancel
            ),
            success=lambda result: published.append(("second", result)),
            receiver=self.coordinator,
        )
        release.set()
        pump_until(lambda: self.coordinator.runner.activity.physical == 0)
        self.assertEqual([name for name, _ in published], ["second"])
        self.assertEqual(set(published[0][1]["ddof"]), {0})


if __name__ == "__main__":
    unittest.main()
