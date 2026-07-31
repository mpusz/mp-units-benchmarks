# mp-units compile-time benchmarks

The official compile-time performance suite for [mp-units](https://github.com/mpusz/mp-units).
It measures what generic cross-library comparisons cannot: the cost of the features that only
mp-units provides — ISQ quantity hierarchies, kind-safe strongly-typed interfaces, the affine
space, and user-defined quantity specifications — next to the basic workloads every units
library shares.

## Workflow corpus

Each workflow is a small idiomatic translation unit in `workflows/<category>/<name>.cpp`:

| category   | what it represents                                                        |
|------------|---------------------------------------------------------------------------|
| `parity/`  | basics common to all units libraries (arithmetic, conversions)            |
| `isq/`     | ISQ hierarchies: user-defined `QUANTITY_SPEC` trees with equations, kind-safe `QuantityOf` interfaces, cross-branch conversions |
| `affine/`  | `quantity_point`: offset units, user-defined absolute/relative origins    |
| `generic/` | templates over references and representation types                        |
| `text/`    | quantity text output: the same workload printed through `printf`, `operator<<`, `std::format` and `std::println`, plus the format-spec grammar in depth |
| `umbrella/`| bare umbrella-header inclusion cost — **churn-expected**: these grow when systems legitimately grow and are excluded from the framework-regression alarm |

Conventions:

- `// REQUIRES: mp-units >= X.Y` — the workflow is reported as `n/a` for older refs.
- `<name>@X.Y.cpp` — a variant in an older API spelling, picked automatically for refs of
  that era; keeps semantically-equivalent workloads reviewable side by side (single branch,
  no per-version branches to merge).
- `-DMP_UNITS_BENCH_VERSION=<major*100+minor>` is injected so tiny renames can be absorbed
  by a compat shim inside a workflow instead of a full variant.
- A category may carry a shared header (only `*.cpp` files are workflows). `text/` uses one:
  `output_workload.h` holds the quantity computations that `output_printf`, `output_ostream`,
  `output_format` and `output_println` all print, so the differences between those four
  measure the output facility and nothing else. Adding a quantity operation to one of them
  breaks that comparison - change the shared header instead.

## Metrics and methodology

Two complementary metrics:

1. **Wall-clock time** (`bench.py time`) — the user-visible truth, but machine-sensitive.
   Refs are compiled interleaved (rep-major, arm-minor) with best-of-K per workflow, so
   load drift affects all arms equally; totals only sum workflows comparable across all
   refs. Meaningful on a quiet machine; on shared CI only *relative* A/B of two refs in the
   same job is trustworthy.
2. **Template-instantiation counts** (`bench.py counts`) — the number of `InstantiateClass`
   + `InstantiateFunction` events from clang's `-ftime-trace` (granularity 0). Bit-stable
   for a pinned compiler, machine-independent, and it measures exactly what makes C++
   headers slow. This is the gated metric.

## The two-sided gate (`bench.py check`)

Baselines live in `baselines/instantiations-<key>.json` and are a **reviewed file**, not a
hidden threshold:

- a workflow growing beyond the slack band (2%) fails with instructions to run
  `bench.py update` — the baseline bump then appears in the same PR, so growth is a
  deliberate, reviewed decision, never a wall;
- the **median** growing across non-umbrella workflows signals a framework-wide regression
  and should almost never be rebaselined away;
- improvements beyond the band emit a `::warning::` annotation and a job-summary entry
  ("baselines can be tightened") — visible on the workflow run page, not buried in logs.

Baselines are recorded per compiler key; bumping the pinned CI clang re-records them in the
same PR. Each record also stores the sha and `git describe` of the mp-units tree it was measured
from — `project(... VERSION)` alone cannot tell a tag from any dev tree of the same era, and that
sha is what lets CI skip re-measuring a tree it already describes.

## Usage

```bash
# compare the current checkout against two releases (quiet machine)
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 time WORKTREE v2.5.0 v2.4.0 --reps 3

# deterministic counts for the current checkout
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 counts

# gate / re-record
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 check
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 update              # every entry
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 update --workflows isq/   # only these
```

A full `update` is authoritative: every entry is rewritten from the checkout and entries whose
workflow no longer exists are dropped — convenient after a major refactoring, but it also blesses
whatever sub-band drift the *other* workflows happen to have. `update --workflows <filters>` moves
only the matching entries, leaves the rest at their reviewed numbers, and records those in a
`not_re_recorded` field so the file says which entries predate its metadata. Neither form will
overwrite a reviewed number with `n/a`, so re-recording against an older ref cannot erase the
baselines of workflows that ref cannot compile.

## Continuous integration

Two workflows, one measurement each, differing in how strict they are - because the same reviewed
baselines are read by two audiences.

`.github/workflows/ci-instantiations.yml` runs here on every commit and pull request, weekly, and
on demand. It measures mp-units `master` (or any ref given to `workflow_dispatch`) against the
baselines with **tight** bands (1% per workflow, 0.5% median) and reacts to that one result in both
directions: growth fails the build, so borderline growth gets investigated here rather than in
mp-units, and an improvement past the tighten band opens a PR re-recording the baselines - stale
baselines are not harmless, since a later regression of the same size would land inside the band.
Every run publishes what it measured (ref, `git describe`, library version, exact compiler build,
bands) to the job summary and uploads the counts table as an artifact. Nothing accumulates between
runs, since `check` always compares against the committed file: the schedule only polls for
movement of the ref, and the job exits before compiling when the checked-out sha is the one the
baselines already record. Dispatching with a second ref turns the run into a side-by-side
comparison of two refs instead of a gate.

mp-units carries the other half itself, in its own `.github/workflows/ci-compile-time.yml`, running
on its pushes as well as its pull requests - instantiation counts are deterministic, so they are
valid on hosted runners where wall-clock timings are not. Its bands are deliberately **loose** (3%
per workflow, 2% median) with an advisory band at 1%: a minor framework extension that grows a
workflow by a couple of percent is annotated but does not block library work, and goes red in this
repo instead. It pins this suite by ref, so adding a workflow here cannot silently change what gates
mp-units.

Two bands over one baseline file are what make the split work: +2% growth annotates in mp-units and
fails here, so the investigation happens where a red build blocks nobody. Because this repo tracks
mp-units `master`, that failure arrives once the change has merged; the advisory annotations in the
mp-units job are what give the same warning before merge.

## Roadmap

- C++ modules workflows (BMI build cost + `import mp_units;` consumers)
- runtime performance workflows
- scheduled trend tracking across release tags with published tables
