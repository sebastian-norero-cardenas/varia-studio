"""F3.B table view-state orchestration and F3.A scheduler integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from ..state import StateSnapshot
from ..table import (
    RowMap,
    SearchMode,
    SearchResult,
    SearchSpec,
    SortDirection,
    SortSpec,
    TableScope,
    build_row_map,
    search_frame,
)
from .table_model import DataFrameTableModel
from .table_view import TableView


class TableController(QObject):
    """Own view-only table state; never mutates AppState."""

    def __init__(
        self,
        view: TableView,
        *,
        snapshot_provider: Callable[[], StateSnapshot],
        submit_task: Callable[..., Any],
        cancel_task: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.view = view
        self.model = DataFrameTableModel(self)
        self.view.set_model(self.model)
        self._snapshot_provider = snapshot_provider
        self._submit_task = submit_task
        self._cancel_task = cancel_task

        self.scope = TableScope.SELECTED
        self.sort_spec: SortSpec | None = None
        self.search_spec: SearchSpec | None = None
        self.search_result: SearchResult | None = None
        self.current_match: int | None = None
        self.visible_columns: tuple[str, ...] = ()
        self.table_epoch = 0
        self._dataset_revision = -1
        self._selection_revision = -1
        self._pending_go_source_id: int | None = None
        self._pending_search_first = False

        view.scope_requested.connect(self.set_scope)
        view.sort_requested.connect(self.set_sort)
        view.reset_sort_requested.connect(self.reset_sort)
        view.search_requested.connect(self.set_search)
        view.previous_requested.connect(self.previous_match)
        view.next_requested.connect(self.next_match)
        view.show_all_requested.connect(self.show_matches_in_all)
        view.go_requested.connect(self.go_to_source_id)
        view.copy_requested.connect(self.copy_selection)

        self.handle_state_change("initial", self._snapshot_provider())

    def cancel_derived(self) -> None:
        self._cancel_task("table-order")
        self._cancel_task("table-search")

    def handle_state_change(self, reason: str, snapshot: StateSnapshot) -> None:
        if snapshot.dataset_revision != self._dataset_revision:
            self._replace_dataset(snapshot)
            return
        if snapshot.selection_revision == self._selection_revision:
            return
        self._selection_revision = snapshot.selection_revision
        if self.scope is TableScope.SELECTED:
            self.table_epoch += 1
            self._request_row_map(snapshot, recalculate_search=self.search_spec is not None)
        else:
            self._update_counts_and_empty_state(snapshot)

    def _replace_dataset(self, snapshot: StateSnapshot) -> None:
        self.cancel_derived()
        self.table_epoch += 1
        self._dataset_revision = snapshot.dataset_revision
        self._selection_revision = snapshot.selection_revision
        self.scope = TableScope.SELECTED
        self.sort_spec = None
        self.search_spec = None
        self.search_result = None
        self.current_match = None
        self._pending_go_source_id = None
        self._pending_search_first = False
        self.view.set_scope(self.scope)
        self.view.reset_sort_indicator()
        self.view.set_search_text("")
        self.view.set_search_status("")

        if snapshot.dataset is None:
            self.visible_columns = ()
            self.model.replace_dataset(None, None)
            self.view.set_counts(self.scope, 0, 0, 0)
            self.view.set_feedback("No dataset loaded")
            return

        self.visible_columns = tuple(str(name) for name in snapshot.dataset.frame.columns)
        row_map = build_row_map(
            snapshot.dataset.frame,
            dataset_revision=snapshot.dataset_revision,
            selection_revision=snapshot.selection_revision,
            scope=self.scope,
            mask=snapshot.mask,
            sort_spec=None,
        )
        self.model.replace_dataset(snapshot.dataset, row_map, self.visible_columns)
        self._update_counts_and_empty_state(snapshot)

    def set_scope(self, scope: TableScope) -> None:
        if not isinstance(scope, TableScope) or scope is self.scope:
            return
        snapshot = self._snapshot_provider()
        if snapshot.dataset is None:
            self.view.set_scope(self.scope)
            return
        self.scope = scope
        self.view.set_scope(scope)
        self.table_epoch += 1
        self._request_row_map(snapshot)

    def set_sort(self, logical_column: int, direction: SortDirection) -> None:
        snapshot = self._snapshot_provider()
        if snapshot.dataset is None:
            return
        if logical_column == 0:
            self.reset_sort()
            return
        try:
            column = self.model.column_name(logical_column)
        except IndexError:
            return
        self.sort_spec = SortSpec(column, direction)
        self.view.set_sort_indicator(logical_column, direction)
        self.table_epoch += 1
        self._request_row_map(snapshot)

    def reset_sort(self) -> None:
        if self.model.dataset is None:
            return
        self.sort_spec = None
        self.view.reset_sort_indicator()
        self.table_epoch += 1
        self._request_row_map(self._snapshot_provider())

    def _request_row_map(
        self,
        snapshot: StateSnapshot,
        *,
        recalculate_search: bool = False,
    ) -> None:
        dataset = snapshot.dataset
        if dataset is None:
            return
        preserved_ids, current_id = self.view.capture_selection(self.model)
        epoch = self.table_epoch
        scope = self.scope
        sort_spec = self.sort_spec
        frame = dataset.frame
        mask = snapshot.mask

        if sort_spec is None:
            result = build_row_map(
                frame,
                dataset_revision=snapshot.dataset_revision,
                selection_revision=snapshot.selection_revision,
                scope=scope,
                mask=mask,
                sort_spec=None,
            )
            self._install_row_map(
                result,
                epoch,
                scope,
                sort_spec,
                preserved_ids,
                current_id,
                recalculate_search,
            )
            return

        def work(cancel):
            return build_row_map(
                frame,
                dataset_revision=snapshot.dataset_revision,
                selection_revision=snapshot.selection_revision,
                scope=scope,
                mask=mask,
                sort_spec=sort_spec,
                cancel=cancel,
            )

        self._submit_task(
            "table-order",
            work,
            success=lambda result: self._install_row_map(
                result,
                epoch,
                scope,
                sort_spec,
                preserved_ids,
                current_id,
                recalculate_search,
            ),
            failure=self._derived_failure,
            receiver=self,
            exclusive_group="table-derived",
        )

    def _install_row_map(
        self,
        row_map: RowMap,
        epoch: int,
        scope: TableScope,
        sort_spec: SortSpec | None,
        preserved_ids: tuple[int, ...],
        current_id: int | None,
        recalculate_search: bool,
    ) -> None:
        snapshot = self._snapshot_provider()
        if (
            epoch != self.table_epoch
            or scope is not self.scope
            or sort_spec != self.sort_spec
            or snapshot.dataset is None
            or row_map.dataset_revision != snapshot.dataset_revision
            or row_map.selection_revision != snapshot.selection_revision
        ):
            return
        self.model.replace_row_map(row_map)
        self.view.restore_selection(self.model, preserved_ids, current_id)
        self._update_counts_and_empty_state(snapshot)

        if self._pending_go_source_id is not None:
            source_id = self._pending_go_source_id
            self._pending_go_source_id = None
            if self.view.select_source_id(self.model, source_id):
                self.view.set_feedback(f"Showing Source Row ID {source_id} in All rows.")

        self._refresh_search_navigation(autonavigate=self._pending_search_first)
        self._pending_search_first = False
        if recalculate_search and self.search_spec is not None:
            self._request_search(snapshot, self.search_spec)

    def set_search(self, text: str, mode: SearchMode) -> None:
        if not isinstance(mode, SearchMode):
            mode = SearchMode.CONTAINS_CASE_INSENSITIVE
        text = text
        if not text:
            self._cancel_task("table-search")
            self.search_spec = None
            self.search_result = None
            self.current_match = None
            self.view.set_search_status("")
            self._update_counts_and_empty_state(self._snapshot_provider())
            return
        spec = SearchSpec(text, mode)
        self.search_spec = spec
        self.search_result = None
        self.current_match = None
        self._request_search(self._snapshot_provider(), spec)

    def _request_search(self, snapshot: StateSnapshot, spec: SearchSpec) -> None:
        if snapshot.dataset is None:
            return
        epoch = self.table_epoch
        frame = snapshot.dataset.frame

        def work(cancel):
            return search_frame(
                frame,
                spec,
                dataset_revision=snapshot.dataset_revision,
                cancel=cancel,
            )

        self._submit_task(
            "table-search",
            work,
            success=lambda result: self._install_search(result, epoch, spec),
            failure=self._derived_failure,
            receiver=self,
            exclusive_group="table-derived",
        )

    def _install_search(self, result: SearchResult, epoch: int, spec: SearchSpec) -> None:
        snapshot = self._snapshot_provider()
        if (
            snapshot.dataset is None
            or result.dataset_revision != snapshot.dataset_revision
            or epoch != self.table_epoch
            or spec != self.search_spec
        ):
            return
        self.search_result = result
        self.current_match = None
        self._refresh_search_navigation(autonavigate=True)

    def _visible_match_rows(self) -> list[int]:
        if self.search_result is None or self.model.row_map is None:
            return []
        return self.model.row_map.visible_rows_for_source_ids(
            self.search_result.source_ids
        ).tolist()

    def _refresh_search_navigation(self, *, autonavigate: bool = False) -> None:
        if self.search_spec is None or self.search_result is None:
            self.view.set_search_status("")
            return
        rows = self._visible_match_rows()
        total_matches = len(self.search_result.source_ids)
        if not rows:
            self.current_match = None
            if self.scope is TableScope.SELECTED and total_matches:
                self.view.set_search_status(
                    f"0 matches in Selected rows · {total_matches:,} matches exist in All rows",
                    show_all=True,
                )
            else:
                self.view.set_search_status("0 matches")
            return

        if self.current_match is None or self.current_match >= len(rows):
            self.current_match = 0
        self.view.set_search_status(f"{self.current_match + 1} / {len(rows)}")
        if autonavigate:
            self._navigate_visible_row(rows[self.current_match])

    def next_match(self) -> None:
        rows = self._visible_match_rows()
        if not rows:
            return
        self.current_match = 0 if self.current_match is None else (self.current_match + 1) % len(rows)
        self.view.set_search_status(f"{self.current_match + 1} / {len(rows)}")
        self._navigate_visible_row(rows[self.current_match])

    def previous_match(self) -> None:
        rows = self._visible_match_rows()
        if not rows:
            return
        self.current_match = len(rows) - 1 if self.current_match is None else (self.current_match - 1) % len(rows)
        self.view.set_search_status(f"{self.current_match + 1} / {len(rows)}")
        self._navigate_visible_row(rows[self.current_match])

    def _navigate_visible_row(self, visible_row: int) -> None:
        if self.model.row_map is None:
            return
        source_id = self.model.source_row_id_at(visible_row)
        self.view.select_source_id(self.model, source_id)

    def show_matches_in_all(self) -> None:
        if (
            self.scope is not TableScope.SELECTED
            or self.search_result is None
            or len(self.search_result.source_ids) == 0
        ):
            return
        self._pending_search_first = True
        self.view.set_feedback("Switching explicitly to All rows for search matches.")
        self.set_scope(TableScope.ALL)

    def go_to_source_id(self, text: str) -> None:
        snapshot = self._snapshot_provider()
        if snapshot.dataset is None:
            self.view.set_feedback("No dataset is loaded.")
            return
        try:
            source_id = int(text.strip())
        except ValueError:
            self.view.set_feedback("Source Row ID must be an integer.")
            return
        total = len(snapshot.dataset.frame)
        if source_id < 1 or source_id > total:
            self.view.set_feedback(f"Source Row ID must be between 1 and {total:,}.")
            return
        if self.view.select_source_id(self.model, source_id):
            self.view.set_feedback(f"Showing Source Row ID {source_id}.")
            return
        if self.scope is TableScope.SELECTED:
            self._pending_go_source_id = source_id
            self.view.set_feedback(
                f"Source Row ID {source_id} is not in Selected rows; switching to All rows."
            )
            self.set_scope(TableScope.ALL)
            return
        self.view.set_feedback(f"Source Row ID {source_id} is not currently visible.")

    def copy_selection(self) -> str:
        rows = self.view.selected_rows()
        if not rows:
            self.view.set_feedback("Select one or more rows to copy.")
            return ""
        text = self.model.copy_text(rows, self.view.visible_columns())
        QApplication.clipboard().setText(text)
        self.view.set_feedback(f"Copied {len(rows):,} row(s) as TSV.")
        return text

    def _update_counts_and_empty_state(self, snapshot: StateSnapshot) -> None:
        dataset = snapshot.dataset
        if dataset is None:
            self.view.set_counts(self.scope, 0, 0, 0)
            self.view.set_feedback("No dataset loaded")
            return
        total = len(dataset.frame)
        visible = self.model.rowCount()
        self.view.set_counts(self.scope, visible, snapshot.selected_count, total)
        if total == 0:
            self.view.set_feedback("Dataset contains 0 rows.")
        elif self.scope is TableScope.SELECTED and visible == 0:
            self.view.set_feedback("Selected rows contains 0 rows.")
        elif self.search_spec is None:
            self.view.set_feedback("Ready")

    def _derived_failure(self, error: BaseException) -> None:
        self.view.set_feedback(f"Table operation failed: {type(error).__name__}")
