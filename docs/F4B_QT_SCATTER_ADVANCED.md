# F4.B — Qt Scatter Advanced Parity

F4.B completes the advanced Scatter settings already supported by the
toolkit-neutral plotting engine and legacy frontend. It does not extend the
scientific engine and does not modify PlotPanel ownership.

## Ownership

`ScatterView` owns staged view configuration:

```text
ScatterStyle
tuple[Overlay, ...]
InsetSpec | None
```

This state is local to the Relationships view. It is not stored in AppState and
is not shared with Histogram or Statistics.

Three parent-owned Qt dialogs edit staged copies:

- `ScatterStyleDialog`
- `ScatterOverlayDialog`
- `ScatterInsetDialog`

Apply replaces only the corresponding staged Scatter settings. Cancel leaves the
previous settings unchanged. Apply never creates or mutates a Figure.

## Appearance and sampling

The migrated defaults remain identical to the plotting engine/legacy frontend:

```text
title = ""
x_label = ""
y_label = ""
color_label = ""
marker = "o"
size = 12.0
opacity = 0.8
excluded_opacity = 0.12
cmap = "viridis"
grid = True
sample_limit = None
```

Supported marker and colormap choices match the approved F4.B scope. Numeric UI
values are parsed as finite numbers and the resulting values are validated by
`validate_scatter()`; Qt does not implement a second scientific validator.

Sampling is display-only. `All points` maps to `sample_limit=None`, while
`Limit each group` maps to a positive integer. The existing deterministic
`prepare_scatter()` sampling remains authoritative. AppState selected counts,
filters and Statistics continue to use the full analytical selection.

## Annotations

The annotation editor owns a staged ordered list and supports Add, Edit, Remove
and Clear all for exactly:

```text
Horizontal line
Vertical line
Slope line
Segment
Point
Text
```

Only fields relevant to the current annotation kind are shown. Every constructed
item is passed through `validate_overlay()` before being added/updated.

## Inset zoom

Inset may be Off or On. When enabled it exposes bounds, width, height, one of the
four supported corners, rectangle toggle, edge color, line width and the legacy
`inherit/linear/log` scale overrides.

The dialog constructs `InsetSpec` and validates it through
`validate_scatter(ScatterSpec(..., inset=inset))` using the current main X/Y
scales. The Qt layer does not duplicate inset formulas or log-bound rules.

## ScatterSpec construction

`ScatterView.build_spec()` combines existing axis/color controls with the
staged advanced settings:

```text
AxisSpec.label
ScatterSpec.title
ScatterSpec.color_label
marker / size / opacity
excluded_opacity
cmap
grid
sample_limit
overlays
inset
```

The plotting pipeline remains:

```text
build ScatterSpec
-> prepare_scatter() in worker
-> render_scatter() on GUI thread
-> PlotPanel atomic replacement
```

No dialog modifies Matplotlib artists directly.

## State and stale protection

A committed dataset replacement:

- cancels pending Scatter work through the existing controller contract;
- closes/rejects advanced dialogs;
- increments a local Scatter settings epoch;
- restores basic and advanced Scatter defaults;
- clears overlays/inset and the published Figure.

A selection/filter change clears the Figure but preserves appearance,
annotations and inset.

`Reset settings` closes/rejects advanced dialogs, invalidates pending Scatter
work, restores X/Y/color/excluded controls and all advanced defaults, and clears
the current Figure with:

```text
Settings reset. Plot again.
```

Dialog Apply callbacks carry the local settings epoch, so a dialog opened before
dataset replacement/reset cannot publish stale values later. Application close
also rejects advanced dialogs before PlotPanel teardown.

## Figure and export relationship

Appearance/annotation/inset Apply leaves the currently published Figure intact
until Plot is pressed again. F3.G therefore continues to export exactly the
published Figure:

```text
Figure A
-> stage settings B
-> export
= Figure A

Plot B
-> export
= Figure B
```

Inset and overlays are rendered by the existing engine and require no export
special cases.

## Structural sampling evidence

`S-200K-SCATTER` uses a synthetic 200,000-row frame. It verifies worker
preparation, selected/excluded pre-sampling counts, deterministic fixed-seed
sampling, visual limits, Statistics independence and latest-request
cancellation. It is structural evidence only; no FPS or 200k-point drawing SLA
is inferred.

## Deferred

F4.B does not change Histogram, add regression fitting/trendlines, introduce new
plot families, categorical color, interactive/drag annotations, packaging
entrypoints, base dependencies or CTk.

`F3A-SIGSEGV-CI = INDETERMINATE` remains unchanged.
