# Varia Studio

Varia Studio is a local-first desktop workspace for inspecting CSV/DAT datasets, exploring
relationships and distributions, and computing descriptive statistics. The canonical desktop
frontend uses PySide6/Qt with pandas, NumPy, and Matplotlib; no web service or account is required.

## Install and launch

Python **3.10 or newer** and a graphical desktop are required. PySide6/Qt is a
normal application dependency and is installed by the standard package install.
The verified CI environments are Python 3.10 and 3.12 on Linux; Windows and
macOS are not certified by the current release process.

On Ubuntu, install virtual-environment support if needed:

```bash
sudo apt install python3-venv
```

From this project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
varia-studio
```

The following launchers are compatibility aliases for the same canonical Qt
frontend:

```bash
varia-studio
csv-analyzer
varia-studio-qt
python -m csv_analyzer
```

All accept an optional dataset path and `--debug`, for example:

```bash
varia-studio examples/scientific_demo.csv
python -m csv_analyzer examples/scientific_demo.csv
```

The installed distribution remains named `csv-analyzer-desktop` and the Python
namespace remains `csv_analyzer` for compatibility.

The previous CustomTkinter implementation is retained temporarily for regression
and compatibility work, but it is no longer the canonical application and is not
installed by default. Install it only when legacy tests or code require it:

```bash
python -m pip install '.[legacy]'
```

The historical `[qt]` extra remains accepted as a no-op compatibility alias;
Qt itself is already a base dependency.

For the exact tested Python 3.12 dependency snapshot, install with
`python -m pip install -c constraints-python312.txt .`. Other Python versions
should use the compatible ranges in `pyproject.toml`.

## Workflow

| Area | What you can do |
| --- | --- |
| **Data** | Open/reload files, configure import options, page through a bounded read-only preview, inspect column types and missing/infinite counts. |
| **Relationships** | Choose X/Y and an optional color column; apply independent absolute-value, log-scale and scientific-format controls; set axis bounds; style markers; add reference elements and an inset; export the displayed figure. |
| **Distributions** | Plot counts or probability density using automatic, FD, Sturges, square-root, or explicit bins; use logarithmic spacing independently of axis scale; restrict the range; export. |
| **Statistics** | Compute numeric descriptions, categorical/date summaries, and pairwise Pearson correlations; choose sample or population conventions; export full-precision CSV. |

**Filters** in the dataset banner apply inclusive numeric bounds to raw values.
Every condition must match. The same selection drives all four areas. Missing
and infinite operands fail a filter condition. Clearing conditions restores all
rows. Relationships can show filter-excluded points in gray.

Plots and statistics update when you press their action button. A changed dataset
resets column choices, filters, plot configuration and results. A changed selection
clears derived results while retaining plot settings. Failed or cancelled imports
leave the current dataset and selection intact. A failed plot keeps the previous
valid figure and reports the error.

Shortcuts: **Ctrl+O** open, **Ctrl+R** reload, **Ctrl+C** copy a selected table row.
The built-in Guide explains the workflow. Matplotlib's toolbar provides pan, zoom,
home and navigation; use **Export figure** to choose PNG, PDF or SVG and DPI.

## Import behavior

- CSV auto-detects comma, semicolon, tab or pipe delimiters and treats the first
  record as a header. Use Import options for an explicit delimiter or no header.
- DAT defaults to whitespace. A first nonblank commented line such as `# mass
  signal` supplies column names without discarding the first data row. Otherwise
  Auto generates names; choose **Header: Yes** for an ordinary un-commented header
  after any leading metadata comments.
  With **Header: No**, comments are ignored and all records are data.
- Double-quoted CSV fields may contain separators and newlines. DAT supports
  single-line double-quoted fields, doubled quotes, literal apostrophes and
  backslashes, and `#` comments outside quotes. Use CSV for multiline fields.
- UTF-8 with or without a BOM is the default. UTF-16, Windows-1252 and Latin-1
  are available explicitly; decoding errors never trigger a silent fallback.
- Decimal commas require a different field delimiter, usually semicolon.
- Every record width is checked. Malformed records are reported rather than
  skipped, padded, or silently turned into an index. Whitespace-only blank lines
  are ignored; quoted empty fields remain missing observations.
- Empty files are rejected. Header-only files can be inspected, with analysis
  actions disabled. Duplicate/blank names are made unique and reported.
- Pandas' standard missing-value markers apply, including empty fields, `NA`,
  `N/A` and `NaN`. Missing and infinite numeric values remain in the source frame.
  Text such as `inf` in a mixed text/numeric column remains text.
- Types are inferred without lossy float downcasting. Mixed columns remain text
  and are excluded from numeric selectors. Boolean columns use categorical
  summaries. Explicit ISO date columns are parsed only when requested; invalid
  dates reject the candidate import. Headers are matched exactly.
- Source row numbers in the preview are one-based **data-record positions**, not
  physical file line numbers. Tables show six significant digits; stored data and
  exported statistics retain computed precision.

## Scientific conventions

**Relationships.** A selected row must have finite X/Y and, when used, finite
color. Log-scaled quantities must be strictly positive after any absolute-value
transformation. Columns always stay aligned by row position. Excluded points
apply the same X/Y transformations; their color-column values are irrelevant.
Captions report selected, usable, invalid and drawn rows. Axis bounds use displayed
coordinates and change the view; filters change the analytical selection.

Appearance controls include labels/title (Matplotlib math text supported), marker,
area, opacity, colormap, grid and optional deterministic sampling. Sampling is
explicit and limits each selected/excluded group separately; statistics and
histograms still use all selected rows. Color normalization uses all usable
selected rows, even when points are sampled. Dense scatter layers are rasterized
in PDF/SVG exports; axes and text remain vector elements.

Annotations include horizontal/vertical lines, a slope line
`y = slope * (x - x0) + y0`, segments, points and arbitrary text. Position and style
are structured fields. They do not alter the data's viewing range. The inset
shares the plot's point selection, marker style and color normalization, with
optional independent scales and a highlighted zoom rectangle.

**Histograms.** Finite selected observations within the requested range contribute
to bins; log axes or logarithmic spacing also require positive values. The last
bin includes its upper endpoint. Density is `count / (in-range N × bin width)` in
the original displayed X units, including for logarithmic bins, and integrates
to one. Automatic rules are capped at 4096 bins; the actual bin count is shown.

**Statistics.** Numeric results include selected rows, finite count, missing,
infinite, mean, standard deviation, variance, min/max, quartiles and median.
Sample variance uses `N−1` (`ddof=1`); population uses `N` (`ddof=0`). The exported
`ddof` column records this choice. Quantiles use linear interpolation. Missing and
infinite values are excluded per column. Undefined results display as an em dash.
Overflow beyond float64 range is reported as infinity; scaled calculations avoid
avoidable intermediate overflow.

Categorical summaries exclude missing values and report distinct count, one most
frequent value, frequency and number of tied modes; parsed dates include earliest
and latest. Correlations use pairwise finite observations with an explicit paired
count; constant columns or fewer than two pairs yield an undefined coefficient.
These are descriptive statistics, not significance tests or fitted models.

## Performance and responsiveness

File validation/parsing, filtering, plot-array preparation and statistics run in
workers. Qt widgets and Matplotlib rendering stay on the GUI thread. There is one
committed dataframe, a small selection mask, and only the per-analysis arrays that
are needed. Preview tables hold at most 200 data rows. A replacement temporarily
needs memory for both the old dataset and the candidate so failure remains atomic.

The supplied 200,000 × 61 CSV was loaded and analyzed without sampling. See
[verification results](docs/VALIDATION.md) for measurements and their limits.
Rendering/export can briefly occupy the GUI thread. Display sampling helps when
millions of points would be expensive. Cancellation is cooperative: an in-progress
pandas parse must finish before its worker releases memory, but its result is
discarded immediately from the application's workflow. Closing during a parse can
leave the Python process alive until that parse completes.

## Development and verification

```bash
python -m pip install -e '.[dev,legacy]'
python -m unittest discover -s tests -v
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m build
```

The normal suite includes desktop integration tests. On Linux without `DISPLAY`,
those two tests are explicitly skipped; other tests use Agg for real figure
rendering. On a Linux system with Xvfb available:

```bash
xvfb-run -a python -m unittest discover -s tests -v
```

Reproduce the large-file check with your separately supplied dataset:

```bash
python tools/benchmark.py /path/to/test.csv --output /tmp/varia-studio-check
```

The benchmark writes timings/environment details, statistics CSVs and rendered
figures. The large private input file is not bundled. Small example datasets are
included for routine verification. `--debug` enables diagnostic logging.

## Project layout

| Path | Responsibility |
| --- | --- |
| `src/csv_analyzer/data.py` | Strict import, schema and quality metadata |
| `src/csv_analyzer/state.py` | Dataset ownership and atomic revision transitions |
| `src/csv_analyzer/analysis.py` | Pure numerical selection/statistics |
| `src/csv_analyzer/plots.py` | Validated plot specifications, arrays and figures |
| `src/csv_analyzer/exports.py` | Atomic output writing |
| `src/csv_analyzer/qt_app/` | Canonical PySide6 shell, controllers, task scheduler and views |
| `src/csv_analyzer/app.py` | Retained non-canonical CustomTkinter application |
| `src/csv_analyzer/ui/` | Retained legacy CustomTkinter views and dialogs |
| `tests/`, `examples/`, `tools/` | Regression/integration tests, small fixtures and benchmark |
| `docs/` | Architecture, migration notes and verification evidence |

Read [architecture](docs/ARCHITECTURE.md), [changes](docs/CHANGES.md) and
[verification/remaining limitations](docs/VALIDATION.md) for implementation details.
