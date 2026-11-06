"""Focused staged editor for Scatter annotations and reference elements."""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..plots import Overlay, validate_overlay
from .scatter_style_dialog import _finite_float


class ScatterOverlayDialog(QDialog):
    overlays_applied = Signal(object)

    KINDS = {
        "Horizontal line": {"y", "linewidth", "linestyle"},
        "Vertical line": {"x", "linewidth", "linestyle"},
        "Slope line": {"x", "y", "slope", "linewidth", "linestyle"},
        "Segment": {"x", "y", "x2", "y2", "linewidth", "linestyle"},
        "Point": {"x", "y", "size", "marker"},
        "Text": {"x", "y", "text", "fontsize", "rotation"},
    }
    MARKERS = ("o", "s", "^", "x", "+")
    LINESTYLES = ("-", "--", "-.", ":")

    def __init__(self, overlays: tuple[Overlay, ...], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Annotations & reference elements")
        self.setModal(True)
        self.resize(650, 720)
        self._items = list(overlays)
        self._edit_index: int | None = None
        self._field_rows: dict[str, tuple[QWidget | None, QWidget]] = {}

        layout = QVBoxLayout(self)
        note = QLabel(
            "Coordinates use the displayed axes. Items are kept in the order shown below.",
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        form_host = QWidget(self)
        form = QFormLayout(form_host)
        self.kind = QComboBox(form_host)
        self.kind.addItems(tuple(self.KINDS))
        self.kind.currentTextChanged.connect(self._sync_fields)
        form.addRow("Element", self.kind)

        defaults = asdict(Overlay("Horizontal line"))
        labels = {
            "x": "X / first X",
            "y": "Y / first Y",
            "x2": "Second X",
            "y2": "Second Y",
            "slope": "Slope",
            "text": "Text",
            "color": "Color",
            "opacity": "Opacity",
            "size": "Marker area",
            "linewidth": "Line width",
            "linestyle": "Line style",
            "marker": "Marker",
            "fontsize": "Font size",
            "rotation": "Rotation",
        }
        self.fields: dict[str, QLineEdit | QComboBox] = {}
        for key, label in labels.items():
            row = QWidget(form_host)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            if key == "linestyle":
                widget = QComboBox(row)
                widget.addItems(self.LINESTYLES)
                widget.setCurrentText(str(defaults[key]))
            elif key == "marker":
                widget = QComboBox(row)
                widget.addItems(self.MARKERS)
                widget.setCurrentText(str(defaults[key]))
            else:
                widget = QLineEdit(str(defaults[key]), row)
            row_layout.addWidget(widget)
            self.fields[key] = widget
            form.addRow(label, row)
            self._field_rows[key] = (form.labelForField(row), row)

        layout.addWidget(form_host)

        edit_actions = QHBoxLayout()
        self.add_button = QPushButton("Add", self)
        self.add_button.clicked.connect(self.add_or_update)
        edit_actions.addWidget(self.add_button)
        new_button = QPushButton("New", self)
        new_button.clicked.connect(self.new_item)
        edit_actions.addWidget(new_button)
        remove_button = QPushButton("Remove", self)
        remove_button.clicked.connect(self.remove_selected)
        edit_actions.addWidget(remove_button)
        clear_button = QPushButton("Clear all", self)
        clear_button.clicked.connect(self.clear_all)
        edit_actions.addWidget(clear_button)
        edit_actions.addStretch(1)
        layout.addLayout(edit_actions)

        self.list = QListWidget(self)
        self.list.currentRowChanged.connect(self._load_selected)
        layout.addWidget(self.list, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        apply = QPushButton("Use these elements", self)
        apply.clicked.connect(self._apply)
        actions.addWidget(apply)
        layout.addLayout(actions)

        self._refresh()
        self._sync_fields()

    def _text(self, key: str) -> str:
        widget = self.fields[key]
        return widget.currentText() if isinstance(widget, QComboBox) else widget.text()

    def _sync_fields(self) -> None:
        enabled = self.KINDS[self.kind.currentText()] | {"color", "opacity"}
        for key, (label, row) in self._field_rows.items():
            visible = key in enabled
            if label is not None:
                label.setVisible(visible)
            row.setVisible(visible)

    def build_item(self) -> Overlay:
        kind = self.kind.currentText()
        values: dict[str, object] = {"kind": kind}
        numeric = {
            "x",
            "y",
            "x2",
            "y2",
            "slope",
            "opacity",
            "size",
            "linewidth",
            "fontsize",
            "rotation",
        }
        for key in self.KINDS[kind] | {"color", "opacity"}:
            raw = self._text(key)
            values[key] = _finite_float(raw, key.replace("_", " ")) if key in numeric else raw
        item = Overlay(**values)
        validate_overlay(item)
        return item

    def add_or_update(self) -> None:
        try:
            item = self.build_item()
        except Exception as error:
            QMessageBox.warning(self, "Invalid annotation", str(error))
            return
        if self._edit_index is None:
            self._items.append(item)
        else:
            self._items[self._edit_index] = item
        self._refresh()
        self.new_item()

    def new_item(self) -> None:
        self._edit_index = None
        self.list.clearSelection()
        self.kind.setCurrentText("Horizontal line")
        defaults = asdict(Overlay("Horizontal line"))
        for key, value in defaults.items():
            if key not in self.fields:
                continue
            widget = self.fields[key]
            if isinstance(widget, QComboBox):
                widget.setCurrentText(str(value))
            else:
                widget.setText(str(value))
        self.add_button.setText("Add")
        self._sync_fields()

    def remove_selected(self) -> None:
        index = self.list.currentRow()
        if 0 <= index < len(self._items):
            del self._items[index]
            self._refresh()
            self.new_item()

    def clear_all(self) -> None:
        self._items.clear()
        self._refresh()
        self.new_item()

    def _refresh(self) -> None:
        self.list.clear()
        for item in self._items:
            detail = item.text if item.kind == "Text" else f"x={item.x:g}, y={item.y:g}"
            self.list.addItem(f"{item.kind} · {detail} · {item.color}")

    def _load_selected(self, index: int) -> None:
        if not 0 <= index < len(self._items):
            return
        self._edit_index = index
        item = self._items[index]
        self.kind.setCurrentText(item.kind)
        for key, value in asdict(item).items():
            if key not in self.fields:
                continue
            widget = self.fields[key]
            if isinstance(widget, QComboBox):
                widget.setCurrentText(str(value))
            else:
                widget.setText(str(value))
        self.add_button.setText("Update")
        self._sync_fields()

    def _apply(self) -> None:
        self.overlays_applied.emit(tuple(self._items))
        self.accept()

    @property
    def staged_items(self) -> tuple[Overlay, ...]:
        return tuple(self._items)
