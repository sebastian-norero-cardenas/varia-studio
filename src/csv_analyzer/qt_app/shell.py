"""Canonical Qt shell with productized F5.A UX polish."""

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


class ElidedLabel(QLabel):
    """Single-line label that preserves full text in its tooltip."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__("", parent)
        self._full_text = ""
        self.set_full_text(text)

    @property
    def full_text(self) -> str:
        return self._full_text

    def text(self) -> str:
        """Return the logical full text while QLabel still paints the elided form."""
        return self._full_text

    def displayed_text(self) -> str:
        return super().text()

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._refresh_elision()

    def _refresh_elision(self) -> None:
        width = max(1, self.contentsRect().width())
        self.setText(
            self.fontMetrics().elidedText(
                self._full_text,
                Qt.TextElideMode.ElideMiddle,
                width,
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_elision()


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
        self.setMinimumSize(900, 600)

        root = QWidget(self)
        layout = QVBoxLayout(root)

        title = QLabel("Varia Studio")
        title.setObjectName("title")
        layout.addWidget(title)

        dataset_row = QHBoxLayout()
        self.dataset_label = ElidedLabel("No dataset loaded", root)
        self.dataset_label.setObjectName("dataset")
        dataset_row.addWidget(self.dataset_label, 2)
        self.dataset_metrics_label = QLabel("", root)
        self.dataset_metrics_label.setObjectName("dataset-metrics")
        dataset_row.addWidget(self.dataset_metrics_label, 3)
        layout.addLayout(dataset_row)

        self.status_label = ElidedLabel("Ready", root)
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

        self.cancel_button = QPushButton("Cancel tasks")
        self.cancel_button.setToolTip("Cancels all running operations")
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

        self.find_action = QAction("Find in Data", self)
        self.find_action.setShortcut(QKeySequence.StandardKey.Find)
        self.find_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.find_action.triggered.connect(self.focus_data_search)
        self.addAction(self.find_action)

        self.tabs = QTabWidget(self)
        self.data_tab = QWidget(self.tabs)
        data_layout = QVBoxLayout(self.data_tab)
        data_layout.setContentsMargins(0, 0, 0, 0)

        self.notices_widget = QWidget(self.data_tab)
        notices_row = QHBoxLayout(self.notices_widget)
        notices_row.setContentsMargins(0, 0, 0, 0)
        self.notices_label = QLabel("", self.notices_widget)
        self.notices_label.setWordWrap(True)
        notices_row.addWidget(self.notices_label, 1)
        self.notices_button = QPushButton("Import notices…", self.notices_widget)
        self.notices_button.clicked.connect(self.show_import_notices)
        notices_row.addWidget(self.notices_button)
        self.notices_widget.hide()
        data_layout.addWidget(self.notices_widget)

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
            self.dataset_label.set_full_text("No dataset loaded")
            self.dataset_metrics_label.clear()
            self._notices = ()
            self.notices_label.clear()
            self.notices_widget.hide()
        else:
            selected = rows if selected is None else selected
            self.dataset_label.set_full_text(name)
            self.dataset_metrics_label.setText(
                f"{rows:,} rows · {columns} columns · {missing:,} missing · "
                f"{infinite:,} infinite · {selected:,} selected"
            )
            self._notices = tuple(notices)
            if self._notices:
                count = len(self._notices)
                self.notices_label.setText(
                    f"{count} import notice{'s' if count != 1 else ''} available"
                )
                self.notices_widget.show()
            else:
                self.notices_label.clear()
                self.notices_widget.hide()
        idle = self._logical_activity == 0
        self.reload_button.setEnabled(self._has_dataset and idle)
        self.reload_action.setEnabled(self._has_dataset and idle)

    def set_status(self, text: str) -> None:
        self.status_label.set_full_text(text)

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

    def focus_data_search(self) -> None:
        self.tabs.setCurrentWidget(self.data_tab)
        self.table_view.focus_search()

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
            "Ctrl+R — Reload\n"
            "Ctrl+F — Find in Data",
        )

    def show_error(self, text: str) -> None:
        QMessageBox.critical(self, "Varia Studio", text)

    def begin_closing(self, physical: int) -> None:
        self.open_button.setEnabled(False)
        self.open_action.setEnabled(False)
        self.reload_button.setEnabled(False)
        self.reload_action.setEnabled(False)
        self.find_action.setEnabled(False)
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
        QMetaObject.invokeMethod(self, "close", Qt.ConnectionType.QueuedConnection)

    def closeEvent(self, event) -> None:
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        self.close_requested.emit()
