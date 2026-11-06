"""Focused column chooser for correlation statistics."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class StatisticsColumnsDialog(QDialog):
    columns_applied = Signal(object)

    def __init__(
        self,
        columns: Sequence[str],
        selected: Sequence[str] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Correlation columns")
        self.resize(420, 460)
        self._columns = tuple(str(column) for column in columns)
        selected_set = set(selected)

        layout = QVBoxLayout(self)
        instructions = QLabel("Choose at least two distinct numeric columns.", self)
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        self.list_widget = QListWidget(self)
        for column in self._columns:
            item = QListWidgetItem(column, self.list_widget)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if column in selected_set else Qt.CheckState.Unchecked
            )
        self.list_widget.itemChanged.connect(self._sync_apply)
        layout.addWidget(self.list_widget, 1)

        self.feedback = QLabel("", self)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)

        quick = QHBoxLayout()
        select_all = QPushButton("Select all", self)
        select_all.clicked.connect(self._select_all)
        quick.addWidget(select_all)
        clear = QPushButton("Clear", self)
        clear.clicked.connect(self._clear)
        quick.addWidget(clear)
        quick.addStretch(1)
        layout.addLayout(quick)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.apply_button = QPushButton("Apply", self)
        self.apply_button.clicked.connect(self._apply)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        self._sync_apply()

    def selected_columns(self) -> tuple[str, ...]:
        return tuple(
            self.list_widget.item(index).text()
            for index in range(self.list_widget.count())
            if self.list_widget.item(index).checkState() == Qt.CheckState.Checked
        )

    def _set_all(self, state: Qt.CheckState) -> None:
        self.list_widget.blockSignals(True)
        try:
            for index in range(self.list_widget.count()):
                self.list_widget.item(index).setCheckState(state)
        finally:
            self.list_widget.blockSignals(False)
        self._sync_apply()

    def _select_all(self) -> None:
        self._set_all(Qt.CheckState.Checked)

    def _clear(self) -> None:
        self._set_all(Qt.CheckState.Unchecked)

    def _sync_apply(self, _item=None) -> None:
        count = len(self.selected_columns())
        self.apply_button.setEnabled(count >= 2)
        self.feedback.setText(
            f"{count} column(s) selected."
            if count >= 2
            else "Select at least two columns before applying."
        )

    def _apply(self) -> None:
        columns = self.selected_columns()
        if len(columns) < 2 or len(set(columns)) != len(columns):
            self._sync_apply()
            return
        self.columns_applied.emit(columns)
        self.accept()
