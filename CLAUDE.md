# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The official compile-time performance suite for mp-units (see README.md for user-facing docs).

## Working rules

- NEVER `git push` or create PRs/repos on GitHub - Mateusz does that himself.
- Present substantial changes as an uncommitted diff for review; commit only on an explicit
  "commit"; one logical commit per reviewable unit.
- Timing measurements: always interleave arms (rep-major, arm-minor), take best-of-K,
  single-threaded, quiet machine; never compare a fresh number against one measured earlier.
- Instantiation counts (clang `-ftime-trace -ftime-trace-granularity=0`, count
  InstantiateClass + InstantiateFunction events) are bit-deterministic for a pinned compiler
  - they are the gated metric; wall time never gates.

## Commands

There is no build system: `runner/bench.py` invokes the compiler directly on each workflow TU
with `-I<repo>/src/{core,systems,utility}/include`. `counts`/`check`/`update` require clang (they
parse `-ftime-trace` output and exit with a clear message on any other compiler); `time`, `report`
and `summary` work with any compiler - GCC builds the whole corpus, it just cannot produce counts.

```bash
R="--repo ~/repos/mp-units --cxx clang++-21"

runner/bench.py $R counts                                  # deterministic counts, all workflows
runner/bench.py $R counts --workflows isq/ affine           # substring filters (single workflow)
runner/bench.py $R counts --output results/counts.json      # machine-readable
runner/bench.py $R counts WORKTREE v2.5.0                   # refs side by side + delta column
runner/bench.py $R time WORKTREE v2.5.0 v2.4.0 --reps 3     # interleaved A/B (quiet machine)
runner/bench.py $R check                                    # two-sided gate (exit 1 on regression)
runner/bench.py $R check --report results/report.json       # + machine-readable deltas
runner/bench.py $R update                                   # re-record every entry
runner/bench.py $R update --workflows isq/ affine           # re-record only these
runner/bench.py $R report WORKTREE v2.5.0 --output r.json   # counts+time+memory, markdown + JSON
runner/bench.py summary r1.json r2.json                     # merge reports (also -> STEP_SUMMARY)
runner/bench.py --std c++26 --cxx g++-16 $R report          # any compiler/standard
runner/bench.py $R --std c++26 --import-std counts          # `import std;` instead of std headers
runner/bench.py $R --std c++26 --modules --import-std report   # consume mp-units as C++20 modules
```

`report`/`summary` put every modules measurement in its OWN `## C++20 modules` section - interface
builds (time, memory, size on disk, instantiations, each with a total row) first, then the consumers
- because a `bmi/*` row interleaved with workflows is noise to every configuration that has no
modules, and the corpus grows a column per compiler. Column headers there drop the tokens all of
them share. A configuration with extra tokens (`-importstd`, `-modules-importstd`) is rendered with
a percentage against the PLAIN build of the same compiler - headers, no `import std` - never against
an intermediate configuration, so a modules delta says what modules are worth against how the library
is consumed today rather than against one step along the way. No plain build in the run means no
percentage, not an incremental one. Those
deltas are consumer cost only - the interface build is paid once per configuration, and the modules
section says so above the table. Everything else renders one table per metric PER COMPILER FAMILY (clang, gcc, other - the supported
set grows, so a single wide table stops being readable). Refs are ordered by measured library version,
oldest first; with exactly two refs each cell becomes `old -> new (change)` so the delta is read rather
than computed. `n/a` means the workflow's `REQUIRES` floor excludes that ref, `FAIL` means it applies
and did not compile - never collapse those two, a FAIL is a finding.

Consumption is a configuration axis, driven by the corpus's two-macro preamble (the same one
mp-units' examples use): `--import-std` defines `MP_UNITS_IMPORT_STD`, `--modules` defines
`MP_UNITS_MODULES`. Both need a BMI pre-step, which `build_modules()` runs once per ref before any workflow, building
only the module units THAT ref has (`mp_units.utility` arrived in 2.6, so a comparison against
v2.5.0 must not demand it): the standard library's module, then mp_units.core/systems/utility and the umbrella. Its
cost is reported as `bmi/*` rows (time, peak memory, size on disk, and instantiation counts from a
traced build) - under modules the consumer instantiates almost nothing because the work happened in
the BMI, so omitting those rows would make modules look free. The std module is built with a MINIMAL
flag set - no `-O2`, no `MP_UNITS_*` macros - because it is not part of mp-units and because GCC 16
ICEs in consumers otherwise. GCC finds BMIs via `gcm.cache` relative to the working directory, so
those compiles run with `cwd` set to the BMI directory.

Metrics: counts are GATED (clang only, bit-deterministic, and `-std`-dependent - so the gate must
keep the standard its baselines were recorded with); peak RSS from the child's `rusage` varies <0.1%
between runs on both clang and GCC, so it is trustworthy everywhere but not gated; wall time is
quiet-machine-only. `-ftime-trace` inflates time ~11-15% and memory ~12-17%, so counts come from a
traced compile and time/memory from an untraced one - NEVER report both from one compile.

`update` without filters is authoritative (drops entries whose workflow is gone) but blesses the
sub-band drift of every other workflow too - that is the one way baseline creep can happen, since
`check` itself always compares against the committed file. `update --workflows` moves only the
matching entries and lists the rest under `not_re_recorded`. Neither form overwrites a reviewed
number with `n/a`/`FAIL`, so re-recording against an older ref cannot erase newer workflows.

Fixed compile flags: `-O2 -DNDEBUG -DMP_UNITS_API_CONTRACTS=0 -DMP_UNITS_API_THROWING_CONSTRAINTS=0`
plus `-std=<--std>` and `-stdlib=libc++` for clang; add anything else via `--extra-flags`. The
throwing-constraints pin matters: that path targets an unadopted constexpr-exceptions extension (the
standard C++26 feature is NOT SFINAE-friendly and is a different thing), yet mp-units auto-enables it
on `__cpp_constexpr_exceptions`, which GCC 16 defines at `-std=c++26` - leaving it unpinned would
have one arm measuring a different library configuration than the rest.

Baselines are keyed by the WHOLE configuration, not the compiler - compiler, `--std`, `--stdlib`,
`--config-label`, and a digest of `--extra-flags`:

```text
clang21-cxx23-libcxx     clang21-cxx23-libstdcxx     gcc15-cxx26
clang21-cxx23-libcxx-fmtlib-d2da0f   (--config-label fmtlib --extra-flags=-DMP_UNITS_API_STD_FORMAT=0)
```

`runner/bench.py --cxx <c> [...] key` prints the file a configuration resolves to - use it instead of
hardcoding names (the CI guard step does). `check`/`update` derive the key (override with
`--baseline-key`), and refuse to compare against numbers recorded with a different
`cxx`/`std`/`stdlib`/`config_label`/`extra_flags`, so each configuration keeps its own reference file
and a formatting-backend or stdlib swap can never be silently compared against another one. Pass
extra flags attached (`--extra-flags=-DFOO=1`): argparse reads a detached `-D...` as an option. `results/` and `.worktrees/` are gitignored scratch space.

## Layout

- `workflows/<category>/<name>.cpp` - benchmark TUs; each is a standalone, idiomatic,
  compile-only-cost `main()` with no test framework. Categories: parity (cross-library
  basics), isq (hierarchies, kind-safe APIs), affine (quantity_point, origins), generic,
  text, scaling, umbrella (churn-expected, excluded from the median regression alarm).
- `scaling/` is a SERIES, not a set of independent workflows: `<shape>_<steps>.cpp` at 16/64/256 over
  `scaling_workload.h`. Read sizes against each other - the difference divided by the difference in
  steps is the marginal cost of user code, which is the only way to separate it from the constant
  cost of inclusion. `narrow` keeps five quantity types warm (3.0 instantiations/step), `broad` uses
  a distinct scaled unit per step (53.3/step); never "fix" broad to reuse units, that erases the
  axis it exists to measure.
- Only `*.cpp` files are workflows, so a category may carry a shared header. `text/` does:
  `output_workload.h` holds the quantity computations that output_printf / output_ostream /
  output_format / output_println all print. Those four differ ONLY in the output facility -
  that is what makes their counts comparable, so never add a quantity operation to one of
  them; change the shared header (which changes all four together) instead.
- Workflow conventions: `// REQUIRES: mp-units >= X.Y` floor; `<name>@X.Y.cpp` variants for
  older API spellings (single branch, NO per-version branches); runner injects
  `-DMP_UNITS_BENCH_VERSION=<major*100+minor>` for compat shims. `select_workflows()` resolves
  base-vs-variant per detected library version and yields `None` (reported `n/a`) below a
  workflow's floor - that is why comparing refs never means comparing different sources.
- `runner/bench.py` - subcommands: time (interleaved A/B across git refs; WORKTREE = the
  checkout as-is), counts, check (two-sided gate), update (re-record baselines).
- `baselines/instantiations-<key>.json` - reviewed baseline file, keyed by pinned compiler.

## Gate semantics (bench.py check)

All bands below are percents and default to 2, overridable per invocation (see CI - strict here,
loose in mp-units).

- Per-workflow growth > `--slack` -> error with "run update and commit baselines in this PR".
- Median growth across non-umbrella workflows > `--median-alarm` -> framework-wide regression
  error (should almost never be rebaselined away).
- Growth > `--advisory-slack` but within `--slack` -> `::warning::` only, never fatal. This is how
  a loose gate still reports borderline growth (and names the repo that will go red on it).
- Improvement > `--tighten-notice` -> `::warning::` annotation + GITHUB_STEP_SUMMARY entry
  suggesting baseline tightening (visible on the run page, never buried in logs).
- Every `check` prints (and posts to `GITHUB_STEP_SUMMARY`) a table of measured value vs baseline,
  delta, the limit and the HEADROOM left before that limit - the distance to the bands has to be
  visible by observation, not inferred from a pass/fail line.
- `check` iterates the *baseline* keys, so a newly added workflow is ungated until an
  `update` records it; a workflow that stops compiling is reported as `FAIL` by
  `measure_counts` and skipped by the gate rather than failing it.

## CI

- `.github/workflows/ci-compile-cost.yml` - three job kinds. `gate`: clang `-std=c++23` (matches
  the baselines), counts, TIGHT bands (`SLACK: 1`, `MEDIAN_ALARM: 0.5`), growth fails the build, an
  improvement past `TIGHTEN_NOTICE` opens the re-record PR (never from a `pull_request` event, never
  when a regression is present), `check` runs with `continue-on-error` so reactions happen before a
  final step fails the job, and a guard skips measuring ONLY on a `schedule` when the baseline's
  `mp_units_sha` already describes the checked-out tree - a push or dispatch always measures,
  because the table is the point of the run, and a skipped arm writes a summary saying why it has
  no table (an empty job summary reads as a bug). `measure`: one runner per compiler - clang++-17/18/20/21
  and g++-14/15, plus g++-16 as `experimental: true` -> `continue-on-error` - all at `-std=c++26`,
  each uploading a `report --output` artifact. That set is mp-units' supported compilers that can do
  c++26; clang 16 (spells it c++2c), gcc 12/13 (no c++26, and gcc 13 has no `<print>`), clang 19
  (unsupported by mp-units) and clang 22 (currently fails to compile the library) are out. `summary`: `needs: measure`, `if: always()`, downloads the artifacts
  and posts `bench.py summary` into `GITHUB_STEP_SUMMARY`.
- Dispatch inputs: `ref` and optional `compare_ref` (measured by every arm alongside the first).
- Bands are CLI flags (`--slack`, `--median-alarm`, `--tighten-notice`, `--advisory-slack`, all
  percents), NOT constants: the same baseline file is read strictly here and loosely there.

## mp-units checkouts

The runner materializes detached worktrees per ref under `.worktrees/` (gitignored) from the
`--repo` path (default: a local mp-units clone, e.g. ~/repos/mp-units). The library version is
parsed from `src/CMakeLists.txt` in each checkout and drives both variant selection and the
injected `MP_UNITS_BENCH_VERSION`.
