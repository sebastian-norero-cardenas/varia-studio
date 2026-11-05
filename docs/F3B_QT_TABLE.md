# F3.B — Qt Table and Navigation

F3.B adds the first functional Qt data exploration view on top of the closed F3.A
foundation. It does not migrate charts, statistics, filters, editing or export.

## Integration record

F3.B was integrated into `main` by fast-forward from candidate
`507e1ec3014ee8cf578c1cbee835efa0fa728182` (tree
`02b3902db708996765c187e91416e76789377e19`). `QA06-F3B-02 = VERIFIED`.

`AUD-F3B-01` previously returned NO-GO with two MEDIUM findings: a
Selected-to-All materialization race during selection-revision changes, and
unordered categoricals being sorted by internal category codes. Both findings
were corrected in R2 and subsequently adjudicated closed by 01B.

`F3A-SIGSEGV-CI = INDETERMINATE` remains a separate, non-blocking incident
for F3.B. Four CI attempts observed a SIGSEGV in the F3.A Qt-foundation test
`test_callback_and_dataset_commit_run_on_gui_thread`; a fifth attempt passed.
QA additionally executed 70 independent processes with 70/70 PASS and no
SIGSEGV. The incident is not considered resolved and remains for follow-up if
it reappears or before a relevant release gate.

## Architecture

The data path is:

```text
Dataset / DataFrame
        ↓
TableController
 ├─ view-only state
 ├─ RowMap
 ├─ SortSpec
 └─ SearchSpec/SearchResult
        ↓
QtTaskRunner
        ↓
immutable derived result
        ↓ GUI thread
DataFrameTableModel
        ↓
QTableView
```

`src/csv_analyzer/table.py` is toolkit-neutral. It owns RowMap construction,
typed ordering, search and canonical copy serialization. Qt objects never enter
these workers.

`DataFrameTableModel` owns only the committed Dataset reference, an immutable
RowMap, exposed columns and column positions. It does not own AppState, filters,
widgets or dataframe copies.

`TableController` owns view-only scope/sort/search/navigation state. None of
scope, sort, search, visual selection, navigation, scrolling or copy changes
AppState revisions.

## Source Row ID and RowMap

Dataset identity is positional:

```text
dataset position = zero-based dataframe row position
Source Row ID    = dataset position + 1
```

The pandas index is not an identity.

RowMap stores an immutable `np.ndarray[int64]` of dataset positions. Selected
scope uses the current immutable AppState mask; All scope uses
`arange(len(frame))`. Reverse lookup is built lazily as one dense int64 inverse
array. No per-row Python dictionary is used.

A dataset revision resets table scope to Selected, sort/search state, visual
selection and focus, and replaces Dataset + RowMap inside a model reset. A
selection-only revision rebuilds the RowMap in Selected scope. In All scope, an
already materialized RowMap remains valid across later selection revisions only
when its dataset revision, scope and sort still satisfy the desired visual
projection. A pending Selected-to-All transition therefore retries
materialization if a selection-revision change makes the original asynchronous
result stale; a correctly materialized All RowMap is not rebuilt merely because
the analytical selection changes.

## Sort semantics

One real dataset column can be active at a time.

- Null/NA/NaT/NaN values are always last.
- Source Row ID ascending is the stable tie-break in both directions.
- DESC reverses only the primary criterion.
- Integer and nullable-integer sorting converts individual scalar values to
  Python integers, never the complete column to float or text.
- Float infinities remain numeric.
- Boolean and typed datetime columns keep typed ordering.
- Ordered categoricals respect their declared category order; unordered
  categoricals sort by deterministic textual representation rather than
  internal category codes.
- Object/mixed columns use a deterministic type-ranked fallback.
- Reset sort restores Source Row ID ascending within the current scope.

The exact integer sequence around 2^53 is covered by regression tests.

## Search and navigation

Search scans all real dataset columns and never includes Source Row ID. It uses
one O(rows) boolean accumulator, not a rows×columns string/boolean matrix.

Modes are exact canonical-text matching and Unicode `casefold()` contains.
SearchResult identifies matches by Source Row ID; it does not alter RowMap or
AppState.mask. Prev/Next derives match order through the current RowMap, so
navigation follows the visible sort.

When Selected scope has zero visible matches but All has matches, the view
reports that fact and offers an explicit “Show in All rows” action. Scope never
changes automatically.

Go to Source Row ID validates the positional identity. A valid ID hidden by
Selected scope explicitly switches to All and then navigates. Invalid IDs leave
scope unchanged.

## Display, selection and copy

Display text, underlying values and canonical copy/search text are separate.
Null, NaN, NaT and infinities have explicit display forms; exact integer values
are never reconstructed from DisplayRole.

QItemSelectionModel owns visual selection. RowMap replacement captures selected
Source Row IDs and restores only identities still visible. A dataset replacement
clears selection.

Copy serializes underlying scalar values as TSV using the CSV quoting rules for
tabs/newlines/quotes. It follows visible row order and visible columns and does
not mutate AppState.

## Scheduler integration

Costly sort and search requests use F3.A keys `table-order` and
`table-search`, both under `exclusive_group="table-derived"`. This prevents
the two derived table operations from consuming both F3.A workers
simultaneously.

Workers receive dataframe/mask snapshots, immutable specs and revision values.
Publication remains protected by F3.A application/task/dataset/selection gates
plus TableController's table epoch and expected scope/spec checks. A stale
worker may temporarily retain its shallow snapshot until return, but cannot
replace the current model state.

## Synthetic structural evidence

`tests/test_table_structural.py` generates S-200K-TABLE in memory:
200,000 rows × 61 int64 columns. It is structural evidence only, not a production
performance benchmark or a substitute for the historical real CSV.

The test checks O(N) RowMap storage, 200k model rows, absence of per-cell
widgets, no dataframe replacement/copy for scope/sort, no global 2-D cache in
the model, positional navigation, exact >2^53 copy and cooperative search
cancellation.

Performance/churn, SLA claims and leak analysis remain outside F3.B.
