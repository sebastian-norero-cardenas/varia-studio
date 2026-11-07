# F4.C — Qt Plot Configuration Coherence & Distribution Parity Closure

F4.C closes the remaining Distribution parity item and makes the relationship
between visible plot controls and the currently published Figure explicit in
both Qt plot tabs. It does not change the scientific plotting engine, PlotPanel,
packaging or the canonical frontend.

## Plot configuration revisions

Each plot view owns a local monotonic `plot_settings_revision`. It is UI state,
not AppState.

For Scatter the existing responsibilities remain distinct:

```text
settings_epoch
-> dataset/reset/dialog stale protection

advanced_revision
-> effective Appearance/Annotations/Inset changes

plot_settings_revision
-> every visible setting that determines the next ScatterSpec
```

An effective advanced Apply increments both `advanced_revision` and
`plot_settings_revision`, but not `settings_epoch`.

Histogram needs only `plot_settings_revision` because it has no staged advanced
dialogs.

## User edits

Only user-facing signals mark settings dirty:

- combo-box `activated`;
- checkbox `clicked`;
- line-edit `textEdited`.

Programmatic dataset/reset population therefore does not create false dirty
events. Text edits only increment the revision and display:

```text
Settings changed. Plot again to apply them.
```

They do not call `build_spec()` or validate partial text. Validation still
occurs only when Plot is requested, so temporary values such as `-`, `1e` or
`abc` do not crash or destroy the current valid Figure.

Changing settings never redraws automatically and never mutates Matplotlib
artists already published by PlotPanel.

## Request/publication contract

Scatter captures:

```text
ScatterSpec
plot_settings_revision
```

Histogram captures:

```text
HistogramSpec
plot_settings_revision
```

The worker receives the captured spec. Publication never reconstructs a spec
from current controls.

The existing authorities remain unchanged:

```text
_plot_epoch
task generation
dataset_revision
selection_revision
exclusive_group="plots"
```

If a valid request finishes with the same settings revision, normal status is
shown:

```text
Relationship figure updated.
Distribution figure updated.
```

If controls changed after request admission, the valid older Figure may still
publish, and `current_spec` remains the spec that produced it, but both local
feedback and global status retain:

```text
Figure uses previous settings. Plot again to apply current settings.
```

A subsequent Plot captures the current settings; after successful publication
the warning disappears.

Latest-request-wins remains independent: an older superseded request cannot
publish merely because its settings revision is meaningful.

## Scatter coherence

The unified revision covers:

- X/Y columns;
- X/Y scale;
- absolute transforms;
- scientific labels;
- X/Y bounds;
- color column;
- color scale;
- color absolute/scientific;
- Show filtered-out rows;
- Appearance/sampling;
- annotations;
- inset.

The F4.B `advanced_revision` contract remains intact.

A control edit with a Figure already visible preserves that Figure and marks it
as needing Plot again. Advanced Apply behaves the same way.

Scatter Reset retains the F4.B behavior: pending Scatter is logically cancelled,
advanced dialogs close, all basic/advanced settings return to defaults, Figure
is cleared, `settings_epoch` advances and the unified plot-settings revision
also advances coherently.

## Distribution parity

Qt Distributions now exposes the legacy-relevant remaining controls:

```text
Title (optional)
Reset settings
```

The title maps directly to `HistogramSpec.title`. Blank preserves the existing
engine default title.

Histogram Reset restores:

```text
first numeric column
linear X
absolute off
scientific X off
automatic bounds
bins Auto
custom bins text 20
log bins off
Count
log Y off
scientific Y off
title empty
```

Reset cancels pending Histogram, clears the Figure, advances the local settings
revision and reports:

```text
Settings reset. Plot again.
```

No AppState field is modified.

## Histogram coherence

The Histogram revision covers:

- column;
- X scale;
- absolute/scientific;
- X bounds;
- bin rule/custom bins;
- logarithmic bins;
- Count/Density;
- logarithmic Y;
- scientific Y;
- title.

As with Scatter, a changed visible setting preserves the current Figure and
marks it dirty. A pending request may publish its valid captured Figure, but a
revision mismatch leaves the explicit Plot-again warning until a new Plot
publishes the current configuration.

## State boundaries

Dataset replacement continues to cancel pending plot work through the existing
controllers, reset controls and clear Figures. Any dirty warning associated with
the previous dataset disappears.

Selection/filter changes continue to cancel pending plots and clear Figures, but
preserve plot settings. They do not increment `plot_settings_revision` because
analytical selection already has `selection_revision`.

Visual configuration changes do not modify:

```text
revision
dataset_revision
selection_revision
filters
mask
source DataFrame
selected_count
```

## Figure/export ownership

PlotPanel remains the sole owner of the published Figure tuple and atomic
replacement. Export remains F3.G-owned:

```text
Figure A
-> controls B
-> no Plot
-> Export
= Figure A

Plot B
-> Export
= Figure B
```

Histogram titles are rendered into the published Figure before export, so PNG,
PDF and SVG require no special title/export logic.

## Cutover-readiness parity matrix

| Area | Classification | F4.C assessment |
| --- | --- | --- |
| Data | PARITY | Qt covers import options, Open/Reload, table exploration, filters, summary, selected count and import notices for the approved operational scope. |
| Relationships | PARITY | Scatter core plus advanced Appearance/sampling, annotations, inset, reset, coherent dirty/publication semantics and figure export are present. |
| Distributions | PARITY | Histogram scientific controls plus title, reset, coherent dirty/publication semantics and figure export are present. |
| Statistics | PARITY | Numeric, categorical, correlation, ddof conventions, column choice and CSV export are present; F4.C adds no statistics scope. |
| Export | INTENTIONALLY DEFERRED | Displayed plot export and Statistics CSV exist. Broader dataset/export formats remain explicitly deferred and are not added by F4.C. |
| Shell | PARITY | Qt has Open, Reload, import options, shortcuts, summary/notices and Guide. Canonical entry-point cutover itself remains deferred. |

F4.C identifies no new scientific/plot-coherence blocker for a future Qt cutover,
but this is implementation evidence only. Independent QA and a separate cutover
decision are still required.

## Validation scope

F4.C focal tests cover base-control dirty semantics, published-Figure
preservation, pending A/current B races, Plot B warning clearance, F4.B advanced
integration, title spec/render/export ownership, full Histogram reset, reset
cancellation, dataset/selection boundaries, invalid partial text, AppState
immutability and latest-request-wins.

The CI regression set is intentionally focal: F4.B, F3.D/E, F3.G export,
F4.A Open/Reload, filter invalidation and PlotPanel lifecycle. No new 200k
performance claim is made.

`F3A-SIGSEGV-CI = INDETERMINATE` remains unchanged.

## Final QA and integration record

Final independent QA:

```text
QA06B-F4C-01 = VERIFIED
PHYSICAL_F4C_X11 = NOT TESTED
QT_CUTOVER_READINESS = CONDITIONAL
```

F4C-OBS-01:

```text
PARITY = functional parity against the approved V2 scope,
not literal equivalence of every legacy widget.
```

The following conditions remain mandatory before any Qt cutover:

1. Real validation on a physical Ubuntu/X11 session.
2. A dedicated packaging/cutover gate covering:
   - `varia-studio`;
   - `python -m csv_analyzer`;
   - the PySide6 dependency;
   - README/install instructions;
   - legacy compatibility.
3. An explicit shutdown/teardown gate while preserving:
   `F3A-SIGSEGV-CI = INDETERMINATE`.

None of these cutover gates was executed as part of the F4.C integration.

Final CI evidence:

```text
Workflow: F4.C Qt plot coherence
Run: 42378806882
Run number: 5
Attempt: 1
Result: SUCCESS
HEAD: 7adff9404e693ebfcc04280bb74744ba65018da2
Tree: 3e347da092d38c8a7aa90718a5546d98751ead67

Artifact: f4-c-evidence
Artifact ID: 13576941386
Digest: sha256:0cdac418c0978d225c2c224fa9b328bfae875fc3fbd0eaebd01c048d650a6eac
```

F4.C was integrated into `main` by fast-forward only from
`4c16a3258aef594363e5e338a1bad1362b37c32f` to
`7adff9404e693ebfcc04280bb74744ba65018da2`, preserving the candidate tree
`3e347da092d38c8a7aa90718a5546d98751ead67` unchanged.

`F3A-SIGSEGV-CI = INDETERMINATE` remains unchanged and is not considered
resolved.
