"""F3.D minimal Qt scatter controls."""

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
from ..plots import AxisSpec, ScatterSpec, validate_scatter
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


class AxisControls(QGroupBox):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(title, parent)
        form = QFormLayout(self)
        self.column = QComboBox(self)
        form.addRow("Column", self.column)
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

    def set_columns(self, columns: tuple[str, ...], current: str | None) -> None:
        self.column.clear()
        self.column.addItems(columns)
        if current in columns:
            self.column.setCurrentText(current)

    def spec(self) -> AxisSpec:
        return AxisSpec(
            scale=self.scale.currentData(),
            scientific=self.scientific.isChecked(),
            absolute=self.absolute.isChecked(),
            lower=_optional_float(self.minimum.text(), f"{self.title()} minimum"),
            upper=_optional_float(self.maximum.text(), f"{self.title()} maximum"),
        )


class ScatterView(QWidget):
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

        self.x_axis = AxisControls("X axis", controls_host)
        self.y_axis = AxisControls("Y axis", controls_host)
        controls.addWidget(self.x_axis)
        controls.addWidget(self.y_axis)

        color_group = QGroupBox("Color", controls_host)
        color_form = QFormLayout(color_group)
        self.color = QComboBox(color_group)
        self.color.addItem("No color", None)
        self.color.currentIndexChanged.connect(self._sync_color_controls)
        color_form.addRow("Column", self.color)
        self.color_scale = QComboBox(color_group)
        self.color_scale.addItem("Linear", "linear")
        self.color_scale.addItem("Log", "log")
        color_form.addRow("Scale", self.color_scale)
        self.color_absolute = QCheckBox("Absolute", color_group)
        color_form.addRow("", self.color_absolute)
        self.color_scientific = QCheckBox("Scientific labels", color_group)
        color_form.addRow("", self.color_scientific)
        controls.addWidget(color_group)

        self.show_excluded = QCheckBox("Show filtered-out rows", controls_host)
        self.show_excluded.setChecked(True)
        controls.addWidget(self.show_excluded)

        self.local_status = QLabel("Load a dataset to plot relationships.", controls_host)
        self.local_status.setWordWrap(True)
        controls.addWidget(self.local_status)

        self.plot_button = QPushButton("Plot relationships", controls_host)
        self.plot_button.setEnabled(False)
        self.plot_button.clicked.connect(self.plot_requested)
        controls.addWidget(self.plot_button)
        controls.addStretch(1)

        controls_scroll.setWidget(controls_host)
        outer.addWidget(controls_scroll)

        self.panel = PlotPanel(
            "Choose numeric X and Y columns, then plot.",
            self,
        )
        outer.addWidget(self.panel, 1)
        self._sync_color_controls()

    def set_dataset(self, dataset: Dataset | None) -> None:
        self._dataset = dataset
        columns = dataset.numeric_columns if dataset is not None else ()
        x_current = columns[0] if columns else None
        y_current = columns[1] if len(columns) > 1 else (columns[0] if columns else None)
        self.x_axis.set_columns(columns, x_current)
        self.y_axis.set_columns(columns, y_current)
        self.color.clear()
        self.color.addItem("No color", None)
        for column in columns:
            self.color.addItem(column, column)
        self.color.setCurrentIndex(0)
        self.x_axis.scale.setCurrentIndex(0)
        self.y_axis.scale.setCurrentIndex(0)
        for axis in (self.x_axis, self.y_axis):
            axis.absolute.setChecked(False)
            axis.scientific.setChecked(False)
            axis.minimum.clear()
            axis.maximum.clear()
        self.color_scale.setCurrentIndex(0)
        self.color_absolute.setChecked(False)
        self.color_scientific.setChecked(False)
        self.show_excluded.setChecked(True)

        if dataset is None:
            message = "Load a dataset to plot relationships."
            enabled = False
        elif not len(dataset.frame):
            message = "The loaded dataset is empty."
            enabled = False
        elif len(columns) < 2:
            message = "Scatter requires at least two numeric columns."
            enabled = False
        else:
            message = f"{len(columns)} numeric columns available."
            enabled = True
        self.local_status.setText(message)
        self.plot_button.setEnabled(enabled)
        self._sync_color_controls()

    def build_spec(self) -> ScatterSpec:
        if self._dataset is None:
            raise DataError("Load a dataset before plotting.")
        if not self.plot_button.isEnabled():
            raise DataError(self.local_status.text())
        spec = ScatterSpec(
            self.x_axis.column.currentText(),
            self.y_axis.column.currentText(),
            x_axis=self.x_axis.spec(),
            y_axis=self.y_axis.spec(),
            color=self.color.currentData(),
            color_absolute=self.color_absolute.isChecked(),
            color_scale=self.color_scale.currentData(),
            color_scientific=self.color_scientific.isChecked(),
            cmap="viridis",
            marker="o",
            size=12.0,
            opacity=0.8,
            show_excluded=self.show_excluded.isChecked(),
            excluded_opacity=0.12,
            sample_limit=None,
        )
        validate_scatter(spec)
        return spec

    def set_feedback(self, text: str) -> None:
        self.local_status.setText(text)

    def _sync_color_controls(self) -> None:
        enabled = self.color.currentData() is not None
        self.color_scale.setEnabled(enabled)
        self.color_absolute.setEnabled(enabled)
        self.color_scientific.setEnabled(enabled)
