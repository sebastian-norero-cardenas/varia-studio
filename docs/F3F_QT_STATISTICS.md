# F3.F — Qt Statistics Core

F3.F replaces the Qt Statistics placeholder with a functional view over the
existing toolkit-neutral statistical engine. It does not change the formulas in
`analysis.py` and does not add export.

## Architecture and ownership

The Qt pipeline is:

```text
StatisticsView
  -> StatisticsRequest
  -> StatisticsController
  -> QtTaskRunner worker
  -> numeric_summary / categorical_summary / correlation_summary
  -> pandas DataFrame
  -> GUI thread
  -> StatisticsResultModel
  -> QTableView
```

Workers receive the committed dataframe reference, committed positional mask and
immutable request settings. They never receive widgets or live AppState.
`StatisticsController` may retain only the current request/result derived from
the dataset. `StatisticsResultModel` owns only the current derived DataFrame
and deliberately does not reuse the F3.B Dataset/RowMap/Source Row ID model.

## Numeric semantics

Numeric mode delegates exclusively to `numeric_summary()`. Boolean columns are
excluded. Only finite selected values participate in mean, standard deviation,
variance, minimum, quartiles, median and maximum. Missing and infinite values
remain separate counts. Quartiles retain the engine's linear interpolation.
Sample maps to `ddof=1`; Population maps to `ddof=0`; the returned `ddof`
column remains visible. For `n <= ddof`, standard deviation and variance may
be undefined.

## Categorical semantics

Categorical mode delegates exclusively to `categorical_summary()`. It includes
non-numeric columns, including boolean and datetime columns. Missing values are
not categories. The existing engine supplies Unique, one Most frequent value,
Frequency and Tied modes. Datetime columns may additionally report Earliest and
Latest.

## Correlation semantics

Correlation mode delegates exclusively to `correlation_summary()` and uses a
focused multi-column dialog. At least two distinct numeric columns are required;
the default after a committed dataset load is the first two numeric columns when
available.

The result is pairwise, not a correlation matrix. Pearson r uses finite
observations pairwise, so Paired N may vary by pair. Constant columns and fewer
than two paired observations produce an undefined coefficient plus the engine's
Note.

## Analytical selection and invalidation

Statistics consumes only committed `AppState.mask`. Table Selected/All scope,
search, sort and visual row selection do not affect results. F3.C filter commits
change `selection_revision` and therefore invalidate the displayed statistics.

Changing mode, ddof or correlation columns cancels pending statistics work,
clears the result and reports `Settings changed. Compute again.`. A committed
selection change clears the result and reports
`Selection changed. Compute again to use the current filters.`.

A committed dataset replacement cancels statistics work, clears the result and
resets correlation columns. Failed or cancelled load attempts do not change
`dataset_revision`, so an already valid result/settings for the active dataset
remain intact.

## Async, stale publication and lifecycle

Statistics uses task key `statistics` with no exclusive group. It therefore
does not supersede scatter or histogram work. The scheduler generation and
application publication gates enforce application epoch, dataset revision,
selection revision and lifecycle. The controller adds a local statistics epoch
and exact request equality so settings-replaced work cannot publish.

Closing cancels statistics logically and relies on the F3.A scheduler for
physical worker ownership before lifecycle reaches CLOSED. No result may publish
after close.

## Result model and display

The result table is read-only and selectable with standard QTableView scrolling
and headers. Values remain unrounded in the stored DataFrame. Display formatting
uses approximately six significant digits for finite floats, `—` for
undefined/NaN, `∞` and `−∞` for infinities, exact integer text, and otherwise
preserves text.

## Empty states

No dataset, no numeric columns, no categorical columns and fewer than two
numeric columns are handled without fallback or crash. A zero-row analytical
selection is valid: Numeric/Categorical retain zero counts from the engine and
Correlation reports Paired N zero with the existing Note.

## Deferred capabilities

F3.F does not implement statistics export, clipboard export, result sorting,
correlation matrices, new statistical formulas, SciPy integration, new plot
families, session persistence or CTk removal.

`S-200K-STATISTICS` is synthetic structural evidence only. It exercises
worker execution, numeric/categorical/correlation summaries, committed masks,
cancellation/latest-request behavior and source DataFrame immutability for
200,000 rows. It is not a performance SLA.

`F3A-SIGSEGV-CI = INDETERMINATE` remains unchanged. Any recurrence in F3.F CI
must be reported rather than hidden by retries.

## Final integration and QA status

F3.F implementation candidate:

- Candidate commit: `61557303f7a9650098922bb1087912d8a57e830e`.
- Candidate tree: `b2c3cad4b855f708bcc37e413d82b8bbd129c87c`.
- Baseline: `c1ca9f4cdcd92bbaf43f5f8d1ff37aa00de7d1c0`.
- Integration: fast-forward only from the baseline to the candidate; no squash,
  rebase, cherry-pick, merge commit or conflict resolution.
- `QA06-F3F-01 = VERIFIED`.
- `PHYSICAL_UI_X11 = PASS`.
- F3.F blockers: none.
- Final findings: HIGH none; MEDIUM none; LOW none.

Final verification evidence:

- F3.F Python 3.10: 8/8 PASS.
- F3.F Python 3.12: 8/8 PASS.
- S-200K-STATISTICS: 2/2 PASS.
- Toolkit-neutral analysis: 26/26 PASS.
- F3.E combined command: 20/20 PASS.
- F3.D combined command: 20/20 PASS.
- F3.C: 22/22 PASS.
- F3.B: 42/42 PASS.
- F3.A: 40/40 PASS.
- historical/core: 56/56 PASS.
- Ruff: PASS.

The F3.D and F3.E 20/20 figures above are the combined-command counts from the
F3.F workflow; they are not reinterpretations of those milestones' historical
focal test counts.

Final CI evidence:

- Workflow: `F3.F Qt statistics`.
- Run: `42238007888` (run number 5, attempt 1).
- Result: SUCCESS.
- Artifact: `f3-f-evidence`, artifact ID `13512578574`.
- Artifact digest:
  `sha256:e17dd6f98ebd7311a5adf69076f17bb3a77a1bf215dd8d3be2a36ba550a196ce`.

The legacy Tk/CTk teardown message
`invalid command name "...update" ("after" script)` is inherited,
non-blocking noise and is not attributed to F3.F.

`F3A-SIGSEGV-CI = INDETERMINATE` remains unchanged. It did not recur during
F3.F verification and is not declared resolved.

F3.F is integrated and independently verified, but formal milestone closure
remains the responsibility of 01B.

