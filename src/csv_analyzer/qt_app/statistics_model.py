"""Read-only model for derived statistics result tables."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

_UNDEFINED = "—"


def display_statistics_value(value: object) -> str:
    """Format a scalar for display without changing the stored result value."""
    if value is None or value is pd.NA or value is pd.NaT:
        return _UNDEFINED
    if isinstance(value, np.datetime64) and np.isnat(value):
        return _UNDEFINED
    if isinstance(value, (bool, np.bool_)):
        return "True" if bool(value) else "False"
    if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if math.isnan(number):
            return _UNDEFINED
        if math.isinf(number):
            return "∞" if number > 0 else "−∞"
        return f"{number:.6g}"
    return str(value)


class StatisticsResultModel(QAbstractTableModel):
    """QAbstractTableModel owning only the current derived DataFrame result."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result: pd.DataFrame | None = None

    @property
    def result(self) -> pd.DataFrame | None:
        return self._result

    def replace_result(self, result: pd.DataFrame | None) -> None:
        if result is not None and not isinstance(result, pd.DataFrame):
            raise TypeError("Statistics result must be a pandas DataFrame.")
        self.beginResetModel()
        self._result = result
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid() or self._result is None:
            return 0
        return len(self._result)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid() or self._result is None:
            return 0
        return len(self._result.columns)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self._result is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return display_statistics_value(self.underlying_value(index.row(), index.column()))
        if role == Qt.ItemDataRole.ToolTipRole:
            return display_statistics_value(self.underlying_value(index.row(), index.column()))
        return None

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or self._result is None:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._result.columns):
                return str(self._result.columns[section])
            return None
        if orientation == Qt.Orientation.Vertical and 0 <= section < len(self._result):
            return str(section + 1)
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def underlying_value(self, row: int, column: int):
        if self._result is None:
            raise IndexError("No statistics result is installed.")
        if row < 0 or row >= len(self._result):
            raise IndexError("Statistics row is out of range.")
        if column < 0 or column >= len(self._result.columns):
            raise IndexError("Statistics column is out of range.")
        return self._result.iat[row, column]
