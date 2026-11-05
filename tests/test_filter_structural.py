"""S-200K-FILTER structural evidence for F3.C."""

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

from csv_analyzer.analysis import build_filter_mask as real_build_filter_mask
from csv_analyzer.data import Dataset, describe_columns
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.state import FilterRule

APP = QApplication.instance() or QApplication(["varia-f3c-structural"])


def pump_until(predicate, timeout_ms=10000):
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


class FilterStructuralTests(unittest.TestCase):
    def setUp(self):
        rows = 200_000
        self.frame = pd.DataFrame(
            {
                "i": np.arange(rows, dtype=np.int64),
                "u": pd.Series(
                    np.arange(rows, dtype=np.uint64) + np.uint64(2**63),
                    dtype="UInt64",
                ),
                "f": np.linspace(-1000.0, 1000.0, rows, dtype=np.float64),
                "j": np.arange(rows, dtype=np.int64) % 17,
            }
        )
        self.dataset = Dataset(
            frame=self.frame,
            path=Path("S-200K-FILTER.csv"),
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

    def test_s_200k_filter_background_mask_shape_and_read_only_commit(self):
        gui_thread = threading.get_ident()
        worker_threads = []

        def observed(frame, rules, cancel=None):
            worker_threads.append(threading.get_ident())
            return real_build_filter_mask(frame, rules, cancel)

        rules = (
            FilterRule("i", lower=50_000, upper=150_000),
            FilterRule("j", lower=3, upper=9),
            FilterRule("f", lower=-500.0, upper=500.0),
        )
        with patch("csv_analyzer.qt_app.coordinator.build_filter_mask", observed):
            self.assertTrue(self.coordinator.apply_filters(rules))
            pump_until(lambda: self.coordinator.runner.activity.physical == 0)

        mask = self.coordinator.state.mask
        self.assertTrue(worker_threads)
        self.assertTrue(all(thread != gui_thread for thread in worker_threads))
        self.assertEqual(mask.dtype, np.bool_)
        self.assertEqual(mask.shape, (200_000,))
        self.assertEqual(mask.nbytes, 200_000)
        self.assertFalse(mask.flags.writeable)
        self.assertIs(self.coordinator.state.dataset.frame, self.frame)
        self.assertEqual(self.coordinator.state.filters, rules)

    def test_s_200k_filter_replacement_cancels_predecessor_and_latest_commits(self):
        entered = threading.Event()
        release = threading.Event()
        calls = {"count": 0}

        def controlled(frame, rules, cancel=None):
            calls["count"] += 1
            if calls["count"] == 1:
                entered.set()
                release.wait(timeout=10)
            return real_build_filter_mask(frame, rules, cancel)

        first = (FilterRule("i", lower=10_000),)
        second = (FilterRule("i", lower=190_000),)
        with patch("csv_analyzer.qt_app.coordinator.build_filter_mask", controlled):
            self.assertTrue(self.coordinator.apply_filters(first))
            self.assertTrue(entered.wait(timeout=2))
            self.assertTrue(self.coordinator.apply_filters(second))
            release.set()
            pump_until(lambda: self.coordinator.runner.activity.physical == 0)

        self.assertEqual(self.coordinator.state.filters, second)
        self.assertEqual(self.coordinator.state.selected_count, 10_000)
        self.assertGreaterEqual(calls["count"], 2)


if __name__ == "__main__":
    unittest.main()
