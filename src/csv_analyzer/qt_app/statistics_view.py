"""F3.F Qt controls for toolkit-neutral statistical summaries."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..data import DataError, Dataset
from .statistics_model import StatisticsResultModel


@dataclass(frozen=True)
class StatisticsRequest:
    mode: str
    ddof: int
    correlation_columns: tuple[str, ...]


class StatisticsView(QWidget):
    compute_requested = Signal()
    settings_changed = Signal()
    choose_columns_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dataset: Dataset | None = None
        self._correlation_columns: tuple[str, ...] = ()

        layout = QVBoxLayout(self)
        controls = QGroupBox("Statistics", self)
        form = QFormLayout(controls)

        self.mode = QComboBox(controls)
        self.mode.addItem("Numeric", "numeric")
        self.mode.addItem("Categorical", "categorical")
        self.mode.addItem("Correlation", "correlation")
        self.mode.currentIndexChanged.connect(self._settings_changed)
        form.addRow("Mode", self.mode)

        self.ddof = QComboBox(controls)
        self.ddof.addItem("Sample (N−1)", 1)
        self.ddof.addItem("Population (N)", 0)
        self.ddof.currentIndexChanged.connect(self._settings_changed)
        form.addRow("Convention", self.ddof)

        columns_row = QWidget(controls)
        columns_layout = QHBoxLayout(columns_row)
        columns_layout.setContentsMargins(0, 0, 0, 0)
        self.columns_button = QPushButton("Choose columns…", columns_row)
        self.columns_button.clicked.connect(self.choose_columns_requested)
        columns_layout.addWidget(self.columns_button)
        self.columns_label = QLabel("No columns selected", columns_row)
        self.columns_label.setWordWrap(True)
        columns_layout.addWidget(self.columns_label, 1)
        form.addRow("Correlation", columns_row)

        self.compute_button = QPushButton("Compute", controls)
        self.compute_button.clicked.connect(self.compute_requested)
        form.addRow("", self.compute_button)
        layout.addWidget(controls)

        self.local_status = QLabel("Open a dataset to compute statistics.", self)
        self.local_status.setWordWrap(True)
        layout.addWidget(self.local_status)

        self.notes = QLabel("", self)
        self.notes.setWordWrap(True)
        layout.addWidget(self.notes)

        self.table = QTableView(self)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setWordWrap(False)
        self.result_model = StatisticsResultModel(self.table)
        self.table.setModel(self.result_model)
        self.table.horizontalHeader().setStretchLastSection(False)
        layout.addWidget(self.table, 1)
        self._sync_mode_controls()

    @property
    def correlation_columns(self) -> tuple[str, ...]:
        return self._correlation_columns

    def set_dataset(self, dataset: Dataset | None) -> None:
        self._dataset = dataset
        numeric = dataset.numeric_columns if dataset is not None else ()
        self._correlation_columns = tuple(numeric[:2])
        self._update_columns_label()
        self._sync_mode_controls()
        if dataset is None:
            self.local_status.setText("Open a dataset to compute statistics.")
        else:
            self.local_status.setText("Choose a mode and compute statistics.")

    def set_correlation_columns(self, columns: tuple[str, ...]) -> None:
        columns = tuple(columns)
        if columns == self._correlation_columns:
            return
        self._correlation_columns = columns
        self._update_columns_label()
        self._sync_mode_controls()
        self.settings_changed.emit()

    def build_request(self) -> StatisticsRequest:
        if self._dataset is None:
            raise DataError("Open a dataset before computing statistics.")
        mode = str(self.mode.currentData())
        ddof = int(self.ddof.currentData())
        numeric = self._dataset.numeric_columns
        categorical = tuple(
            column.name for column in self._dataset.columns if column.kind != "numeric"
        )

        if mode == "numeric" and not numeric:
            raise DataError("No numeric columns are available for numeric statistics.")
        if mode == "categorical" and not categorical:
            raise DataError("No categorical, boolean, or datetime columns are available.")
        if mode == "correlation":
            if len(numeric) < 2:
                raise DataError("At least two numeric columns are required for correlations.")
            if len(self._correlation_columns) < 2:
                raise DataError("Choose at least two numeric columns for correlations.")
            if len(set(self._correlation_columns)) != len(self._correlation_columns):
                raise DataError("Choose distinct correlation columns.")
            if any(column not in numeric for column in self._correlation_columns):
                raise DataError(
                    "Correlation columns must be numeric columns in the current dataset."
                )

        return StatisticsRequest(mode, ddof, self._correlation_columns)

    def clear_result(self, message: str) -> None:
        self.result_model.replace_result(None)
        self.local_status.setText(message)

    def show_result(self, result, request: StatisticsRequest, selected_count: int) -> None:
        self.result_model.replace_result(result)
        if len(result):
            convention = ""
            if request.mode == "numeric":
                convention = " · Sample (N−1)" if request.ddof == 1 else " · Population (N)"
            self.local_status.setText(
                f"{request.mode.title()}{convention} · {selected_count:,} selected rows · "
                f"{len(result):,} result rows"
            )
            self.table.resizeColumnsToContents()
        else:
            self.local_status.setText(
                f"No {request.mode} result rows for the current dataset and selection."
            )

    def set_feedback(self, text: str) -> None:
        self.local_status.setText(text)

    def _settings_changed(self, _index=None) -> None:
        self._sync_mode_controls()
        self.settings_changed.emit()

    def _sync_mode_controls(self) -> None:
        mode = str(self.mode.currentData())
        dataset = self._dataset
        numeric = dataset.numeric_columns if dataset is not None else ()
        categorical = (
            tuple(column.name for column in dataset.columns if column.kind != "numeric")
            if dataset is not None
            else ()
        )
        self.ddof.setEnabled(mode == "numeric")
        self.columns_button.setEnabled(mode == "correlation" and len(numeric) >= 2)

        enabled = False
        if dataset is not None:
            if mode == "numeric":
                enabled = bool(numeric)
            elif mode == "categorical":
                enabled = bool(categorical)
            elif mode == "correlation":
                enabled = len(numeric) >= 2 and len(self._correlation_columns) >= 2
        self.compute_button.setEnabled(enabled)

        notes = {
            "numeric": (
                "Finite numeric values only; boolean columns are excluded. Missing and infinite "
                "values are counted separately. Quartiles use linear interpolation."
            ),
            "categorical": (
                "Non-numeric columns include boolean and datetime values. Missing values are not "
                "categories; datetime columns may report earliest/latest values."
            ),
            "correlation": (
                "Pearson r uses pairwise finite observations. Paired N may differ by pair; "
                "constant columns or fewer than two pairs produce an undefined coefficient."
            ),
        }
        self.notes.setText(notes.get(mode, ""))

    def _update_columns_label(self) -> None:
        if not self._correlation_columns:
            text = "No columns selected"
        else:
            text = ", ".join(self._correlation_columns)
        self.columns_label.setText(text)
