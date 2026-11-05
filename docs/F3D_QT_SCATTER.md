# F3.D — Qt Scatter Core

F3.D establishes the first scientific Qt visualization while preserving the
existing toolkit-neutral scatter engine in `plots.py`.

## Final integration record

F3.D was integrated into `main` by fast-forward from final R2 candidate
`d61f6f52a72d7f31ecd9799f9c158822569d7a7c` (tree `f9ad0f517bc7d96e0a840f4e7f01476b56519f67`).

Final verification state:

- `QA06-F3D-01-R2 = VERIFIED`.
- `F3D-QA-01 = CLOSED` in R1.
- `F3D-QA-02 = CLOSED` in R2.
- Physical X11 UI gate: PASS.
- Physical rapid-navigation / draw_idle R2 gate: PASS.
- `F3A-SIGSEGV-CI = INDETERMINATE`; it did not reappear during final F3.D
  validation and is not considered resolved.

## Ownership and boundary

`ScatterView` owns only controls and staged view configuration.
`ScatterController` owns scatter view-state, request generation and stale
invalidation. `PlotPanel` owns exactly one Matplotlib Figure,
`FigureCanvasQTAgg` and `NavigationToolbar2QT` tuple.

The pipeline is:

```text
AppState snapshot
  -> ScatterSpec
  -> QtTaskRunner worker
  -> prepare_scatter(frame, spec, mask, cancel)
  -> ScatterData
  -> GUI thread
  -> render_scatter(data, spec)
  -> FigureCanvasQTAgg.draw()
```

Workers never receive AppState or widgets and never create Matplotlib Figures.
Rendering, canvas construction and drawing are asserted to occur on the Qt GUI
thread.

## Exposed Scatter Core

The Relationships tab exposes independent numeric X/Y selectors; linear/log
axes; absolute transforms; scientific tick labels; optional lower/upper bounds;
optional numeric color with linear/log scale, absolute transform and scientific
labels; and `Show filtered-out rows` defaulting to true.

F3.D uses the existing defaults: viridis, marker `o`, size 12, opacity 0.8,
excluded opacity 0.12 and no sampling limit. Advanced appearance, annotations,
inset, custom labels/title/cmap/marker/opacity/size and sampling UI remain
deferred.

## Races and state

Scatter preparation uses key `scatter` and `exclusive_group="plots"`.
F3.A application epoch, generation, dataset revision, selection revision and
lifecycle gates remain authoritative. ScatterController adds a local plot epoch
so replaced requests cannot install a superseded view.

A committed dataset change cancels pending scatter work, resets controls to the
new numeric columns and clears the figure. A committed selection/filter change
cancels pending work and clears the figure with an explicit stale-selection
message. Failed/cancelled dataset replacement does not notify a committed state
change and therefore preserves the current valid figure.

Plotting is derived and never mutates AppState revisions, filters or mask.

## Figure lifecycle

A candidate Figure is rendered first. PlotPanel then constructs a candidate
QtAgg canvas and toolbar and calls `draw()` before touching the current valid
figure. Render or draw failure reports an error while preserving the last valid
figure.

On successful replacement the previous toolbar/canvas callbacks and bidirectional
references are detached, the old Figure is cleared, Qt widgets are removed and
scheduled for deletion, and only the new tuple remains active. The toolbar
retains Home/Back/Forward/Pan/Zoom while the Matplotlib Save action is removed.
No pyplot API or global close/backend mutation is used.

## Limits

F3.D does not migrate histograms, statistics, explicit export, advanced
annotations/inset/appearance, sampling UI, regression fitting, session
persistence or new plot families. CTk continues to coexist.

`S-200K-SCATTER` is structural evidence only. It prepares 200,000-row numeric
arrays in a worker, verifies mask/excluded structure and replacement/cancellation
without repeatedly drawing 200k points. It is not an FPS or rendering SLA.

`F3A-SIGSEGV-CI = INDETERMINATE` remains a separate incident. If it reappears
in F3.D CI, the run is reported rather than hidden with retries.
