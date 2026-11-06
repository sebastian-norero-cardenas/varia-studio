# F3.G — Qt Export Core

F3.G migrates the product's existing export workflows to the Qt frontend without
changing the toolkit-neutral export engines. It adds explicit export of the
currently published Scatter/Histogram Figure and CSV export of the current valid
Statistics result.

## Architecture and ownership

The Qt layer reuses `export_figure()`, `export_statistics()`,
`ExportConflictError` and the existing `atomic_destination()` authority.

`qt_app/exporting.py` is a focal Qt helper only. It owns the parented save
and overwrite-confirmation dialogs and implements:

```text
writer(overwrite=False)
  -> success
  -> ExportConflictError
       -> explicit Qt confirmation
       -> decline: stop without write
       -> accept: writer(overwrite=True) exactly once
```

It does not inspect the filesystem before publication and does not implement an
alternative atomic-write mechanism.

## Figure export

Each `PlotPanel` exposes `Export figure…` and a fixed DPI selector with
150, 300 and 600 DPI; 300 DPI is the default. The export control is disabled
without a valid Figure and enabled only while that panel owns a published
Figure.

Supported extensions are PNG, PDF and SVG, validated by `export_figure()`.
The Qt layer passes the panel's exact current `PlotPanel.figure` to the
existing engine. It never reconstructs the plot from data or a plot spec.

Figure export runs synchronously on the Qt GUI thread because it operates on
the published interactive Matplotlib Figure. A replacement or invalidation
closes an open export dialog and disables export before disposing the old
Figure, preventing stale publication.

Scatter and Histogram retain independent PlotPanel ownership, so exporting one
tab cannot select the other tab's Figure. Existing candidate ownership,
transactional replacement, rollback, draw_idle neutralization, toolbar lifecycle
and removal of Matplotlib's native Save action are unchanged.

## Statistics CSV export

Statistics exposes `Export CSV…` only for a currently valid, non-empty derived
result. The controller passes `StatisticsController.current_result` directly
to `export_statistics()`; it does not export the QTableView's formatted text.

The CSV therefore retains full computed precision, headers, no generated index,
UTF-8 encoding and the engine's `ddof` column when present.

Dataset, selection or settings invalidation, worker failure and display failure
clear the valid result and disable export. A failed/cancelled dataset load that
does not replace the committed dataset preserves the valid result and export
availability.

## Overwrite, races and atomic publication

The save dialog does not provide the final overwrite authority. Every write
first calls the existing engine with `overwrite=False`. Existing destinations,
dangling symlinks and concurrent destination creation therefore remain governed
by `atomic_destination()`.

On `ExportConflictError`, a parented Qt warning asks explicitly whether to
replace the destination. Decline performs no retry. Accept performs exactly one
retry with `overwrite=True`.

The core export regression suite remains authoritative for hard-link
publication, concurrent races, symlink/dangling-symlink behavior, once-resolved
overwrite targets, failure cleanup and preservation of an existing destination.

## Failures, cancellation and lifecycle

Cancelling the save dialog or declining replacement is not an error and does not
alter the Figure, Statistics result or AppState.

Figure/savefig and Statistics/to_csv failures are presented through the normal
Qt error path. The active Figure/result remains valid and exportable after such
an error.

Export dialogs are parent-owned. Plot invalidation/replacement and Statistics
invalidation close export dialogs owned by their view. Closing the application
uses the existing controller lifecycle; no export callback is retained for a
destroyed QObject.

## State invariants

Export does not modify AppState revision, dataset_revision, selection_revision,
filters or mask. It does not mutate the source dataset, replace/clear a Figure,
alter the canvas/toolbar, or modify the derived Statistics DataFrame.

## Deferred capabilities

F3.G does not add dataset/Data-table export, XLSX, JSON, clipboard export,
session/project export, batch export, background export architecture, new
graphics formats, widget screenshots, plot-data export or F3.H functionality.

`F3A-SIGSEGV-CI = INDETERMINATE` remains unchanged. Any recurrence in F3.G CI
must be reported rather than hidden by retries.
