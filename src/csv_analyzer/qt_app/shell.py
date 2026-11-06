"""Qt shell with the F3.B tabular exploration view."""

from __future__ import annotations

from PySide6.QtCore import QMetaObject, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .histogram_view import HistogramView
from .scatter_view import ScatterView
from .statistics_view import StatisticsView
from .table_view import TableView


class QtShell(QMainWindow):
    open_requested = Signal()
    reload_requested = Signal()
    import_options_requested = Signal()
    cancel_requested = Signal()
    close_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._allow_close = False
        self._has_dataset = False
        self._logical_activity = 0
        self._notices: tuple[str, ...] = ()
        self.setWindowTitle("Varia Studio")
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
        self.open_button.clicked.connect(lambda _checked=False: self.open_requested.emit())
        actions.addWidget(self.open_button)

        self.reload_button = QPushButton("Reload")
        self.reload_button.setEnabled(False)
        self.reload_button.clicked.connect(lambda _checked=False: self.reload_requested.emit())
        actions.addWidget(self.reload_button)

        self.import_options_button = QPushButton("Import options…")
        self.import_options_button.clicked.connect(
            lambda _checked=False: self.import_options_requested.emit()
        )
        actions.addWidget(self.import_options_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(lambda _checked=False: self.cancel_requested.emit())
        actions.addWidget(self.cancel_button)

        self.guide_button = QPushButton("Guide")
        self.guide_button.clicked.connect(self.show_guide)
        actions.addWidget(self.guide_button)

        self.quit_button = QPushButton("Quit")
        self.quit_button.clicked.connect(self.close)
        actions.addWidget(self.quit_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.open_action = QAction("Open…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.open_action.triggered.connect(lambda _checked=False: self.open_requested.emit())
        self.addAction(self.open_action)

        self.reload_action = QAction("Reload", self)
        self.reload_action.setShortcut(QKeySequence("Ctrl+R"))
        self.reload_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.reload_action.setEnabled(False)
        self.reload_action.triggered.connect(lambda _checked=False: self.reload_requested.emit())
        self.addAction(self.reload_action)

        self.tabs = QTabWidget(self)
        self.data_tab = QWidget(self.tabs)
        data_layout = QVBoxLayout(self.data_tab)
        data_layout.setContentsMargins(0, 0, 0, 0)

        notices_row = QHBoxLayout()
        self.notices_label = QLabel("No import notices")
        self.notices_label.setWordWrap(True)
        notices_row.addWidget(self.notices_label, 1)
        self.notices_button = QPushButton("Import notices…")
        self.notices_button.setEnabled(False)
        self.notices_button.clicked.connect(self.show_import_notices)
        notices_row.addWidget(self.notices_button)
        data_layout.addLayout(notices_row)

        self.table_view = TableView(self.data_tab)
        data_layout.addWidget(self.table_view)
        self.tabs.addTab(self.data_tab, "Data")

        self.scatter_view = ScatterView(self.tabs)
        self.tabs.addTab(self.scatter_view, "Relationships")

        self.histogram_view = HistogramView(self.tabs)
        self.tabs.addTab(self.histogram_view, "Distributions")

        self.statistics_view = StatisticsView(self.tabs)
        self.tabs.addTab(self.statistics_view, "Statistics")

        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

    def set_dataset(
        self,
        name: str | None,
        rows: int = 0,
        columns: int = 0,
        *,
        missing: int = 0,
        infinite: int = 0,
        selected: int | None = None,
        notices: tuple[str, ...] = (),
    ) -> None:
        self._has_dataset = name is not None
        if name is None:
            self.dataset_label.setText("No dataset loaded")
            self._notices = ()
            self.notices_label.setText("No import notices")
            self.notices_button.setEnabled(False)
        else:
            selected = rows if selected is None else selected
            self.dataset_label.setText(
                f"{name} · {rows:,} rows · {columns} columns · "
                f"{missing:,} missing · {infinite:,} infinite · {selected:,} selected"
            )
            self._notices = tuple(notices)
            if self._notices:
                count = len(self._notices)
                self.notices_label.setText(
                    f"{count} import notice{'s' if count != 1 else ''} available"
                )
                self.notices_button.setEnabled(True)
            else:
                self.notices_label.setText("No import notices")
                self.notices_button.setEnabled(False)
        idle = self._logical_activity == 0
        self.reload_button.setEnabled(self._has_dataset and idle)
        self.reload_action.setEnabled(self._has_dataset and idle)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text if len(text) <= 180 else text[:177] + "…")

    def set_activity(self, logical: int, physical: int) -> None:
        self._logical_activity = logical
        busy = logical > 0 or physical > 0
        self.progress.setVisible(busy)
        self.cancel_button.setEnabled(logical > 0)
        idle = logical == 0
        self.open_button.setEnabled(idle)
        self.open_action.setEnabled(idle)
        self.reload_button.setEnabled(self._has_dataset and idle)
        self.reload_action.setEnabled(self._has_dataset and idle)

    def show_import_notices(self) -> None:
        if not self._notices:
            return
        QMessageBox.information(self, "Import notices", "\n\n".join(self._notices))

    def show_guide(self) -> None:
        QMessageBox.information(
            self,
            "Varia Studio guide",
            "Open & inspect\n"
            "Filters\n"
            "Relationships / Distributions\n"
            "Statistics\n"
            "Export\n\n"
            "Ctrl+O — Open\n"
            "Ctrl+R — Reload",
        )

    def show_error(self, text: str) -> None:
        QMessageBox.critical(self, "Varia Studio", text)

    def begin_closing(self, physical: int) -> None:
        self.open_button.setEnabled(False)
        self.open_action.setEnabled(False)
        self.reload_button.setEnabled(False)
        self.reload_action.setEnabled(False)
        self.import_options_button.setEnabled(False)
        self.guide_button.setEnabled(False)
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
