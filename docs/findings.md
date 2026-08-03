# Making compile-time cost a measured property

A working record of how this suite came to exist: what we set out to measure, the assumptions that
turned out to be wrong, the gotchas that cost real time, what we found, and what we changed as a
result. Written to be mined for talks and articles, so every claim carries the number behind it and
names the configuration it came from.

Unless stated otherwise, numbers are clang++-21, `-std=c++26`, libc++, `-O2 -DNDEBUG`, against
mp-units master.

---

## 0. Where this came from

None of this was planned. It started on Discord on **20 July 2026**, when **Chip Hogg** — author of
[Au](https://aurora-opensource.github.io/au/) — posted preliminary compile-time measurements for every
library on Au's comparison page, and asked:

> *"@Mateusz Pusz, I take it the posture for mp-units is that modules are what will bring good build
> time performance?"*

mp-units was last by a wide margin. His numbers, gcc-12 / C++20 / release, on an uncontrolled laptop
and explicitly flagged as provisional:

| library | total | headers | use | examples covered |
|---|---:|---:|---:|---:|
| SI (bernedom) | +261 ms | +260 | +2 | 4 / 15 |
| Boost.Units | +411 ms | +399 | +10 | 15 / 15 |
| Au | +463 ms | +414 | +35 | 15 / 15 |
| nholthaus/units v2 | +1787 ms | +1791 | +0 | 6 / 15 |
| **mp-units** | **+4518 ms** | **+4474** | **+74** | 15 / 15 |

Per-example, mp-units ran 4055–5118 ms against Au's 337–630 ms — roughly **10×**, consistently, with
no example bucking the trend.

Three caveats belong with those numbers, and the third is the one that gets dropped every time this
kind of table is quoted.

The test programs were AI-generated and unreviewed — Chip said so up front, twice. The coverage column
is not decoration: SI expresses 4 of the 15 examples and nholthaus 6, so their totals describe a
smaller job than the rest.

And **"15 / 15" does not mean two libraries were asked to do the same amount of work.** The corpus was
written to exercise **Au's** feature set — reasonably, since it lives on Au's comparison page. Au
scoring 15/15 means the examples cover Au. mp-units scoring 15/15 means mp-units can *also* express
them, because it is a superset. What the examples never touch is most of what mp-units is: full
quantity-kind safety, the ISQ hierarchy, quantity specs and their conversion rules, quantity
character, quantity points and origins. That machinery is in the headers whether an example asks for
it or not, so mp-units pays for capability the benchmark does not measure. **It is more expensive by
construction, not by inefficiency** — a benchmark built from mp-units' feature set would report the
other libraries as unable to express most of it, which is the same information wearing a different
sign.

That is not a defense, it is a methodological point worth a slide of its own: **a cross-library
comparison corpus silently encodes the feature set of whoever wrote it.** The number you actually want
is cost *per unit of capability* — and the usual objection is that no such unit exists.

Except one does, and it is already published. mp-units' write-up on
[safety levels in units libraries](https://mpusz.github.io/mp-units/latest/blog/2026/03/23/understanding-safety-levels-in-physical-units-libraries/#c-libraries)
defines six tiers:

| level | guarantee | who implements it |
|---|---|---|
| 1 | dimension safety | all of them |
| 2 | unit safety | all of them |
| 3 | representation safety (overflow, truncation) | mp-units, Au (Au stricter, via a heuristic threshold) |
| 4 | quantity **kind** safety (Hz vs Bq, Gy vs Sv) | mp-units only, and only mp-units fully |
| 5 | quantity safety (hierarchies, equations) | mp-units |
| 6 | mathematical space safety (points, deltas, origins) | mp-units |

Now the table has a denominator. The Au corpus exercises roughly levels 1–3, because that is what
every library in the comparison can express. So the measurement is **mp-units' level-6 headers
compiling level-2 code, against Boost.Units' level-2 headers compiling level-2 code.** Levels 4, 5
and 6 are present in every byte of those headers and are never asked to do anything.

That reframes the 10× from an indictment into a question worth answering: *what does each safety level
cost?* Which is a corpus axis this suite does not yet have and should — a set of workflows solving the
same problem at each level, so the price of kind safety is a number instead of an argument. It is also
the honest way to advise a user: if you only need level 2, you should be able to see what levels 4–6
are costing you, and mp-units should be able to tell you whether they can be excluded from a build.

Until that exists, the two defensible options are: compare a library against *itself* over time (what
this suite does), or state plainly which levels the corpus exercises and which it ignores.

What the table did establish, and this part is solid and independent of any of the above, is the
**shape**: for every library, cost is overwhelmingly *headers*, not *use*. mp-units' own split is
4474 ms of inclusion against 74 ms of use.

That shape is the whole reason `scaling/` exists (§4). It also means the headline number is dominated
by *which headers you include* — which is a usage question, not a library-quality question. Hence
Mateusz' first response: `si.h` and `isq.h` are the expensive ones, `<format>` is another, and
fine-grained headers exist.

Chip tried it. The **"lean"** approach — fine-grained headers, no `unit_symbols.h` — **cut the time
roughly in half**, landing mp-units near nholthaus. It also confirmed the trade-off honestly: without
unit symbols you write clunkier code. So "go lean" is real advice with a real cost, and knowing the
exact exchange rate is something only measurement gives you.

### The finding that made a suite necessary

Then Chip ran the same corpus against master as a 2.6.0 preview, interleaved, 5 reps, median across
23 examples:

| config | 2.5.0 | master `bd05df6` | delta |
|---|---:|---:|---:|
| default | 3936 ms | 4345 ms | **+409 ms (+10%)** |
| lean | 1722 ms | 2259 ms | **+537 ms (+31%)** |

Master was **meaningfully slower than the released 2.5.0**, across the board, and hit the lean
configuration harder — worst cases `temperature-convert` (+1.2 s) and `fuel-economy` (+0.9 s).
Mateusz' reaction was *"That is a surprise 🫢"*, which is the honest reaction and also the point.
Chip's diagnosis was blunt and correct:

> *"You landed performance improvements, but also a ton of features. If you're not in the habit of
> measuring compile time as you go, it's not surprising that doing more stuff also costs more."*

A regression of that size, shipped between two releases, unnoticed. Not because anyone was careless —
because **nothing was watching**. That is the gap this suite closes, and it is worth stating plainly
in any talk: the problem was never that the numbers were bad, it was that nobody had them.

(Perf work on 25 July then took about a third off `si.h` and brought lean back near 2.5.0 — still
slightly worse. Interestingly, the fixes moved lean so close to `si.h` that the lean advice may no
longer be worth its ergonomic cost. That, too, is a measurement result.)

### The starting point was already optimized

This matters for reading everything below, so it goes first: **the suite is not measuring an
unoptimized library.** Every cheap win was taken years before it existed, and the remaining findings
are subtle *because* of that.

[mp-units#643](https://github.com/mpusz/mp-units/issues/643), opened 16 November 2024, records the
first serious round - reporting compilation time already **improved roughly 2x** by the commits landed
that same day. The mechanism is worth stating precisely, because it is transferable advice that no
compile-time talk gives: **memoize `consteval` metafunctions in a variable template so the compiler
instantiates once per type instead of re-evaluating per call site.**

```cpp
// before: re-evaluated at every call site
[[nodiscard]] consteval auto get_canonical_unit(Unit auto u) { return detail::get_canonical_unit_impl(u, u); }

// after: one instantiation per type, reused everywhere
template<Unit U>
struct get_canonical_unit_result {
  inline static constexpr auto value = get_canonical_unit_impl(U{}, U{});
};
[[nodiscard]] consteval auto get_canonical_unit(Unit auto u)
{ return detail::get_canonical_unit_result<decltype(u)>::value; }
```

That pattern was applied to `get_canonical_unit`, `get_kind_tree_root`, `get_associated_quantity`, and
later `get_complexity`, `explode` and `extract_convertible_quantities`; `first_100_primes` moved to
static storage; `expr_projectable` was deleted outright. Roughly 2x, from perhaps 150 lines of change.

The 2024 measurements, via `-ftime-trace` and ClangBuildAnalyzer over the example corpus:

| configuration | frontend | codegen + optimizer |
|---|---:|---:|
| headers (50 TUs) | 590.1 s | 212.0 s |
| modules (53 TUs) | 383.9 s | 232.1 s |

with `explicitly_convertible<derived_quantity_spec<...>>` at 11305 ms and `detail::convertible<...>` at
11301 ms as the top templates, and the worst headers being `systems/si.h` (166546 ms over 19
inclusions) and `systems/si/unit_symbols.h` (157625 ms over 24).

Two more rounds followed, and both are directly relevant to findings below:

- **February 2026** - `perf: qualified calls added for lots of framework functions` across 20 files,
  `get_unit` converted to a hidden friend, `sudo_cast` instantiations reduced, and *two* commits of
  formatter compile-time work. So when §10 reports that a qualified-lookup audit finds almost nothing
  left to fix, that is not luck; it is this commit.
- **July 2026** - four commits in one day, prompted by the Discord exchange above: trimming
  `<functional>`, `<cmath>` and `<algorithm>` out of the core header closure, rejecting stateless tag
  types in the representation concepts up front, deriving `prefixed_unit` directly from `scaled_unit`,
  and checking named-unit magnitude signs structurally. This is the "about a third off `si.h`" that
  Chip measured.

Two consequences worth carrying into the rest of the document.

**The 10x gap is a post-optimization number.** It is what remains after two rounds of deliberate work,
which strengthens rather than weakens the "expensive by construction" reading: the cost is in what the
library *is*, not in obvious waste.

**And the bottleneck has not moved in two years.** `si.h` and `unit_symbols.h` were the top cost
centres in 2024 and inclusion is still 90-99% of a realistic TU today (§4). Every round of optimization
has reduced the constant without changing which term dominates. That is the strongest argument for
attacking inclusion cost next rather than anything else on the list.

### Two assumptions on the record, both wrong

The exchange is unusually valuable because both sides committed to predictions in writing, and the
suite later measured them.

**"Modules make the problem go away."** Chip, and it is the assumption almost everyone holds:

> *"I don't think there's a point to measuring modules... I basically assume the problem goes away
> with modules."* — and later — *"the whole standard library is a lot bigger than mp-units, so surely
> mp-units should cost less to import. [...] If we get the expected result, there's no need to create
> recurring measurements just to say 'cost too small to matter'."*

Measured, once the suite could do it: `import std;` shaves **15–30%**, and consuming mp-units as
modules shaves **40–70%**. A large, real improvement — and nowhere near "the problem goes away". Even
with both, mp-units remains the most expensive library in that comparison.

And the size intuition inverts completely: **mp-units' `systems` BMI is about twice the size of the
standard library's module.** "Bigger library ⇒ bigger module" is simply not how it works, because
what a BMI stores is not source volume, it is *instantiated entities* — and a units library's headers
are almost entirely instantiations. Chip's response is the best one-line summary of the whole project:

> *"Wow. So much for my ignorant assumptions!"*

Which is exactly why the suite reports BMI build cost as first-class `bmi/*` rows (§6). Had it not,
modules would have appeared free, and the assumption would have survived contact with the data.

**"More metrics means more diverse signal."** When told the suite tracks instantiation counts, wall
time and peak memory, Chip's reaction was *"Nice! That's a wider diversity"* — and the honest answer
was *"Not really. And that is interesting."* Peak memory correlates **0.97** with instantiation
counts; three metrics were largely one metric wearing hats. That is §2, and it is the reason the suite
went looking for numbers that are genuinely orthogonal — which is how it ended up at emitted code and
symbol metadata (§7).

**A third, from Khalil Estell**, on seeing the BMI sizes:

> *"Wow, those are some large binaries. [...] some people complain about their object sizes being too
> big. Which doesn't really matter because it's not the final binary that we run."*

Half right, and the half that is wrong is measurable. Object size is *not* binary size — §7 shows two
thirds of a 440 KB object file is mangled names that never reach the executable, and that the code
which does reach it is 27–93 bytes for almost every workflow in the corpus. But it is not free either:
that metadata is linker input, and it is why mp-units error messages are unreadable. The reason the
suite now gates emitted code and symbol metadata **separately** is precisely so this argument can be
settled with numbers instead of intuitions.

---

## 1. The problem

"mp-units is slow to compile" is the library's most common adoption objection, and before this suite
nobody on either side of the argument could put a number on it. That is a bad place to be twice
over: you cannot tell a user how much they are paying, and you cannot tell whether your last commit
made it worse.

The goal was not a microbenchmark. It was to make compile-time cost a **property of the library that
CI enforces**, the same way correctness is.

That turns out to be three separate problems, and only the first one is obvious:

1. What number do you measure, given that the number users care about is unmeasurable in CI?
2. What do you compile, so that the number means something about real usage?
3. How do you gate it, so that it neither cries wolf nor quietly stops meaning anything?

---

## 2. Choosing a metric

### The number users feel is the one you cannot gate

Wall-clock compile time is the thing people complain about, and it is hopeless as a CI gate. Shared
runners are noisy in ways that dwarf the effects you are hunting.

We got a free calibration of exactly how bad, and it is worth stealing as a technique. The suite
builds a standard-library module (`bmi/std`) as a pre-step for the modules configurations. That
compile is *identical work* on every arm of the matrix — same source, same flags, no dependency on
mp-units at all. In one CI run it varied by **+56%** between arms. Any gate with a threshold below
that is measuring the weather.

So: wall time is measured and reported, because it is what users experience and what a talk audience
wants to see, but it never gates anything, and two timings taken at different moments are never
compared. The runner interleaves arms rep-major/arm-minor and takes best-of-K.

### What is actually deterministic

Clang's `-ftime-trace -ftime-trace-granularity=0` emits an event per template instantiation. Counting
`InstantiateClass` + `InstantiateFunction` gives a number that is **bit-identical** for a pinned
compiler, standard and flag set.

We verified this rather than assuming it: 105 cells across two independent CI runs, **0 mismatches**.
That is the property that makes a 1% gate possible at all.

### Wrong assumption: instantiation count is a proxy for time

It is not, and this became the most interesting finding in the project.

Across 21 workflows on one configuration, the **rank correlation between instantiation count and wall
time is only 0.69**. The outliers are not noise, they are systematic. `text/output_format` ranks
**6th of 21 on instantiations but 17th on time**.

Chasing that discrepancy is section 6, and it changed what the suite gates.

### Wrong assumption (ours): more metrics are better

Peak resident memory looked like an obvious third gate. It is beautifully stable — under **0.1%**
run-to-run on both clang and GCC, so unlike wall time it *could* be gated.

It should not be. Its correlation with instantiation count is **0.97**. It is measuring the same
frontend data structures from a different angle; gating it would only ever fire when instantiations
already had, while doubling the number of ways a legitimate change gets blocked. It stays measured
and reported, where it answers a genuinely different question: *how much RAM does a parallel build of
this need*, which is what makes `-j` choices go wrong.

The lesson generalises: **before adding a gate, correlate it against the gates you have.** A metric
that is 0.97 correlated with an existing one is not extra safety, it is extra false positives.

### Gotcha: the measurement changes the thing measured

`-ftime-trace` inflates wall time by **11–15%** and peak memory by **12–17%**. So counts come from a
traced compile and time/memory from an untraced one, and the runner never reports both from a single
compile. This is easy to get wrong and produces plausible, wrong numbers.

---

## 3. The second problem: drowning in what you measured

Choosing a metric felt like the hard part. It was the first of two, and the second one is where the
time actually went.

One `report` run over the current matrix produces four deterministic metrics plus time and memory,
across ~30 workflows, across up to eight compilers, across four consumption modes, times two refs when
comparing. That is several thousand numbers. Every one is correct. Almost none of them, on their own,
tells you whether the commit you are looking at is good or bad.

The failure mode is specific and worth naming, because it is not "too much output" — it is **output
that cannot be checked**. Early reports had all of these, and every one of them shipped a plausible
wrong reading:

- BMI rows interleaved with workflow rows, so a modules cost appeared to be a workflow cost.
- Two configurations rendering under the same label (clang + libc++ and clang + libstdc++), so a
  regression in one looked like noise in the other.
- A totals row printing a bare number in comparison mode, so the reader could not tell which ref it
  belonged to.
- 40-character SHAs as column headers, pushing the data off the side of the page.
- `n/a` counted as a failure, inflating the "how much did not compile" line.
- "Largest growth" phrasing on a run where nothing grew.
- A modules delta computed against an intermediate configuration, so the percentage answered a
  question nobody asked.

None of those were measurement bugs. All of them were **presentation bugs that produce false beliefs**,
which makes them worse than a crash — a crash gets fixed.

### What actually fixed it

Three things, in increasing order of how much they helped.

**Structure that matches how the data is read.** One table per metric per compiler family, not one wide
table. Modules in their own section, because a `bmi/*` row is noise to every configuration without
modules. Refs ordered oldest-first so `old -> new (change)` reads left to right. Column headers with
the tokens every column shares removed. Percentages always against the plain build of the same
compiler, never against an intermediate.

**A findings section that states conclusions in prose.** Every report now opens with at most a handful
of ranked, plain-language findings — compile failures first, then corpus-wide movement, then constant
versus marginal cost diverging, then a wall-clock noise warning derived from the `bmi/std` control row,
then what modules buy. The tables moved into a collapsed block beneath. The rule that makes this work
is: **never state a number without saying what follows from it.** A finding that reads "instantiations
+2.3%" is a table row wearing a sentence.

**Headroom, not verdicts.** The gate prints measured value, baseline, delta, limit, and the distance
remaining to the limit. "Pass" is not information; "pass with 0.08pp of headroom" is.

### And that is why the tooling exists

The pattern behind all of it: **at this data volume, analysis is not a step after measurement, it is
part of the instrument.** A suite that emits correct numbers a human cannot triage has not finished its
job — it has moved the work somewhere less rigorous, namely someone skimming a CI log at the end of a
day.

Which is what the analysis and CI helpers are for, and why they are not conveniences:

- `bench.py summary` merges per-arm artifacts into one document, so eight runners produce one thing to
  read rather than eight.
- `bench.py attribute` answers *which* when `counts` has answered *how many*. It is the difference
  between "the slope regressed 9.8%" and "`type_list_merge_many_sorted_impl` costs 5.5 more per step",
  and it is the reason the regression in §8 was found by reading rather than guessing.
- The gate runs `attribute` automatically when it fails, so a red build arrives with a cause attached
  instead of an invitation to go and measure.
- The report is uploaded as a downloadable artifact, not only posted as a job summary, because a job
  summary cannot be diffed against last week's or pasted into a talk.

The general lesson, and it generalises past compile times: **the second hard problem in any measurement
project is triage, and it is usually mistaken for a reporting detail.**

## 4. Choosing what to compile

### Gotcha: one translation unit measures two things at once

Any single TU's cost is *inclusion cost* plus *your code's cost*, and a single number cannot separate
them. A library can look terrible because its headers are expensive, or because each line of user
code is expensive, and the fixes are unrelated.

The fix is the `scaling/` series: the same shape at 16, 64 and 256 steps. The difference in cost
divided by the difference in steps is the **marginal cost per unit of user code**; the intercept is
inclusion. Two shapes, deliberately:

| shape | what it varies | instantiations per step |
|---|---|---:|
| `narrow` | five quantity types kept warm | 3.0 |
| `broad` | a distinct derived unit per step | 53.3 |

An 18× spread between "reusing types" and "introducing new ones" is the single most useful number in
the suite for advising users, and no fixed-size benchmark can produce it.

**What didn't work, twice.** The first `broad` cycled through 8 units — so past step 8 it was
`narrow` with extra syntax, and its slope silently collapsed. The second varied unit *magnitudes*,
which measures magnitude arithmetic rather than unit diversity — a different axis that happened to
look similar. Only the third (`base_a * pow<e>(base_b)`) measures what the name claims.

The lesson: a scaling benchmark whose slope you have not plotted is probably not scaling. Both bugs
were invisible in the total and obvious in the per-step number.

### Isolating a single facility

To answer "what does *formatting* cost, as opposed to the quantity arithmetic around it", four
workflows print the same quantities through `printf`, `ostream`, `std::format` and `std::println`,
with the arithmetic in a **shared header**. They differ only in the output facility. That is what
makes their counts comparable, and it is a standing rule that no quantity operation may be added to
one of them — you change the shared header, which changes all four together.

This is the discipline that made section 6 possible. Without it, `output_format` being expensive
would have been unattributable.

### Comparing versions without comparing different code

Workflows carry a `// REQUIRES: mp-units >= X.Y` floor and optional `<name>@X.Y.cpp` variants for
older API spellings. The runner picks the right source per detected library version, so comparing two
refs never silently compares two different programs.

One rule earns its keep: `n/a` (the floor excludes this ref) and `FAIL` (the floor applies and it did
not compile) are **never collapsed into one symbol**. A FAIL is a finding, and a report that renders
it as a blank is actively lying.

---

## 5. Building the gate

### Wrong assumption (ours, corrected by Mateusz): baselines drift

The initial pitch for tight thresholds was "ten PRs at +1.9% each are individually green and land you
+21%." That is wrong, and the correction is worth stating because the intuition is so natural:
**the baseline is a committed file.** Every comparison is against the same fixed number. Ten
sub-threshold PRs land you at +1.9%, not +21%. There is no accumulation and no daily or weekly drift
— which also means running the gate more often buys you nothing, a mistake we made twice before it
stuck.

The **real** creep vector is different and much narrower: a blanket `update` re-records every
workflow, blessing the sub-threshold drift of all of them in one go. So `update --workflows <filter>`
moves only matching entries and lists everything else under `not_re_recorded`, and the unfiltered
form is documented as the one operation that can launder drift.

Getting this right mattered more than any threshold value. **The threat model for a gate is not
"noise", it is "how does a human make this stop working".**

### Two-sided, or it stops meaning anything

A gate that only catches regressions rots: every genuine improvement widens the gap between baseline
and reality until the threshold covers a change nobody would accept. So an improvement past a notice
threshold raises a `::warning::`, writes to the step summary, and opens a re-record PR.

### The bands are flags, not constants

The same baseline file is read **strictly** in this repo (1% per workflow, 0.5% median) and **loosely**
in mp-units (3%, 2%, with a 1% advisory that warns without failing). The library's own CI should not
be blocked by a 1.2% fluctuation in a suite it does not own; the suite's CI should be.

Two more pieces that turned out to matter:

- A **median alarm** across non-umbrella workflows, because a framework-wide regression can sit under
  the per-workflow threshold on every single workflow and still be real. (`umbrella/` workflows —
  which include entire systems — are excluded, since churn there is expected.)
- A **headroom column** on every gate table. "Pass" tells you nothing about whether you are at 0.1%
  or 0.99% of a 1% band. The distance to the limit has to be visible by observation.

### Gotcha: the configuration is the key, not the compiler

Counts depend on `-std`. And on the standard library. And on flags. Baselines are therefore keyed by
the whole configuration — compiler, `--std`, `--stdlib`, a label, and a digest of extra flags —
producing files like `instantiations-clang21-cxx23-libcxx.json`, and `check` refuses to compare
across keys.

The gotcha that justified this: mp-units auto-enables a throwing-constraints code path on
`__cpp_constexpr_exceptions`, which **GCC 16 defines at `-std=c++26`**. That path targets an
unadopted extension (the standard C++26 feature is not SFINAE-friendly and is a different thing
entirely). Left unpinned, one arm of the matrix would have been measuring a different library
configuration than all the others, and the resulting numbers would have looked like a GCC finding.
The suite pins it explicitly for every arm.

---

## 6. Modules: the assumption that modules are cheap

This is the section with the most wrong assumptions per square inch, including one that would have
produced a genuinely misleading result.

### The accounting trap

Measure a modules build naively and modules look almost free: the consumer TU instantiates *almost
nothing*. That is not because the work vanished — it is because the work moved into the BMI, which
you did not measure.

So the suite builds the BMIs as an explicit pre-step and reports them as first-class `bmi/*` rows:
wall time, peak memory, size on disk, and instantiation counts from a traced BMI build. The report
says in prose, above the table, that the interface cost is paid once per configuration while the
consumer deltas are consumer-only. **Omitting those rows would have made modules look free**, which
is the exact shape of a benchmark result that gets quoted for years.

### Wrong assumption (ours): headers plus `import std` is not a thing

Claimed it was impossible. Mateusz: *"Not if you do it right."* He was right — the missing piece was
one macro (`MP_UNITS_IMPORT_STD`), and it works on clang-21 and g++-15. It is now a first-class
configuration axis, driven by the same two-macro preamble mp-units' own examples use:
`MP_UNITS_IMPORT_STD` and `MP_UNITS_MODULES`, independently selectable.

That gives four consumption modes to compare, which is the interesting matrix: headers, headers +
`import std`, modules, modules + `import std`.

### Gotchas, all of which cost time

- **A std module built with the project's flags makes GCC 16 ICE in consumers.** The std module is
  now built with a deliberately minimal flag set — no `-O2`, no project macros — both because it is
  not part of mp-units and because otherwise the compiler falls over downstream.
- **GCC locates BMIs via `gcm.cache` relative to the working directory**, not via a flag. Those
  compiles run with `cwd` set to the BMI directory. Nothing about the error message says this.
- **`stdout` is a macro**, so it does not exist under `import std`. One workflow opens its own sink
  with `std::fopen`.
- **`mp_units.systems` BMI fails on GCC 15 and 16** (mp-units#717).
- **GCC 15 ICEs on headers + `import std`**; fixed in 16.
- **clang-22 cannot compile the library**: the fix landed on `main` and was never backported to
  `release/22.x` — and the distribution package *is* the release-branch tip. Two reductions written.
  This is a good reminder that "fixed upstream" and "fixed in what users install" are different
  claims.

### Presentation is part of the finding

Modules rows interleaved with workflow rows are noise to every configuration that has no modules, and
the corpus grows a column per compiler. Modules now get their **own section**: interface builds first,
then consumers.

And one hard-won rule about deltas: a configuration with extra tokens is shown as a percentage against
the **plainer build of the same compiler**, and that delta is **absolute, never incremental**. When
`modules + import std` is shown as a delta against `import std`, which is itself a delta against
plain, no reader can reconstruct what they are looking at.

---

## 7. Chasing the metric that lied

The best story in the project, because it started as a discrepancy, ran through two wrong hypotheses,
and ended by changing what the suite gates.

**The discrepancy.** `text/output_format` is 6th of 21 on instantiations and 17th on time.

**Hypothesis A (Mateusz):** formatting is long `constexpr` functions, so it is expensive to *evaluate*
at compile time even though it instantiates less.

**Hypothesis B (ours):** something else — and we were wrong to frame it as a competing hypothesis at
all, which is itself part of the story.

**The measurement.** Phase-splitting the trace and adding two deterministic numbers to the same
traced compile:

| workflow | instantiations | constant evaluations | backend share of time |
|---|---:|---:|---:|
| `text/output_format` | 14542 | 53345 | **30.6%** |
| `scaling/narrow_064` | 20502 | **74618** | ~0% |

`narrow_064` performs **40% more constant evaluations** and finishes faster. So the constant
*evaluator* is not where the money goes. But the backend — the optimizer — is eating a third of
`output_format`'s compile, and the reason was sitting in the object file: **440 KB, against 1–2 KB for
every other workflow in the corpus.**

Mateusz's hypothesis was right about the mechanism — fewer instantiations of much larger functions —
and the refinement is only *which stage of the pipeline* charges you for it. Not the constant
evaluator. The optimizer, on emitted code.

**Then the number turned out to be two numbers.** Breaking the object file down by section:

| workflow | file | machine code + data | symbol/string/reloc | symbols | avg name length |
|---|---:|---:|---:|---:|---:|
| `text/output_format` | 439912 | 146711 | **293201** | 662 | 101 |
| `text/output_printf` | 1928 | 206 | 1722 | 10 | 7 |
| `isq/derived_spec_conversions` | 1784 | 70 | 1556 | 9 | 7 |
| `scaling/narrow_064` | 1392 | 87 | 1305 | 4 | 6 |

**Two thirds of that "440 KB of code" is not code.** It is the symbol table, string table and
relocations — 662 symbols averaging 101 characters, the longest **454**. Gating file size would have
conflated two costs that share nothing but a byte count:

- **emitted code** is optimizer time and binary size; it shrinks by instantiating fewer copies.
- **symbol metadata** is linker input and error-message length; it shrinks by keeping details out of
  the object's symbol table.

They now gate as separate numbers.

**Whose code is it?** Attributing the emitted symbols by owner:

| owner | bytes | symbols |
|---|---:|---:|
| mp-units `formatter<quantity>` | 54774 | 174 |
| libc++ `__format` | 64235 | 142 |

174 symbols is the tell. The *entire* write path is being instantiated per quantity type, including
the parts that do not depend on the type at all.

### How much of it is ours

"Half of it is libc++'s" is the kind of claim that needs a control, because a libc++ template
instantiated *for an mp-units type* is our cost wearing a `std::` name. So: a TU that formats six plain
`double`s through `std::format` and includes no mp-units at all.

That control alone costs **175008 bytes of object, 75165 bytes of code, 338 symbols.** Diffing the
symbol sets:

| bucket | symbols | bytes | ours to fix? |
|---|---:|---:|---|
| present in the control too - `std::format`'s fixed entry price | 213 | 64672 | **no** |
| libc++ machinery only this TU provokes, no mp-units in the name | 275 | 3765 | negligible |
| templates instantiated **for mp-units types** | 174 | **54774** | **yes** |

So the libc++ half is a floor no change to mp-units can move, and mp-units roughly *doubles* both the
object size and the symbol count on top of it.

That matters beyond tidiness, because it is a real user-facing failure: on Compiler Explorer, printing
a quantity with `std::format` or `std::println` can trip the compile watchdog, and a units library that
cannot run in a shareable playground loses arguments it should win. Decomposing the ~4.2 s
(interleaved best-of-3, one machine):

| TU | ms |
|---|---:|
| `std::format` only, no mp-units | 1210 |
| mp-units quantity work + `printf`, no `<format>` | 2368 |
| mp-units + `std::format` | 4244 |
| mp-units + `std::println` | 4366 |

**~2.4 s is inclusion and arithmetic, ~1.2 s is `std::format`'s own floor, and ~0.7 s is the
interaction** - formatting our types specifically, which is the 174-symbol bucket and the only part a
formatter refactor can recover. Worth having, and still not the dominant term. Which is the same answer
the scaling series gives from the other direction (§4): inclusion is where the money is.

### What this means for the library

- **Everywhere except formatting, mp-units emits between 27 and 93 bytes.** `broad_064` does 53
  instantiations per step and emits 27 bytes in total. The abstraction genuinely folds away — which
  is a *result*, and a better answer to "what does mp-units cost at runtime" than any assertion.
- Therefore, for 20 of 21 workflows the emitted-code gate is a **tripwire, not a target**: it fires
  only if something that used to fold away stops folding. That is precisely the failure mode of
  replacing template logic with `constexpr` functions — and note that in that scenario the
  instantiation count goes *down* while cost goes up, so the old gate would have applauded.
- Where the logic genuinely is needed, the levers are structural, not "write less code":
  1. **Outline the type-independent tail.** Convert `quantity` → `(value, unit symbol as
     `string_view`)` early, then hand off to one non-template routine. Nothing after that step needs
     the type. 174 symbols collapses toward a handful, and the mangled names go with them.
  2. **Make spec parsing `consteval` on the checked path.** With a compile-time-checked format string
     `parse()` runs at compile time, but its body is still emitted per instantiation to serve the
     throwing path; one out-of-line non-template parser can serve that instead.
  3. **Hidden visibility / internal linkage on details**, so 450-character names never reach the
     object file. Nearly free, and it pays twice — those same names are what makes an mp-units error
     message unreadable.

The general shape: **code volume is not fixed by shortening functions, it is fixed by making fewer
copies of them** — the same move as short-circuiting for instantiations. Reduce multiplicity, not
logic.

---

## 8. Finding a regression that no single number showed

mp-units master had grown a **slope** regression: cost per unit of user code, not per TU. The totals
looked unremarkable; only the scaling series exposed it, because the intercept swamped it.

The hunt, in order:

1. **Mechanical bisection over the scaling slope.** One commit dominated — `490d18b64`, which changed
   a constraint from `NotQuantity<T>` to `NotQuantity<value_type_t<T>>`: **+4.0 instantiations per
   step**. Plus diffuse drift accumulating through `703235d1d`.
2. **Per-entity trace attribution.** `bench.py attribute` diffs instantiation counts per template
   entity with arguments collapsed, for two refs of one workflow. It pointed at the expression
   machinery: `type_list_merge_many_sorted_impl` **+5.5/step**, plus `expr_simplify` and
   `get_optimized_expression`.

A one-line constraint change costing 4 instantiations *per user expression* is the kind of thing no
review catches and no total-based benchmark reports.

### Two things that did not work, and why they're instructive

- **A `sed`-based probe that silently matched nothing.** The concepts it was patching had been
  refactored, so the "experiment" was a no-op — and it was reported as a result. A probe that can
  match nothing without complaining is not an experiment. Replaced by trace attribution, which cannot
  silently succeed.
- **Filtering a compile's output through `grep` and reporting "all relations hold"** — when the
  compile had failed. Check exit status before you read output, always.

Both failures share a shape: **a measurement that cannot distinguish "verified" from "did not run".**
That is the class of bug to design against.

---

## 9. On doing this with an AI agent

Worth its own section, because the division of labour turned out to be sharp and not what you would
guess.

**What the agent was genuinely good at:**

- Crunching wide result tables — correlating three metrics across 21 workflows and several compilers,
  which is where the "counts ≠ time" and "memory ≈ counts" findings came from. Nobody eyeballs that.
- Mechanical bisection over a numeric metric: dozens of builds, one number each, no boredom.
- Parsing traces and object files to attribute cost per template entity and per symbol owner.
- Noticing that a metric it had *just* proposed was two-thirds metadata and needed splitting.

**What it got wrong, and how each was caught:**

- The "+21% accumulation" argument for tight thresholds — **wrong reasoning about a system whose
  design it had read**. Caught by the human knowing the baseline was a committed file.
- "Headers plus `import std` is impossible" — **a capability claim stated with unearned confidence**.
  Caught by *"Not if you do it right."*
- The no-op `sed` probe and the grep-hidden compile failure — **reporting a conclusion the evidence
  did not support**.
- Repeating "24 commits ahead of origin" without re-checking. Caught by *"Why do you keep counting
  this without checking?"* Both repos were in sync.
- Destructive git operations: a `reset` that moved `master` behind five pushed commits, a
  pathspec-less `commit` that swallowed the user's staged files, and an amended commit that had
  already been pushed. All recoverable, none acceptable.

**The pattern is consistent enough to state as a rule.** The agent's *measurements* were reliable
wherever they were deterministic and reproducible — nobody ever had to double-check an instantiation
count. Its *narratives about those measurements* needed review every time, especially when they were
confident and especially when they explained why something was fine. And it should be kept away from
history-rewriting operations entirely; the value it adds there is zero and the downside is unbounded.

Which suggests the useful framing for this kind of work: **let the agent build the instrument and run
it, and reserve human attention for the claims, not the arithmetic.** Compile-time performance is an
unusually good fit, because the instrument's output is deterministic — you can check whether a number
reproduces without re-deriving how it was obtained.

---

## 10. The advice everyone gives, checked

Since the standard compile-time talk is IWYU plus "qualify your calls so ADL doesn't run", it is worth
recording what happened when we audited mp-units against exactly that. Two useful results, one of them
uncomfortable.

**Most of it is already done - deliberately, and on the record.** `perf: qualified calls added for lots
of framework functions` (`4f6b3d972`, February 2026) did exactly this audit across 20 files, and
`perf: get_unit made Hidden Friend` (`01c328221`) went further. So the survey below is measuring the
residue of finished work, not finding neglect.

Surveying every unqualified call to
a free function in the headers turns up ~120 sites for `get_quantity_spec`, ~86 for `get_unit`, and
similar for `get_dimension`, `get_character` and `equivalent`. Every one of those is a **hidden friend**
— `friend consteval QuantitySpec auto get_quantity_spec(reference)` — which has *no* lookup path except
ADL. Qualifying them would not speed anything up, it would fail to compile. And that is the point:
the hidden-friend idiom is the strong form of the advice. The name is invisible to ordinary lookup, so
the candidate set comes only from the associated classes, which is a much smaller set than "everything
declared in `mp_units`". Reaching for `detail::foo(x)` is the fix you apply when you did *not* use
hidden friends.

What is left after removing hidden friends, intentional customization points (`sqrt`, `pow`, `cbrt`),
public helpers reached by ordinary enclosing-namespace lookup (`square`, `cubic`) and one macro
false positive, is a modest tail of genuine internals — `unit_mag_is_positive`,
`get_canonical_unit_impl`, `collect_measured_constants`, `unit_symbol_impl`, `defined_as_kind_impl`,
`get_complexity_impl` — around 60 call sites, all made from *inside* `namespace detail`, where the
name is found by ordinary lookup anyway and ADL merely runs alongside it.

**And here is the uncomfortable part: this suite cannot tell you whether fixing them helps.** Name
lookup produces no template instantiation, no constant evaluation, and no emitted code. It is
invisible to all four gated metrics. It shows up only in frontend wall time — the one number we
established is unusable as a gate (§2), varying up to 56% between runs on identical work.

So the honest position on the most commonly given piece of compile-time advice is: *plausible,
already largely satisfied here by a better pattern, and not verifiable with deterministic
measurement.* It can be measured as an interleaved best-of-K wall-time A/B on a quiet machine, which
is a real experiment worth running once — but it is not something to accept on faith, and it is not
something to gate.

Which is itself the talk's thesis in miniature: **the advice that circulates is the advice that is
easy to state, not the advice with the largest measured effect.** A one-line constraint change worth
4 instantiations per user expression (§8) never appears on anyone's slide deck.

## 11. What is gated today

Four bit-deterministic numbers, all from one traced compile, per configuration:

| metric | what it catches | what it misses |
|---|---|---|
| template instantiations | frontend template work | anything after the frontend |
| constant evaluations | what a `constexpr` rewrite trades instantiations for | how much of it is emitted |
| emitted code (bytes) | optimizer work and binary size; a helper that stops folding | why |
| symbol metadata (bytes) | mangled-name volume: linker input, error-message length | runtime cost (there is none) |

Measured and reported but **not** gated: peak memory (0.97 correlated with instantiations), wall time
(quiet-machine only; 0.69 correlated with instantiations), BMI build cost, object size on disk.

---

## Talk skeleton

Most compile-time talks are about IWYU, qualified lookup, forward declarations, and PCH hygiene. That
material is fine and thoroughly covered. This one is about the layer above it: **what to measure, why
the obvious metric misleads you, and how to make CI hold the line** — with a real regression, real
wrong predictions from named people who were reasoning sensibly, and numbers for all of it.

1. **The challenge.** Chip Hogg's table: mp-units 10× the nearest competitor, and the author's own
   reaction to master being 10–31% slower than the release he'd already shipped past. Nobody was
   careless — nothing was watching. (§0)
2. **We did not start from garbage.** ~2x already won in 2024 by memoizing consteval metafunctions,
   qualified calls done in Feb 2026, header closure trimmed in July 2026. Everything that follows is
   what is left *after* that — which is why it is subtle. (§0)
3. **What a comparison table cannot tell you.** "15 / 15" is not equal work — a cross-library corpus
   encodes its author's feature set. Put the six safety levels on the slide and the 10× becomes a
   question with a denominator: mp-units' level-6 headers were compiling level-2 code. (§0)
4. **Headers, not use.** 4474 ms vs 74 ms. Where the cost actually lives, and what that implies about
   which benchmark you should build. (§0, §4)
5. **The second hard problem: too much data.** Thousands of correct numbers and no way to tell whether
   the commit was good. Seven presentation bugs that each shipped a false reading — and why analysis
   belongs *inside* the instrument, not after it. (§3)
6. **The metric you want is the one you cannot use.** `bmi/std` varying 56% on identical work. (§2)
7. **What is deterministic**, and proving it — 105 cells, 0 mismatches. (§2)
8. **The twist: counts don't predict time.** 0.69 correlation; one workflow at rank 6 vs rank 17. (§2)
9. **Intercept vs slope.** Why one TU can't answer, and the 3.0 vs 53.3 per-step spread. (§4)
10. **"Modules make the problem go away."** 15–30% and 40–70% are big — and not that. Plus the
   inverted intuition: mp-units' systems BMI is 2× the standard library's. (§0, §6)
11. **Following the lying metric to the optimizer** — and finding two thirds of the "code" was mangled
   names. Settles "object size doesn't matter" with numbers. (§7)
12. **A one-line constraint change worth 4 instantiations per user expression.** (§8)
13. **The advice everyone gives, checked** — and why this suite cannot verify it. (§10)
14. **What we gate now, and what we deliberately don't** — including a metric rejected for being 0.97
    correlated with one we had. (§2, §11)
15. **Building the instrument with an agent**: what to trust, what to check. (§9)

**Blog-length cut:** §0 down to the 2.5.0-vs-master table as the hook, then §2 (metric choice,
including the 0.69 and 0.97 correlations) and §6 (the `output_format` chase, ending on the
code/metadata split and the three refactors). One self-contained argument — *the number everyone counts
is not the number that costs you* — which needs no mp-units background at all.

## Open threads

- Re-record baselines at the `v2.6.0` tag once it exists.
- V3: hierarchy trees via language features (inheritance, aggregation) instead of template tricks, and
  a reflection-based implementation (g++-16 only so far). Both are exactly the kind of change the
  emitted-code and constant-evaluation gates exist to watch, since both trade instantiations for other
  work — and GCC's constant evaluator is currently the weak spot.
- The three `formatter<quantity>` refactors in §6.
- **A safety-level corpus axis** (§0): the same problem solved at each of the six levels, so "what does
  quantity-kind safety cost?" has an answer. Currently the most valuable missing measurement, and the
  one that would let a level-2 user see what levels 4-6 are charging them.
- Whether the "lean header" advice still earns its ergonomic cost after the July perf work, which
  moved lean close enough to `si.h` to raise the question.
