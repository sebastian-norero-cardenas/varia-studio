# Architecture

## Canonical frontend and dependency direction

PySide6/Qt is the canonical Varia Studio desktop frontend. The public launchers
`varia-studio`, `csv-analyzer`, `varia-studio-qt`, and
`python -m csv_analyzer` all route to `csv_analyzer.qt_app`.

`QtCoordinator` owns one `AppState`, one bounded `QtTaskRunner`, and the
four Qt views. Views read shared snapshots and submit work through the
coordinator; they do not own replacement dataframes or update another view's
selectors.

`data.py` has no GUI dependency and returns a complete `Dataset` candidate.
`analysis.py` implements numerical operations without GUI or Matplotlib.
`plots.py` transforms selected column arrays into validated plot data, then
creates object-oriented Matplotlib `Figure` instances without pyplot or a global
backend. `exports.py` shares atomic destination handling between figures and
tables.

The canonical UI uses explicit views and focused dialogs without a generic event
framework, plugin layer or duplicated model objects. Configuration dataclasses
make scientific choices inspectable and independently testable.

The previous CustomTkinter frontend remains under `app.py` and `ui/` as a
non-canonical legacy implementation. It is available only through the
`[legacy]` dependency extra for compatibility/regression work and is not a
public launcher. Its source is intentionally retained during the cutover phase.

Importing `csv_analyzer` does not initialize either GUI toolkit. Qt imports are
deferred until the canonical entry point starts the application.

## Dataset replacement

1. The main thread increments a load ticket and submits a candidate load.
2. A worker scans record widths, parses values, checks requested date conversions,
   profiles columns and checks that the source file did not change while read.
3. The completion queue is polled on the main thread. Only the newest ticket can
   commit. Failure, cancellation or a stale result leaves existing state intact.
4. Commit installs the dataset, clears selection rules/mask and increments the
   revision before notifying the coordinator.
5. The coordinator cancels obsolete analyses, closes old dataset editors, resets
   selectors/configuration and clears figures/statistics across all four tabs.

A committed dataframe is read-only **by convention**, avoiding a redundant full
copy. Views and numerical routines never mutate it. External callers using the
Python API must observe the same rule. Positional row masks are copied on commit
and made non-writeable. Duplicate dataframe index labels cannot misalign points.

## Selection and derived results

Filters are immutable numeric conditions combined with AND. Bounds are inclusive
and apply before absolute-value transformations. A filter worker captures a
dataset revision; a result for any other revision is discarded. Plot preparation
and statistics capture the same dataset/mask/revision references.

`None` means all rows selected, avoiding a permanent all-true mask. Views invalidate
derived results after a filter change. Numeric formatting and plotting transforms
never alter source data. Plot sampling is an explicit rendering choice, separate
from the analytical selection.

## Threads and cancellation

The canonical Qt frontend uses the application-owned bounded `QThread`
scheduler approved by `ARQ-F3A-R4-01`. Each admitted request owns one
`QThread`, with at most two active requests and at most eight
application-pending requests. Newer generations supersede older work with the
same task key, while publication also checks application, dataset and selection
revisions.

`_active` retains physical ownership until `QThread.finished`, so
`physical == 0` means the request's `run()` has completed and that completion
has been recognized by the scheduler; it does not retrospectively resolve the
historical `F3A-SIGSEGV-CI` incident.

Cancellation checks occur during structural scanning and between analysis
steps. Pandas parsing is one non-interruptible section. A cancelled parse may
finish in the background, but stale results cannot publish. Qt widgets,
Matplotlib figure construction/rendering/navigation and export remain on the GUI
thread.

The retained CTk implementation keeps its historical worker architecture only
for legacy compatibility; it is not the canonical scheduler or frontend.

## Import and export choices

The structural scan costs an extra sequential read. It detects ragged input that
could otherwise cause pandas to infer an unintended index or pad missing fields.
It does not keep a second copy of text in memory. Delimited CSV uses the standard
CSV reader for quoting/multiline records; DAT uses a small whitespace tokenizer
with matching double-quote/comment rules. Parsing uses pandas' C engine and
round-trip float conversion without lossy downcasting.

Exports write to a temporary sibling file and publish via `os.replace` only after
successful completion. Tests verify that failed exports preserve previous files
and remove partial output. The GUI's native save dialog obtains overwrite
confirmation; programmatic exports reject existing destinations by default.

## Extension points

Add a new calculation to `analysis.py` with finite/missing-value semantics and
tests before exposing it in the UI. Add a plot via a typed specification,
preparation function and renderer. Keep long data work in the worker queue,
capture the current revision and invalidate its output on state changes. Avoid
adding mutable dataset copies to views or embedding scientific calculations in
button callbacks.
