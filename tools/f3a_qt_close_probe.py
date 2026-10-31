"""Real Qt-shell close probe used by F3.A QA gates."""

from __future__ import annotations

import argparse
import logging
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from csv_analyzer.data import CancelledError
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.shell import QtShell


class _BrokenHandler(logging.Handler):
    def emit(self, record) -> None:
        raise RuntimeError("qa-broken-log-sink")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("quit", "window"), required=True)
    parser.add_argument(
        "--worker",
        choices=("none", "cooperative", "noncancelable"),
        required=True,
    )
    parser.add_argument("--broken-logging", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    app = QApplication(["varia-f3a-close-probe"])
    app.setQuitOnLastWindowClosed(True)

    qt_logger = logging.getLogger("csv_analyzer.qt_app")
    broken_handler = None
    if args.broken_logging:
        broken_handler = _BrokenHandler()
        qt_logger.addHandler(broken_handler)
        qt_logger.setLevel(logging.DEBUG)

    shell = QtShell()
    coordinator = QtCoordinator(shell, max_workers=1)
    coordinator.start()
    shell.show()

    entered = threading.Event()
    cancel_seen = threading.Event()
    release = threading.Event()
    published: list[str] = []

    if args.worker == "cooperative":

        def work(cancel):
            entered.set()
            if not cancel.wait(timeout=5):
                raise RuntimeError("probe worker never received cancellation")
            raise CancelledError()

        coordinator.submit_task("close-probe", work, success=published.append)

    elif args.worker == "noncancelable":

        def work(cancel):
            entered.set()
            if not cancel.wait(timeout=5):
                raise RuntimeError("probe worker never received cancellation")
            cancel_seen.set()
            if not release.wait(timeout=5):
                raise RuntimeError("probe worker release was never signalled")
            return "late"

        def release_after_cancel() -> None:
            if cancel_seen.wait(timeout=5):
                release.set()

        threading.Thread(target=release_after_cancel, daemon=True).start()
        coordinator.submit_task("close-probe", work, success=published.append)

    def request_close() -> None:
        if args.action == "quit":
            shell.quit_button.click()
        else:
            shell.close()

    if args.worker == "none":
        QTimer.singleShot(0, request_close)
    else:
        poll = QTimer()
        poll.setInterval(1)

        def close_when_worker_is_physical() -> None:
            if entered.is_set():
                poll.stop()
                request_close()

        poll.timeout.connect(close_when_worker_is_physical)
        poll.start()

    exit_code = app.exec()

    if coordinator.lifecycle is not Lifecycle.CLOSED:
        raise RuntimeError(f"lifecycle did not close: {coordinator.lifecycle.name}")
    if shell.isVisible():
        raise RuntimeError("shell remained visible after event loop exit")
    if published:
        raise RuntimeError(f"late publication escaped closing gate: {published!r}")

    coordinator.begin_close()
    coordinator.begin_close()
    if coordinator.lifecycle is not Lifecycle.CLOSED:
        raise RuntimeError("idempotent close changed lifecycle")

    if broken_handler is not None:
        qt_logger.removeHandler(broken_handler)

    print(f"action={args.action}")
    print(f"worker={args.worker}")
    print(f"broken_logging={str(args.broken_logging).lower()}")
    print("lifecycle=CLOSED")
    print("window_closed=true")
    print("late_publications=0")
    print(f"event_loop_exit_code={exit_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
