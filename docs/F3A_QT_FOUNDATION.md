# F3.A — Qt Foundation

F3.A introduces an independent PySide6 bootstrap without replacing the existing
CustomTkinter application. The canonical `varia-studio` and compatibility
`csv-analyzer` commands still start CTk; `varia-studio-qt` and
`python -m csv_analyzer.qt_app` start the minimal Qt foundation.

## Ownership

- `AppState` remains the single committed dataset store.
- `QtCoordinator` owns publication policy and lifecycle.
- `QtTaskRunner` owns at most two per-request `QThread` workers; Qt signals remain the GUI-thread publication boundary.
- The runner queue is application-managed and bounded to eight pending requests.
- Workers receive only a callable, cancellation Event and immutable revision
  metadata. They never receive live `AppState`, widgets or view callbacks.
- The Qt shell is intentionally minimal; table, charts and statistics remain
  outside F3.A.

A load is exclusive with other physical loads. Replacing a logical LOAD sets the
old cancellation Event immediately but waits for that physical worker to finish
before starting another LOAD. Other work may use the second worker.

Closing follows STARTING -> RUNNING -> CLOSING -> CLOSED. CLOSING invalidates
publication, stops accepting work and requests cancellation, while keeping the
bridge and admitted per-request `QThread` objects alive until physical activity
reaches zero. No indefinite wait is performed on the GUI thread.

## Revision domains

`AppState.revision` is preserved for CTk compatibility.

- Dataset commit: revision +1, dataset_revision +1, selection_revision +1.
- Filter commit: revision +1, selection_revision +1.
- Failed, cancelled or stale load: no revision changes.

Toolkit-neutral `StateSnapshot` instances carry stable committed references and
revision tokens. Qt publication checks application epoch, key/generation,
dataset revision, selection revision, load ticket and lifecycle before invoking
a GUI-thread callback.

## Optional PySide6 policy

PySide6 is a transitional **optional dependency** while CTk and Qt coexist.

A base installation:

```bash
python -m pip install .
```

installs CTk/core dependencies but does not require PySide6.

The Qt route is enabled explicitly:

```bash
python -m pip install '.[qt]'
```

Development of the Qt route uses both extras:

```bash
python -m pip install '.[dev,qt]'
```

The `qt` extra declares `PySide6>=6.11.3,<6.13`. This avoids making the
experimental spike's exact 6.12.0 pin the project dependency policy while
keeping F3.A inside the current Qt 6.11/6.12 compatibility band.

The CPython 3.12 constraints file pins PySide6 6.12.0 and its split wheels only
for reproducible environments that actually install the `qt` extra. Those
constraint entries do not turn PySide6 into a base dependency.

Importing `csv_analyzer`, core modules, `csv_analyzer.qt_app`, or the Qt
bootstrap does not import PySide6 eagerly. Without the `qt` extra:

- `varia-studio-qt --help` and `python -m csv_analyzer.qt_app --help` work,
  because argument parsing does not require Qt;
- attempting to start the Qt UI exits non-zero with an actionable stderr
  message telling the user to install the `qt` extra, without a normal raw
  traceback.

Matplotlib's QtAgg backend is compatible with PySide6, although QtAgg
integration itself is not part of F3.A.

This is transitional packaging, not the final release packaging decision.

## R2 close, error-boundary and observability notes

The final close request is deliberately deferred by one Qt event-loop turn.
`closeEvent()` first ignores the user's close request and emits
`close_requested`; once the coordinator reaches `CLOSED`, `allow_close()`
sets the acceptance flag and schedules the definitive `close()` with
`QMetaObject.invokeMethod(..., Qt.QueuedConnection)`. This avoids re-entering
`closeEvent()` from the same synchronous signal chain and ties the queued call
to the shell QObject rather than to a standalone Python timer callback.

Task logging uses compact Python logging messages rather than a telemetry
framework. DEBUG events include task identity (key, generation, application
epoch, dataset revision and selection revision), admission, cancellation,
physical completion, outcome receipt/publication/discard and elapsed time.
R3 supersedes the former raw-traceback policy: technical logs keep structural
exception metadata only and never log exception messages or raw traceback text.

The development extra uses `tomli` only on Python <3.11 so metadata tests remain
compatible with the declared Python 3.10 floor without adding a runtime
dependency.


## R3 logging, physical exclusivity and callback lifetime (historical; superseded by R4)

Observability is best-effort and outside the functional correctness boundary.
Every Qt-foundation log call goes through a no-throw helper. A broken logging
handler cannot prevent outcome delivery, scheduler cleanup, publication or
shutdown. Error logs contain event/task identity, status/timing and structural
exception metadata such as exception class, cause class, function and line.
They do not include `str(exception)`, `repr(exception)`, raw traceback text,
dataset values or full dataset paths. User-facing domain errors remain separate
and may retain their appropriate UX message.

R3 introduced a separate physical-finished signal, deferred release and a
per-group lock to keep logical outcome availability distinct from worker
ownership. `AUD-F3A-01-R1` showed that this still released ownership before
the runnable had provably returned and could consume the second worker with a
replacement LOAD waiting on the lock. Those R3 mechanics are historical and do
not describe the final scheduler; R4 below supersedes them with per-request
`QThread.finished` ownership and application-level admission.

When `submit_task()` receives a bound QObject method and no explicit
`receiver`, the coordinator infers that QObject as the receiver for that
specific success or failure callback. Success and failure callbacks may belong
to different QObjects; each callback has an independent inferred lifetime gate
and only the callback selected by the outcome is checked. An explicit
`receiver` is an additional lifetime gate; it never replaces the inferred
owner gate of a bound QObject method. Ordinary Python callables require no receiver. A closure that merely
captures a QObject cannot be inferred reliably; callers must pass
`receiver=<that QObject>` explicitly when publication must depend on its
lifetime.

State snapshots are shallowly immutable. Dataset/DataFrame objects are shared by
reference and consumers have a read-only contract; future derived-data work
must prove that it does not mutate those shared objects.


## R4 diagnostic boundary, physical return and callback gates

R4 makes exception metadata preparation itself best-effort. Structural error
introspection and the logging call now execute inside a no-throw boundary that
catches diagnostic failures without replacing the functional exception.
Messages, raw tracebacks, paths and dataset payloads remain excluded from
technical logs.

R4 removes the R3 assumption that a signal emitted inside a QRunnable, plus a
queued GUI turn, proves physical return. The bounded scheduler keeps two worker
slots and eight application-pending requests and uses one owned `QThread` per
admitted request. Outcome publication may happen before `run()` returns;
scheduler ownership remains in `_active` until that request's Qt
`finished` signal, which is emitted after `run()` returns. The historical
`physical_finished` bridge signal is retained only as a pre-return adversarial
checkpoint and is never used to release ownership. Consequently a replacement
LOAD stays in the application queue while its predecessor is physically owned,
and unrelated work can use the second worker instead of being trapped behind an
exclusive-group lock.

A bound QObject callback now has two independent lifetime gates when
`receiver=` is supplied: its real QObject owner and the explicit receiver.
Both must be alive for the selected callback to run. Success and failure
bindings keep independent owner gates, and only the callback selected by the
outcome is evaluated. Bound Python methods that are not QObjects continue to
use normal weak-method semantics. Closures that merely capture a QObject remain
non-inferable and must pass `receiver=` explicitly.

State snapshots remain shallowly immutable: Dataset/DataFrame objects are
shared by reference and consumers retain the read-only contract.
