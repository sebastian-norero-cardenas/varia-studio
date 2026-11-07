# F5.A-UX1 — Qt Productization Polish

F5.A-UX1 closes the pre-release UX polish requested after UX05-F5A-01 without
redesigning Varia Studio or changing scientific/state/worker architecture.

## Targeted audit findings

Required candidate closure set:

```text
UX-H01 UX-H02 UX-H03
UX-M01 UX-M03 UX-M04 UX-M05 UX-M07
UX-M02 UX-M06 UX-M08
UX-L01 UX-L02 UX-L03 UX-L04
```

The candidate addresses the requested remediation areas below. The audit IDs
remain the authority for their original wording; this document records the
implemented product behavior rather than reinterpreting the audit.

## Relationships and Distributions

The parameter controls remain inside their scroll areas, while status, primary
Plot/Update action and `Reset all settings` live in a fixed footer outside the
scroll region.

`PlotPanel` retains sole ownership of the published Figure/canvas/toolbar and
adds a persistent result-state label next to the figure caption:

```text
No result
Current
Changes not applied
Previous result
```

A successfully published Figure matching visible controls is `Current`.
Changing visible settings preserves the Figure, marks
`Changes not applied`, and changes the primary action to `Update plot`.
When a valid older request publishes after controls changed, the Figure is
published normally but is marked `Previous result`. Publishing the current
settings returns to `Current`.

Reset clears the Figure, restores the established F4.B/F4.C defaults and leaves
the panel in `No result`.

Export still exports exactly the Figure owned by PlotPanel; no scientific or
export logic was moved into the result-state UI.

Plot errors no longer use a hard-coded `#a33` color. Error recognition is
textual and palette-compatible.

## Data tab

Without a dataset, dataset-dependent controls are disabled and the view says:

```text
Open a dataset to inspect rows.
```

Scope, Reset sort, Search, Search mode, Prev/Next, source-row navigation, Filters,
table interaction and Ctrl+C copy are enabled only when appropriate.

Derived table work now reports honest pending states:

```text
Sorting…
Searching…
Updating view…
```

The previous committed RowMap remains visible during pending work. Search clears
the old match counter while pending. Sort and scope failures restore controls to
the projection that is still published.

Contextual failures use the useful worker cause without a traceback:

```text
Could not sort the table: <cause>
Could not search the dataset: <cause>
Could not update the table view: <cause>
```

Existing generation/revision gates remain authoritative; stale derived results
still cannot publish.

Horizontal table-header tooltips expose metadata already present in
`Dataset.columns`:

- full column name;
- kind;
- dtype;
- missing count;
- infinite count for numeric columns.

No metadata is recomputed or invented.

## Statistics

Mode, Compute and Export CSV remain stable. Contextual controls are now shown
only when relevant:

```text
Numeric      -> Convention
Categorical  -> no Convention, no Correlation controls
Correlation  -> Choose columns + selected-column summary
```

Statistical calculations and conventions are unchanged.

## Shell and navigation

The shell now separates dataset filename, dataset metrics and global status.
Filename/status labels preserve the full value as a tooltip while visually
eliding long text.

Import notices consume no permanent height when absent. With notices, a compact
band and `Import notices…` button are shown.

Global cancel is labelled:

```text
Cancel tasks
tooltip: Cancels all running operations
```

`Ctrl+F` is a window shortcut. It activates Data, focuses Search and selects
existing search text.

The window opens at 1100×700 and has a 900×600 minimum. Fixed plot footers keep
essential actions reachable without introducing a responsive-layout framework.

The filter editor action previously labelled `New` is now
`Clear editor`, preserving its existing behavior.

## Scope boundaries preserved

F5.A-UX1 does not change:

- AppState or task scheduler architecture;
- scientific formulas;
- search/filter/table engines;
- plot families or statistical modes;
- canonical Qt packaging/routing;
- CTk legacy retention;
- release/tag/version.

No 200k performance campaign is claimed.

## Validation

Focal UX tests cover fixed plot footers/result-state transitions, export
ownership, Data empty/pending/failure/stale semantics, Statistics contextual
visibility, Ctrl+F, shell hierarchy/notices/minimum size, header metadata and
filter wording.

Regression CI is intentionally focal across F3.B–F4.D. Installed canonical Qt
smoke verifies the productized shell and lifecycle from the built wheel.

`PHYSICAL_F5A_UX1 = NOT TESTED` until an external physical UI gate is run.

`F3A-SIGSEGV-CI = INDETERMINATE` remains unchanged.
