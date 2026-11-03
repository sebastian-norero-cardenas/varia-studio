"""Qt shell with the F3.B tabular exploration view."""

from __future__ import annotations

from PySide6.QtCore import QMetaObject, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .table_view import TableView


class QtShell(QMainWindow):
    open_requested = Signal()
    cancel_requested = Signal()
    close_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._allow_close = False
        self.setWindowTitle("Varia Studio — Qt Foundation")
        self.resize(1100, 700)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        title = QLabel("Varia Studio")
        title.setObjectName("title")
        layout.addWidget(title)

        self.dataset_label = QLabel("No dataset loaded")
        self.dataset_label.setObjectName("dataset")
        layout.addWidget(self.dataset_label)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        actions = QHBoxLayout()
        self.open_button = QPushButton("Open…")
        self.open_button.clicked.connect(self.open_requested)
        actions.addWidget(self.open_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested)
        actions.addWidget(self.cancel_button)

        self.quit_button = QPushButton("Quit")
        self.quit_button.clicked.connect(self.close)
        actions.addWidget(self.quit_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.table_view = TableView(self)
        layout.addWidget(self.table_view, 1)
        layout.addWidget(QLabel("Charts — not migrated yet"))
        layout.addWidget(QLabel("Statistics — not migrated yet"))
        self.setCentralWidget(root)

    def set_dataset(self, name: str | None, rows: int = 0, columns: int = 0) -> None:
        if name is None:
            self.dataset_label.setText("No dataset loaded")
        else:
            self.dataset_label.setText(f"{name} · {rows:,} rows · {columns} columns")

    def set_status(self, text: str) -> None:
        self.status_label.setText(text if len(text) <= 180 else text[:177] + "…")

    def set_activity(self, logical: int, physical: int) -> None:
        busy = logical > 0 or physical > 0
        self.progress.setVisible(busy)
        self.cancel_button.setEnabled(logical > 0)
        self.open_button.setEnabled(logical == 0)

    def show_error(self, text: str) -> None:
        QMessageBox.critical(self, "Varia Studio", text)

    def begin_closing(self, physical: int) -> None:
        self.open_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.quit_button.setEnabled(False)
        self.set_status(
            "Closing…"
            if physical == 0
            else f"Closing… waiting for {physical} physical task(s) to finish."
        )

    def allow_close(self) -> None:
        if self._allow_close:
            return
        self._allow_close = True
        # close_requested is emitted synchronously from closeEvent(). Queue the
        # definitive QWidget.close() until that ignored event has fully unwound.
        # Using the QObject as receiver avoids retaining a Python callback after
        # the shell itself has been destroyed.
        QMetaObject.invokeMethod(self, "close", Qt.ConnectionType.QueuedConnection)

    def closeEvent(self, event) -> None:
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        self.close_requested.emit()
