# F4.A — Qt Import & Shell Parity

F4.A closes basic operational parity for import and shell behavior in the Qt
frontend. It does not promote Qt to the canonical entry point and does not
extend scientific visualization.

## ImportOptions authority

The toolkit-neutral `ImportOptions` dataclass and `load_dataset()` remain
unchanged and authoritative:

```text
delimiter
encoding
header
decimal
comment
date_columns
```

The Qt dialog exposes delimiter, encoding, header row, decimal separator and ISO
date columns. `comment` has no F4.A UI and is preserved from the staged
`ImportOptions` value when Save constructs a new immutable options object.

## Staged import dialog

`ImportOptionsDialog` is a focused parent-owned QDialog. Opening the editor
copies the current coordinator options into its controls. Only Save emits a new
`ImportOptions` and updates `QtCoordinator.import_options`; Cancel or window
close leaves the existing value unchanged.

ISO date columns are one exact name per line. Only lines that are exactly empty
are discarded. Non-empty lines, including leading/trailing or whitespace-only
text, are preserved exactly apart from the newline separator.

Saving options does not reload the current dataset and does not modify AppState.
The shell reports:

```text
Import options saved. Open or reload a file to apply them.
```

## Open and Reload

Open continues to use a Qt `QFileDialog` and calls the existing
`QtCoordinator.load_path()` route.

Reload is enabled only with a committed dataset and calls the same
`load_path(dataset.path)` route. There is no separate reload task key or
scheduler contract. Both Open and Reload therefore retain F3.A load tickets,
replacement/cancellation and atomic commit semantics.

`load_path()` captures the current immutable `ImportOptions` before submitting
the worker. Editing import options while an admitted load is running cannot
retroactively alter that request.

A failed or cancelled reload does not commit state, so the previous dataset,
filters, selection and already-published derived views remain valid. A
successful reload is a normal new dataset commit and triggers the existing F3
invalidation contracts.

## Shell parity

The Qt window title is now simply `Varia Studio`.

Visible controls are:

```text
Open…
Reload
Import options…
Cancel
Guide
Quit
```

Shortcuts:

```text
Ctrl+O -> Open
Ctrl+R -> Reload
```

The existing F3.B table Copy action remains unchanged.

The compact dataset summary uses committed Dataset/AppState metadata and shows
filename, row count, column count, missing count, infinite count and analytical
selected count. Filter commits refresh the selected count without rescanning the
DataFrame.

Import notices are accessible in the Data tab through an `Import notices…`
button when `Dataset.notices` is non-empty. No global notification framework is
introduced.

The Guide is intentionally static and small: Open & inspect, Filters,
Relationships / Distributions, Statistics, Export and the two shortcuts.

## Lifecycle

The import dialog is parent-owned. Dataset commits do not close it because its
staged options are global rather than dataset-specific. Application close rejects
an open import dialog before Qt teardown, preventing callbacks to destroyed
objects.

## Import integration coverage

F4.A integration tests exercise Qt-driven loads for representative:

- CSV automatic delimiter;
- semicolon, tab and pipe delimiters;
- whitespace DAT;
- header Auto / Yes / No;
- UTF-8-SIG, UTF-8, CP1252, Latin-1 and UTF-16;
- decimal dot and comma;
- explicit ISO date columns.

These tests reuse `load_dataset()`; they do not duplicate its parser.

## Deferred

F4.A does not change entry points, base dependencies, CTk, advanced plot
appearance, sampling, annotations, new plot families, dataset export or other
F4 increments.

The repository CI does not contain the private/project-only
`test_sample_500.csv`; CI therefore uses representative temporary integration
fixtures. The 500-row sample may be used for manual/physical functional flow,
but it is not performance evidence.

`F3A-SIGSEGV-CI = INDETERMINATE` remains unchanged.


## Final QA and integration record

Initial F4.A candidate:

```text
8421a86ef0a293ba7192a3570ce1232087bbe0c2
```

The initial independent QA result was:

```text
QA06-F4A-01 = NOT VERIFIED
F4A-QA-01 = MEDIUM / OPEN
```

The confirmed defect was:

```text
Statistics active
-> Open succeeds
-> new dataset is committed
-> Statistics incorrectly remains active
```

F4.A-R1 made the Open navigation intent explicit and focal. The final contract is:

```text
Open success
-> commit_dataset() succeeds
-> Data becomes active
```

The navigation occurs only after a successful dataset commit. The following
cases preserve the current tab:

```text
Open cancel       -> current tab
Open failure      -> current tab
Open cancellation -> current tab
superseded Open   -> no late navigation
Reload success    -> current tab
generic load_path -> current tab
```

Only Open sets:

```text
activate_data_on_success=True
```

Reload and generic/programmatic `load_path()` calls preserve the default
`False`.

Final R1 candidate:

```text
2ab6fc327ee315e85af14fd5eff00e3874c68009
tree 12a55321d05cdf37600185f0318bb69133598beb
```

Final QA state:

```text
QA06-F4A-01-R1 = VERIFIED
F4A-QA-01 = CLOSED
PHYSICAL_OPEN_DATA_TAB_R1 = PASS
SOURCE_REPO_UNTOUCHED = PASS
```

Final R1 evidence:

```text
F4.A Python 3.10  21/21 PASS
F4.A Python 3.12  21/21 PASS
independent R1     10/10 PASS
Ruff                      PASS
PHYSICAL X11              PASS

Workflow: F4.A Qt import shell
Run: 42264654878
Run number: 4
Attempt: 1
Result: SUCCESS
Artifact: f4-a-evidence
Artifact ID: 13526343081
Digest: sha256:3032b59bf4ed702141332f9d781fe8170f54fa41755ab75c6faa6498305743e4
```

The final F4.A Qt capability set includes:

```text
Import options...
Open...
Reload
Ctrl+O
Ctrl+R
dataset summary
selected count
import notices
Guide
```

Import Options covers delimiter, encoding, header, decimal and explicit ISO date
columns. `comment` remains preserved by the existing `ImportOptions` contract
without a dedicated F4.A UI.

F4.A-R1 was integrated into `main` by fast-forward from production
`36b7e2e0b85db8973614b36b882147ebdecfab61` to the final R1 candidate
`2ab6fc327ee315e85af14fd5eff00e3874c68009`, preserving the candidate tree
unchanged.

`F3A-SIGSEGV-CI = INDETERMINATE` remains unchanged. It did not reappear during
F4.A-R1 and is not considered resolved.
