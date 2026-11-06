"""Focused staged editor for Qt import options."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..data import ImportOptions


class ImportOptionsDialog(QDialog):
    """Edit a staged ImportOptions value and publish it only on Save."""

    options_saved = Signal(object)

    DELIMITERS = (
        ("Auto", "auto"),
        ("Comma", ","),
        ("Semicolon", ";"),
        ("Tab", "\t"),
        ("Pipe", "|"),
        ("Whitespace (DAT)", "whitespace"),
    )
    ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1", "utf-16")
    HEADERS = (("Auto", "auto"), ("Yes", "yes"), ("No", "no"))
    DECIMALS = (".", ",")

    def __init__(self, options: ImportOptions, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import options")
        self.setModal(True)
        self.resize(520, 430)
        self._initial = options

        layout = QVBoxLayout(self)
        note = QLabel(
            "These settings apply to the next Open or Reload. "
            "Cancel leaves the current options unchanged.",
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()

        self.delimiter = QComboBox(self)
        for label, value in self.DELIMITERS:
            self.delimiter.addItem(label, value)
        self._select_data(self.delimiter, options.delimiter)
        form.addRow("Delimiter", self.delimiter)

        self.encoding = QComboBox(self)
        self.encoding.addItems(self.ENCODINGS)
        self.encoding.setCurrentText(options.encoding)
        form.addRow("Encoding", self.encoding)

        self.header = QComboBox(self)
        for label, value in self.HEADERS:
            self.header.addItem(label, value)
        self._select_data(self.header, options.header)
        form.addRow("Header row", self.header)

        self.decimal = QComboBox(self)
        self.decimal.addItems(self.DECIMALS)
        self.decimal.setCurrentText(options.decimal)
        form.addRow("Decimal separator", self.decimal)

        layout.addLayout(form)

        dates_label = QLabel("ISO date columns", self)
        layout.addWidget(dates_label)
        self.date_columns = QPlainTextEdit(self)
        self.date_columns.setPlaceholderText("One exact column name per line")
        self.date_columns.setPlainText("\n".join(options.date_columns))
        layout.addWidget(self.date_columns, 1)

        dates_note = QLabel(
            "Only completely empty lines are ignored. Non-empty column names are "
            "preserved exactly as entered.",
            self,
        )
        dates_note.setWordWrap(True)
        layout.addWidget(dates_note)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = QPushButton("Save options", self)
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

    @staticmethod
    def _select_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def staged_options(self) -> ImportOptions:
        """Build a new immutable value without mutating coordinator state."""
        date_columns = tuple(
            line for line in self.date_columns.toPlainText().splitlines() if line != ""
        )
        return ImportOptions(
            delimiter=str(self.delimiter.currentData()),
            encoding=self.encoding.currentText(),
            header=str(self.header.currentData()),
            decimal=self.decimal.currentText(),
            comment=self._initial.comment,
            date_columns=date_columns,
        )

    def _save(self) -> None:
        self.options_saved.emit(self.staged_options())
        self.accept()
