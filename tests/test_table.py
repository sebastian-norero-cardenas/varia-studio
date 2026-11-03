"""Pure F3.B table semantics."""

from __future__ import annotations

import threading
import unittest

import numpy as np
import pandas as pd

from csv_analyzer.data import CancelledError
from csv_analyzer.table import (
    RowMap,
    SearchMode,
    SearchSpec,
    SortDirection,
    SortSpec,
    TableScope,
    build_row_map,
    canonical_text,
    display_text,
    rows_to_tsv,
    search_frame,
)


class RowMapTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({"x": [30, 10, 20, 20], "label": list("abcd")})

    def test_scope_source_identity_inverse_and_reset(self):
        mask = np.array([False, True, True, False], dtype=np.bool_)
        selected = build_row_map(
            self.frame,
            dataset_revision=3,
            selection_revision=5,
            scope=TableScope.SELECTED,
            mask=mask,
            sort_spec=None,
        )
        self.assertEqual(selected.positions.tolist(), [1, 2])
        self.assertEqual(selected.source_row_id_at(0), 2)
        self.assertEqual(selected.source_row_id_at(1), 3)
        self.assertEqual(selected.visible_row_for_source_id(1), None)
        self.assertEqual(selected.visible_row_for_source_id(3), 1)
        self.assertFalse(selected.positions.flags.writeable)

        all_rows = build_row_map(
            self.frame,
            dataset_revision=3,
            selection_revision=5,
            scope=TableScope.ALL,
            mask=mask,
            sort_spec=None,
        )
        self.assertEqual(all_rows.positions.tolist(), [0, 1, 2, 3])
        self.assertEqual(all_rows.visible_rows_for_source_ids(np.array([4, 2])).tolist(), [1, 3])

    def test_empty_selected_keeps_dense_reverse_domain(self):
        row_map = RowMap(
            dataset_revision=1,
            selection_revision=1,
            scope=TableScope.SELECTED,
            sort_spec=None,
            positions=np.empty(0, dtype=np.int64),
            dataset_size=4,
        )
        self.assertEqual(row_map.visible_row_for_source_id(4), None)


class SortTests(unittest.TestCase):
    def sort(self, series, direction=SortDirection.ASC):
        frame = pd.DataFrame({"v": series})
        return build_row_map(
            frame,
            dataset_revision=1,
            selection_revision=1,
            scope=TableScope.ALL,
            mask=None,
            sort_spec=SortSpec("v", direction),
        ).positions.tolist()

    def test_int64_above_2pow53_is_exact(self):
        values = pd.Series(
            [9007199254740994, 9007199254740992, 9007199254740993],
            dtype="int64",
        )
        self.assertEqual(self.sort(values), [1, 2, 0])

    def test_nullable_signed_and_unsigned_integer(self):
        signed = pd.Series([3, pd.NA, 1, 2], dtype="Int64")
        unsigned = pd.Series([3, pd.NA, 1, 2], dtype="UInt64")
        self.assertEqual(self.sort(signed), [2, 3, 0, 1])
        self.assertEqual(self.sort(unsigned, SortDirection.DESC), [0, 3, 2, 1])

    def test_float_inf_nan_and_null_last_both_directions(self):
        series = pd.Series([np.nan, np.inf, -np.inf, 1.5, 1.5])
        self.assertEqual(self.sort(series), [2, 3, 4, 1, 0])
        self.assertEqual(self.sort(series, SortDirection.DESC), [1, 3, 4, 2, 0])

    def test_tie_break_source_id_ascending_even_descending(self):
        series = pd.Series([2, 1, 2, 2, 1])
        self.assertEqual(self.sort(series, SortDirection.ASC), [1, 4, 0, 2, 3])
        self.assertEqual(self.sort(series, SortDirection.DESC), [0, 2, 3, 1, 4])

    def test_text_bool_category_datetime_and_mixed_are_deterministic(self):
        self.assertEqual(self.sort(pd.Series(["b", "a", "c"])), [1, 0, 2])
        self.assertEqual(self.sort(pd.Series([True, False, True], dtype="bool")), [1, 0, 2])
        category = pd.Series(pd.Categorical(["medium", "low", "high"], categories=["low", "medium", "high"]))
        self.assertEqual(self.sort(category), [1, 0, 2])
        dates = pd.Series(pd.to_datetime(["2024-02-01", "2023-01-01", None]))
        self.assertEqual(self.sort(dates), [1, 0, 2])
        mixed = pd.Series(["10", 2, True, 1.5], dtype="object")
        self.assertEqual(self.sort(mixed), [2, 1, 3, 0])

    def test_reset_sort_returns_source_order_inside_scope(self):
        frame = pd.DataFrame({"v": [3, 1, 2, 0]})
        mask = np.array([True, False, True, True], dtype=np.bool_)
        row_map = build_row_map(
            frame,
            dataset_revision=1,
            selection_revision=2,
            scope=TableScope.SELECTED,
            mask=mask,
            sort_spec=None,
        )
        self.assertEqual(row_map.positions.tolist(), [0, 2, 3])


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "text": ["Alpha", "beta", "A very long STRING " + "x" * 500],
                "number": pd.Series([9007199254740993, 12, 7], dtype="int64"),
            }
        )

    def test_exact_contains_casefold_and_numeric_canonicalization(self):
        exact = search_frame(
            self.frame,
            SearchSpec("9007199254740993", SearchMode.EXACT),
            dataset_revision=7,
        )
        self.assertEqual(exact.source_ids.tolist(), [1])

        contains = search_frame(
            self.frame,
            SearchSpec("VERY LONG string", SearchMode.CONTAINS_CASE_INSENSITIVE),
            dataset_revision=7,
        )
        self.assertEqual(contains.source_ids.tolist(), [3])

    def test_cancel_is_cooperative(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            search_frame(
                self.frame,
                SearchSpec("x"),
                dataset_revision=1,
                cancel=cancel,
            )


class PresentationAndCopyTests(unittest.TestCase):
    def test_display_and_canonical_text_are_separate(self):
        self.assertEqual(display_text(None), "∅")
        self.assertEqual(display_text(pd.NA), "∅")
        self.assertEqual(display_text(np.nan), "NaN")
        self.assertEqual(display_text(pd.NaT), "NaT")
        self.assertEqual(display_text(np.inf), "∞")
        self.assertEqual(display_text(-np.inf), "−∞")
        self.assertEqual(canonical_text(np.inf), "inf")
        self.assertEqual(canonical_text(9007199254740993), "9007199254740993")

    def test_tsv_copy_preserves_exact_values_and_quotes_special_strings(self):
        text = rows_to_tsv(
            [
                [9007199254740993, 0.1, np.inf, np.nan, None, True, "plain"],
                [1, -np.inf, pd.NaT, "a\tb", "line1\nline2", 'say "hi"', ""],
            ]
        )
        rows = list(csv_rows(text))
        self.assertEqual(rows[0][0], "9007199254740993")
        self.assertEqual(rows[0][1], repr(0.1))
        self.assertEqual(rows[0][2:5], ["inf", "nan", ""])
        self.assertEqual(rows[1][1:3], ["-inf", ""])
        self.assertEqual(rows[1][3], "a\tb")
        self.assertEqual(rows[1][4], "line1\nline2")
        self.assertEqual(rows[1][5], 'say "hi"')


def csv_rows(text):
    import csv
    import io

    return csv.reader(io.StringIO(text), delimiter="\t", quotechar='"')


if __name__ == "__main__":
    unittest.main()
