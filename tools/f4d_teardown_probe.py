"""Independent-process shutdown probe for the F4.D Qt cutover gate."""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from csv_analyzer.data import CancelledError, ImportOptions, load_dataset
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.shell import QtShell


def pump_until(predicate, timeout_ms: int = 10_000) -> None:
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    timeout = QTimer()
    expired = {"value": False}
    poll.setInterval(2)
    timeout.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)

    def expire() -> None:
        expired["value"] = True
        loop.quit()

    timeout.timeout.connect(expire)
    poll.start()
    timeout.start(timeout_ms)
    loop.exec()
    poll.stop()
    timeout.stop()
    if expired["value"] and not predicate():
        raise RuntimeError("Qt teardown probe timed out waiting for the requested state.")


def make_csv(root: Path) -> Path:
    path = root / "probe.csv"
    path.write_text("x,y,c\n1,2,10\n2,4,20\n3,6,30\n4,8,40\n", encoding="utf-8")
    return path


def commit_dataset(coordinator: QtCoordinator, path: Path) -> None:
    dataset = load_dataset(path, ImportOptions())
    ticket = coordinator.state.begin_load()
    if not coordinator.state.commit_dataset(ticket, dataset):
        raise RuntimeError("Could not commit teardown-probe dataset.")
    QApplication.processEvents()


def make_app(*, loader=load_dataset):
    app = QApplication.instance() or QApplication(["varia-f4d-teardown-probe"])
    shell = QtShell()
    coordinator = QtCoordinator(shell, loader=loader)
    coordinator.start()
    shell.show()
    QApplication.processEvents()
    return app, coordinator, shell


def close_and_assert(coordinator: QtCoordinator, shell: QtShell) -> None:
    shell.close()
    pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED and not shell.isVisible())
    if coordinator.runner.activity.physical != 0:
        raise RuntimeError("Coordinator closed with physical task ownership remaining.")


def blocking_load(path, options, *, cancel=None):
    while cancel is not None and not cancel.wait(0.002):
        pass
    raise CancelledError()


def blocking_plot(*_args, cancel=None, **_kwargs):
    while cancel is not None and not cancel.wait(0.002):
        pass
    raise CancelledError()


def blocking_numeric(*_args, cancel=None, **_kwargs):
    while cancel is not None and not cancel.wait(0.002):
        pass
    raise CancelledError()


def run_scenario(name: str) -> None:
    with tempfile.TemporaryDirectory(prefix="varia-f4d-") as directory:
        path = make_csv(Path(directory))

        if name == "pending_load_close":
            _app, coordinator, shell = make_app(loader=blocking_load)
            coordinator.load_path(path)
            pump_until(lambda: coordinator.runner.activity.physical > 0)
            close_and_assert(coordinator, shell)
            return

        _app, coordinator, shell = make_app()
        if name == "launch_close":
            close_and_assert(coordinator, shell)
            return

        if name == "load_close":
            coordinator.load_path(path)
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            if coordinator.state.dataset is None:
                raise RuntimeError("Load did not commit before close.")
            close_and_assert(coordinator, shell)
            return

        commit_dataset(coordinator, path)

        if name == "scatter_close":
            coordinator.scatter_controller.request_plot()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            if coordinator.scatter_controller.current_spec is None:
                raise RuntimeError("Scatter did not publish before close.")
            close_and_assert(coordinator, shell)
            return

        if name == "histogram_close":
            coordinator.histogram_controller.request_plot()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            if coordinator.histogram_controller.current_spec is None:
                raise RuntimeError("Histogram did not publish before close.")
            close_and_assert(coordinator, shell)
            return

        if name == "statistics_close":
            coordinator.statistics_controller.request_compute()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            if coordinator.statistics_controller.current_result is None:
                raise RuntimeError("Statistics did not publish before close.")
            close_and_assert(coordinator, shell)
            return

        if name == "pending_scatter_close":
            import csv_analyzer.qt_app.scatter_controller as scatter_module

            original = scatter_module.prepare_scatter
            scatter_module.prepare_scatter = blocking_plot
            try:
                coordinator.scatter_controller.request_plot()
                pump_until(lambda: coordinator.runner.activity.physical > 0)
                close_and_assert(coordinator, shell)
            finally:
                scatter_module.prepare_scatter = original
            return

        if name == "pending_histogram_close":
            import csv_analyzer.qt_app.histogram_controller as histogram_module

            original = histogram_module.prepare_histogram
            histogram_module.prepare_histogram = blocking_plot
            try:
                coordinator.histogram_controller.request_plot()
                pump_until(lambda: coordinator.runner.activity.physical > 0)
                close_and_assert(coordinator, shell)
            finally:
                histogram_module.prepare_histogram = original
            return

        if name == "pending_statistics_close":
            import csv_analyzer.qt_app.statistics_controller as statistics_module

            original = statistics_module.numeric_summary
            statistics_module.numeric_summary = blocking_numeric
            try:
                coordinator.statistics_controller.request_compute()
                pump_until(lambda: coordinator.runner.activity.physical > 0)
                close_and_assert(coordinator, shell)
            finally:
                statistics_module.numeric_summary = original
            return

        raise ValueError(f"Unknown teardown scenario: {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        choices=(
            "launch_close",
            "load_close",
            "scatter_close",
            "histogram_close",
            "statistics_close",
            "pending_load_close",
            "pending_scatter_close",
            "pending_histogram_close",
            "pending_statistics_close",
        ),
    )
    args = parser.parse_args()
    start = time.monotonic()
    run_scenario(args.scenario)
    elapsed = time.monotonic() - start
    print(
        f"scenario={args.scenario} lifecycle=CLOSED physical=0 "
        f"exit=0 elapsed_seconds={elapsed:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
