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

import argparse
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

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / "workflows"
BASELINES = ROOT / "baselines"

GATE_SLACK = 0.02          # per-workflow tolerance band (2%)
MEDIAN_ALARM = 0.02        # median growth across non-umbrella workflows -> framework regression
TIGHTEN_NOTICE = 0.02      # improvement beyond this -> suggest tightening baselines


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def detect_version(repo: Path):
    text = (repo / "src/CMakeLists.txt").read_text()
    m = re.search(r"project\([^)]*?VERSION\s+(\d+)\.(\d+)", text, re.S)
    if not m:
        sys.exit(f"cannot detect mp-units version in {repo}")
    return int(m.group(1)), int(m.group(2))


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


def compile_cmd(cxx, repo, extra, out, src, trace=False):
    ver = detect_version(repo)
    cmd = [cxx, "-std=c++23", "-O2", "-DNDEBUG", "-DMP_UNITS_API_CONTRACTS=0",
           f"-DMP_UNITS_BENCH_VERSION={ver[0] * 100 + ver[1]}"]
    if "clang" in cxx:
        cmd += ["-stdlib=libc++"]
    if trace:
        cmd += ["-ftime-trace", "-ftime-trace-granularity=0"]
    cmd += extra.split() + [f"-I{d}" for d in include_dirs(repo)]
    cmd += ["-c", str(src), "-o", str(out)]
    return cmd


def checkout(repo: Path, ref, cache: Path):
    """Create (or reuse) a detached worktree of `repo` at `ref`."""
    wt = cache / ref.replace("/", "_")
    if not wt.exists():
        run(["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), ref])
    return wt


def measure_counts(repo: Path, cxx, extra, patterns=None):
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
                run(compile_cmd(cxx, repo, extra, out, src, trace=True))
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


def measure_time(repos, cxx, extra, reps, patterns=None):
    """Interleaved best-of-K wall-clock across checkouts (rep-major, arm-minor)."""
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
                cmd = compile_cmd(cxx, repo, extra, out, src)
                try:
                    run(cmd)
                except subprocess.CalledProcessError:
                    best[ref][name] = "FAIL"
            for _ in range(reps):
                for ref, repo in repos.items():
                    src = selections[ref].get(name)
                    if src is None or best[ref].get(name) == "FAIL":
                        continue
                    cmd = compile_cmd(cxx, repo, extra, out, src)
                    t0 = time.perf_counter_ns()
                    subprocess.run(cmd, check=True, capture_output=True)
                    ms = (time.perf_counter_ns() - t0) // 1_000_000
                    cur = best[ref].get(name)
                    if not isinstance(cur, int) or ms < cur:
                        best[ref][name] = ms
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


def cmd_counts(args):
    repo = Path(args.repo).resolve()
    results = measure_counts(repo, args.cxx, args.extra_flags, args.workflows)
    rows = [(n, v) for n, v in sorted(results.items())]
    print_table(["inst_class", "inst_func"],
                [(n, v and v != "FAIL" and v["InstantiateClass"], v and v != "FAIL" and v["InstantiateFunction"])
                 for n, v in rows],
                lambda v: "n/a" if v in (None, False) else str(v))
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)  # results/ is gitignored: absent in fresh checkouts
        out.write_text(json.dumps(
            {"repo_version": ".".join(map(str, detect_version(repo))), "cxx": args.cxx,
             "host": platform.node(), "results": results}, indent=2))


def cmd_time(args):
    cache = Path(args.worktree_cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    repo = Path(args.repo).resolve()
    repos = {}
    for ref in args.refs:
        repos[ref] = repo if ref == "WORKTREE" else checkout(repo, ref, cache)
    best = measure_time(repos, args.cxx, args.extra_flags, args.reps, args.workflows)
    names = sorted({n for r in best.values() for n in r})
    rows = [(n, *[best[ref].get(n) for ref in args.refs]) for n in names]
    print_table(list(args.refs), rows, lambda v: "n/a" if v is None else str(v))
    totals = {ref: sum(v for v in best[ref].values() if isinstance(v, int)) for ref in args.refs}
    print("\nTOTALS (comparable workflows only):",
          "  ".join(f"{ref}={totals[ref]} ms" for ref in args.refs))


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


def cmd_check(args):
    repo = Path(args.repo).resolve()
    baseline_file = BASELINES / f"instantiations-{args.baseline_key}.json"
    baseline = json.loads(baseline_file.read_text())["results"]
    details = baseline_deltas(baseline, measure_counts(repo, args.cxx, args.extra_flags))
    regressions = {n: d for n, d in details.items() if d["rel"] > GATE_SLACK}
    improvements = {n: d for n, d in details.items() if d["rel"] < -TIGHTEN_NOTICE}
    deltas = [d["rel"] for d in details.values() if not d["umbrella"]]
    median = statistics.median(deltas) if deltas else 0.0
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(
            {"mp_units_version": ".".join(map(str, detect_version(repo))), "cxx": args.cxx,
             "baseline_key": args.baseline_key, "median_non_umbrella": median,
             "regressions": list(regressions), "improvements": list(improvements),
             "workflows": details}, indent=2) + "\n")
    for name, d in regressions.items():
        gate_summary_line(f"instantiation regression: {name} {d['baseline']} -> {d['current']} "
                          f"({d['rel']:+.1%}); if intentional, run bench.py update and commit the new "
                          f"baselines in this PR", "error")
    if median > MEDIAN_ALARM:
        gate_summary_line(f"framework-wide regression: median instantiation growth {median:+.1%} "
                          f"across all workflows - this should almost never be rebaselined away", "error")
    for name, d in improvements.items():
        gate_summary_line(f"improvement: {name} {d['baseline']} -> {d['current']} ({d['rel']:+.1%}) - "
                          f"baselines can be tightened; run bench.py update in a follow-up PR", "warning")
    if not regressions and median <= MEDIAN_ALARM:
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
    measured = measure_counts(repo, args.cxx, args.extra_flags, args.workflows)
    BASELINES.mkdir(exist_ok=True)
    out = BASELINES / f"instantiations-{args.baseline_key}.json"
    previous = json.loads(out.read_text())["results"] if out.exists() else {}
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
    data = {"mp_units_version": ".".join(map(str, detect_version(repo))), "cxx": args.cxx}
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


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=".", help="path to an mp-units checkout")
    p.add_argument("--cxx", default="clang++", help="compiler (counts/check/update require clang)")
    p.add_argument("--extra-flags", default="", help="extra compiler flags")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("time", help="interleaved wall-clock A/B across refs")
    t.add_argument("refs", nargs="+", help="git refs to compare; WORKTREE = the checkout as-is")
    t.add_argument("--reps", type=int, default=3)
    t.add_argument("--workflows", nargs="*", help="substring filters")
    t.add_argument("--worktree-cache", default=str(ROOT / ".worktrees"))

    c = sub.add_parser("counts", help="deterministic instantiation counts")
    c.add_argument("--workflows", nargs="*")
    c.add_argument("--output", help="write JSON results")

    g = sub.add_parser("check", help="gate against baselines (two-sided)")
    g.add_argument("--baseline-key", default="clang21")
    g.add_argument("--report", help="write a JSON report (per-workflow deltas, median, regressions, improvements)")

    u = sub.add_parser("update", help="re-record baselines")
    u.add_argument("--baseline-key", default="clang21")
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


if __name__ == "__main__":
    main()
