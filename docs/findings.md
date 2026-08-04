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

### The history is longer than the `perf:` prefix suggests

Searching the log for `perf:` undercounts it. A whole round in **June 2024** is prefixed `refactor:`,
and it predates the ~2x claim entirely:

| date | commit | what |
|---|---|---|
| 2024-06-05 | `760d48502` | unit `constexpr` evaluation limited to the one call that determines the return type |
| 2024-06-06 | `e38c7c446` | magnitudes refactored to improve compile times |
| 2024-06-12 | `ba0ba44dd` | compile-time optimizations for expression templates |
| 2024-06-13 | `5760d6e15` | the rest of `quantity_spec.h` |
| 2024-06-13 | `921aae23d` | `explode` and `get_complexity` |
| 2024-06-13 | `f63c4eec4` | `get_associated_quantity` and hierarchy traversal |
| 2024-06-14 | `f49b4c6f5` | **"compile-time optimizations reverted"** - 15 files, 680 deletions, including 852 lines of `quantity_spec.h` |

So the full picture is **five rounds, not three**: June 2024 (`refactor:`), November 2024 (`perf:`,
the ~2x), December 2024, February 2026, July 2026. And the November round used a different technique
(memoization) than the June one, which is consistent with June not having stuck.

### Three commits that are the argument for this suite

Read in sequence, the history contains its own justification - written by the author, before any of
this existed.

**An optimization round reverted with no reason recorded.** `f49b4c6f5` has an empty body. It sits on a
contributor's long-running branch (PR #571) rather than being a decision on master, and two years of
restructuring since - `magnitude.h` no longer exists - make it genuinely inconclusive whether that work
survived. Nobody can now say whether June 2024's optimizations are in the library or not. That is what
an unmeasured optimization looks like eighteen months later.

**An optimization reverted after nineteen days.** `09488409d` (2024-12-09) replaced `QuantitySpec`
convertibility concepts with direct function calls - a classic compile-time move. `b685521a1`
(2024-12-28) reverted it. Twelve lines each way.

**An optimization that made a compiler slower, cause unknown.** `dc47ac32d` (2025-04-21), in the
author's own words:

> *"For some reason this new implementation of `RepresentationOf` was causing long build times again in
> the Kalman filter examples. I'm not sure why this is and if we should keep the old implementation only
> for Xcode 15 or if we should revert this implementation change in general."*

And the sentence that sums up the whole problem, from `d00108bb0` (2025-06-10): *"gcc-15 bug workaround
and **hopefully** a compile-time improvement."* Hopefully. There was no way to check.

None of those four is carelessness. Each is what happens when the only available feedback is how long a
build feels. A gate does not make anyone smarter - it makes the difference between "hopefully" and
"measured -8.2%" available at review time.

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

## 11. First results from attributing inclusion cost

Inclusion is 90-99% of a realistic TU (§4), so it is where the attribution went first: group the
instantiation events of a TU that includes a header and *uses almost nothing*, by entity, arguments
collapsed.

An objection has to be dealt with before reading the output. You cannot make this work lazy in general,
because a unit's or quantity's **equation is its template argument** - `newton` is
`named_unit<"N", kilogram * metre / square(second)>`, and that expression must be instantiated to name
the type at all. So the question is not "what could be deferred" but **"what appears here that is an
*analysis of* the equation rather than the equation itself"**, since analyses are already lazy by
construction: a static data member of a class template is instantiated only when used, which is exactly
what the 2024 memoization gave for free.

| `umbrella/si_umbrella` - 11206 events, 579 entities | count | share |
|---|---:|---:|
| `detail::scaled_unit_impl` + `scaled_unit` + `prefixed_unit` | 2069 | **18.5%** |
| `std::is_trivially_{destructible,move_constructible,copy_constructible}` | 933 | **8.3%** |
| `std::basic_string` | 223 | 2.0% |
| `detail::type_list_merge_many_sorted_impl` | 178 | 1.6% |
| `detail::operator*` | 166 | 1.5% |
| `detail::unit_magnitude` + `magnitude_base` + `prime_factorization` | 438 | 3.9% |

| `umbrella/isq_umbrella` - 22199 events, 599 entities | count | share |
|---|---:|---:|
| `std::is_trivially_{destructible,move_constructible,copy_constructible}` | 1902 | **8.6%** |
| `detail::type_list_*` (merge_many_sorted, size, merge_sorted, push_front, map, front, element) | 3301 | 14.9% |
| `detail::expr_*` (simplify, consolidate, fractions, fractions_result, fractions_impl) | 1887 | 8.5% |
| `detail::try_extract_common_base` | 540 | 2.4% |
| `detail::get_optimized_expression` | 315 | 1.4% |

So `si.h` is dominated by a **per-definition tax** - 41 `named_unit`s and 24 prefixes produce 2069
instantiations of the unit-construction machinery, and that is already *after* July's
`derive prefixed_unit directly from scaled_unit`. `isq.h` is dominated by **type-list and expression
machinery**. Almost nothing here is deferrable analysis, which confirms the objection and redirects the
work to per-definition cost.

### One three-line change worth 8.7%

The `std::is_trivially_*` rows are the exception: they are not equation, not analysis, just a tax. They
come from one concept in `symbolic_expression.h`, checked for every unit, dimension and quantity spec:

```cpp
concept SymbolicConstant = SymbolicArg<T> && std::is_empty_v<T> && std::is_trivially_default_constructible_v<T> &&
                           std::is_trivially_copy_constructible_v<T> && std::is_trivially_move_constructible_v<T> &&
                           std::is_trivially_destructible_v<T>;
```

Each named `std` trait costs one class template instantiation *per symbolic constant*. The compiler
builtins are the same predicates by definition - `is_trivially_copy_constructible_v<T>` is specified as
`is_trivially_constructible<T, const T&>` - without the instantiation. Replacing them:

| workflow | before | after | delta |
|---|---:|---:|---:|
| `umbrella/si_umbrella` | 11206 | 10291 | **-8.2%** |
| `umbrella/isq_umbrella` | 22199 | 20381 | **-8.2%** |
| `scaling/narrow_064` | 20502 | 18651 | **-9.0%** |
| `scaling/broad_064` | 25776 | 23490 | -8.9% |
| `isq/derived_spec_conversions` | 22331 | 20303 | -9.1% |
| `affine/temperature_points` | 14882 | 13430 | **-9.8%** |
| `text/output_format` | 14542 | 13450 | -7.5% |
| `parity/kinetic_energy` | 12634 | 11587 | -8.3% |
| `systems/user_defined_units` | 18295 | 16768 | -8.3% |

Uniform 7.5-9.8%, median -8.7%, every workflow still compiling. Constant evaluations drop by the same
915 on `si_umbrella`, consistent with the traits being the whole of it.

**It is libc++-only, and that is the more interesting result.** Repeating the measurement against
libstdc++:

| workflow | stdlib | before | after | delta |
|---|---|---:|---:|---:|
| `umbrella/si_umbrella` | libc++ | 11521 | 10606 | **-7.9%** |
| `umbrella/isq_umbrella` | libc++ | 23365 | 21547 | **-7.8%** |
| `umbrella/si_umbrella` | libstdc++ | 14432 | 14432 | **+0.0%** |
| `umbrella/isq_umbrella` | libstdc++ | 25667 | 25667 | **+0.0%** |

Exactly neutral on libstdc++, because libstdc++ already defines all of these `_v` helpers as direct
builtins. So this is not "mp-units was using traits wrong" - it is **libc++ leaving ~8% on the table for
any heavily-templated library**, because three of its traits are class templates where libstdc++ uses a
builtin. Worth an upstream report as well as a local fix; the local fix is still worth making, since it
helps libc++ users and costs libstdc++ users nothing.

### What the trait spellings actually cost

Since "use `::value` instead of the `_v` helper, it instantiates less" is common advice, it is worth
measuring rather than reasoning about. 200 distinct types per row, baseline row is header overhead, and
`T<I>` itself costs one instantiation whenever a complete type is required:

| spelling | libc++ | libstdc++ | net of `T<I>`, on libc++ |
|---|---:|---:|---:|
| baseline, no trait | 35 | 95 | - |
| `is_same_v<T,U>` | 35 | 95 | **0** - builtin; does not even require complete types |
| `is_same<T,U>::value` | 235 | 295 | **+1** |
| `is_empty_v<T>` | 235 | 295 | **0** - builtin |
| `is_empty<T>::value` | 435 | - | **+1** |
| `is_trivially_destructible_v<T>` | 435 | 295 | +1 on libc++, 0 on libstdc++ |
| `is_trivially_destructible<T>::value` | 435 | - | +1 - *identical to `_v`; the helper is just an alias* |
| `__is_trivially_destructible(T)` | 235 | 295 | **0** |
| `is_trivially_copy_constructible_v<T>` | 435 | 295 | +1 on libc++, 0 on libstdc++ |
| `__is_trivially_constructible(T, const T&)` | 235 | 295 | **0** |

**The `_v` -> `::value` advice is backwards.** Where `_v` is a direct builtin, spelling it `::value`
*adds* one class instantiation per type. Where `_v` is an alias to a class template, the two are
byte-identical. On libc++ it is never a win: neutral at best, a pessimization at worst. libc++'s own
source says so in a comment - it maintains an internal `_IsSame` alias precisely because
"`is_same<A,B>` and `is_same<C,D>` are guaranteed to be different types", i.e. one instantiation each.

The lever is the third column: the builtin, at exactly **one instantiation saved per type per trait**.
Three such traits across ~311 symbolic constants is the 933 events this section opened with.

A limitation of the gated metric falls out of this too, and it belongs on the record: clang emits
`InstantiateClass` and `InstantiateFunction` and **nothing for variable-template instantiation**. So the
cost of a `_v` helper that is not builtin-backed is only visible through the class it aliases. A change
that moved work purely between variable templates would be invisible to the gate.

Portability of the builtins is the last wrinkle, and `__has_builtin` settles it: clang and GCC 16 have
`__is_trivially_destructible`; GCC 14 and 15 have only `__has_trivial_destructor`, which is equivalent
for a type already known to be empty. Gated on `__has_builtin`, the portable form measures bit-identical
to the clang-only one. MSVC is untested here.

Two honest caveats. The corpus has no negative tests, so it shows the concept still *accepts* what it
should and cannot show it still *rejects* what it should - mp-units' own test suite has to confirm that.
And the GCC wall-time check was worthless: best-of-3 said +7.7%, best-of-9 said -3.1% on g++-15 and
+7.4% on g++-16, with a 24-41% spread *within* a single arm. A textbook demonstration of §2 - the
deterministic count is the only number here worth quoting.

### Reapplying a reverted optimization, seventeen months later

`ba0ba44dd` (June 2024, "compile-time performance optimizations for expression templates") used one
technique uniformly: replace `return f<...>()` with `return decltype(f<...>()){}`. These are all
stateless tag types, so the value is meaningless and only the type matters; `decltype` is an unevaluated
operand, so - the reasoning goes - the consteval body never gets constant-evaluated.

It is gone from master: zero occurrences of `return decltype(`, and every site the commit touched is
back to the plain form, line for line. The author's recollection is that it was re-measured and gave
"nothing besides the code complications".

The suite can now check that, because the benefit such a technique would produce lives almost entirely
in **constant evaluations** - a metric nobody could count in 2024 and that this suite started gating
this week. Reapplying the same transformation mechanically to current master, 14 sites:

| workflow | instantiations | constant evaluations |
|---|---:|---:|
| `umbrella/si_umbrella` | 11206 -> 11206 (**+0.0%**) | 40045 -> 40045 (**+0.0%**) |
| `umbrella/isq_umbrella` | 22199 -> 22199 (+0.0%) | 79959 -> 79959 (+0.0%) |
| `scaling/broad_064` | 25776 -> 25776 (+0.0%) | 100187 -> 100187 (+0.0%) |
| `isq/derived_spec_conversions` | 22331 -> 22331 (+0.0%) | 81939 -> 81939 (+0.0%) |

Bit-identical. Numbers that identical demand proof the experiment ran at all - §8's lesson - so an
`#error` was injected into the patched header and confirmed to fire from the workflow's include path.

**The reason is that the technique was made redundant by the fix that replaced it.** What
`decltype(f()){}` avoids is *re-evaluating* a consteval function at every call site. The November 2024
memoization achieves the same thing by other means: `get_canonical_unit_result<U>::value` is computed
once per type and read thereafter. With the caches in place there is no repeated evaluation left for
`decltype` to remove, which is exactly why both metrics are unmoved.

A secondary reason reinforces it: these functions are all `consteval auto`, so `decltype(f())` still
forces return-type deduction and therefore body instantiation. The technique can only ever avoid
*evaluation*, never instantiation, and it needs an explicit return type to avoid even that.

Which makes the two approaches **substitutes, not complements** - and the June attempt was reverted
five months *before* the memoization that superseded it, so it was never a fair fight. The interesting
question is which of the two is cheaper, because they are not equivalent in cost: a memoizing variable
template costs one class instantiation per type, while `decltype` on an explicit-return-type function
costs nothing. That is a real experiment, and it needs the memoization stripped first (see
`memoization-experiment`) - measuring `decltype` against a memoized tree, as done above, can only ever
return zero.

### Two custom traits: one win, one expired

mp-units replaces two standard traits with its own, from `perf:` commits in December 2020
(`3d081d37e`, `a365bca07`). Neither had been measured since. Re-pointing each at the `std` version
one line at a time, so no call site moves:

| workflow | as shipped | `std::conditional_t` | `std::is_same_v` |
|---|---:|---:|---:|
| `si_umbrella` (libc++) | 11206 | 11329 (**+1.10%**) | 11206 (**+0.00%**) |
| `isq_umbrella` (libc++) | 22199 | 22687 (**+2.20%**) | 22199 (**+0.00%**) |
| `broad_064` (libc++) | 25776 | 26120 (**+1.33%**) | 25776 (**+0.00%**) |
| `si_umbrella` (libstdc++) | 14432 | 14555 (+0.85%) | 14432 (+0.00%) |
| `isq_umbrella` (libstdc++) | 25667 | 26155 (+1.90%) | 25667 (+0.00%) |

**`conditional` is a genuine, validated win: 0.85-2.20% of all instantiations, on both standard
libraries, from 21 usages.** The idiom is worth naming because it generalises to any trait selecting
between types - put the alias template *inside* a class template specialized on the `bool`:

```cpp
template<bool> struct conditional_impl { template<typename T, typename F> using type = F; };
template<> struct conditional_impl<true> { template<typename T, typename F> using type = T; };
template<bool B, typename T, typename F> using conditional = detail::conditional_impl<B>::template type<T, F>;
```

Only ever **two** class instantiations exist, no matter how many type pairs pass through, where
`std::conditional<B, T, F>` instantiates one per distinct triple. Measured in isolation: exactly one
instantiation saved per use.

**`is_same_v` is exactly neutral - 0.00%, on both standard libraries, on both metrics, across 66
usages.** The custom version is a partially-specialized variable template; `std::is_same_v` in both
libc++ and libstdc++ is the `__is_same` builtin, which costs nothing either. There is no work left to
avoid.

That is very likely an optimization that *was* real when it was written in 2020 and has since been
made redundant by the standard libraries adopting builtins. We did not measure a 2020 toolchain, so
that reading is a hypothesis - but the shipped code is 66 call sites of custom trait with no
measurable benefit today, which is the same maintenance cost with none of the payoff.

**Optimizations expire.** That is the lesson worth carrying: an unmeasured optimization is not merely
unverified, it is *undated*. The compiler and standard library it was written against are moving
targets, and the only way to notice a win evaporating is to keep measuring it. Neither of these was
re-measured for six years.

(Removing `is_same_v` is not free of risk: MSVC and older standard libraries are untested here, and
the trait may still pay there. The measured claim is narrow - clang-21 with libc++ and libstdc++.)

### The 2024 diagnosis was right, was acted on, and worked

The attribution above independently rediscovers what mp-units#643 concluded in November 2024 from
ClangBuildAnalyzer flame graphs: the cost centres were `_multiply_impl` on magnitudes, `expr_map`
(which, in the issue's words, *"gets the expression template of one abstraction and maps it to another.
To get from one type list to a second type list, it uses C++ operators. Maybe it is possible to
implement it directly on typelists?"*), `are_ingredients_convertible`, `explode`, and some
`get_canonical_unit` overloads.

The issue's own suggested answer - implement the mapping directly on type lists instead of routing it
through C++ operators - **was implemented**, in `60d313bda` (27 February 2026, *"refactor: `expr_map_impl`
refactored to not switch between domains all the time"*). Prefixed `refactor:`, so a `perf:` search
misses it; this is the sixth round.

Measured against its own parent, it worked:

| workflow | before | after | delta |
|---|---:|---:|---:|
| `scaling/broad_016` | 23977 | 23640 | -1.4% |
| `scaling/broad_064` | 27342 | 27003 | -1.2% |
| `scaling/broad_256` | 53730 | 52943 | -1.5% |
| `scaling/narrow_*` | - | - | -1.2% uniformly |
| `umbrella/si_umbrella` | 13560 | 13324 | **-1.7%** |
| `umbrella/isq_umbrella` | 21399 | 21400 | +0.0% |

And on the axis that matters for user code, the broad **slope improved from 124.0 to 122.1 per step**.
Modest, but real and in the right direction.

That reframes an inference I had drawn wrongly. `type_list_merge_many_sorted_impl` being the single
largest entity in `isq.h` today is **not** evidence the rewrite failed - it is the *consolidated* form
of work that used to be spread across `operator*` and `expr_multiply`. The cost was concentrated into
one named entity, which is precisely what makes it attributable now. Concentration is not creation.

The slope regression is therefore a **separate and later** problem: 122.1 per step in February, 130.3
today, **+6.7% after the rewrite**, from `490d18b64` and the mid-2026 drift (§8) - none of it expression
mapping. Two distinct issues that the top-entity list makes look like one.

The lesson is about attribution, not about type lists: **the biggest entity in a profile is not
necessarily the regressed one.** Only a diff against a specific ref tells you which - which is why
`attribute` takes two measurements and never one.

## 12. What a unit definition actually costs

The attribution in §11 said `si.h` was dominated by a per-definition tax and I divided 2069
instantiations by 65 definitions to get "~32 each". That was wrong - it averaged three different
operations with wildly different costs. The right instrument is the one `scaling/` already uses, applied
to *definitions* instead of expressions: TUs that define K things and nothing else, at K = 1, 8, 32.

| what is defined | K=1 | K=8 | K=32 | per definition | framework floor |
|---|---:|---:|---:|---:|---:|
| base unit - `named_unit<"b", kind_of<isq::length>>` | 5794 | 5801 | 5825 | **1.0** | 5793 |
| prefixed unit - `prefixed_unit<"p", mag_power<10,-n>, U>` | 5839 | 5909 | 6231 | **12.6** | 5826 |
| derived unit - `ba * pow<e>(bb)` | 5849 | 6605 | 9389 | **114.2** | 5735 |

**Declaring units is free. Composing them is what costs.** A base named unit is one instantiation; a
prefixed unit twelve; a derived unit built from an equation a hundred and fourteen.

### The constant cost and the marginal cost are the same mechanism

This is the result that reorganises the whole picture. 114 per derived-unit composition is the same
order as the 130.3 per step the `broad` scaling series measures for *user* code. They are not analogous,
they are **the same operation**: `si.h` defining `newton` as `kilogram * metre / square(second)` does
exactly what a user writes when they say `m / s`.

So the suite's two headline numbers - the constant cost of inclusion and the marginal cost of user code -
have one root cause, and **one fix pays twice**. That is a far better place to be than the earlier
reading, where inclusion and user cost looked like separate problems needing separate work.

### Where the 114 goes

| group | per definition | share |
|---|---:|---:|
| `std::is_trivially_{destructible,move_constructible,copy_constructible}` | 23.4 | **20.5%** |
| `expr_*` - `fractions`/`fractions_impl`/`fractions_result`, `consolidate_impl`, `map`, `type_map`, `map_contributions`, `make_spec_impl` | ~27.6 | ~24% |
| `type_list_*` - `merge_many_sorted`, `merge_sorted`, `size`, `front`, `map` | ~17.8 | ~15.6% |
| `identity_fn::operator()` | 3.0 | 2.6% |

48 distinct entities grow with each definition.

**The trait change from §11 is therefore worth 20% of the most expensive operation in the library**, not
merely 8% of a translation unit. Same three-line patch, much better framing.

Two smaller observations from the same table:

`expr_fractions`, `expr_fractions_impl` and `expr_fractions_result` each cost ~3.9 per definition - three
entities for one logical operation. An impl/result split that exists for readability is being paid for
per composition.

`identity_fn` costs 3.0 per composition and exists purely for diagnostics: the source comment says it
"helps to resolve an using alias identifier to the actual type identifier in the clang compile-time
errors". Removing all three call sites measures -1.5% on derived compositions and -0.6%/-1.1% on the
umbrellas. **That is a priced tradeoff, not a win** - 1% of instantiations against error-message quality
in a library whose diagnostics are already hard to read (§7: 450-character mangled names). Recorded here
so the decision can be made with the number in hand rather than by feel; a macro would let
compile-time-sensitive users opt out without degrading everyone's errors.

### The cost is diffuse, which decides the strategy

The obvious next move was to attack the biggest contributor inside that 114. Measuring the distribution
first says not to bother:

| | |
|---|---|
| total | 114.2 instantiations across **48 entities** |
| largest single entity | 7.8 (6.8%) - and it is one of the three traits the pending patch removes |
| median entity | **2.0** |
| top 13 entities | 50% of the cost |
| top 26 entities | 80% |
| top 36 entities | 90% |
| after the trait patch | 90.8 across 45 entities, largest remaining 4.0 |

**There is no hot spot.** Once the traits are gone the biggest single template in the most expensive
operation in the library is worth 4.4% of it, and the median is 2.0. `expr_fractions` - the helper that
looked worth attacking, three entities per use for one logical operation - is worth about 1%.

That is a strategy result, not a disappointment:

- **Entity-level optimization is a treadwheel here.** 45 entities at ~2 each; every fix is worth 1-4%,
  each needs its own correctness argument, and there are dozens.
- **The trait patch is the only concentrated win available**, and it is concentrated precisely because it
  deletes the top three entities in one three-line change. That is why it is worth 20.5% while a
  targeted helper rewrite is worth 1%.
- **Going meaningfully below 90 per composition requires removing *layers*, not entities.** The 48
  entities are roughly a pipeline depth of 12-16 templates multiplied by the 2-4 composition operations
  in `ba * pow<e>(bb)`. Each layer costs about one instantiation per operation, so the lever is pipeline
  depth.

### A measurable target for the reflection rewrite

Which is exactly the argument for replacing the expression pipeline rather than tuning it - and it now
has numbers to hit. Per derived-unit composition, today:

| metric | per composition |
|---|---:|
| template instantiations | **114.2** |
| constant evaluations | **335.1** |
| ratio | 2.93 constant evaluations per instantiation |

A reflection-based implementation does the consolidate / simplify / merge / make-spec work inside one
`consteval` function over a sequence of `std::meta::info` instead of one template layer per step. The
prediction is therefore specific and falsifiable: **instantiations should collapse toward zero while
constant evaluations rise.** Whether that is a win depends entirely on the exchange rate, which nobody
in this space has published - and both halves are gated metrics here, from the same traced compile.

That is the single most useful thing this suite can do for V3: not "reflection should be faster", but a
before-number for both metrics and a harness that will price the trade the day a branch exists.

## 13. Bisecting the slope regression

The window is `60d313bda` (Feb 2026, where the `expr_map` rewrite left the slope at its best) to master.
189 commits touch `src/`. Rather than bisect - which finds one cause when drift may be multi-step - a
ladder of ten refs across the window shows the *shape* first.

A measurement note: this ladder computes the slope from `broad_016` -> `broad_064`, which gives ~70-76,
not the ~122-130 of `broad_016` -> `broad_256`. The series is deliberately non-linear (`unit_for` only
varies the exponent above I=64), so the two are different quantities. Relative movement is what matters
and is valid; the absolute numbers must not be mixed.

| date | slope | change | window contains |
|---|---:|---:|---|
| 2026-02-27 | 70.1 | - | |
| 2026-03-28 | 71.7 | **+1.60** | `490d18b64` |
| 2026-04-11 | 72.5 | +0.88 | |
| 2026-04-27 -> 06-23 | 72.5 -> 73.1 | +0.6 gradual | |
| 2026-07-03 | 76.2 | **+3.10** | the two-axis character cluster |
| 2026-07-17 | 76.6 | +0.44 | |
| 2026-08-03 | 74.4 | **-2.21** | the four July 24 `perf:` commits |

Bisecting the biggest jump through the June character cluster gives a single commit, and it is **neither**
of the two standing suspects:

| ref | slope | change | |
|---|---:|---:|---|
| `1298be79c` | 73.06 | - | |
| `619b99df5` | 73.06 | +0.00 | split character into orthogonal axes |
| `703235d1d` | 73.38 | +0.31 | intrinsic order/field traits - *a standing suspect, and it is not this* |
| **`e061bf0ef`** | **76.17** | **+2.79** | **replace `disable_real`/`NotQuantity` with `disable_representation`** |
| `e1c2b9562` ... `88d40e7ce` | 76.17 | +0.00 each | |

The mechanism is in that commit's own message: the representation tier now leads with
`RepresentationBaseline<T> = !disable_representation<T> && ...`, and `disable_representation`'s default is
`is_quantity_abstraction<value_type_t<T>>`. Overload resolution for symbolic expressions like `mag * unit`
evaluates it on every candidate - and every `broad` step introduces a **new unit type**, so it is a fresh
instantiation per step rather than a cached one.

That is also why July's `9b765ee2e` ("bar stateless tag types from the representation concepts upfront")
recovered -2.21 of it: barring `SymbolicConstant` types short-circuits the chain before it starts.
Attribution on current master confirms the fix held - `value_type_impl`, `is_quantity_abstraction` and
`disable_representation` are all absent from the top 20 entities in the slope.

**And the standing lead was wrong.** `490d18b64` sits in a window worth +1.60, not the +4.0 previously
recorded, and `703235d1d` - also blamed - is worth +0.31. Both were plausible from a coarse bisection over
a wider range; neither survives a ladder. Recorded because a wrong lead costs more than no lead.

### The fix is already written

The three `std` traits are +102 instantiations each across those 48 steps - **6.4 per step of the current
74.4.** So the trait patch from §11, which was measured for its effect on *inclusion*, also does this:

| tree | broad_016 | broad_064 | slope |
|---|---:|---:|---:|
| master as shipped | 22205 | 25776 | **74.40** |
| master + the trait patch | 20225 | 23490 | **68.02** |
| for reference: 2026-02-27, before the regression | | | 70.1 |

**Three lines take the slope below where it was before the regression window began.** Which is a neat
demonstration of why the two-axis view matters: a change found by attributing *constant* cost turned out
to fix a *marginal* cost regression, because - per §12 - they are the same mechanism.

## 14. Two thirds of what you parse is the standard library

A different question with a bigger answer: which standard headers does the core drag in, and are they
paid for by users who never use the feature?

Standalone cost of every standard header mp-units names, worst first:

| header | instantiations | preprocessed lines |
|---|---:|---:|
| `<ostream>` | 1849 | 56,818 |
| `<chrono>` | 1832 | 61,784 |
| `<format>` | 1376 | 48,506 |
| `<complex>` | 1170 | 46,151 |
| `<locale>` | 1140 | 36,400 |
| `<sstream>` | 1121 | 39,733 |
| `<string>` | 685 | 22,271 |
| `<ranges>` | 150 | 30,333 |
| ... `<type_traits>`, `<concepts>`, `<limits>`, `<compare>`, `<cstdint>` | 0-2 each | |

**Every one of the top seven is in `si.h`'s closure**, which is 1180 headers deep. They overlap heavily,
so measure the union rather than the sum:

| translation unit | instantiations |
|---|---:|
| all seven together | **2138** |
| minus `<chrono>` | 1956 (chrono's marginal cost: **182**) |
| minus `<complex>` too | 1933 (complex's marginal cost: **23**) |
| minus the ostream family | 1376 (ostream/locale/sstream marginal: **557**) |
| `<format>` + `<string>` only | 1376 |
| a cheap core - `type_traits`, `concepts`, `limits`, `utility`, `compare`, `cstdint`, `string_view` | **145** |

So **2138 of `umbrella/si_umbrella`'s 11206 instantiations (19%) are standard-library**, and 1993 of that
is avoidable in principle. On the parse side it is worse:

| | lines |
|---|---:|
| `si_umbrella` preprocessed | 98,241 |
| the seven std headers | **64,936 (66%)** |
| all of mp-units' own `src/*.h`, for scale | 25,688 |

**Two thirds of what the compiler parses when you include `si.h` is the standard library.** Instantiation
counts, being a frontend-work metric, understate this - which is a reminder that they are not a proxy for
everything (§2).

### Moving an include is not removing it

An important constraint on all of this: relocating `<ostream>` out of one header buys **nothing** if
another header in the same closure still reaches it - the translation unit pays either way. The unit of
work is therefore "eliminate this header from the project", not "move this include". Which makes the
useful question: how many places must *all* be fixed?

Excluding `bits/core_gmf.h` (modules-only, and confirmed absent from the headers closure), the answer is
better than it looks - most expensive headers have exactly **one** core includer:

| std header | core includers | what is actually used |
|---|---:|---|
| `<format>` | 1 - `ext/format.h` | a formatter specialization |
| `<ostream>` | 1 - `ext/fixed_string.h` | `operator<<` for `basic_fixed_string` |
| `<ranges>` | 1 - `ext/fixed_string.h` | a `ranges::input_range` constructor |
| `<sstream>` | 1 - `bits/ostream.h` | one `std::basic_ostringstream` |
| `<locale>` | 1 - `framework/quantity.h` | one `std::locale` object for `vformat_to` |
| `<complex>` | 1 - `framework/representation_concepts.h` | the `Complex` concept, complex-scalar support |
| `<chrono>` | 1 - `framework/customization_points.h` | `treat_as_floating_point_v`, `duration_values<Rep>` |
| `<string>` | 4 - `dimension.h`, `unit.h`, `bits/unsatisfied.h`, `bits/constexpr_format.h` | |

Three of those are single-use and reimplementable rather than merely relocatable:

- **`<sstream>` for one `basic_ostringstream`** (39,733 lines). mp-units already has `fixed_string` and
  `inplace_vector`; building the text with those retires the header outright.
- **`<chrono>` for `duration_values<Rep>`** (61,784 lines) - borrowed to supply the *default* for
  `representation_values`, i.e. three functions (`zero`, `min`, `max`). Defining them locally removes the
  largest standard header in the closure. `treat_as_floating_point_v` is the other use and is one line.
- **`<locale>` for a single default-constructed `std::locale`** passed to `vformat_to` (36,400 lines).

And `ext/fixed_string.h` is the hub - naming a unit pulls it, and it drags `<ostream>`, `<ranges>` and
(via `ext/format.h`) `<format>` and `<string>`. `<iosfwd>` is enough to *declare* a streaming operator,
with instantiation deferred to the caller's TU where `<ostream>` is already present.

One coupling to respect before summing anything: `<chrono>`'s and `<complex>`'s marginal costs look tiny
(182 and 23) **only because `<format>` and the ostream family already pay for the shared libc++
internals.** Remove those first and these two become expensive again - 1832 and 1170 standalone. Re-measure
after each step; do not add up the table.

### The module BMIs are over-included, and it is measurable

The same question for the modules build, because `bits/core_gmf.h` is the global module fragment shared by
**every** component - so `mp_units.core`'s BMI carries every standard header *any* component needs.
Cross-referencing what the GMF declares against what the headers actually include:

- `<expected>` - declared in the GMF, included by **no header anywhere in the project**.
- `<random>` - declared in the shared GMF, needed only by `utility/`.
- `<cassert>`, `<memory>`, `<stdexcept>`, `<version>` - included **unguarded** by core headers but *not*
  declared in the GMF. The module build currently succeeds because they arrive transitively (`<string>`
  brings `<memory>`, `<sstream>` brings `<stdexcept>`). Working by luck rather than by construction.

Removing just those two, measured on real BMI builds:

| BMI | stock GMF | minus both | change |
|---|---:|---:|---:|
| `mp_units.core` | 24,465,552 | 22,151,472 | **-9.5%** |
| `mp_units.systems` | 85,332,092 | 82,921,884 | **-2.8%** |
| `mp_units.utility` | 22,986,684 | **FAILS** | needs `<random>` in its own GMF |
| core, `<expected>` alone (zero-risk) | 24,465,552 | 24,079,120 | **-1.6%** |

The `utility` failure is the mechanism made visible: `utility/random.h` includes `<random>` itself, and in a
module build that include is a no-op *only because the GMF already pulled it*. Take it out of the shared GMF
and the include lands inside the module purview. **So the fix is a per-component GMF**, not a deletion - and
with one, core drops 9.5% and systems 2.8% while `utility` keeps what it needs. Dropping `<expected>`
requires nothing at all.

Total BMI footprint today, for scale: **126.6 MB** across the three interfaces.

Note also that any header retired from the headers build must be retired from the GMF too, or the modules
build keeps paying for it - the two lists have to be maintained together, and today they disagree in both
directions.

### The chains, which is what a decomposition needs

| std header | reached via | why |
|---|---|---|
| `<ostream>`, `<ranges>`, and `<format>`/`<string>` | **`ext/fixed_string.h`** - included by `framework/dimension.h`, `framework/unit.h`, `framework/symbol_text.h` | `operator<<` on `basic_fixed_string`; a `ranges::input_range` constructor; a formatter specialization |
| `<sstream>` | `bits/ostream.h` - included by `dimension.h`, `unit.h`, `quantity.h`, `quantity_point.h` | stream insertion |
| `<chrono>` | `framework/customization_points.h` | `std::chrono::treat_as_floating_point_v` and `duration_values<Rep>` as the *default* for `representation_values` |
| `<complex>` | `framework/representation_concepts.h` | the `Complex` concept and complex-scalar support |

`ext/fixed_string.h` is the hub: naming a unit pulls it, and it drags four expensive headers. The ordinary
remedies apply and none of them removes a feature - a forward declaration of `std::basic_ostream` for a
declaration with the definition in an opt-in header; an iterator-pair constructor instead of a
`ranges::input_range` one; the formatter specialization moved to the formatting header. The `<chrono>`
dependency is the most striking, because it is 61,784 lines borrowed to supply a *default* for three
values (`zero`, `min`, `max`) that mp-units could define itself.

One coupling to note before acting: `<chrono>`'s and `<complex>`'s marginal costs look tiny (182 and 23)
**only because `<format>` and the ostream family already pay for the shared libc++ internals.** Remove
those first and these two become expensive again - 1832 and 1170 standalone. Order the work accordingly,
and re-measure after each step rather than summing the table.

## 15. The gate blocks its first change - and it is wrong to

Three days after the four-metric gate landed, Mateusz added measurement uncertainty to the library - a
real feature, deliberately small. Every gate job went red: **52 failures across three standards.**

The numbers behind the red:

- Instantiations moved **+0.08% median** against a 0.5% alarm. The feature was essentially free.
- All 52 failures were one metric: `symbol metadata (bytes)`, moving **+40 to +48 bytes** per workflow -
  one or two mangled names, appearing because the library gained a class.
- On the smallest workflow, 48 bytes read as +5.3% against a 1% band. On `text/output_format` the same 48
  bytes is +0.02%.

Mateusz' verdict, quoted because it is the design requirement the gate had violated:

> *"Our tests prevent extending the library in a meaningful way. We should guard against things that
> actually make it slower, not make it a bit larger."*

Two design errors, both from adding byte-valued metrics without checking their absolute scale against a
percentage band. `symbol_bytes` has a corpus median of **1477 bytes**, so one added symbol name is +3%;
`code_bytes` has a median of **145 bytes**, so a 1% band resolves to **1.4 bytes** - a single instruction.
A percentage band on a three-digit number is not a measurement.

The repairs:

1. **`symbol_bytes` is no longer gated.** It is linker input and error-message length, not compile-time
   cost - the weakest claim to "slower" of the four metrics, with the worst signal-to-noise. Still
   measured and reported, where the one interesting case (293 KB on `output_format`) is visible.
2. **`code_bytes` keeps its gate but gains an absolute floor**: 512 bytes of movement required on top of
   the percentage. Its job is to catch a `constexpr` helper that stops folding away - a step from ~90
   bytes to thousands - so the floor removes the entire noise regime and costs zero sensitivity. Verified
   at the boundary: on an 84-byte baseline, +48 bytes (+57%!) passes, +512 fires.
3. **One annotation instead of 52.** Every error line also read `instantiation regression:` regardless of
   which metric moved. Now: one `::error::` naming the count, the per-metric spread, and the worst case -
   the tables above already carry every delta with its headroom.

### Gate the number that means "slower"

The deeper fix is that "guard slower, not larger" names two different measurements, and the gate had only
one of them. A feature addition lifts every workflow's **total** by a similar small amount. A change that
makes user code more expensive lifts the **slope** - the marginal cost per step from the `scaling/`
series - and almost nothing legitimate moves it.

Gating totals alone also *under-reacts* to real regressions, which is the half nobody noticed until now:
the slope contributes only ~62% of `scaling/broad_256`'s total, so a slope regression arrives at the one
workflow best placed to see it diluted by a third. Demonstrated with a synthetic baseline: **a +2.04%
slope regression fails the new 1% slope band while the 2% totals band passes it** - the same movement is
only +1.17% of the total.

So the bands are now asymmetric, and the asymmetry is the design:

| band | value | meaning |
|---|---:|---|
| per-workflow totals (`--slack`) | 2% | loose - totals grow when the library gains features, and that must not block |
| **marginal cost per step (`--slope-slack`)** | **1%** | tight - nothing legitimate makes one more line of user code more expensive |
| median across workflows | 0.5% | catches framework-wide drift that stays under the per-workflow band |

The slope gets its own table with headroom, and its own error message that says why it is different: a
slope regression is paid by every translation unit that introduces units, and unlike a total it cannot be
explained by the library growing.

The general lesson joins §2's collection: **a gate is a statement about what you refuse to ship, and "it
got bigger" was never that statement.** It took a false positive on the first real feature to notice the
gate was enforcing something nobody meant.

## 16. What is gated today

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

## 17. C++26 pack indexing: one win, one measured loss, and an idea the data talked us out of

Pack indexing (P2662) was already used in three places in mp-units: `type_list_element`, where it
replaces an `indexed_type_list` multiple-inheritance trick, `type_list_split`, where it avoids building
that indexed list and then re-traversing it, and two spots in `vector_components.h`. The question was
what else could use it. All numbers below are instantiation counts from clang-21 at `-std=c++26 -O2`,
so the C++26 branch is live and the counts are bit-deterministic.

### The win: `type_list_extract`

The old formulation went through `type_list_split<List, N>` and then pattern-matched the two halves,
which **materializes both sublists as real types only to discard them and rejoin them**:

```cpp
struct type_list_extract :
    type_list_extract_impl<typename type_list_split<List, N>::first_list,
                           typename type_list_split<List, N>::second_list> {};
```

With pack indexing the element and the remainder are indexed straight out of the pack, so the
intermediates never come into existence:

```cpp
using element = Args...[sizeof...(Pre)];
using rest = List<Args...[Pre]..., Args...[sizeof...(Pre) + 1 + Post]...>;
```

| TU | before | after | delta |
|---|---:|---:|---:|
| `umbrella/isq_umbrella` | 25022 | 24872 | **-150** |
| `isq/derived_spec_conversions` | 25032 | 24958 | **-74** |
| `isq/hierarchy_conversions` | 22388 | 22326 | -62 |
| `isq/kind_conversions` | 21754 | 21692 | -62 |

The single textual call site understates it. The caller is `are_ingredients_convertible`, the explosion
algorithm that dominates every profile in this document, so the count scales with the explosion and each
step was leaving four dead specializations behind.

### The measured loss: `type_list_unique`

The same treatment looked obvious for `type_list_unique`, which is recursive and costs one
specialization per element plus a `push_front` per survivor. Pack indexing can materialize the
deduplicated list in one expansion, but only after a duplicate mask exists, and building that mask
needs two `static consteval` member function templates.

Measured on a TU with 57 genuine `get_common_unit` computations, which is the only path that reaches
`type_list_unique` at all (via `collapse_common_unit`):

| version | class | func | total |
|---|---:|---:|---:|
| recursive (baseline) | 10759 | 6556 | 17315 |
| pack indexing | 10756 | 6573 | **17329** |

**Three fewer class instantiations, seventeen more function instantiations, so 14 worse overall.** The
rewrite also had *zero* effect on `isq_umbrella` and `derived_spec_conversions`, because that path is
barely reached there. Reverted.

The reason is the one worth carrying forward. `common_unit` lists hold two or three elements, and at
that size the recursion being replaced is only two or three instantiations deep, while the consteval
scaffolding needed to replace it costs about the same. There was no headroom to win.

One worry that did not materialize: the mask uses `std::is_same_v`, not name equality, so
adjacent-only collapse is preserved exactly. `L<A, B, A>` stays intact under both implementations. An
equivalence test asserting that on both the C++20 and C++26 paths is the cheapest way to keep a dual
implementation honest, and it is what caught the boundary cases in `extract`.

### The idea the data talked us out of: permutation-based merge

The attractive observation is that **the ordering is entirely value-derived**. `type_name_less` compares
`type_name<T>()`, a constexpr `std::string_view`, and `expr_less` adds only a `ratio` exponent plus the
rule that a bare `T` sorts before `power<T, ...>`. Nothing in the comparison is an irreducible relation
between types.

So in principle a merge could be: one pack expansion building an array of keys, one consteval function
computing the permutation, one pack expansion materializing `List<Args...[perm[Is]]...>`. That converts
O(N log N) intermediate list types into roughly one, and the C++20 fallback `type_list_element` means it
does not even strictly require C++26.

Before building it, attributing every instantiation in `isq_umbrella` to its template gave the headroom:

| template | count |
|---|---:|
| `std::pair` | 943 |
| `type_list_merge_many_sorted_impl` | 606 |
| `type_list_size_impl` | 594 |
| `type_list_merge_sorted_impl` | 566 |
| `try_extract_common_base` | 540 |
| `remove_reference` | 517 |
| `expr_simplify` | 472 |
| `type_list_push_front_impl` | 431 |
| `type_list_map_impl` | 386 |
| `expr_consolidate_impl` | 349 |
| `expr_fractions` + `_impl` + `_result` | ~966 |
| `type_list_front_impl` | 312 |
| `type_list_element_impl` | 301 |

The merge ladders are 1172 instantiations, **4.7% of the TU**. But every one of those 566
`merge_sorted_impl` entries is a distinct specialization, so merging two 2-element lists costs two or
three of them, and the permutation replacement costs about the same one class plus one or two consteval
function templates. That is precisely the arithmetic that just cost us 14 instantiations in
`type_list_unique`. **For the pairwise merge, expect a wash.**

The one variant that could still pay is collapsing an entire `merge_many` in a single pass rather than
accumulating pairwise, which targets the combined 1172 with a realistic ceiling near 700 instantiations,
about **2.8%**. It needs a value-key extractor reproducing `expr_less` semantics exactly for both
`type_list_name_less` and `type_list_of_quantity_spec_less`. Left undone deliberately, with the ceiling
recorded so the decision is informed rather than hopeful.

### The transferable lesson

**A value-domain rewrite has a per-call constant cost, so it only pays when N is large, and in mp-units
N is 2 to 5.** Expression factor lists, `common_unit` members and ingredient lists are all short. This
is a real caveat on the whole "move it into the constant evaluator" family of ideas, reflection
included: the reflection work wins by removing intermediate types *across* the explosion, where the
count is large, not by making any single short-list operation cheaper. Anywhere the argument is "O(N log
N) becomes O(1)", ask what N actually is first.

### Compiler availability, measured 2026-08-04

Pack indexing matters partly because it is the only compile-time-relevant C++26 feature Clang already
ships, so unlike reflection it can be exercised outside GCC.

| feature | clang-21 | clang-22 | gcc-16 |
|---|---|---|---|
| pack indexing (P2662) | 202311 | 202311 | 202311 |
| structured bindings (P2497) | 202411 | 202411 | 202411 |
| reflection (P2996) | - | - | 202506 |
| expansion statements (P1306) | - | - | 202506 |
| contracts (P2900) | - | - | 202502 |
| constexpr exceptions (P3068) | - | - | 202411 |
| trivial relocatability (P2786) | - | 202502 | - |
| annotations (P3394) | - | - | - |

No upstream Clang has any P2996 support. `-freflection` is a hard `error: unknown argument` on
clang-20, 21 and 22, and no reflection option exists anywhere in the driver or `-cc1` flag tables.

**A trap worth recording: `__has_include(<meta>)` is a false positive on Clang.** Clang uses the system
libstdc++, so with GCC 16 installed it finds GCC's `<meta>` and even compiles it, while
`std::meta::info` does not exist. A gate must AND the header check with
`__cpp_impl_reflection >= 202506L`. Annotations are not implemented anywhere yet, so the
symbol-as-metadata idea cannot be prototyped at all.

### When a feature gets a named flag

Two different kinds of macro, and the distinction is worth keeping. An inline feature test is right when
the feature is unconditionally better, which is why the four pack-indexing sites simply spell
`defined(__cpp_pack_indexing) && __cplusplus > 202302`. A named flag earns its place only while the
answer is still "we do not know", which is the case for reflection, where the flag also allows forcing
the implementation either way for A/B measurement. Once a feature is proven a clear win everywhere, the
flag becomes noise and should be removed.

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
14. **What a unit definition costs** — 1, 12.6, and 114.2 instantiations for base, prefixed and derived.
    Declaring is free, composing is everything — and inclusion cost and user cost turn out to be one
    mechanism, so one fix pays twice. (§12)
15. **What we gate now, and what we deliberately don't** — including a metric rejected for being 0.97
    correlated with one we had. (§2, §11)
17. **Building the instrument with an agent**: what to trust, what to check. (§9)

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
- Single-pass `merge_many` in the value domain (§17): ceiling measured at ~700 instantiations, about
  2.8%, needing a value-key extractor that reproduces `expr_less` semantics. Deliberately not built,
  because the pairwise form is predicted a wash and the `type_list_unique` result is direct evidence for
  that prediction.
- The fatter targets the same attribution exposes (§17): the `expr_fractions`/`expr_simplify`/
  `expr_consolidate` cluster at ~1800 instantiations, and `std::pair` plus `try_extract_common_base` at
  ~1500 in the ingredient matcher. Both are what the reflection work aims at, and both are large enough
  that a value-domain rewrite has headroom the short-list operations lack.
