# F3.E — Qt Histogram / Distributions Core

F3.E migrates histogram distributions to the Qt frontend without changing the
toolkit-neutral scientific engine in `plots.py`. It reuses the QtAgg
`PlotPanel` lifecycle closed by F3.D-R2.

## Architecture

`HistogramView` owns only controls and builds `HistogramSpec`.
`HistogramController` owns derived histogram view-state, async request epoch
and stale invalidation. `PlotPanel` remains the sole owner of the active
Figure/canvas/toolbar tuple.

```text
AppState snapshot
  -> HistogramSpec
  -> QtTaskRunner worker
  -> prepare_histogram(frame, spec, mask, cancel)
  -> HistogramData
  -> GUI thread
  -> render_histogram(data, spec)
  -> PlotPanel / FigureCanvasQTAgg.draw()
```

Workers receive dataframe, immutable spec, committed positional mask and
cancellation event. They never receive AppState, widgets or Figures. Rendering,
QtAgg canvas construction and drawing remain on the GUI thread.

## HistogramSpec mapping

The Distributions tab exposes one numeric variable, X linear/log scale,
absolute transformation, scientific labels and optional X bounds. Binning maps
directly to `auto`, `fd`, `sturges`, `sqrt` or a custom integer from 1
through 4096. Logarithmically spaced bins map only to `log_bins`; this is
independent from logarithmic X display.

Y normalization maps Count/Density to `density=False/True`. Logarithmic Y and
scientific Y labels map to the existing fields. Appearance is intentionally
fixed to the existing F3.E defaults: color `#178F80`, opacity `0.85`, empty
title.

## Scientific semantics

F3.E does not implement binning or density formulas in Qt. The existing engine
applies absolute value before bounds, discards missing/infinite values, requires
positive values when log bins or a log X axis demand them, computes strictly
increasing finite edges and reports `HistogramData.description`.

Density retains the toolkit-neutral convention
`sum(height_i * bin_width_i) == 1` over values included in the selected range,
including unequal logarithmic bins. Counts satisfy
`counts.sum() == in_range`.

Only committed `AppState.mask` determines analytical selection. Table
search/sort and Selected/All scope do not alter histogram input, and excluded
rows are never drawn.

## Async, cross-plot group and stale state

Histogram preparation uses key `histogram` and
`exclusive_group="plots"`. The coordinator gives the shared plots group
application-level supersession semantics across scatter/histogram keys: a newly
accepted plot request cancels unpublished work from the other plot family while
leaving its already committed visible Figure untouched. The F3.A scheduler
itself is unchanged.

HistogramController also keeps a local plot epoch. Same-family latest request
wins, and stale dataset/selection results cannot publish through the existing
F3.A publication gates.

A committed dataset replacement cancels histogram work, resets controls to the
new numeric columns and clears the histogram. Failed/cancelled replacement
preserves the current dataset, controls and valid Figure. A committed
selection/filter change cancels pending histogram work and clears the Figure
with an explicit replot message. Apply and Clear therefore both require a new
histogram built from the current mask.

## PlotPanel lifecycle

Histogram figures use the same PlotPanel as scatter and inherit atomic
replacement, partial constructor rollback, synchronous candidate cleanup,
toolbar lifecycle, pending `draw_idle` neutralization, absence of native Save
and clean teardown. F3.E does not duplicate these fixes in its controller.

## Deferred capabilities

F3.E does not add explicit figure export, custom color/opacity/title, advanced
appearance, annotations, inset, sampling UI, new plot families, statistics,
session persistence or CTk removal.

`S-200K-HISTOGRAM` is synthetic structural evidence only: 200,000 rows with a
numeric distribution, filter column and sparse NaNs. It verifies worker
preparation, committed mask use, edge/count invariants, absence of a retained
DataFrame and cancellation/latest-request behavior. It is not a performance SLA.

`F3A-SIGSEGV-CI = INDETERMINATE` remains a separate incident. Any recurrence
during F3.E CI must be reported rather than hidden by retries.
