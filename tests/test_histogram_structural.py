"""S-200K-HISTOGRAM structural preparation evidence without repeated drawing."""

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

from csv_analyzer.data import CancelledError, Dataset, describe_columns
from csv_analyzer.plots import HistogramSpec
from csv_analyzer.plots import prepare_histogram as real_prepare_histogram
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule

APP = QApplication.instance() or QApplication(["varia-f3e-structural"])


def pump_until(predicate, timeout_ms=10000):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(2)
    timeout = QTimer()
    timeout.setSingleShot(True)
    expired = {"value": False}

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


class HistogramStructuralTests(unittest.TestCase):
    def setUp(self):
        rows = 200_000
        values = np.linspace(1.0, 1000.0, rows, dtype=np.float64)
        values[::1000] = np.nan
        self.frame = pd.DataFrame(
            {
                "distribution": values,
                "filter": np.arange(rows, dtype=np.int64),
            }
        )
        self.dataset = Dataset(
            frame=self.frame,
            path=Path("S-200K-HISTOGRAM.csv"),
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

    def test_s_200k_histogram_worker_mask_edges_counts_and_no_dataframe_copy(self):
        snap = self.coordinator.state.snapshot()
        mask = np.arange(200_000) >= 100_000
        self.assertTrue(
            self.coordinator.state.commit_filters(
                snap.revision,
                (FilterRule("filter", lower=100_000),),
                mask,
            )
        )
        snapshot = self.coordinator.state.snapshot()
        gui = threading.get_ident()
        worker_threads = []
        result = {}
        spec = HistogramSpec("distribution", bins="fd")

        def observed(frame, spec, mask=None, *, cancel=None):
            worker_threads.append(threading.get_ident())
            return real_prepare_histogram(frame, spec, mask, cancel=cancel)

        token = self.coordinator.submit_task(
            "histogram-struct",
            lambda cancel: observed(
                snapshot.dataset.frame,
                spec,
                snapshot.mask,
                cancel=cancel,
            ),
            success=lambda data: result.setdefault("data", data),
            receiver=self.coordinator,
            exclusive_group="plots",
        )
        self.assertIsNotNone(token)
        pump_until(lambda: self.coordinator.runner.activity.physical == 0)

        data = result["data"]
        self.assertTrue(worker_threads)
        self.assertTrue(all(thread != gui for thread in worker_threads))
        self.assertEqual(data.total, 200_000)
        self.assertEqual(data.selected, 100_000)
        self.assertEqual(int(data.counts.sum()), data.in_range)
        self.assertTrue((np.diff(data.edges) > 0).all())
        self.assertFalse(hasattr(data, "frame"))
        self.assertIs(self.coordinator.state.dataset.frame, self.frame)

    def test_s_200k_histogram_latest_request_cancels_predecessor(self):
        snapshot = self.coordinator.state.snapshot()
        entered = threading.Event()
        release = threading.Event()
        published = []

        def controlled(spec, cancel):
            if spec.bins == 5:
                entered.set()
                release.wait(timeout=10)
                if cancel.is_set():
                    raise CancelledError()
            return real_prepare_histogram(
                snapshot.dataset.frame,
                spec,
                snapshot.mask,
                cancel=cancel,
            )

        first = HistogramSpec("distribution", bins=5)
        second = HistogramSpec("distribution", bins=11)
        self.coordinator.submit_task(
            "histogram-struct",
            lambda cancel: controlled(first, cancel),
            success=lambda data: published.append(("first", data)),
            receiver=self.coordinator,
            exclusive_group="plots",
        )
        self.assertTrue(entered.wait(timeout=2))
        self.coordinator.submit_task(
            "histogram-struct",
            lambda cancel: controlled(second, cancel),
            success=lambda data: published.append(("second", data)),
            receiver=self.coordinator,
            exclusive_group="plots",
        )
        release.set()
        pump_until(lambda: self.coordinator.runner.activity.physical == 0)
        self.assertEqual([name for name, _ in published], ["second"])
        self.assertEqual(len(published[0][1].counts), 11)


if __name__ == "__main__":
    unittest.main()
