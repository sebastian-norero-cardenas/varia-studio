"""Qt histogram controls with coherent staged plot configuration."""

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
    reset_requested = Signal()
    settings_updated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dataset: Dataset | None = None
        self._plot_settings_revision = 0

        outer = QHBoxLayout(self)
        controls_column = QWidget(self)
        controls_column.setMinimumWidth(320)
        controls_column_layout = QVBoxLayout(controls_column)
        controls_column_layout.setContentsMargins(0, 0, 0, 0)

        self.controls_scroll = QScrollArea(controls_column)
        self.controls_scroll.setWidgetResizable(True)
        controls_host = QWidget(self.controls_scroll)
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

        title_group = QGroupBox("Figure", controls_host)
        title_form = QFormLayout(title_group)
        self.title = QLineEdit(title_group)
        self.title.setPlaceholderText("Optional")
        title_form.addRow("Title", self.title)
        controls.addWidget(title_group)

        controls.addStretch(1)
        self.controls_scroll.setWidget(controls_host)
        controls_column_layout.addWidget(self.controls_scroll, 1)

        footer = QWidget(controls_column)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        self.local_status = QLabel("Load a dataset to plot a distribution.", footer)
        self.local_status.setWordWrap(True)
        footer_layout.addWidget(self.local_status)

        actions = QHBoxLayout()
        self.plot_button = QPushButton("Plot distribution", footer)
        self.plot_button.setEnabled(False)
        self.plot_button.clicked.connect(self.plot_requested)
        actions.addWidget(self.plot_button)
        self.reset_button = QPushButton("Reset all settings", footer)
        self.reset_button.setEnabled(False)
        self.reset_button.clicked.connect(self.reset_requested)
        actions.addWidget(self.reset_button)
        footer_layout.addLayout(actions)
        controls_column_layout.addWidget(footer)

        outer.addWidget(controls_column)

        self.panel = PlotPanel("Choose a numeric column, then plot.", self)
        outer.addWidget(self.panel, 1)
        self._sync_custom_bins()
        self._connect_plot_setting_signals()

    @property
    def plot_settings_revision(self) -> int:
        return self._plot_settings_revision

    def set_dataset(self, dataset: Dataset | None) -> None:
        self._dataset = dataset
        self._reset_controls(dataset)

    def reset_settings(self) -> None:
        self._reset_controls(self._dataset)

    def _reset_controls(self, dataset: Dataset | None) -> None:
        self._plot_settings_revision += 1
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
        self.title.clear()
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
        self.plot_button.setText("Plot distribution")
        self.plot_button.setEnabled(enabled)
        self.reset_button.setEnabled(enabled)

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
            title=self.title.text(),
            color="#178F80",
            opacity=0.85,
        )

    def set_feedback(self, text: str) -> None:
        self.local_status.setText(text)

    def mark_current_result(self) -> None:
        self.panel.mark_current()
        if self.panel.has_figure:
            self.plot_button.setText("Update plot")

    def mark_previous_result(self) -> None:
        self.panel.mark_previous_result()
        self.plot_button.setText("Update plot")

    def mark_result_cleared(self) -> None:
        self.plot_button.setText("Plot distribution")

    def _connect_plot_setting_signals(self) -> None:
        for combo in (self.column, self.axis.scale, self.bin_mode, self.normalization):
            combo.activated.connect(self._plot_setting_changed)

        for checkbox in (
            self.axis.absolute,
            self.axis.scientific,
            self.log_bins,
            self.log_y,
            self.scientific_y,
        ):
            checkbox.clicked.connect(self._plot_setting_changed)

        for edit in (
            self.axis.minimum,
            self.axis.maximum,
            self.custom_bins,
            self.title,
        ):
            edit.textEdited.connect(self._plot_setting_changed)

    def _plot_setting_changed(self, *_args) -> None:
        if self._dataset is None:
            return
        self._plot_settings_revision += 1
        if self.panel.has_figure:
            self.panel.mark_changes_not_applied()
            self.plot_button.setText("Update plot")
        message = "Settings changed. Plot again to apply them."
        self.set_feedback(message)
        self.settings_updated.emit(message)

    def _sync_custom_bins(self) -> None:
        self.custom_bins.setEnabled(self.bin_mode.currentData() == "custom")
