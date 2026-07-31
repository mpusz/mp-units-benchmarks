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
with `-I<repo>/src/{core,systems,utility}/include`. `counts`/`check`/`update` require clang
(they parse `-ftime-trace` output); `time` works with any compiler.

```bash
R="--repo ~/repos/mp-units --cxx clang++-21"

runner/bench.py $R counts                                  # deterministic counts, all workflows
runner/bench.py $R counts --workflows isq/ affine           # substring filters (single workflow)
runner/bench.py $R counts --output results/counts.json      # machine-readable
runner/bench.py $R time WORKTREE v2.5.0 v2.4.0 --reps 3     # interleaved A/B (quiet machine)
runner/bench.py $R check                                    # two-sided gate (exit 1 on regression)
runner/bench.py $R check --report results/report.json       # + machine-readable deltas
runner/bench.py $R update                                   # re-record every entry
runner/bench.py $R update --workflows isq/ affine           # re-record only these
```

`update` without filters is authoritative (drops entries whose workflow is gone) but blesses the
sub-band drift of every other workflow too - that is the one way baseline creep can happen, since
`check` itself always compares against the committed file. `update --workflows` moves only the
matching entries and lists the rest under `not_re_recorded`. Neither form overwrites a reviewed
number with `n/a`/`FAIL`, so re-recording against an older ref cannot erase newer workflows.

Fixed compile flags: `-std=c++23 -O2 -DNDEBUG -DMP_UNITS_API_CONTRACTS=0` plus `-stdlib=libc++`
for clang; add anything else via `--extra-flags`. `--baseline-key` (default `clang21`) selects
the baseline file. `results/` and `.worktrees/` are gitignored scratch space.

## Layout

- `workflows/<category>/<name>.cpp` - benchmark TUs; each is a standalone, idiomatic,
  compile-only-cost `main()` with no test framework. Categories: parity (cross-library
  basics), isq (hierarchies, kind-safe APIs), affine (quantity_point, origins), generic,
  text, umbrella (churn-expected, excluded from the median regression alarm).
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

- Per-workflow growth > 2% -> error with "run update and commit baselines in this PR".
- Median growth across non-umbrella workflows > 2% -> framework-wide regression error
  (should almost never be rebaselined away).
- Improvement > 2% -> `::warning::` annotation + GITHUB_STEP_SUMMARY entry suggesting
  baseline tightening (visible on the run page, never buried in logs).
- `check` iterates the *baseline* keys, so a newly added workflow is ungated until an
  `update` records it; a workflow that stops compiling is reported as `FAIL` by
  `measure_counts` and skipped by the gate rather than failing it.

## CI

- `.github/workflows/ci-self-test.yml` - tests THIS repo, does not gate mp-units. Pins
  `MP_UNITS_REF` and `CLANG_VERSION` (either bump requires an `update` in the same PR), records
  provenance into `GITHUB_STEP_SUMMARY`, writes `counts --output` BEFORE `check` so numbers are
  published even when the check fails, then smoke-tests `time` on one workflow (a shared
  runner's wall time is not comparable to anything - the step only proves the path runs).
- `.github/workflows/ci-tighten-baselines.yml` - opens a PR re-recording the baselines when a
  workflow improves >=2% (the band `check` warns at) or the non-umbrella median >=1%
  (`check --report` JSON drives the decision). Nothing accumulates between runs - `check` always
  compares against the committed file - so the weekly cron is only polling for `MP_UNITS_REF`
  movement, and the job short-circuits before compiling when the checked-out sha equals the
  baseline's `mp_units_sha` (recorded by `update`) for the same compiler. Never auto-PRs regressions, and skips entirely when any regression is present -
  mixed signals need a human. Keep its `MP_UNITS_REF` in step with the self-test's.
- `ci/mp-units-compile-time-gate.yml` - copy target for the mp-units repo, NOT a workflow here.
  This is the gate that actually blocks compile-time regressions, because it runs where the
  offending change is authored. It pins this suite by ref, so a corpus change here cannot
  silently change what gates mp-units.

## mp-units checkouts

The runner materializes detached worktrees per ref under `.worktrees/` (gitignored) from the
`--repo` path (default: a local mp-units clone, e.g. ~/repos/mp-units). The library version is
parsed from `src/CMakeLists.txt` in each checkout and drives both variant selection and the
injected `MP_UNITS_BENCH_VERSION`.
