# F3.C — Qt Numeric Filters and Analytical Selection

F3.C makes the Qt frontend a producer of the analytical row selection already
consumed by F3.B. It reuses the existing toolkit-neutral `FilterRule`,
`build_filter_mask()` and `AppState.commit_filters()` contracts; it does not
add filter types or change scientific semantics.

## Semantics

Only `Dataset.numeric_columns` are offered. Rules are inclusive numeric bounds
on raw dataset values and are combined with AND. Missing, NaN and positive or
negative infinity do not satisfy a numeric rule. Empty staged rules mean clear
filters: committed rules become `()` and mask becomes `None`.

Integer, nullable-integer and unsigned-integer bounds are parsed through
`Decimal`, converting to `int` only when integral. This preserves exact values
above 2^53, UInt64 values above 2^63 and fractional Decimal bounds used by the
engine's ceil/floor semantics. Float-column bounds are finite Python floats;
NaN, infinity and invalid text are rejected before task submission.

## Staging and ownership

`FilterDialog` owns only staged editor state. Opening it copies committed
`AppState.filters` and captures revision, dataset revision and selection
revision. Add/update/remove/new/clear operations affect only the staging list.
Cancel never mutates application state. Apply is rejected if the captured state
is stale, requiring the editor to be reopened against the current dataset and
selection.

`QtCoordinator` owns task submission and commit. Non-empty rules run
`build_filter_mask(frame, rules, cancel)` in the Qt worker scheduler under key
`filters` and `exclusive_group="filters"`. Workers receive the dataframe,
immutable rules and cancellation event, never live AppState. Successful results
return to the GUI thread and commit through revision-checked
`AppState.commit_filters()`.

A newer filter request replaces the older logical generation. Failed, cancelled
or stale work cannot clear or replace the last committed mask. Clearing filters
cancels/invalidate prior filter work before synchronously committing the empty
selection state. Successful dataset replacement cancels obsolete filter work;
failed/cancelled replacement leaves the committed dataset and filter selection
untouched.

## F3.B interaction

F3.B remains the sole owner of table view-state. A filter commit changes
`selection_revision`, which causes Selected scope to rebuild its RowMap while
preserving the active sort, recalculating search as needed and restoring only
visual Source Row IDs still visible. Zero selected rows are valid.

In All scope, the analytical selection count changes but an already valid All
RowMap stays materialized. This preserves the F3.B R2 fix for the historical
Selected-to-All race: selection changes do not collapse All back to Selected.
Go-to for a filtered-out Source Row ID still uses F3.B's explicit transition to
All.

## UI and limits

The existing table area gains one compact `Filters · none` / `Filters · N`
button. It is disabled with no dataset or no numeric columns. The editor is a
small QDialog with a QTableWidget for the short rule list; the large dataset
continues to use QTableView/QAbstractTableModel.

F3.C does not implement categorical/date/text filters, OR/NOT/groups, arbitrary
expressions, saved presets, transformations, plots, statistics, export, QtAgg,
DuckDB, Polars, CTk removal or F3.D.

`S-200K-FILTER` is synthetic structural evidence only: 200,000 rows with
representative numeric columns. It checks background execution, O(N) boolean
mask storage, multiple rules, replacement/cancellation semantics, no dataframe
copy as selection and read-only committed mask. It is not a production SLA or a
substitute for the historical real CSV.

`F3A-SIGSEGV-CI = INDETERMINATE` remains a separate incident. F3.C does not
modify or investigate the F3.A scheduler; any recurrence in F3.C CI must be
reported rather than hidden by retries.
