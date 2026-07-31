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
same PR.

## Usage

```bash
# compare the current checkout against two releases (quiet machine)
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 time WORKTREE v2.5.0 v2.4.0 --reps 3

# deterministic counts for the current checkout
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 counts

# gate / re-record
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 check
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 update
```

## Continuous integration

This repository gates itself with `.github/workflows/ci-instantiation-gate.yml`: it pins both
the mp-units ref and the clang version, publishes what was measured (ref, sha, library version,
exact compiler build) to the job summary, uploads the counts table as an artifact, and only then
runs the gate. Bumping either pin means re-recording the baselines in the same PR.

The mp-units repository can consume the suite the same way to gate its own PRs - instantiation
counts are deterministic, so they are valid on hosted runners where wall-clock timings are not.
A ready-to-copy job lives in [`ci/mp-units-compile-time-gate.yml`](ci/mp-units-compile-time-gate.yml);
it pins the suite by ref so that adding a workflow here cannot silently change what gates
mp-units. Regressions fail the job with actionable messages naming the workflows; improvements
surface as warning annotations and in the job summary, so threshold-tightening never goes
unnoticed.

## Roadmap

- C++ modules workflows (BMI build cost + `import mp_units;` consumers)
- runtime performance workflows
- scheduled trend tracking across release tags with published tables
