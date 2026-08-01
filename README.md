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

Three metrics, with very different trust levels:

1. **Template-instantiation counts** (`bench.py counts`) — `InstantiateClass` + `InstantiateFunction`
   events from clang's `-ftime-trace` (granularity 0). Bit-stable for a pinned compiler and
   standard, machine-independent, and it measures exactly what makes C++ headers slow. This is the
   **gated** metric. It needs clang, and the count depends on `-std`, so the gate always runs with
   the standard the baselines were recorded with.
2. **Peak compiler memory** (`bench.py time`, `report`) — peak RSS of the compiler process, taken
   from the child's own `rusage`. Measured spread between runs is <0.1% on both clang and GCC, which
   makes it almost as trustworthy as counts, works with **any** compiler, and maps directly onto
   what users feel: how many parallel compiles fit in RAM.
3. **Wall-clock time** (`bench.py time`, `report`) — the user-visible truth, but machine-sensitive.
   Refs are compiled interleaved (rep-major, arm-minor) with best-of-K per workflow, so load drift
   hits all arms equally. Meaningful on a quiet machine; on CI, only compare within one arm, never
   between arms measured on different runners.

Counts and memory must never come from the same compile: `-ftime-trace` inflates wall time by
11–15% and peak memory by 12–17%, so the runner takes counts from a traced compile and time/memory
from an untraced one.

Only counts require clang. GCC (and anything else) can produce time and memory, which is what makes
cross-compiler comparison — and comparing a template-based implementation against a reflection-based
one, where instantiation counts stop being a fair measure — possible.

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

Baselines are recorded per **configuration**, not per compiler. The file name carries the compiler,
the language standard, the standard library and any label you give a configuration —
`instantiations-clang21-cxx23-libcxx.json`, `instantiations-gcc15-cxx26.json`,
`instantiations-clang21-cxx23-libcxx-fmtlib-d2da0f.json` — because the formatting backend, the
standard library and any other library option change what is being measured just as much as the
compiler does. The runner derives that key itself (`bench.py … key` prints it), and `check` refuses to
compare a run against numbers recorded with a different configuration. So any number of
configurations can be gated side by side, and bumping the pinned CI compiler or standard re-records
its own file in the same PR without touching the others. Each record also stores the sha and `git describe` of the mp-units tree it was measured
from — `project(... VERSION)` alone cannot tell a tag from any dev tree of the same era, and that
sha is what lets CI skip re-measuring a tree it already describes.

## Usage

```bash
# compare the current checkout against two releases (quiet machine)
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 time WORKTREE v2.5.0 v2.4.0 --reps 3

# deterministic counts for the current checkout
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 counts

# every metric a compiler can produce, as a markdown report + JSON
runner/bench.py --repo ~/repos/mp-units --cxx clang++-21 report WORKTREE v2.5.0 --output results/clang.json
runner/bench.py --repo ~/repos/mp-units --cxx g++-15 --std c++26 report --output results/gcc.json
runner/bench.py summary results/clang.json results/gcc.json   # one table per metric, all compilers

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

`.github/workflows/ci-compile-cost.yml` runs three kinds of job:

- **gate** — clang with `-std=c++23` (what the baselines were recorded with), instantiation counts
  against those baselines with **tight** bands (1% per workflow, 0.5% median). Growth fails the
  build, so borderline growth is investigated here rather than in mp-units; an improvement past the
  tighten band opens a PR re-recording the baselines, because stale baselines silently desensitize
  the gate. It exits before compiling when the checked-out sha is the one the baselines record.
- **measure** — one runner per compiler: `clang++-17/18/20/21` and `g++-14/15`, plus `g++-16` as a
  never-fatal experimental arm. That set is exactly mp-units' supported compilers that can do
  `-std=c++26`, so every column shares one standard and stays comparable. Arms cannot interfere, and
  a compiler that cannot build the corpus costs only its own arm.
- **summary** — merges those artifacts into one markdown report in the job summary: a table per
  metric, a column per (ref, compiler), and a provenance block naming each compiler build, standard,
  CPU and library tree. Counts and memory are comparable across all columns; time is not, and the
  report says so.

`workflow_dispatch` takes a `ref` (any tag, sha or `origin/<branch>`) and an optional `compare_ref`,
which every arm then measures alongside the first.

mp-units carries only the gate, in its own `.github/workflows/ci-compile-time.yml`, running on its
pushes and pull requests and behaving identically when triggered by hand — counts are what belongs in
every build, while wall time and peak memory across a fleet of compilers are asked for occasionally
and answered *here*, by dispatching the workflow above against any mp-units ref (a feature branch
included). Its bands are deliberately **loose** (3% per workflow,
2% median) with an advisory band at 1%: a minor framework extension that grows a workflow by a couple
of percent is annotated but does not block library work, and goes red in this repo instead. It pins
this suite by an exact commit, so a corpus change here cannot silently change what gates mp-units,
and a maintenance branch can keep pointing at the last suite commit that supports its library
version.

## Roadmap

- C++ modules workflows (BMI build cost + `import mp_units;` consumers)
- runtime performance workflows
- scheduled trend tracking across release tags with published tables
