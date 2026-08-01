#!/usr/bin/env python3
"""mp-units compile-time benchmark runner.

Measures the compile-time cost of idiomatic mp-units workflows across library versions.

Subcommands:
  time     interleaved wall-clock A/B across two or more git refs (best-of-K, rep-major
           interleaving so machine-load drift hits all arms equally)
  counts   deterministic template-instantiation counts (clang -ftime-trace event counts;
           bit-stable for a pinned compiler, usable even on noisy CI machines)
  check    compare instantiation counts of a checkout against baselines/ with a two-sided
           gate: regressions fail; improvements emit a visible "tighten baselines" notice
  update   re-record baselines/ from a checkout

Workflow corpus conventions (workflows/<category>/<name>.cpp):
  // REQUIRES: mp-units >= X.Y     minimal library version (skipped -> n/a otherwise)
  <name>@X.Y.cpp                   variant for older API spellings: applies to refs with
                                   version >= X.Y until a younger variant or the base file
                                   (which covers the newest API) takes over
  umbrella/ category               churn-expected: excluded from the median regression alarm

The measured mp-units version is parsed from the checkout's src/CMakeLists.txt and also
injected as -DMP_UNITS_BENCH_VERSION=<major*100+minor> for compat shims.
"""

from __future__ import annotations  # 3.12 evaluates annotations eagerly, 3.14 does not

import argparse
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / "workflows"
BASELINES = ROOT / "baselines"

# Defaults for `check`; every band is a percent and overridable per invocation, because the same
# baselines are read with different strictness depending on who is asking (this repo gates itself
# tightly, mp-units blocks only on egregious growth).
GATE_SLACK = 2.0           # per-workflow growth beyond this -> error
MEDIAN_ALARM = 2.0         # median growth across non-umbrella workflows -> framework regression
TIGHTEN_NOTICE = 2.0       # improvement beyond this -> baselines can be tightened


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def detect_version(repo: Path):
    text = (repo / "src/CMakeLists.txt").read_text()
    m = re.search(r"project\([^)]*?VERSION\s+(\d+)\.(\d+)", text, re.S)
    if not m:
        sys.exit(f"cannot detect mp-units version in {repo}")
    return int(m.group(1)), int(m.group(2))


class Toolchain(NamedTuple):
    """Everything that has to be identical for two measurements to be comparable. The library's own
    configuration (formatting backend, contracts, freestanding, ...) rides along in `extra` and in
    `label`: `extra` is what the compiler sees, `label` is what a human calls it."""
    cxx: str
    std: str = "c++23"
    extra: str = ""
    stdlib: str = ""
    label: str = ""

    @property
    def is_clang(self):
        return "clang" in Path(self.cxx).name

    @property
    def standard_library(self):
        """Explicit choice, else clang's non-default libc++ (what mp-units CI exercises), else the
        compiler's own default - GCC has no -stdlib switch to override it with."""
        return self.stdlib or ("libc++" if self.is_clang else "")

    def version(self):
        try:
            return run([self.cxx, "--version"]).stdout.splitlines()[0]
        except (subprocess.CalledProcessError, FileNotFoundError):
            return self.cxx


def config_key(tc: Toolchain):
    """Baselines are only comparable within one configuration, so the key names the whole of it:
    compiler, standard, and a digest of any extra flags."""
    name = Path(tc.cxx).name
    digits = "".join(ch for ch in name if ch.isdigit())
    if "clang" in name:
        base = f"clang{digits}"
    elif name.startswith("g++") or "gcc" in name:
        base = f"gcc{digits}"
    else:
        base = re.sub(r"[^a-zA-Z0-9]+", "", name)
    parts = [base, tc.std.replace("+", "x")]
    if tc.standard_library:
        parts.append(tc.standard_library.replace("+", "x").replace("-", ""))
    if tc.label:
        parts.append(re.sub(r"[^a-zA-Z0-9]+", "", tc.label))
    extra = " ".join(tc.extra.split())
    if extra:
        # Any other flag changes what is being measured, so it changes the key - opaquely, but
        # `--config-label` exists precisely so a meaningful configuration gets a readable name.
        parts.append(hashlib.sha1(extra.encode()).hexdigest()[:6])
    return "-".join(parts)


def baseline_path(args, tc: Toolchain):
    override = getattr(args, "baseline_key", None)  # not every subcommand offers the override
    return BASELINES / f"instantiations-{override or config_key(tc)}.json"


def cpu_model():
    """Which machine produced the wall-clock numbers - two runners are never comparable."""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def git_provenance(repo: Path):
    """Which tree the numbers came from. `project(... VERSION)` cannot distinguish a tag from any
    dev tree of the same era, and the recorded sha is also what lets CI skip re-measuring a tree
    the baselines already describe."""
    try:
        return {"mp_units_sha": run(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip(),
                "mp_units_describe": run(["git", "-C", str(repo), "describe", "--tags", "--always",
                                          "--dirty"]).stdout.strip()}
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}  # not a git checkout (tarball, vendored copy): version metadata is all we have


def include_dirs(repo: Path):
    dirs = [repo / f"src/{c}/include" for c in ("core", "systems", "utility")]
    return [d for d in dirs if d.is_dir()]


def workflow_requires(path: Path):
    m = re.search(r"//\s*REQUIRES:\s*mp-units\s*>=\s*(\d+)\.(\d+)", path.read_text())
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def select_workflows(version, patterns=None):
    """Pick the right variant of every workflow for the given library version."""
    chosen = {}
    for path in sorted(WORKFLOWS.glob("*/*.cpp")):
        m = re.fullmatch(r"(.+?)(?:@(\d+)\.(\d+))?\.cpp", path.name)
        name = f"{path.parent.name}/{m.group(1)}"
        variant_floor = (int(m.group(2)), int(m.group(3))) if m.group(2) else None
        entry = chosen.setdefault(name, {"base": None, "variants": []})
        if variant_floor:
            entry["variants"].append((variant_floor, path))
        else:
            entry["base"] = path
    result = {}
    for name, entry in chosen.items():
        if patterns and not any(p in name for p in patterns):
            continue
        # variants sorted oldest-first; the base file covers everything newer than any variant
        applicable = [p for floor, p in sorted(entry["variants"]) if version >= floor]
        path = entry["base"]
        if applicable and entry["variants"]:
            newest_variant_floor = max(f for f, _ in entry["variants"])
            if version < newest_variant_floor or entry["base"] is None:
                path = applicable[-1] if applicable else None
            else:
                # base requirement decides between base and newest variant
                base_req = workflow_requires(entry["base"]) if entry["base"] else (99, 99)
                path = entry["base"] if version >= base_req else applicable[-1]
        if path is None or version < workflow_requires(path):
            result[name] = None  # n/a for this version
        else:
            result[name] = path
    return result


def compile_cmd(tc: Toolchain, repo, out, src, trace=False):
    ver = detect_version(repo)
    cmd = [tc.cxx, f"-std={tc.std}", "-O2", "-DNDEBUG", "-DMP_UNITS_API_CONTRACTS=0",
           # The throwing-constraints path targets an experimental constexpr-exceptions compiler and
           # is not standard-compliant, but mp-units auto-enables it on __cpp_constexpr_exceptions -
           # which GCC 16 defines at -std=c++26. Left alone, that arm would silently measure a
           # different library configuration than every other column (and fails to compile).
           "-DMP_UNITS_API_THROWING_CONSTRAINTS=0",
           f"-DMP_UNITS_BENCH_VERSION={ver[0] * 100 + ver[1]}"]
    if tc.standard_library:
        if not tc.is_clang:
            sys.exit(f"{tc.cxx} has no -stdlib switch; drop --stdlib for GCC")
        cmd += [f"-stdlib={tc.standard_library}"]
    if trace:
        # Tracing inflates BOTH wall time (~11-15%) and peak RSS (~12-17%), so counts must never
        # come from the same compile as the time/memory numbers.
        cmd += ["-ftime-trace", "-ftime-trace-granularity=0"]
    cmd += tc.extra.split() + [f"-I{d}" for d in include_dirs(repo)]
    cmd += ["-c", str(src), "-o", str(out)]
    return cmd


def checkout(repo: Path, ref, cache: Path):
    """Create (or reuse) a detached worktree of `repo` at `ref`."""
    wt = cache / ref.replace("/", "_")
    if not wt.exists():
        run(["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), ref])
    return wt


def measure_counts(repo: Path, tc: Toolchain, patterns=None):
    if not tc.is_clang:
        sys.exit(f"instantiation counts need clang's -ftime-trace; {tc.cxx} cannot produce them "
                 f"(time and peak memory work with any compiler)")
    version = detect_version(repo)
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, src in select_workflows(version, patterns).items():
            if src is None:
                results[name] = None
                continue
            out = Path(tmp) / "wf.o"
            trace = Path(tmp) / "wf.json"
            try:
                run(compile_cmd(tc, repo, out, src, trace=True))
            except subprocess.CalledProcessError as exc:
                print(f"::error::{name} failed to compile: {exc.stderr.splitlines()[:1]}")
                results[name] = "FAIL"
                continue
            data = json.loads(trace.read_text())
            counts = {"InstantiateClass": 0, "InstantiateFunction": 0}
            for e in data["traceEvents"]:
                if e.get("ph") == "X" and e["name"] in counts:
                    counts[e["name"]] += 1
            results[name] = counts
    return results


def compile_once(cmd):
    """Wall time and peak RSS of one compile. os.wait4 gives this child's own rusage, so no
    /usr/bin/time dependency and no interference between measurements."""
    started = time.perf_counter_ns()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _, status, usage = os.wait4(proc.pid, 0)
    ms = (time.perf_counter_ns() - started) // 1_000_000
    if status != 0:
        raise subprocess.CalledProcessError(status, cmd)
    return {"ms": ms, "peak_mib": round(usage.ru_maxrss / 1024, 1)}  # ru_maxrss is KiB on Linux


def measure_time(repos, tc: Toolchain, reps, patterns=None):
    """Interleaved best-of-K wall-clock and peak RSS across checkouts (rep-major, arm-minor)."""
    versions = {ref: detect_version(repo) for ref, repo in repos.items()}
    selections = {ref: select_workflows(versions[ref], patterns) for ref in repos}
    names = sorted({n for sel in selections.values() for n in sel})
    best = {ref: {} for ref in repos}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "wf.o"
        for name in names:
            # warmup + applicability
            for ref, repo in repos.items():
                src = selections[ref].get(name)
                if src is None:
                    best[ref][name] = None
                    continue
                try:
                    run(compile_cmd(tc, repo, out, src))
                except subprocess.CalledProcessError:
                    best[ref][name] = "FAIL"
            for _ in range(reps):
                for ref, repo in repos.items():
                    src = selections[ref].get(name)
                    if src is None or best[ref].get(name) == "FAIL":
                        continue
                    got = compile_once(compile_cmd(tc, repo, out, src))
                    cur = best[ref].get(name)
                    # Best-of-K per metric: the minimum of each is the least contaminated estimate,
                    # and peak RSS barely moves between reps anyway (measured spread <0.1%).
                    best[ref][name] = got if not isinstance(cur, dict) else {
                        k: min(cur[k], got[k]) for k in got}
    return best


def print_table(columns, rows, cell):
    widths = [max(len(str(r[0])) for r in rows)] + [max(10, len(c)) for c in columns]
    print(f"| {'workflow':<{widths[0]}} | " + " | ".join(f"{c:>{w}}" for c, w in zip(columns, widths[1:])) + " |")
    print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for r in rows:
        print(f"| {r[0]:<{widths[0]}} | " + " | ".join(f"{cell(v):>{w}}" for v, w in zip(r[1:], widths[1:])) + " |")


def gate_summary_line(text, kind="notice"):
    print(f"::{kind}::{text}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(text + "\n\n")


def total(entry):
    """class + function instantiations, or None when the workflow is n/a or failed to compile."""
    return entry["InstantiateClass"] + entry["InstantiateFunction"] if isinstance(entry, dict) else None


def materialize(args, refs):
    """Map each ref to a checkout: WORKTREE is the --repo tree as-is, anything else a worktree."""
    repo = Path(args.repo).resolve()
    cache = Path(args.worktree_cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    return {ref: repo if ref == "WORKTREE" else checkout(repo, ref, cache) for ref in refs}


def toolchain(args):
    return Toolchain(args.cxx, args.std, args.extra_flags, args.stdlib, args.config_label)


def cmd_counts(args):
    refs = args.refs or ["WORKTREE"]
    repos = materialize(args, refs)
    tc = toolchain(args)
    measured = {ref: measure_counts(r, tc, args.workflows) for ref, r in repos.items()}
    if len(refs) == 1:
        results = measured[refs[0]]
        print_table(["inst_class", "inst_func"],
                    [(n, v and v != "FAIL" and v["InstantiateClass"], v and v != "FAIL" and v["InstantiateFunction"])
                     for n, v in sorted(results.items())],
                    lambda v: "n/a" if v in (None, False) else str(v))
    else:
        # Several refs: totals side by side, plus the delta of the last against the first. Counts are
        # deterministic per compiler, so unlike `time` this comparison is valid anywhere.
        names = sorted({n for m in measured.values() for n in m})
        rows = []
        for name in names:
            totals = [total(measured[ref].get(name)) for ref in refs]
            delta = None
            if totals[0] and totals[-1]:
                delta = (totals[-1] - totals[0]) / totals[0]
            rows.append((name, *totals, delta))
        print_table([*refs, f"{refs[-1]} vs {refs[0]}"], rows,
                    lambda v: "n/a" if v is None else (f"{v:+.1%}" if isinstance(v, float) else str(v)))
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)  # results/ is gitignored: absent in fresh checkouts
        payload = {ref: {"repo_version": ".".join(map(str, detect_version(repos[ref]))),
                         **git_provenance(repos[ref]), "results": measured[ref]} for ref in refs}
        out.write_text(json.dumps({"cxx": args.cxx, "cxx_version": toolchain(args).version(),
                                   "host": platform.node(), "refs": payload}, indent=2) + "\n")


def metric_cell(entry, key, fmt="{}"):
    return "n/a" if not isinstance(entry, dict) else fmt.format(entry[key])


def cmd_time(args):
    repos = materialize(args, args.refs)
    best = measure_time(repos, toolchain(args), args.reps, args.workflows)
    names = sorted({n for r in best.values() for n in r})
    for title, key, fmt in (("wall time (ms, best of K)", "ms", "{}"),
                            ("peak memory (MiB)", "peak_mib", "{:.1f}")):
        print(f"\n{title}:")
        print_table(list(args.refs),
                    [(n, *[metric_cell(best[ref].get(n), key, fmt) for ref in args.refs]) for n in names],
                    str)
    # Only workflows every arm could measure - otherwise an n/a would silently shrink one total.
    comparable = [n for n in names if all(isinstance(best[ref].get(n), dict) for ref in args.refs)]
    print(f"\nTOTALS over the {len(comparable)}/{len(names)} workflows comparable across all arms:")
    for ref in args.refs:
        ms = sum(best[ref][n]["ms"] for n in comparable)
        mib = max((best[ref][n]["peak_mib"] for n in comparable), default=0)
        print(f"  {ref}: {ms} ms, peak {mib:.1f} MiB")


def baseline_deltas(baseline, results):
    """Per-workflow baseline/current/relative-delta for every comparable entry."""
    details = {}
    for name, base in sorted(baseline.items()):
        cur = results.get(name)
        if not isinstance(cur, dict) or not isinstance(base, dict):
            continue  # n/a, FAIL, or a baseline entry whose workflow is gone
        b = base["InstantiateClass"] + base["InstantiateFunction"]
        c = cur["InstantiateClass"] + cur["InstantiateFunction"]
        details[name] = {"baseline": b, "current": c, "rel": (c - b) / b,
                         "umbrella": name.startswith("umbrella/")}
    return details


def assert_same_config(recorded, tc: Toolchain, where):
    """Numbers from another configuration are not a baseline, they are a different measurement."""
    for field, current in (("cxx", tc.cxx), ("std", tc.std), ("stdlib", tc.standard_library),
                           ("config_label", tc.label), ("extra_flags", " ".join(tc.extra.split()))):
        was = recorded.get(field)
        if was is not None and was != current:
            sys.exit(f"{where} was recorded with {field}={was!r}, this run uses {current!r}; "
                     f"counts are only comparable within one configuration")


def cmd_check(args):
    repo = Path(args.repo).resolve()
    tc = toolchain(args)
    baseline_file = baseline_path(args, tc)
    if not baseline_file.exists():
        sys.exit(f"no baselines for this configuration ({baseline_file.name}); run `update` first")
    recorded = json.loads(baseline_file.read_text())
    assert_same_config(recorded, tc, baseline_file.name)
    baseline = recorded["results"]
    slack, alarm, notice = args.slack / 100, args.median_alarm / 100, args.tighten_notice / 100
    advisory_band = args.advisory_slack / 100 if args.advisory_slack is not None else None
    details = baseline_deltas(baseline, measure_counts(repo, tc))
    regressions = {n: d for n, d in details.items() if d["rel"] > slack}
    improvements = {n: d for n, d in details.items() if d["rel"] < -notice}
    # Growth inside the blocking band but past the advisory one: reported, never fatal. This is how
    # a loose gate can still say "this grew" without stopping the change.
    advisory = {n: d for n, d in details.items()
                if advisory_band is not None and advisory_band < d["rel"] <= slack}
    deltas = [d["rel"] for d in details.values() if not d["umbrella"]]
    median = statistics.median(deltas) if deltas else 0.0
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(
            {"mp_units_version": ".".join(map(str, detect_version(repo))), **git_provenance(repo),
             "cxx": args.cxx, "std": tc.std, "baseline_key": baseline_file.stem.split("instantiations-")[-1],
             "bands": {"slack": args.slack, "median_alarm": args.median_alarm,
                       "tighten_notice": args.tighten_notice, "advisory_slack": args.advisory_slack},
             "median_non_umbrella": median, "regressions": list(regressions),
             "improvements": list(improvements), "advisory": list(advisory),
             "workflows": details}, indent=2) + "\n")
    for name, d in regressions.items():
        gate_summary_line(f"instantiation regression: {name} {d['baseline']} -> {d['current']} "
                          f"({d['rel']:+.1%}, band {args.slack:g}%); if intentional, run bench.py update "
                          f"and commit the new baselines in this PR", "error")
    if median > alarm:
        gate_summary_line(f"framework-wide regression: median instantiation growth {median:+.1%} "
                          f"across all workflows - this should almost never be rebaselined away", "error")
    for name, d in advisory.items():
        gate_summary_line(f"growth within the blocking band: {name} {d['baseline']} -> {d['current']} "
                          f"({d['rel']:+.1%}, advisory band {args.advisory_slack:g}%) - not fatal here, but "
                          f"the benchmarks repo gates tighter and will go red on it", "warning")
    for name, d in improvements.items():
        gate_summary_line(f"improvement: {name} {d['baseline']} -> {d['current']} ({d['rel']:+.1%}) - "
                          f"baselines can be tightened; run bench.py update in a follow-up PR", "warning")
    if not regressions and median <= alarm:
        msg = f"instantiation gate OK (median delta {median:+.1%})"
        if improvements:
            msg += f"; {len(improvements)} workflow(s) improved - consider tightening baselines"
        gate_summary_line(msg, "notice")
        return 0
    return 1


def cmd_update(args):
    """Re-record baselines. Without --workflows every entry is rewritten from this checkout,
    which also blesses whatever sub-band drift the other workflows happen to have; with
    --workflows only the matching entries move and the rest keep their reviewed numbers."""
    repo = Path(args.repo).resolve()
    tc = toolchain(args)
    measured = measure_counts(repo, tc, args.workflows)
    BASELINES.mkdir(exist_ok=True)
    out = baseline_path(args, tc)
    previous = {}
    if out.exists():
        recorded = json.loads(out.read_text())
        assert_same_config(recorded, tc, out.name)
        previous = recorded["results"]
    if args.workflows and not measured:
        sys.exit(f"no workflow matches {args.workflows}; baselines left untouched")
    recorded, preserved = {}, {}
    for name, value in measured.items():
        if isinstance(value, dict) or name not in previous:
            recorded[name] = value  # a fresh measurement, or a new workflow with nothing to keep
        else:
            preserved[name] = previous[name]  # n/a or FAIL here: keep the reviewed number instead
    # A full update is authoritative - entries whose workflow is gone must disappear. A filtered
    # one only moves what it measured, so everything else is carried over verbatim.
    results = dict(sorted({**(previous if args.workflows else preserved), **recorded}.items()))
    carried = sorted(n for n in results if n not in recorded)
    data = {"mp_units_version": ".".join(map(str, detect_version(repo))), **git_provenance(repo),
            "cxx": args.cxx, "std": tc.std, "stdlib": tc.standard_library,
            "config_label": tc.label, "extra_flags": " ".join(tc.extra.split())}
    if carried:
        # The metadata above describes the re-recorded entries only; these predate it.
        data["not_re_recorded"] = carried
    data["results"] = results
    out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"baselines written to {out}: {len(recorded)} re-recorded, {len(carried)} carried over")
    if preserved:
        print(f"kept the previous numbers for {', '.join(preserved)} (n/a or FAIL in this checkout)")
    if not args.workflows and (dropped := sorted(set(previous) - set(results))):
        print(f"dropped entries with no workflow: {', '.join(dropped)}")
    if carried:
        print(f"carried over: {', '.join(carried)}")


METRICS = (("instantiations", "template instantiations (InstantiateClass + InstantiateFunction)"),
           ("time_ms", "wall time (ms, best of K - only trustworthy on a quiet machine)"),
           ("peak_mib", "peak compiler memory (MiB, best of K)"))


def cmd_report(args):
    """Measure every metric this toolchain can produce and emit a markdown report plus JSON.
    Counts come from a traced compile, time and memory from an untraced one - tracing inflates
    both by >10%, so they can never share a compile."""
    refs = args.refs or ["WORKTREE"]
    repos = materialize(args, refs)
    tc = toolchain(args)
    timed = measure_time(repos, tc, args.reps, args.workflows)
    counts = {ref: measure_counts(r, tc, args.workflows) if tc.is_clang else {}
              for ref, r in repos.items()}
    metrics = {"instantiations": {}, "time_ms": {}, "peak_mib": {}}
    for ref in refs:
        for name, entry in sorted(counts[ref].items()):
            metrics["instantiations"].setdefault(name, {})[ref] = "FAIL" if entry == "FAIL" else total(entry)
        for name, entry in sorted(timed[ref].items()):
            # None means the workflow does not apply to this ref (version floor); "FAIL" means it
            # applies and did not compile. Collapsing both into n/a hides a real failure.
            for metric, field in (("time_ms", "ms"), ("peak_mib", "peak_mib")):
                metrics[metric].setdefault(name, {})[ref] = entry[field] if isinstance(entry, dict) else entry
    payload = {"cxx": args.cxx, "cxx_version": tc.version(), "std": tc.std, "reps": args.reps,
               "stdlib": tc.standard_library, "config_label": tc.label, "config_key": config_key(tc),
               "host": platform.node(), "cpu": cpu_model(), "extra_flags": tc.extra,
               "refs": {ref: {"mp_units_version": ".".join(map(str, detect_version(repos[ref]))),
                              **git_provenance(repos[ref])} for ref in refs},
               "metrics": metrics}
    print(render_report([payload]))
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nreport written to {out}", file=sys.stderr)


FAMILIES = ("clang", "gcc")


def family_of(key):
    """Compiler families get their own tables: the supported set grows, and a clang column next to a
    gcc column invites a comparison that only the metric's determinism justifies."""
    return next((f for f in FAMILIES if key.startswith(f)), "other")


def version_of(key):
    m = re.match(r"[a-z]+(\d+)", key)
    return int(m.group(1)) if m else 0


def refs_oldest_first(payloads):
    """Order refs by the library version they measured, so a comparison reads old -> new."""
    versions = {}
    for p in payloads:
        for ref, info in p["refs"].items():
            versions.setdefault(ref, info.get("mp_units_version", ""))
    return sorted(versions, key=lambda r: (tuple(int(x) for x in re.findall(r"\d+", versions[r])), r))


def fmt_value(v):
    if v is None:
        return "n/a"  # workflow does not apply to this ref
    return f"{v:.1f}" if isinstance(v, float) else str(v)


def fmt_change(old, new):
    """One cell telling the whole story: where it was, where it is, and by how much it moved."""
    if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
        return f"{fmt_value(old)} -> {fmt_value(new)}"
    return f"{fmt_value(old)} -> {fmt_value(new)} ({(new - old) / old:+.1%})" if old else fmt_value(new)


def markdown_table(header, rows):
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    lines = ["| " + " | ".join(h.ljust(w) for h, w in zip(header, widths)) + " |",
             "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for row in rows:
        lines.append("| " + row[0].ljust(widths[0]) + " | "
                     + " | ".join(str(c).rjust(w) for c, w in zip(row[1:], widths[1:])) + " |")
    return lines


def render_report(payloads):
    """A table per metric per compiler family. With exactly two refs each cell carries the change, so
    one column per configuration replaces a pair of columns the reader has to diff by eye."""
    refs = refs_oldest_first(payloads)
    keys, cells, notes = [], {}, []
    for p in payloads:
        key = p.get("config_key") or p["cxx"]
        keys.append(key)
        notes.append(f"- `{key}` - {p['cxx_version']}, `-std={p['std']}`"
                     + (f", `-stdlib={p['stdlib']}`" if p.get("stdlib") else "")
                     + f", best of {p['reps']}"
                     + (f", extra flags `{p['extra_flags']}`" if p.get("extra_flags") else "")
                     + f"<br>on {p.get('cpu', 'unknown CPU')} (`{p.get('host', '?')}`)")
        for metric, per_workflow in p["metrics"].items():
            for name, by_ref in per_workflow.items():
                for ref, value in by_ref.items():
                    if value is not None:
                        cells.setdefault(metric, {}).setdefault(name, {})[(key, ref)] = value
    for ref in refs:
        info = next(p["refs"][ref] for p in payloads if ref in p["refs"])
        notes.append(f"- `{ref}` - mp-units {info['mp_units_version']}"
                     f" ({info.get('mp_units_describe', 'unknown tree')})")

    legend = ["**n/a** - the workflow does not apply to that ref, because its "
              "`// REQUIRES: mp-units >= X.Y` floor is newer. **FAIL** - it applies but did not compile."]
    if len(refs) == 2:
        legend.insert(0, f"Cells read `{refs[0]} -> {refs[1]} (change)`, oldest library version first.")
    lines = [" ".join(legend), ""]
    for metric, title in METRICS:
        by_workflow = cells.get(metric)
        if not by_workflow:
            continue
        for family in (*FAMILIES, "other"):
            present = sorted({k for k in keys if family_of(k) == family
                              and any(any(c[0] == k for c in row) for row in by_workflow.values())},
                             key=lambda k: (version_of(k), k))
            if not present:
                continue
            lines += [f"### {title} - {family}" if family != "other" else f"### {title}", ""]
            if len(refs) == 2:
                old, new = refs
                header = ["workflow", *present]
                rows = [[name, *[fmt_change(row.get((k, old)), row.get((k, new))) for k in present]]
                        for name, row in sorted(by_workflow.items())]
                lines += markdown_table(header, rows) + [""]
            else:
                header = ["workflow", *[f"{k} @ {ref}" for k in present for ref in refs
                                       if any((k, ref) in row for row in by_workflow.values())]]
                pairs = [(k, ref) for k in present for ref in refs
                         if any((k, ref) in row for row in by_workflow.values())]
                rows = [[name, *[fmt_value(row.get(pair)) for pair in pairs]]
                        for name, row in sorted(by_workflow.items())]
                lines += markdown_table(header, rows) + [""]
    if not any(line.startswith("###") for line in lines):
        return "no measurements to report"
    lines += ["<details><summary>How this was measured</summary>", ""] + notes + [
        "", "Instantiation counts are bit-deterministic for a pinned compiler and peak memory varies by",
        "<0.1% between runs, so both are comparable across every column above. Wall time is not: each",
        "configuration is measured on its own runner, and CI runners differ in CPU and in load, so",
        "compare time only within a column, never between columns. Presentation-quality timings need a",
        "quiet machine and `bench.py time`, which interleaves the arms.", "", "</details>"]
    return "\n".join(lines)


def cmd_summary(args):
    """Render one combined report from several `report --output` files (e.g. one per compiler)."""
    payloads = [json.loads(Path(f).read_text()) for f in args.reports]
    text = render_report(payloads)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(text + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=".", help="path to an mp-units checkout")
    p.add_argument("--cxx", default="clang++", help="compiler (counts/check/update require clang)")
    p.add_argument("--extra-flags", default="", help="extra compiler flags; pass them attached, as "
                                                     "--extra-flags=-DFOO=1, since argparse takes a "
                                                     "detached value starting with '-' for an option")
    p.add_argument("--stdlib", default="", help="standard library for clang (default libc++); GCC "
                                                "has no such switch")
    p.add_argument("--config-label", default="", help="readable name for a configuration axis that "
                                                     "is not visible in the flags (e.g. fmtlib)")
    p.add_argument("--std", default="c++23", help="language standard (default c++23; use c++26 for "
                                                 "reflection-based experiments)")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("time", help="interleaved wall-clock A/B across refs")
    t.add_argument("refs", nargs="+", help="git refs to compare; WORKTREE = the checkout as-is")
    t.add_argument("--reps", type=int, default=3)
    t.add_argument("--workflows", nargs="*", help="substring filters")
    t.add_argument("--worktree-cache", default=str(ROOT / ".worktrees"))

    c = sub.add_parser("counts", help="deterministic instantiation counts")
    c.add_argument("refs", nargs="*", help="git refs to measure (default: WORKTREE = the checkout as-is); "
                                          "more than one prints them side by side with a delta column")
    c.add_argument("--workflows", nargs="*")
    c.add_argument("--output", help="write JSON results")
    c.add_argument("--worktree-cache", default=str(ROOT / ".worktrees"))

    g = sub.add_parser("check", help="gate against baselines (two-sided)")
    g.add_argument("--baseline-key", help="baseline file to use (default: derived from the "
                                          "configuration, e.g. clang21-cxx23, gcc15-cxx26)")
    g.add_argument("--report", help="write a JSON report (per-workflow deltas, median, regressions, improvements)")
    g.add_argument("--slack", type=float, default=GATE_SLACK, metavar="PCT",
                   help=f"per-workflow growth beyond this percent fails (default {GATE_SLACK:g})")
    g.add_argument("--median-alarm", type=float, default=MEDIAN_ALARM, metavar="PCT",
                   help=f"non-umbrella median growth beyond this percent fails (default {MEDIAN_ALARM:g})")
    g.add_argument("--tighten-notice", type=float, default=TIGHTEN_NOTICE, metavar="PCT",
                   help=f"improvement beyond this percent suggests tightening (default {TIGHTEN_NOTICE:g})")
    g.add_argument("--advisory-slack", type=float, metavar="PCT",
                   help="report growth beyond this percent as a warning without failing; use with a "
                        "looser --slack to block only on egregious growth while still flagging the rest")

    r = sub.add_parser("report", help="all metrics this compiler can produce, as markdown + JSON")
    r.add_argument("refs", nargs="*", help="git refs to measure (default: WORKTREE)")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--workflows", nargs="*")
    r.add_argument("--output", help="write the JSON payload (feed several of these to `summary`)")
    r.add_argument("--worktree-cache", default=str(ROOT / ".worktrees"))

    sub.add_parser("key", help="print the baseline file this configuration resolves to")

    s = sub.add_parser("summary", help="merge report JSONs into one markdown report (+ job summary)")
    s.add_argument("reports", nargs="+", help="JSON files written by `report --output`")

    u = sub.add_parser("update", help="re-record baselines")
    u.add_argument("--baseline-key", help="baseline file to write (default: derived from the "
                                          "configuration, e.g. clang21-cxx23, gcc15-cxx26)")
    u.add_argument("--workflows", nargs="*",
                   help="substring filters: re-record only these, leaving the rest untouched "
                        "(default: rewrite every entry from this checkout)")

    args = p.parse_args()
    if args.cmd == "time":
        cmd_time(args)
    elif args.cmd == "counts":
        cmd_counts(args)
    elif args.cmd == "check":
        sys.exit(cmd_check(args))
    elif args.cmd == "update":
        cmd_update(args)
    elif args.cmd == "report":
        cmd_report(args)
    elif args.cmd == "summary":
        cmd_summary(args)
    elif args.cmd == "key":
        print(baseline_path(args, toolchain(args)))


if __name__ == "__main__":
    main()
