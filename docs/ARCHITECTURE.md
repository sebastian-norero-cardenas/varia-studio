# Architecture

## Ownership and dependency direction

`Application` coordinates one `AppState`, one `TaskRunner` and four views.
Views read the shared state and submit requests; they never own replacement
dataframes or update another tab's selectors. `Application.app_state` is distinct
from Tk's inherited `state()` window method.

`data.py` has no GUI dependency and returns a complete `Dataset` candidate.
`analysis.py` implements numerical operations without Matplotlib or Tk.
`plots.py` transforms selected column arrays into validated plot data, then creates
object-oriented Matplotlib `Figure` instances without pyplot or a global backend.
`exports.py` shares atomic destination handling between figures and tables.

The UI is deliberately small: explicit views and focused dialogs, without a
generic event framework, plugin layer or duplicated model objects. Configuration
dataclasses make scientific choices inspectable and independently testable.

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

Two worker threads handle import and numerical work. They never call Tk, including
`after()`. Each task key has a generation number and cancellation event; newer
requests cancel and supersede older requests with that key. A queue carries results
to a main-thread poller. Errors in a callback do not stop polling unrelated tasks.

Cancellation checks occur during the structural scan and between analysis steps.
Pandas parsing is one non-interruptible section. A cancelled parse may finish in
the background, but it cannot publish stale state. Tk and Matplotlib creation,
navigation, drawing and export remain on the main thread because their interactive
backends are not thread-safe.

### F3.A Qt scheduler amendment

`ARQ-F3A-R4-01` approves the Qt-foundation scheduler as an
application-owned bounded `QThread` scheduler rather than a private
`QThreadPool`. Each admitted request owns one `QThread`, with at most two active
requests and at most eight application-pending requests. `_active` retains
physical ownership until the corresponding `QThread.finished` notification, so
`physical == 0` means the request's `run()` has completed and that completion has
been recognized by the scheduler; it does not claim pthread join/TLS teardown.
Exclusive load replacement remains application-scheduled, allowing unrelated
work to use the second worker while a prior load is still physically owned.

This amendment applies to the PySide6 foundation only. The existing Tk/CTk
worker architecture remains unchanged during the coexistence phase.

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
