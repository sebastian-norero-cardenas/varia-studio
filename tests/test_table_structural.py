"""Synthetic S-200K-TABLE structural evidence for F3.B."""

from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QApplication

from csv_analyzer.data import CancelledError, ColumnInfo, Dataset
from csv_analyzer.qt_app.table_model import DataFrameTableModel
from csv_analyzer.qt_app.table_view import TableView
from csv_analyzer.table import (
    SearchSpec,
    SortDirection,
    SortSpec,
    TableScope,
    build_row_map,
    search_frame,
)

APP = QApplication.instance() or QApplication(["varia-f3b-structural"])


class SyntheticTableStructuralTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = 200_000
        data = {}
        base = np.arange(cls.rows, dtype=np.int64)
        base[12345] = 9007199254740993
        data["c00"] = base
        for index in range(1, 61):
            data[f"c{index:02d}"] = np.arange(cls.rows, dtype=np.int64) + index
        cls.frame = pd.DataFrame(data, copy=False)
        columns = tuple(
            ColumnInfo(name, "int64", "numeric", 0, 0) for name in cls.frame.columns
        )
        cls.dataset = Dataset(cls.frame, Path("S-200K-TABLE.csv"), columns)

    def test_structural_contract(self):
        frame = self.frame
        all_rows = build_row_map(
            frame,
            dataset_revision=1,
            selection_revision=1,
            scope=TableScope.ALL,
            mask=None,
            sort_spec=None,
        )
        self.assertEqual(all_rows.positions.dtype, np.int64)
        self.assertEqual(all_rows.positions.nbytes, self.rows * 8)
        self.assertEqual(len(all_rows), self.rows)

        mask = np.zeros(self.rows, dtype=np.bool_)
        mask[::2] = True
        selected = build_row_map(
            frame,
            dataset_revision=1,
            selection_revision=2,
            scope=TableScope.SELECTED,
            mask=mask,
            sort_spec=None,
        )
        self.assertEqual(len(selected), self.rows // 2)

        ordered = build_row_map(
            frame,
            dataset_revision=1,
            selection_revision=2,
            scope=TableScope.ALL,
            mask=None,
            sort_spec=SortSpec("c00", SortDirection.ASC),
        )
        self.assertIs(self.dataset.frame, frame)
        self.assertEqual(ordered.source_row_id_at(ordered.visible_row_for_source_id(12346)), 12346)

        model = DataFrameTableModel()
        model.replace_dataset(self.dataset, all_rows, tuple(frame.columns))
        view = TableView()
        view.set_model(model)
        self.assertEqual(model.rowCount(), self.rows)
        self.assertEqual(model.columnCount(), 62)
        self.assertEqual(model.source_row_id_at(self.rows - 1), self.rows)
        self.assertEqual(model.underlying_value(12345, 1), 9007199254740993)
        self.assertEqual(model.copy_text([12345], [1]).strip(), "9007199254740993")
        self.assertTrue(view.select_source_id(model, 199999))
        self.assertIsNone(view.table.indexWidget(model.index(12345, 1)))
        self.assertLessEqual(len(model._columns), 61)
        self.assertFalse(any(
            isinstance(value, np.ndarray) and value.ndim == 2
            for value in model.__dict__.values()
        ))

        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            search_frame(
                frame,
                SearchSpec("not-present"),
                dataset_revision=1,
                cancel=cancel,
            )

        view.deleteLater()
        model.deleteLater()
        APP.processEvents()


if __name__ == "__main__":
    unittest.main()
