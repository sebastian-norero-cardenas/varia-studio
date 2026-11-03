"""Toolkit-neutral row mapping, sort, search and copy helpers for F3.B."""

from __future__ import annotations

import csv
import io
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from pandas.api.types import (
    is_bool_dtype,
    is_categorical_dtype,
    is_datetime64_any_dtype,
    is_float_dtype,
    is_integer_dtype,
    is_string_dtype,
)

from .data import CancelledError


class TableScope(Enum):
    SELECTED = "selected"
    ALL = "all"


class SortDirection(Enum):
    ASC = "asc"
    DESC = "desc"


class SearchMode(Enum):
    EXACT = "exact"
    CONTAINS_CASE_INSENSITIVE = "contains_case_insensitive"


@dataclass(frozen=True)
class SortSpec:
    column: str
    direction: SortDirection = SortDirection.ASC


@dataclass(frozen=True)
class SearchSpec:
    text: str
    mode: SearchMode = SearchMode.CONTAINS_CASE_INSENSITIVE


@dataclass(frozen=True)
class SearchResult:
    dataset_revision: int
    spec: SearchSpec
    source_ids: np.ndarray

    def __post_init__(self) -> None:
        values = np.asarray(self.source_ids, dtype=np.int64).copy()
        values.flags.writeable = False
        object.__setattr__(self, "source_ids", values)


@dataclass(frozen=True)
class RowMap:
    """Immutable visible-row to dataset-position mapping."""

    dataset_revision: int
    selection_revision: int
    scope: TableScope
    sort_spec: SortSpec | None
    positions: np.ndarray
    dataset_size: int
    _inverse: np.ndarray | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        positions = np.asarray(self.positions, dtype=np.int64).copy()
        if positions.ndim != 1:
            raise ValueError("RowMap positions must be one-dimensional.")
        if self.dataset_size < 0:
            raise ValueError("dataset_size cannot be negative.")
        if positions.size:
            if int(positions.min()) < 0 or int(positions.max()) >= self.dataset_size:
                raise ValueError("RowMap contains an out-of-range dataset position.")
            if np.unique(positions).size != positions.size:
                raise ValueError("RowMap positions must be unique.")
        positions.flags.writeable = False
        object.__setattr__(self, "positions", positions)

    def __len__(self) -> int:
        return int(self.positions.size)

    def dataset_position_at(self, visible_row: int) -> int:
        if visible_row < 0 or visible_row >= len(self):
            raise IndexError("Visible row is out of range.")
        return int(self.positions[visible_row])

    def source_row_id_at(self, visible_row: int) -> int:
        return self.dataset_position_at(visible_row) + 1

    def _inverse_map(self) -> np.ndarray:
        inverse = self._inverse
        if inverse is None:
            inverse = np.full(self.dataset_size, -1, dtype=np.int64)
            if self.positions.size:
                inverse[self.positions] = np.arange(self.positions.size, dtype=np.int64)
            inverse.flags.writeable = False
            object.__setattr__(self, "_inverse", inverse)
        return inverse

    def visible_row_for_source_id(self, source_row_id: int) -> int | None:
        position = source_row_id - 1
        if position < 0 or position >= self.dataset_size:
            return None
        visible = int(self._inverse_map()[position])
        return None if visible < 0 else visible

    def visible_rows_for_source_ids(self, source_ids: np.ndarray) -> np.ndarray:
        ids = np.asarray(source_ids, dtype=np.int64)
        if ids.size == 0:
            return np.empty(0, dtype=np.int64)
        positions = ids - 1
        valid = (positions >= 0) & (positions < self.dataset_size)
        rows = np.full(ids.shape, -1, dtype=np.int64)
        rows[valid] = self._inverse_map()[positions[valid]]
        visible = rows[rows >= 0]
        visible.sort()
        return visible


@dataclass(frozen=True)
class TableViewState:
    scope: TableScope = TableScope.SELECTED
    sort_spec: SortSpec | None = None
    search_spec: SearchSpec | None = None
    current_match: int | None = None
    visible_columns: tuple[str, ...] = ()
    table_epoch: int = 0


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError()


def scope_positions(
    row_count: int,
    scope: TableScope,
    mask: np.ndarray | None,
) -> np.ndarray:
    if row_count < 0:
        raise ValueError("row_count cannot be negative.")
    if scope is TableScope.ALL or mask is None:
        return np.arange(row_count, dtype=np.int64)
    if mask.dtype != np.bool_ or mask.shape != (row_count,):
        raise ValueError("Selection mask must contain one boolean per dataset row.")
    return np.flatnonzero(mask).astype(np.int64, copy=False)


def _classify_special(value: object) -> str | None:
    if value is None or value is pd.NA:
        return "null"
    if value is pd.NaT:
        return "nat"
    if isinstance(value, np.datetime64) and np.isnat(value):
        return "nat"
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if np.isnan(number):
            return "nan"
        if np.isposinf(number):
            return "posinf"
        if np.isneginf(number):
            return "neginf"
    return None


def display_text(value: object) -> str:
    kind = _classify_special(value)
    if kind == "null":
        return "∅"
    if kind == "nat":
        return "NaT"
    if kind == "nan":
        return "NaN"
    if kind == "posinf":
        return "∞"
    if kind == "neginf":
        return "−∞"
    if isinstance(value, (bool, np.bool_)):
        return "True" if bool(value) else "False"
    if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        return repr(float(value))
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    return str(value)


def canonical_text(value: object) -> str:
    """Canonical scalar text for exact copy/search; never derives from display text."""
    kind = _classify_special(value)
    if kind == "null":
        return ""
    if kind == "nat":
        return "NaT"
    if kind == "nan":
        return "nan"
    if kind == "posinf":
        return "inf"
    if kind == "neginf":
        return "-inf"
    if isinstance(value, (bool, np.bool_)):
        return "True" if bool(value) else "False"
    if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        return repr(float(value))
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    return str(value)


def copy_scalar_text(value: object) -> str:
    kind = _classify_special(value)
    if kind in {"null", "nat"}:
        return ""
    return canonical_text(value)


def _mixed_sort_key(value: object) -> tuple[object, ...]:
    if isinstance(value, (bool, np.bool_)):
        return (0, int(bool(value)))
    if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)):
        return (1, int(value))
    if isinstance(value, (float, np.floating)):
        return (2, float(value))
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return (3, pd.Timestamp(value).isoformat())
    if isinstance(value, str):
        return (4, value)
    return (5, type(value).__module__, type(value).__qualname__, str(value))


def _sort_key_factory(series: pd.Series):
    dtype = series.dtype
    if is_categorical_dtype(dtype):
        categories = series.cat.categories
        category_to_code = {value: index for index, value in enumerate(categories)}

        def categorical_key(value: object):
            return int(category_to_code.get(value, len(categories)))

        return categorical_key
    if is_bool_dtype(dtype):
        return lambda value: int(bool(value))
    if is_integer_dtype(dtype):
        return lambda value: int(value)
    if is_float_dtype(dtype):
        return lambda value: float(value)
    if is_datetime64_any_dtype(dtype):
        return lambda value: pd.Timestamp(value).value
    if is_string_dtype(dtype) and not dtype == object:
        return lambda value: str(value)
    return _mixed_sort_key


def sort_positions(
    frame: pd.DataFrame,
    positions: np.ndarray,
    spec: SortSpec | None,
    *,
    cancel: threading.Event | None = None,
) -> np.ndarray:
    base = np.sort(np.asarray(positions, dtype=np.int64), kind="stable")
    if spec is None or base.size < 2:
        base.flags.writeable = False
        return base
    if spec.column not in frame.columns:
        raise KeyError(f"Unknown sort column: {spec.column}")

    series = frame[spec.column]
    key_for = _sort_key_factory(series)
    nonnull: list[tuple[int, object]] = []
    nulls: list[int] = []
    for offset, position in enumerate(base):
        if offset % 2048 == 0:
            _check_cancel(cancel)
        value = series.iat[int(position)]
        if _classify_special(value) in {"null", "nat", "nan"}:
            nulls.append(int(position))
        else:
            nonnull.append((int(position), key_for(value)))

    reverse = spec.direction is SortDirection.DESC
    nonnull.sort(key=lambda item: item[1], reverse=reverse)
    ordered = np.fromiter(
        (position for position, _ in nonnull),
        dtype=np.int64,
        count=len(nonnull),
    )
    if nulls:
        ordered = np.concatenate((ordered, np.asarray(nulls, dtype=np.int64)))
    ordered.flags.writeable = False
    return ordered


def build_row_map(
    frame: pd.DataFrame,
    *,
    dataset_revision: int,
    selection_revision: int,
    scope: TableScope,
    mask: np.ndarray | None,
    sort_spec: SortSpec | None,
    cancel: threading.Event | None = None,
) -> RowMap:
    positions = scope_positions(len(frame), scope, mask)
    ordered = sort_positions(frame, positions, sort_spec, cancel=cancel)
    return RowMap(
        dataset_revision=dataset_revision,
        selection_revision=selection_revision,
        scope=scope,
        sort_spec=sort_spec,
        positions=ordered,
        dataset_size=len(frame),
    )


def _matches(text: str, spec: SearchSpec) -> bool:
    if spec.mode is SearchMode.EXACT:
        return text == spec.text
    return spec.text.casefold() in text.casefold()


def search_frame(
    frame: pd.DataFrame,
    spec: SearchSpec,
    *,
    dataset_revision: int,
    cancel: threading.Event | None = None,
) -> SearchResult:
    """Search all real dataset columns with O(rows) auxiliary memory."""
    _check_cancel(cancel)
    row_count = len(frame)
    matched = np.zeros(row_count, dtype=np.bool_)
    if not spec.text or row_count == 0 or len(frame.columns) == 0:
        return SearchResult(dataset_revision, spec, np.empty(0, dtype=np.int64))

    for series in (frame.iloc[:, index] for index in range(len(frame.columns))):
        for position, value in enumerate(series.array):
            if position % 2048 == 0:
                _check_cancel(cancel)
            if matched[position]:
                continue
            if _matches(canonical_text(value), spec):
                matched[position] = True
        _check_cancel(cancel)

    source_ids = np.flatnonzero(matched).astype(np.int64, copy=False) + 1
    return SearchResult(dataset_revision, spec, source_ids)


def rows_to_tsv(rows: Iterable[Sequence[object]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(
        stream,
        delimiter="\t",
        quotechar='"',
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )
    for row in rows:
        writer.writerow([copy_scalar_text(value) for value in row])
    return stream.getvalue()
