"""F3.E minimal Qt histogram controls."""

from __future__ import annotations

import math

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..data import DataError, Dataset
from ..plots import AxisSpec, HistogramSpec
from .plot_panel import PlotPanel


def _optional_float(text: str, label: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError as error:
        raise DataError(f"{label}: enter a finite number.") from error
    if not math.isfinite(value):
        raise DataError(f"{label}: enter a finite number.")
    return value


class HistogramAxisControls(QGroupBox):
    def __init__(self, parent=None) -> None:
        super().__init__("X axis", parent)
        form = QFormLayout(self)
        self.scale = QComboBox(self)
        self.scale.addItem("Linear", "linear")
        self.scale.addItem("Log", "log")
        form.addRow("Scale", self.scale)
        self.absolute = QCheckBox("Absolute", self)
        form.addRow("", self.absolute)
        self.scientific = QCheckBox("Scientific labels", self)
        form.addRow("", self.scientific)
        self.minimum = QLineEdit(self)
        self.minimum.setPlaceholderText("Automatic")
        form.addRow("Minimum", self.minimum)
        self.maximum = QLineEdit(self)
        self.maximum.setPlaceholderText("Automatic")
        form.addRow("Maximum", self.maximum)

    def reset(self) -> None:
        self.scale.setCurrentIndex(0)
        self.absolute.setChecked(False)
        self.scientific.setChecked(False)
        self.minimum.clear()
        self.maximum.clear()

    def spec(self) -> AxisSpec:
        return AxisSpec(
            scale=self.scale.currentData(),
            scientific=self.scientific.isChecked(),
            absolute=self.absolute.isChecked(),
            lower=_optional_float(self.minimum.text(), "X minimum"),
            upper=_optional_float(self.maximum.text(), "X maximum"),
        )


class HistogramView(QWidget):
    plot_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dataset: Dataset | None = None

        outer = QHBoxLayout(self)
        controls_scroll = QScrollArea(self)
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setMinimumWidth(320)
        controls_host = QWidget(controls_scroll)
        controls = QVBoxLayout(controls_host)

        variable_group = QGroupBox("Variable", controls_host)
        variable_form = QFormLayout(variable_group)
        self.column = QComboBox(variable_group)
        variable_form.addRow("Column", self.column)
        controls.addWidget(variable_group)

        self.axis = HistogramAxisControls(controls_host)
        controls.addWidget(self.axis)

        bin_group = QGroupBox("Binning", controls_host)
        bin_form = QFormLayout(bin_group)
        self.bin_mode = QComboBox(bin_group)
        for label, value in (
            ("Auto", "auto"),
            ("FD", "fd"),
            ("Sturges", "sturges"),
            ("Sqrt", "sqrt"),
            ("Custom", "custom"),
        ):
            self.bin_mode.addItem(label, value)
        self.bin_mode.currentIndexChanged.connect(self._sync_custom_bins)
        bin_form.addRow("Rule", self.bin_mode)
        self.custom_bins = QLineEdit(bin_group)
        self.custom_bins.setText("20")
        self.custom_bins.setPlaceholderText("1–4096")
        bin_form.addRow("Custom bins", self.custom_bins)
        self.log_bins = QCheckBox("Logarithmically spaced bins", bin_group)
        bin_form.addRow("", self.log_bins)
        controls.addWidget(bin_group)

        y_group = QGroupBox("Y axis", controls_host)
        y_form = QFormLayout(y_group)
        self.normalization = QComboBox(y_group)
        self.normalization.addItem("Count", False)
        self.normalization.addItem("Density", True)
        y_form.addRow("Normalization", self.normalization)
        self.log_y = QCheckBox("Logarithmic Y", y_group)
        y_form.addRow("", self.log_y)
        self.scientific_y = QCheckBox("Scientific Y labels", y_group)
        y_form.addRow("", self.scientific_y)
        controls.addWidget(y_group)

        self.local_status = QLabel("Load a dataset to plot a distribution.", controls_host)
        self.local_status.setWordWrap(True)
        controls.addWidget(self.local_status)

        self.plot_button = QPushButton("Plot distribution", controls_host)
        self.plot_button.setEnabled(False)
        self.plot_button.clicked.connect(self.plot_requested)
        controls.addWidget(self.plot_button)
        controls.addStretch(1)

        controls_scroll.setWidget(controls_host)
        outer.addWidget(controls_scroll)

        self.panel = PlotPanel("Choose a numeric column, then plot.", self)
        outer.addWidget(self.panel, 1)
        self._sync_custom_bins()

    def set_dataset(self, dataset: Dataset | None) -> None:
        self._dataset = dataset
        columns = dataset.numeric_columns if dataset is not None else ()
        self.column.clear()
        self.column.addItems(columns)
        self.axis.reset()
        self.bin_mode.setCurrentIndex(0)
        self.custom_bins.setText("20")
        self.log_bins.setChecked(False)
        self.normalization.setCurrentIndex(0)
        self.log_y.setChecked(False)
        self.scientific_y.setChecked(False)
        self._sync_custom_bins()

        if dataset is None:
            message = "Load a dataset to plot a distribution."
            enabled = False
        elif not len(dataset.frame):
            message = "The loaded dataset is empty."
            enabled = False
        elif not columns:
            message = "No numeric columns are available for a histogram."
            enabled = False
        else:
            message = f"{len(columns)} numeric column(s) available."
            enabled = True
        self.local_status.setText(message)
        self.plot_button.setEnabled(enabled)

    def build_spec(self) -> HistogramSpec:
        if self._dataset is None:
            raise DataError("Load a dataset before plotting.")
        if not self.plot_button.isEnabled():
            raise DataError(self.local_status.text())
        column = self.column.currentText()
        if column not in self._dataset.numeric_columns:
            raise DataError("Choose a numeric column.")

        bin_mode = self.bin_mode.currentData()
        if bin_mode == "custom":
            text = self.custom_bins.text().strip()
            try:
                bins = int(text)
            except ValueError as error:
                raise DataError("Custom bins must be an integer from 1 to 4096.") from error
            if not 1 <= bins <= 4096:
                raise DataError("Custom bins must be an integer from 1 to 4096.")
        else:
            bins = bin_mode

        return HistogramSpec(
            column,
            axis=self.axis.spec(),
            bins=bins,
            log_bins=self.log_bins.isChecked(),
            density=bool(self.normalization.currentData()),
            log_y=self.log_y.isChecked(),
            scientific_y=self.scientific_y.isChecked(),
            title="",
            color="#178F80",
            opacity=0.85,
        )

    def set_feedback(self, text: str) -> None:
        self.local_status.setText(text)

    def _sync_custom_bins(self) -> None:
        self.custom_bins.setEnabled(self.bin_mode.currentData() == "custom")
