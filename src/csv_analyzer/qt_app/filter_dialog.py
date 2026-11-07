"""Small staged editor for F3.C numeric analytical filters."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from pandas.api.types import is_integer_dtype
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..data import DataError, Dataset
from ..state import FilterRule


def parse_filter_bound(text: str, label: str, *, exact: bool) -> int | float | Decimal | None:
    """Parse optional UI text without losing integer-column precision."""
    text = text.strip()
    if not text:
        return None
    try:
        if exact:
            value = Decimal(text)
            if not value.is_finite():
                raise ValueError
            return int(value) if value == value.to_integral_value() else value
        value = float(text)
        if not math.isfinite(value):
            raise ValueError
        return value
    except (InvalidOperation, ValueError, OverflowError) as error:
        raise DataError(f"{label}: enter a finite number.") from error


class FilterDialog(QDialog):
    """Edit a short staged list of numeric FilterRule values."""

    apply_requested = Signal(object, int, int, int)

    def __init__(
        self,
        dataset: Dataset,
        rules: tuple[FilterRule, ...],
        *,
        revision: int,
        dataset_revision: int,
        selection_revision: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filters")
        self.setModal(True)
        self.resize(620, 460)
        self.dataset = dataset
        self.rules = list(rules)
        self.revision = revision
        self.dataset_revision = dataset_revision
        self.selection_revision = selection_revision
        self._edit_index: int | None = None

        layout = QVBoxLayout(self)
        note = QLabel(
            "All conditions must match. Bounds are inclusive and apply to raw values. "
            "Missing, NaN and infinite values do not match a condition."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        fields = QHBoxLayout()
        self.column_combo = QComboBox()
        self.column_combo.addItems(dataset.numeric_columns)
        fields.addWidget(self.column_combo, 2)
        self.minimum_edit = QLineEdit()
        self.minimum_edit.setPlaceholderText("Minimum (optional)")
        fields.addWidget(self.minimum_edit, 1)
        self.maximum_edit = QLineEdit()
        self.maximum_edit.setPlaceholderText("Maximum (optional)")
        fields.addWidget(self.maximum_edit, 1)
        layout.addLayout(fields)

        edit_actions = QHBoxLayout()
        self.add_button = QPushButton("Add condition")
        self.add_button.clicked.connect(self.add_condition)
        edit_actions.addWidget(self.add_button)
        self.update_button = QPushButton("Update condition")
        self.update_button.setEnabled(False)
        self.update_button.clicked.connect(self.update_condition)
        edit_actions.addWidget(self.update_button)
        self.new_button = QPushButton("Clear editor")
        self.new_button.clicked.connect(self.new_condition)
        edit_actions.addWidget(self.new_button)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_condition)
        edit_actions.addWidget(self.remove_button)
        edit_actions.addStretch(1)
        layout.addLayout(edit_actions)

        self.rules_table = QTableWidget(0, 3)
        self.rules_table.setHorizontalHeaderLabels(["Column", "Minimum", "Maximum"])
        self.rules_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rules_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rules_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rules_table.itemSelectionChanged.connect(self._load_selected_rule)
        layout.addWidget(self.rules_table, 1)

        staged_actions = QHBoxLayout()
        self.clear_button = QPushButton("Clear all conditions")
        self.clear_button.clicked.connect(self.clear_conditions)
        staged_actions.addWidget(self.clear_button)
        staged_actions.addStretch(1)
        layout.addLayout(staged_actions)

        self.feedback_label = QLabel("")
        self.feedback_label.setWordWrap(True)
        layout.addWidget(self.feedback_label)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        self.apply_button = QPushButton("Apply filters")
        self.apply_button.clicked.connect(self._request_apply)
        footer.addWidget(self.apply_button)
        layout.addLayout(footer)

        self._refresh_rules()

    @property
    def staged_rules(self) -> tuple[FilterRule, ...]:
        return tuple(self.rules)

    def set_feedback(self, text: str) -> None:
        self.feedback_label.setText(text)

    def _current_rule(self) -> FilterRule:
        column = self.column_combo.currentText()
        if column not in self.dataset.numeric_columns:
            raise DataError("Choose a numeric column from the list.")
        exact = is_integer_dtype(self.dataset.frame[column].dtype)
        lower = parse_filter_bound(self.minimum_edit.text(), "Minimum", exact=exact)
        upper = parse_filter_bound(self.maximum_edit.text(), "Maximum", exact=exact)
        if lower is None and upper is None:
            raise DataError("Enter at least one bound.")
        if lower is not None and upper is not None and lower > upper:
            raise DataError("Minimum must not exceed maximum.")
        return FilterRule(column, lower, upper)

    def _run_edit(self, callback) -> None:
        try:
            callback()
            self.set_feedback("")
        except DataError as error:
            self.set_feedback(str(error))

    def add_condition(self) -> None:
        def add() -> None:
            self.rules.append(self._current_rule())
            self._refresh_rules()
            self.new_condition()

        self._run_edit(add)

    def update_condition(self) -> None:
        def update() -> None:
            if self._edit_index is None or self._edit_index >= len(self.rules):
                raise DataError("Select a condition to update.")
            self.rules[self._edit_index] = self._current_rule()
            self._refresh_rules()
            self.new_condition()

        self._run_edit(update)

    def new_condition(self) -> None:
        self._edit_index = None
        self.rules_table.clearSelection()
        self.minimum_edit.clear()
        self.maximum_edit.clear()
        self.update_button.setEnabled(False)

    def remove_condition(self) -> None:
        row = self.rules_table.currentRow()
        if row < 0 or row >= len(self.rules):
            return
        del self.rules[row]
        self._refresh_rules()
        self.new_condition()

    def clear_conditions(self) -> None:
        self.rules.clear()
        self._refresh_rules()
        self.new_condition()

    def _load_selected_rule(self) -> None:
        row = self.rules_table.currentRow()
        if row < 0 or row >= len(self.rules):
            self._edit_index = None
            self.update_button.setEnabled(False)
            return
        self._edit_index = row
        rule = self.rules[row]
        self.column_combo.setCurrentText(rule.column)
        self.minimum_edit.setText("" if rule.lower is None else str(rule.lower))
        self.maximum_edit.setText("" if rule.upper is None else str(rule.upper))
        self.update_button.setEnabled(True)

    def _refresh_rules(self) -> None:
        self.rules_table.setRowCount(len(self.rules))
        for row, rule in enumerate(self.rules):
            for column, value in enumerate((rule.column, rule.lower, rule.upper)):
                text = "" if value is None else str(value)
                self.rules_table.setItem(row, column, QTableWidgetItem(text))
        self.rules_table.resizeColumnsToContents()

    def _request_apply(self) -> None:
        self.set_feedback("")
        self.apply_requested.emit(
            tuple(self.rules),
            self.revision,
            self.dataset_revision,
            self.selection_revision,
        )
