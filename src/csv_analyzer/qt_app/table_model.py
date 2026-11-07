"""Read-only QAbstractTableModel backed by Dataset + immutable RowMap."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal

from ..data import Dataset
from ..table import RowMap, display_text, rows_to_tsv


class DataFrameTableModel(QAbstractTableModel):
    sort_requested = Signal(int, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dataset: Dataset | None = None
        self._row_map: RowMap | None = None
        self._columns: tuple[str, ...] = ()
        self._column_positions: tuple[int, ...] = ()

    @property
    def dataset(self) -> Dataset | None:
        return self._dataset

    @property
    def row_map(self) -> RowMap | None:
        return self._row_map

    @property
    def columns(self) -> tuple[str, ...]:
        return self._columns

    def replace_dataset(
        self,
        dataset: Dataset | None,
        row_map: RowMap | None,
        columns: Sequence[str] | None = None,
    ) -> None:
        self.beginResetModel()
        self._dataset = dataset
        self._row_map = row_map
        if dataset is None:
            self._columns = ()
            self._column_positions = ()
        else:
            requested = tuple(columns) if columns is not None else tuple(dataset.frame.columns)
            locations = []
            for name in requested:
                location = dataset.frame.columns.get_loc(name)
                if not isinstance(location, (int,)):
                    raise ValueError("F3.B requires unique exposed dataframe columns.")
                locations.append(int(location))
            self._columns = tuple(str(name) for name in requested)
            self._column_positions = tuple(locations)
        self.endResetModel()

    def replace_row_map(self, row_map: RowMap) -> None:
        if self._dataset is None:
            raise RuntimeError("Cannot install a RowMap without a dataset.")
        self.beginResetModel()
        self._row_map = row_map
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid() or self._row_map is None:
            return 0
        return len(self._row_map)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid() or self._dataset is None:
            return 0
        return 1 + len(self._columns)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self._dataset is None or self._row_map is None:
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return display_text(self.underlying_value(index.row(), index.column()))
        return None

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if section == 0:
                if role == Qt.ItemDataRole.DisplayRole:
                    return "Source Row ID"
                if role == Qt.ItemDataRole.ToolTipRole:
                    return "Source Row ID\n1-based row position in the committed dataset."
                return None
            offset = section - 1
            if not 0 <= offset < len(self._columns):
                return None
            name = self._columns[offset]
            if role == Qt.ItemDataRole.DisplayRole:
                return name
            if role == Qt.ItemDataRole.ToolTipRole and self._dataset is not None:
                info = next((column for column in self._dataset.columns if column.name == name), None)
                if info is None:
                    return name
                lines = [
                    name,
                    f"Kind: {info.kind}",
                    f"Type: {info.dtype}",
                    f"Missing: {info.missing:,}",
                ]
                if info.kind == "numeric":
                    lines.append(f"Infinite: {info.infinite:,}")
                return "\n".join(lines)
            return None
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Vertical
            and self._row_map is not None
            and 0 <= section < len(self._row_map)
        ):
            return str(section + 1)
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        if 0 <= column < self.columnCount():
            self.sort_requested.emit(column, order)

    def source_row_id_at(self, visible_row: int) -> int:
        if self._row_map is None:
            raise IndexError("No rows are available.")
        return self._row_map.source_row_id_at(visible_row)

    def dataset_position_at(self, visible_row: int) -> int:
        if self._row_map is None:
            raise IndexError("No rows are available.")
        return self._row_map.dataset_position_at(visible_row)

    def visible_row_for_source_id(self, source_row_id: int) -> int | None:
        if self._row_map is None:
            return None
        return self._row_map.visible_row_for_source_id(source_row_id)

    def column_name(self, logical_column: int) -> str:
        if logical_column <= 0 or logical_column >= self.columnCount():
            raise IndexError("Logical column is not a dataset column.")
        return self._columns[logical_column - 1]

    def underlying_value(self, visible_row: int, logical_column: int):
        if self._dataset is None or self._row_map is None:
            raise IndexError("No dataset is installed.")
        position = self._row_map.dataset_position_at(visible_row)
        if logical_column == 0:
            return position + 1
        offset = logical_column - 1
        if offset < 0 or offset >= len(self._column_positions):
            raise IndexError("Column is out of range.")
        return self._dataset.frame.iat[position, self._column_positions[offset]]

    def copy_text(
        self,
        visible_rows: Sequence[int],
        logical_columns: Sequence[int] | None = None,
    ) -> str:
        if self._dataset is None or self._row_map is None:
            return ""
        rows = sorted({int(row) for row in visible_rows if 0 <= int(row) < self.rowCount()})
        columns = (
            list(range(self.columnCount()))
            if logical_columns is None
            else [int(column) for column in logical_columns]
        )
        return rows_to_tsv(
            [self.underlying_value(row, column) for column in columns] for row in rows
        )
