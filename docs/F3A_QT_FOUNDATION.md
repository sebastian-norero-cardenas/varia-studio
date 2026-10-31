# F3.A — Qt Foundation

F3.A introduces an independent PySide6 bootstrap without replacing the existing
CustomTkinter application. The canonical `varia-studio` and compatibility
`csv-analyzer` commands still start CTk; `varia-studio-qt` and
`python -m csv_analyzer.qt_app` start the minimal Qt foundation.

## Ownership

- `AppState` remains the single committed dataset store.
- `QtCoordinator` owns publication policy and lifecycle.
- `QtTaskRunner` owns one private `QThreadPool` with two worker threads.
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
pool, bridge and runnable objects alive until physical activity reaches zero. No
indefinite wait is performed on the GUI thread.

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


## R3 logging, physical exclusivity and callback lifetime

Observability is best-effort and outside the functional correctness boundary.
Every Qt-foundation log call goes through a no-throw helper. A broken logging
handler cannot prevent outcome delivery, scheduler cleanup, publication or
shutdown. Error logs contain event/task identity, status/timing and structural
exception metadata such as exception class, cause class, function and line.
They do not include `str(exception)`, `repr(exception)`, raw traceback text,
dataset values or full dataset paths. User-facing domain errors remain separate
and may retain their appropriate UX message.

Outcome availability no longer releases physical scheduler ownership. A
runnable emits its outcome first and a separate physical-finished signal only
after the post-outcome worker tail. The scheduler keeps the request in
`_active` until that second signal is handled on a later GUI event turn.
Exclusive groups additionally use a per-group lock held for the entire runnable
body, so a replacement LOAD cannot enter its loader body while the previous
LOAD still owns the runnable tail. The global pool remains at two workers and
unrelated work may still use the other worker.

When `submit_task()` receives a bound QObject method and no explicit
`receiver`, the coordinator infers that QObject as the receiver for that
specific success or failure callback. Success and failure callbacks may belong
to different QObjects; each callback has an independent inferred lifetime gate
and only the callback selected by the outcome is checked. An explicit
`receiver` retains the existing behavior and overrides inference for both
callbacks. Ordinary Python callables require no receiver. A closure that merely
captures a QObject cannot be inferred reliably; callers must pass
`receiver=<that QObject>` explicitly when publication must depend on its
lifetime.

State snapshots are shallowly immutable. Dataset/DataFrame objects are shared by
reference and consumers have a read-only contract; future derived-data work
must prove that it does not mutate those shared objects.
