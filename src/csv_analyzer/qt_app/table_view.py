"""Minimal F3.B Qt table exploration view."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..table import SearchMode, SortDirection, TableScope


class TableView(QWidget):
    scope_requested = Signal(object)
    sort_requested = Signal(int, object)
    reset_sort_requested = Signal()
    search_requested = Signal(str, object)
    search_activated = Signal(str, object)
    previous_requested = Signal()
    next_requested = Signal()
    show_all_requested = Signal()
    go_requested = Signal(str)
    copy_requested = Signal()
    filters_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Scope:"))
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Selected rows", TableScope.SELECTED)
        self.scope_combo.addItem("All rows", TableScope.ALL)
        self.scope_combo.currentIndexChanged.connect(self._emit_scope)
        scope_row.addWidget(self.scope_combo)

        self.filter_button = QPushButton("Filters · none")
        self.filter_button.setEnabled(False)
        self.filter_button.clicked.connect(self.filters_requested)
        scope_row.addWidget(self.filter_button)

        self.reset_sort_button = QPushButton("Reset sort")
        self.reset_sort_button.clicked.connect(self.reset_sort_requested)
        scope_row.addWidget(self.reset_sort_button)
        self.count_label = QLabel("Selected rows · 0 / 0")
        scope_row.addWidget(self.count_label)
        scope_row.addStretch(1)
        layout.addLayout(scope_row)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search all dataset columns")
        search_row.addWidget(self.search_edit, 1)
        self.search_mode = QComboBox()
        self.search_mode.addItem("Contains", SearchMode.CONTAINS_CASE_INSENSITIVE)
        self.search_mode.addItem("Exact", SearchMode.EXACT)
        search_row.addWidget(self.search_mode)
        self.previous_button = QPushButton("Prev")
        self.next_button = QPushButton("Next")
        self.previous_button.clicked.connect(self.previous_requested)
        self.next_button.clicked.connect(self.next_requested)
        search_row.addWidget(self.previous_button)
        search_row.addWidget(self.next_button)
        self.match_label = QLabel("")
        search_row.addWidget(self.match_label)
        self.show_all_button = QPushButton("Show in All rows")
        self.show_all_button.hide()
        self.show_all_button.clicked.connect(self.show_all_requested)
        search_row.addWidget(self.show_all_button)
        layout.addLayout(search_row)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._emit_search)
        self.search_edit.textChanged.connect(lambda _text: self._search_timer.start())
        self.search_mode.currentIndexChanged.connect(lambda _index: self._search_timer.start())
        self.search_edit.returnPressed.connect(self._activate_search)

        go_row = QHBoxLayout()
        go_row.addWidget(QLabel("Go to Source Row ID:"))
        self.go_edit = QLineEdit()
        self.go_edit.setMaximumWidth(180)
        go_row.addWidget(self.go_edit)
        self.go_button = QPushButton("Go")
        self.go_button.clicked.connect(
            lambda _checked=False: self.go_requested.emit(self.go_edit.text())
        )
        self.go_edit.returnPressed.connect(lambda: self.go_requested.emit(self.go_edit.text()))
        go_row.addWidget(self.go_button)
        self.feedback_label = QLabel("No dataset loaded")
        go_row.addWidget(self.feedback_label, 1)
        layout.addLayout(go_row)

        self.table = QTableView()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self._header_clicked)
        layout.addWidget(self.table, 1)

        copy_action = QAction(self)
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy_action.triggered.connect(self.copy_requested)
        self.addAction(copy_action)

        self._sort_column: int | None = None
        self._sort_direction = SortDirection.ASC

    def set_model(self, model) -> None:
        self.table.setModel(model)

    def prepare_close(self) -> None:
        """Stop deferred UI work and detach the cross-owner model safely."""
        self._search_timer.stop()
        self.table.setModel(None)

    def _emit_scope(self, _index: int | None = None) -> None:
        scope = self.scope_combo.currentData()
        if scope is not None:
            self.scope_requested.emit(scope)

    def _emit_search(self) -> None:
        mode = self.search_mode.currentData()
        self.search_requested.emit(self.search_edit.text(), mode)

    def _activate_search(self) -> None:
        self._search_timer.stop()
        mode = self.search_mode.currentData()
        self.search_activated.emit(self.search_edit.text(), mode)

    def _header_clicked(self, column: int) -> None:
        if column == 0:
            self.reset_sort_requested.emit()
            return
        if self._sort_column == column:
            direction = (
                SortDirection.DESC
                if self._sort_direction is SortDirection.ASC
                else SortDirection.ASC
            )
        else:
            direction = SortDirection.ASC
        self._sort_column = column
        self._sort_direction = direction
        order = (
            Qt.SortOrder.AscendingOrder
            if direction is SortDirection.ASC
            else Qt.SortOrder.DescendingOrder
        )
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(column, order)
        self.sort_requested.emit(column, direction)

    def set_scope(self, scope: TableScope) -> None:
        index = self.scope_combo.findData(scope)
        if index >= 0 and index != self.scope_combo.currentIndex():
            blocked = self.scope_combo.blockSignals(True)
            self.scope_combo.setCurrentIndex(index)
            self.scope_combo.blockSignals(blocked)

    def reset_sort_indicator(self) -> None:
        self._sort_column = None
        self._sort_direction = SortDirection.ASC
        self.table.horizontalHeader().setSortIndicatorShown(False)

    def set_sort_indicator(self, column: int, direction: SortDirection) -> None:
        self._sort_column = column
        self._sort_direction = direction
        order = (
            Qt.SortOrder.AscendingOrder
            if direction is SortDirection.ASC
            else Qt.SortOrder.DescendingOrder
        )
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(column, order)

    def set_search_text(self, text: str) -> None:
        self._search_timer.stop()
        blocked = self.search_edit.blockSignals(True)
        self.search_edit.setText(text)
        self.search_edit.blockSignals(blocked)

    def set_filter_state(self, rule_count: int, *, enabled: bool) -> None:
        self.filter_button.setText(f"Filters · {rule_count if rule_count else 'none'}")
        self.filter_button.setEnabled(enabled)
        self.filter_button.setToolTip(
            "Edit inclusive numeric filters on raw dataset values."
            if enabled
            else "Numeric filters require a dataset with at least one numeric column."
        )

    def set_counts(self, scope: TableScope, visible: int, selected: int, total: int) -> None:
        if scope is TableScope.SELECTED:
            self.count_label.setText(f"Selected rows · {visible:,} / {total:,}")
        else:
            self.count_label.setText(f"All rows · {visible:,} / {total:,}")
        self.count_label.setToolTip(f"Analytically selected rows: {selected:,} / {total:,}")

    def set_search_status(self, text: str, *, show_all: bool = False) -> None:
        self.match_label.setText(text)
        self.show_all_button.setVisible(show_all)

    def set_feedback(self, text: str) -> None:
        self.feedback_label.setText(text)

    def clear_visual_selection(self) -> None:
        selection_model = self.table.selectionModel()
        if selection_model is not None:
            selection_model.clear()

    def capture_selection(self, model) -> tuple[tuple[int, ...], int | None]:
        selection_model = self.table.selectionModel()
        if selection_model is None:
            return (), None
        selected = tuple(
            model.source_row_id_at(index.row())
            for index in sorted(selection_model.selectedRows(0), key=lambda item: item.row())
        )
        current = selection_model.currentIndex()
        current_id = model.source_row_id_at(current.row()) if current.isValid() else None
        return selected, current_id

    def restore_selection(
        self,
        model,
        source_ids: Iterable[int],
        current_source_id: int | None,
    ) -> None:
        selection_model = self.table.selectionModel()
        if selection_model is None:
            return
        selection_model.clearSelection()
        for source_id in source_ids:
            row = model.visible_row_for_source_id(int(source_id))
            if row is None:
                continue
            index = model.index(row, 0)
            selection_model.select(
                index,
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )
        if current_source_id is not None:
            row = model.visible_row_for_source_id(current_source_id)
            if row is not None:
                selection_model.setCurrentIndex(
                    model.index(row, 0),
                    QItemSelectionModel.SelectionFlag.NoUpdate,
                )

    def select_source_id(self, model, source_id: int) -> bool:
        row = model.visible_row_for_source_id(source_id)
        if row is None:
            return False
        selection_model = self.table.selectionModel()
        if selection_model is None:
            return False
        index = model.index(row, 0)
        selection_model.clearSelection()
        selection_model.select(
            index,
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
        selection_model.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        return True

    def selected_rows(self) -> list[int]:
        selection_model = self.table.selectionModel()
        if selection_model is None:
            return []
        return sorted({index.row() for index in selection_model.selectedRows(0)})

    def visible_columns(self) -> list[int]:
        model = self.table.model()
        if model is None:
            return []
        return [
            column for column in range(model.columnCount()) if not self.table.isColumnHidden(column)
        ]
