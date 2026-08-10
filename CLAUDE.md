# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The official compile-time performance suite for mp-units (see README.md for user-facing docs).

## Working rules

- NEVER `git push` or create PRs/repos on GitHub - Mateusz does that himself.
- Present substantial changes as an uncommitted diff for review; commit only on an explicit
  "commit"; one logical commit per reviewable unit.
- NEVER write to `/mnt/d/Claude` (the shared drive) without asking first. It is a deliberate
  inter-repo/inter-machine/inter-OS exchange point, not scratch space - temporary files go to the
  session scratchpad. The one standing exception is this repo's `memory/` symlink, which lives
  there by design.
- Timing measurements: always interleave arms (rep-major, arm-minor), take best-of-K,
  single-threaded, quiet machine; never compare a fresh number against one measured earlier.
- Instantiation counts (clang `-ftime-trace -ftime-trace-granularity=0`, count
  InstantiateClass + InstantiateFunction events) are bit-deterministic for a pinned compiler
  - they are the gated metric; wall time never gates.

## Commands

There is no build system: `runner/bench.py` invokes the compiler directly on each workflow TU
with `-I<repo>/src/{core,systems,utility}/include`. `counts`/`check`/`update` require clang (they
parse `-ftime-trace` and `-print-stats` output and exit with a clear message on any other compiler);
`time`, `report`
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
runner/bench.py $R attribute v2.5.0 master --workflows isq/kind_safe_interfaces   # why it moved
runner/bench.py $R attribute --workflows scaling/broad_016 scaling/broad_256      # slope, by entity
runner/bench.py --std c++26 --cxx g++-16 $R report          # any compiler/standard
runner/bench.py $R --std c++26 --import-std counts          # `import std;` instead of std headers
runner/bench.py $R --std c++26 --modules --import-std report   # consume mp-units as C++20 modules
```

Every report opens with a `## What changed` section: at most a handful of ranked, plain-language
findings (compile failures first, then the corpus-wide movement, then the declaration count moving
by something the instantiation count does not explain - emitted ONLY when the two disagree, since
that is the whole reason the metric exists - then constant-vs-marginal cost
diverging, then a wall-clock noise warning derived from the `bmi/std` control row, then what modules
buy, then how much of an improvement is really the compiler, then how many `n/a` cells exist and
why), followed by a `How to read this` block that defines instantiations, declarations, constant vs
marginal cost,
and which metrics are trustworthy - because these summaries get shared with people who do not know
the library. All the tables live in a collapsed `<details>` beneath. NEVER put a number in a finding
without saying what follows from it. A finding that names a tradeoff must compute where it flips: an
intercept that improved while the slope worsened is reported as the CROSSOVER (how many distinct unit
types a file needs before the change stops paying, against the largest workflow measured), because
"a file using many units loses" was false when the crossover was ~4500 and the corpus tops out at 256.

Under `What changed` comes ONE per-configuration summary table, and which one depends on what the run
measured, never on what was passed: a run measuring two refs gets the RANGE (`v2.5.0 -> master` inside
each arm, both sides from one interleaved session, so even wall time is a real A/B), and a run
measuring one ref gets the previous CI run as a control (`--previous`, matched by configuration key).
A range run must never be summarized against the previous run - that compares master with master,
prints "nothing moved", and contradicts every cell of the report below it. Corpus totals in either
table are summed over the entries BOTH sides measured; summing each side's own set turned a -19.0%
range into -6.7% by counting workflows that do not exist on the older ref. Module-interface totals are
deliberately NOT intersected: a module unit the newer ref has is a real cost of building it. Its
columns are instantiations, DECLARATIONS, the broad slope and wall time (`SUMMARY_METRICS`): a
declaration-only change moves the second and nothing else, and this is the one table every reader
sees, so leaving it out would summarize such a change as "nothing moved" above a report of cells
saying otherwise.

Every emitted markdown paragraph is ONE line - in `bench.py`, in the workflow files' `echo` blocks
into `$GITHUB_STEP_SUMMARY`, and in anything else GitHub renders (issue bodies, PR descriptions,
comments). GitHub turns a newline inside a paragraph into a line break, so source-wrapped prose
reaches the reader as a ragged column. Wrap in the SOURCE with Python implicit string concatenation
(adjacent literals, one per source line) and keep the emitted line unbroken. This file and the other
checked-in `*.md` stay hard-wrapped; only generated output follows the rule.

`report`/`summary` render a `## Price list` section per configuration right under `What changed`
(the rows travel in the report JSON per ref, so `summary` merges them across arms), and put every
modules measurement in its OWN `## C++20 modules` section - interface
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

`attribute` answers WHICH, where `counts` answers how many: it groups instantiation events by the
entity instantiated (template arguments collapsed) and diffs exactly two measurements - two refs of
one workflow ("what did this library change cost"), or two workflows of one ref ("what does the
bigger one instantiate", which is how a scaling slope gets attributed). Pairs are ordered by library
version, so a delta always reads old -> new. This is the tool that turned "slope +9.8%" into
`type_list_merge_many_sorted_impl +5.5 per step`; reach for it before guessing at source.

FOUR metrics are gated, all bit-deterministic and all from ONE traced compile: template
instantiations (frontend work), constant evaluations (`EvaluateAsConstantExpr` - what a constexpr
implementation trades instantiations for), `function_decls` (declarations, see below) and `code_bytes`.
The same compile also yields the ENTITY CENSUS (distinct specializations of the definition
scaffolding templates - see Gate semantics) and, for every workflow with a body, its INCLUDE TWIN
(the `include/*` rows), which together are what let the gate charge growth at cost and gate only
what got slower. `code_bytes` is every SHF_ALLOC section of the object file -
what reaches the binary; `object_sizes()` also computes `symbol_bytes`, the symbol/string/reloc
remainder, which is neither gated nor rendered - see below). Emitted code is gated because instantiation
counts are a FRONTEND metric and cannot see codegen: `text/output_format` spends ~31% of its time in the optimizer while
ranking 6th of 21 on instantiations and 17th on wall time. NEVER gate the object file's total size -
for that workflow it is 440 KB of which only 147 KB is code and 293 KB is mangled names (662 symbols
averaging 101 characters), and the two shrink by unrelated means: code by instantiating fewer copies
of a write path, metadata by keeping details out of the symbol table. Every workflow except the
formatting family emits 27-93 bytes, so `code_bytes` is a TRIPWIRE for a constexpr helper that stops
folding away - a change that lowers instantiations while raising cost.

`function_decls` is gated because instantiation counts cannot see a DECLARATION. It comes from
`-Xclang -print-stats`, which clang prints for the same `-ftime-trace -c` compile the other counts come
from (verified byte-identical to a clean compile, so the AST census costs no second invocation) -
`ast_stats()` parses `decls total`, `Function decls` and `types total` off stderr. NEVER pair it with
`-fsyntax-only` instead: skipping codegen drops 16 decls and 10 types on isq/kind_safe_interfaces, which
would make these numbers describe a different compile than the ones beside them. `Total bytes` of AST is
deliberately not parsed - it moved ±0.03% with no consistent sign on the arms that moved everything else,
because fewer declarations are offset by the added base subobject. The mechanism the metric exists for:
a hidden friend declared in a class template is REDECLARED BY EVERY SPECIALIZATION, so hosting it in a
non-template interface base removes `friends x specializations` declarations and instantiates nothing
differently. Findings §22 measured three such conversions at -206 to -747 declarations per TU
(2.1-3.3%) with instantiations moving EXACTLY 0 and constant evaluations by 2-12 out of 37k-222k. Wall
time cannot substitute: pinned with taskset on WSL2 the repeat spread was 16-30% against a sub-3%
effect. The oracle for any change here is arithmetic, not a baseline - `runner/bench.py counts` on
scaling/broad_256 must charge exactly 476 for `quantity`'s two vector-component `get()` friends
(2 x 238 specializations), and every step of the §22 table equals `friends x specializations` to the
declaration.

A gate must catch the library getting SLOWER without firing when it GROWS - adding a class or a
function is normal evolution, and a gate that goes red on it gets rebaselined away until it catches
nothing. `function_decls` qualifies for a structural reason: `-print-stats` counts what THIS TU built,
so an entity a workflow never includes costs it nothing. Measured on `feat(systems): essential SI unit
symbols header` (mp-units 77cca13c1, a new 328-line header): +0.00% on isq/kind_safe_interfaces,
scaling/broad_256 and umbrella/si_umbrella, and +3 declarations out of 200k-750k on `decls_total`.
Inside a header a workflow DOES include, declaring costs 1 and using costs many - so this metric is if
anything more growth-tolerant than instantiations, which every new unit definition moves in every
umbrella workflow. The one case where it moves in bulk - a new class template with hidden friends that
a workflow specializes - is the cost it exists to price, not a false positive. Apply the same test to
any metric proposed for `GATED`: what does a purely additive library change do to it?

Each gated metric carries a MINIMUM ABSOLUTE MOVEMENT (third field of `GATED`) that must be exceeded in
addition to the percentage band, because a percentage on a small number is not a measurement:
`code_bytes` has a median of 145 across the corpus, so a 1% band resolves to 1.4 bytes. Its floor is 512
bytes - a helper that stops folding moves it by thousands, so the floor costs no sensitivity. The
opposite case is `function_decls`, whose floor is 0 and has to be: its SMALLEST value in the corpus is
8269 (umbrella/si_lean_umbrella), so CI's 1% band already resolves to 83 declarations and repeats are
bit-identical - any floor below that would never once bind, and a floor that never binds is decoration
rather than a threshold. Derive a floor from the corpus spread, never by analogy with another metric.
`symbol_bytes`
is neither gated nor rendered (`UNRENDERED`): it is a SECOND SHADOW of what `code_bytes` already gates, not a
second axis. It ranks the corpus 0.98 with `code_bytes` and moved on exactly the same workflows across
v2.5.0 -> master on all eight counting configurations - never once alone. "Names explode, so gate names" does
not survive the decomposition (findings §19): of text/output_format's 293 KB only 105 KB is names, the rest
being 24 B per symbol, 24 B per relocation and 64 B per section - counts of emitted entities. For the other
26 workflows the number is a ~1.3 KB floor of ELF scaffolding. It stays in the payload because it is free
and `counts` still prints it. A gate must catch code getting SLOWER, not code getting BIGGER. Peak RSS is measured and
reported but deliberately NOT gated - it correlates 0.97 with instantiations, so it would only ever
fire when they already had. The other two `-print-stats` numbers are not gated either, and the reason is
DILUTION rather than redundancy: each is a mixture of the two mechanisms the sharp metrics own, measured
on the two arms that isolate one each - the hidden-friend conversions moved `function_decls` -2.1 to
-3.3% while `decls_total` moved -0.3 to -0.8%, and forcing `MP_UNITS_API_NO_CRTP=0` on
isq/kind_safe_interfaces moved instantiations +2.2% while `decls_total` moved +3.4%. The one thing only
`decls_total` can see - a non-function member added to a widely-specialized class template - it sees at
1/23rd the resolution (one member alias on `quantity` is 238 declarations out of 310759, i.e. 0.08%), so
no band would ever catch it. It IS rendered, as the whole-AST context under the gated row.
`types_total` is UNRENDERED with `symbol_bytes`: rank 0.997 with `decls_total`, same direction and
smaller magnitude on both arms, and no design decision in the library adds types without declarations. Wall time is quiet-machine-only (rank correlation with counts is just
0.69) - and `time` now prints each arm's own repeat-to-repeat spread beside the between-arm delta,
plus a verdict when the delta is smaller than the spread, because a 2% difference is meaningless on a
host whose repeats vary by 20%. `--pin CPU` binds each compile with taskset, which halved the spread
on WSL2 (23-31% -> 8-12%); the floor there is still 20-27% for a corpus total, so no single-digit
effect is measurable under a VM at all. Counts are `-std`-dependent, so the gate keeps the standard its baselines were recorded with.
A metric absent from either side of a comparison is skipped, so baselines predating a metric do not
read as change. `-ftime-trace` inflates time ~11-15% and memory ~12-17%, so counts come from a
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
  basics), isq (hierarchies, kind-safe APIs, `kind_of` edges, vector/complex fields, fractional
  exponents), affine (quantity_point, origins), generic,
  text, scaling, systems (DEFINING units, not consuming them - umbrella measures inclusion),
  constants (computing WITH measured constants: arbitrary-rational magnitudes, so every product is a
  unit the framework has never seen - the one axis `scaling/broad` deliberately holds fixed),
  umbrella (churn-expected, excluded from the median regression alarm, gated on the census-priced
  residual; includes per-chapter ISQ umbrellas, whose rows are read as MARGINAL diffs because
  chapters include their dependencies - mechanics contains space_and_time), control (core_only: the
  framework intercept every umbrella row and per-entity price is read against; it defines nothing,
  so its census is empty by construction), safety (ONE flight profile at four safety rungs -
  raw_doubles / simple_quantities (levels 1-4) / typed_quantities (adds level 5) /
  affine_quantities (adds level 6) - identical std includes and identical printed values on every
  rung, so adjacent use-cost diffs price each safety increment and raw_doubles doubles as the
  adoption-cost control; never let the rungs' computations drift apart, comparability IS the
  workload), real_life (frozen snapshots of mp-units' own example programs - they price the MIX of
  features in one TU, not production SIZE, which is the scaling series' axis; a production file
  reads as intercept + slopes x its size. NEVER include them from the measured checkout - a
  workflow whose source changes with the ref compares different programs and calls it a
  regression). Workflow preambles are STRUCTURED by convention -
  comments, then `#include <mp-units/compat_macros.h>`, then the `#ifdef` import/include branches,
  then the first line of C++ - because `workflow_preamble()` splits on exactly that shape to
  generate the include twin; user code above the includes would silently land in the twin.
- The `codata` series is read like `scaling/`: `umbrella/codata_lean_umbrella` (the 25 essential
  constants), `codata_2022_umbrella` (one full adjustment) and `codata_umbrella` (all three) are
  nested includes, so the differences are the marginal cost of a constant DEFINITION and of a second
  table of the same constants at different measured values. `constants/codata_expressions` includes
  the lean tier and nothing more, so the difference against its umbrella is use cost, not inclusion
  cost - never move it to `<mp-units/systems/codata.h>`, which would bury the former in the latter.
- `report`/`summary` derive a `## Marginal cost of user code` section from the series: slope (per
  step) and intercept per shape, per configuration, with the delta in comparison mode. That is where
  a slope regression becomes visible - v2.5.0 -> master improved every intercept ~9% while the broad
  slope went 118.7 -> 130.3 per step (+9.8%), which is why `scaling/broad_256` shows only -0.2% while
  every other workflow reports ~-20%. The gate covers the slope numerically: at the 1% band on
  broad_256, a further regression of >=1.4 instantiations per step trips it.
- `scaling/` is a SERIES, not a set of independent workflows: `<shape>_<steps>.cpp` at 16/64/256 over
  `scaling_workload.h`. Read sizes against each other - the difference divided by the difference in
  steps is the marginal cost of user code, which is the only way to separate it from the constant
  cost of inclusion. `narrow` keeps five quantity types warm (3.0 instantiations/step), `broad` COMPOSES a distinct
  derived unit per step - `base_a * pow<e>(base_b)` - at 130.3/step; never "fix" broad to reuse
  units, that erases the axis it exists to measure. It must not scale magnitudes either (an earlier
  `mag<I+1> * unit` made each step a new magnitude, so ~12% of its slope was prime factorization
  rather than unit diversity - a user reaches for `m / s`, not a new scaling factor). The
  `define_*` shapes (units / specs / constants over `define_workload.h`) are the DEFINITION-side
  series: one entity defined per line, names minted from `__LINE__`, because a definition cannot be
  stamped out by a template loop without changing what is measured. Their slopes are immune to
  library growth by construction - the entities live in the workflow - so they carry the tightest
  bands and are never rebaselined for growth. The reference unit stays FIXED for units and specs
  (the broad lesson); the constants shape VARIES its magnitude per line on purpose, factorization
  being what a measured constant is.
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
loose in mp-units). The invariant every rule below serves: RED MEANS SLOWER, NEVER BIGGER - the
library adding units, constants or quantity specs must pass an untouched gate, and only a change
that makes existing work more expensive may fail it. Three mechanisms deliver that, and each row of
the gate table names which one it used in its `basis` column:

- `use` (every workflow with a body): the gated number is the workflow MINUS ITS INCLUDE TWIN - an
  empty-main TU with the identical include preamble, generated by `measure_counts` and measured once
  per distinct include set (the `include/*` rows). A system header gaining entities moves workflow
  and twin equally and cancels; what remains is the cost of the code the workflow itself writes.
  Twin identity is the preamble's DIRECTIVES (not its text - leading comments differ across a
  family), and the twin subtraction stays valid when a workflow's include set changes, because each
  side subtracts its own twin.
- `residual` (`umbrella/`, whose workflows ARE their include sets): the baseline is replaced by
  baseline + census growth priced at the recorded per-kind ENTITY RATES, and the remainder gates.
  The census (see `CENSUS_KINDS`) counts distinct specializations of the definition scaffolding
  templates (`named_unit`, `prefixed_unit`, `quantity_spec`, `named_constant`, point origins) off
  the same traced compile as every other count - clang emits exactly one InstantiateClass event per
  distinct specialization (verified events == distinct on six umbrellas), so it is bit-deterministic
  and free. One caveat, discovered by the define-series: under the deducing-this API (the default on
  every gate compiler) a leaf spec's base is `quantity_spec<parent>`, so SIBLING leaf specs share one
  specialization and the census's quantity_spec kind counts distinct base FORMS, not defined names.
  Both sides of every comparison count the same way, which is all the residual needs - but never
  read that census column as "number of specs the chapter defines". Rates are recorded by `update`
  into the baseline file (`entity_rates`, per metric) from
  the `RATE_AXES` pairs - workflow pairs whose include sets differ in almost nothing but one kind
  (codata_2022 -> codata for constants, si_lean -> si for prefixed units, core -> space_and_time for
  specs, core -> si_lean for named units, in that order because later axes subtract kinds priced by
  earlier ones; an axis with a significant unpriced side kind is skipped rather than mispriced).
  With an unchanged census the residual IS the plain total, so no sensitivity is lost on the common
  run; every census move is itemized under `### census changes` with what it was priced at.
- `total` (control/ by design; any entry whose baseline predates twins and censuses): the raw
  number, the pre-redesign behavior.
- Per-workflow growth on the gated basis > `--slack` (AND past the metric's absolute floor) -> error
  naming the count, the spread across metrics and the worst case. ONE annotation, not one per workflow: a
  uniform change used to
  emit 52 identical `::error::` lines that buried the finding, and the per-metric table already carries
  every delta with its headroom.
- Anything past the advisory band gets ATTRIBUTED, not just measured: `check` materializes the
  baseline's recorded `mp_units_sha` as a worktree and runs the `attribute` machinery on the worst
  `--attribute-top` (default 3) workflows, so a red gate arrives with the entities that moved it
  (`### what got slower, by entity`) instead of a bare percentage. Costs two traced compiles per
  named workflow, paid only when something moved; degrades to a warning on a shallow clone.
- Every `check` (and single-ref `counts`) opens with the PRICE LIST - the corpus reduced to what one
  thing costs: the core-framework intercept, each umbrella with its census and per-entity average
  over core, the per-kind definition rates, and the scaling slopes. That table is the result; the
  per-workflow cells below are the evidence.
- Marginal cost per step from the `scaling/` series > `--slope-slack` (default `--slack`) -> error. This is
  the band that distinguishes "the library grew a feature" from "the library got slower": a feature lifts
  every total a little, only a real regression lifts the slope. Keep it TIGHTER than `--slack`, because
  totals must stay loose enough for the library to gain features. Gating totals alone also under-reacts:
  the slope is ~62% of `scaling/broad_256`'s total, so a +2% slope move shows there as +1.2% and a 2%
  totals band misses it entirely. `SLOPE_GATED` names which metrics get this treatment and the noun each
  is counted in: instantiations (125.0/step on `broad`, 3.0 on `narrow`; on the definition side 1.0 per
  bare named unit, 0.0 per leaf spec and 28.6 per bare constant) and DECLARATIONS (74.2 and 1.0).
  Declarations are there because the slope is the only form of the metric a purely additive library change
  cannot move at all - a new class or function costs a workflow the same at 16 unit types as at 256, so it
  lifts the intercept and leaves the per-step cost alone. A ZERO slope is a number to protect, not a gap:
  `slope_deltas` takes the delta against max(base, 1), so define_specs going from 0.0 to 1.0
  instantiations per step reads as +100% and trips the band instead of being skipped for the division. `code_bytes` is excluded for having no slope (87
  bytes flat across the series); `EvaluateAsConstantExpr` has a real one (~585/step) and is a candidate,
  left out until that slope has been reviewed across configurations - a band on a number nobody has looked
  at is how a gate goes red for a reason no one can explain.
- Median growth across non-umbrella workflows (each on its gated basis, so normally a median of
  use-costs) > `--median-alarm` -> framework-wide regression error (should almost never be
  rebaselined away).
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
  (unsupported by mp-units) and clang 22 (currently fails to compile the library) are out. `report`: `needs: measure`, `if: always()`, downloads the arm
  artifacts, posts `bench.py summary` into `GITHUB_STEP_SUMMARY` AND uploads the same markdown plus
  the per-arm JSON as the `compile-cost-report` artifact - a job summary cannot be downloaded,
  diffed against last week's, or pasted into a talk. The markdown file is named after the refs it
  measured.
- Dispatch inputs: `ref` and optional `compare_ref` (measured by every arm alongside the first).
- Bands are CLI flags (`--slack`, `--median-alarm`, `--tighten-notice`, `--advisory-slack`, all
  percents), NOT constants: the same baseline file is read strictly here and loosely there.

## mp-units checkouts

The runner materializes detached worktrees per ref under `.worktrees/` (gitignored) from the
`--repo` path (default: a local mp-units clone, e.g. ~/repos/mp-units). The library version is
parsed from `src/CMakeLists.txt` in each checkout and drives both variant selection and the
injected `MP_UNITS_BENCH_VERSION`.
