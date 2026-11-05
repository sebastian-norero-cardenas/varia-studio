"""Toolkit-neutral F3.C filter semantics."""

from __future__ import annotations

import unittest
from decimal import Decimal

import numpy as np
import pandas as pd

from csv_analyzer.analysis import build_filter_mask
from csv_analyzer.data import DataError
from csv_analyzer.state import FilterRule


class FilterEngineTests(unittest.TestCase):
    def test_and_inclusive_one_sided_missing_nan_and_infinite(self):
        frame = pd.DataFrame(
            {
                "x": [0.0, 1.0, 2.0, np.nan, np.inf, -np.inf],
                "y": [10.0, 9.0, 8.0, 7.0, 6.0, 5.0],
            }
        )
        rules = (FilterRule("x", lower=1, upper=2), FilterRule("y", upper=9))
        self.assertEqual(
            build_filter_mask(frame, rules).tolist(),
            [False, True, True, False, False, False],
        )

    def test_integer_decimal_bounds_preserve_values_above_2pow53(self):
        base = 2**53
        frame = pd.DataFrame({"id": np.array([base, base + 1, base + 2], dtype=np.int64)})
        rules = (
            FilterRule(
                "id",
                lower=Decimal("9007199254740992.5"),
                upper=Decimal("9007199254740993"),
            ),
        )
        self.assertEqual(build_filter_mask(frame, rules).tolist(), [False, True, False])

    def test_uint64_and_nullable_integer_precision(self):
        base = 2**63
        frame = pd.DataFrame(
            {
                "u": pd.Series([base, pd.NA, base + 1], dtype="UInt64"),
                "i": pd.Series([2**53, pd.NA, 2**53 + 1], dtype="Int64"),
            }
        )
        self.assertEqual(
            build_filter_mask(frame, (FilterRule("u", lower=base + 1),)).tolist(),
            [False, False, True],
        )
        self.assertEqual(
            build_filter_mask(frame, (FilterRule("i", upper=2**53),)).tolist(),
            [True, False, False],
        )

    def test_zero_rows_and_zero_rules(self):
        empty = pd.DataFrame({"x": pd.Series([], dtype="float64")})
        self.assertEqual(build_filter_mask(empty, (FilterRule("x", lower=0),)).tolist(), [])
        frame = pd.DataFrame({"x": [1.0, 2.0]})
        self.assertEqual(build_filter_mask(frame, ()).tolist(), [True, True])

    def test_invalid_rules_are_rejected(self):
        frame = pd.DataFrame(
            {"x": [1.0, 2.0], "flag": [True, False], "name": ["a", "b"]}
        )
        invalid = (
            FilterRule("x"),
            FilterRule("x", lower=3, upper=2),
            FilterRule("x", lower=np.nan),
            FilterRule("x", lower=np.inf),
            FilterRule("missing", lower=1),
            FilterRule("flag", lower=0),
            FilterRule("name", lower=0),
        )
        for rule in invalid:
            with self.subTest(rule=rule), self.assertRaises(DataError):
                build_filter_mask(frame, (rule,))


if __name__ == "__main__":
    unittest.main()
