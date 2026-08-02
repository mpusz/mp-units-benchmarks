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


class BuildContext(NamedTuple):
    """What consumers need after the module pre-step: flags to find the BMIs, a working directory
    (GCC looks for gcm.cache relative to it), and what building those BMIs cost."""
    flags: tuple = ()
    cwd: str | None = None
    steps: tuple = ()


class Toolchain(NamedTuple):
    """Everything that has to be identical for two measurements to be comparable. The library's own
    configuration (formatting backend, contracts, freestanding, ...) rides along in `extra` and in
    `label`: `extra` is what the compiler sees, `label` is what a human calls it."""
    cxx: str
    std: str = "c++23"
    extra: str = ""
    stdlib: str = ""
    label: str = ""
    modules: bool = False
    import_std: bool = False

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
    if tc.modules:
        parts.append("modules")
    if tc.import_std:
        parts.append("importstd")
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


def std_number(std):
    """c++23 -> 23, and the pre-ratification spellings clang uses for the same thing."""
    digits = re.sub(r"[^0-9a-z]", "", std.lower()).removeprefix("c").removeprefix("gnu")
    return {"2a": 20, "2b": 23, "2c": 26}.get(digits[-2:], int(digits[-2:]) if digits[-2:].isdigit() else 0)


def workflow_std_floor(path: Path):
    """A workflow measuring a facility that does not exist in an older standard is not applicable
    there - the same distinction as the library floor, and not a compile failure."""
    m = re.search(r"//\s*REQUIRES:\s*(c\+\+\d\w)", path.read_text())
    return std_number(m.group(1)) if m else 0


def select_workflows(version, patterns=None, std=None):
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
        too_new_std = path is not None and std is not None and std_number(std) < workflow_std_floor(path)
        if path is None or version < workflow_requires(path) or too_new_std:
            result[name] = None  # n/a for this library version or language standard
        else:
            result[name] = path
    return result


MODULE_UNITS = (("mp_units.core", "src/core/mp-units-core.cpp"),
                ("mp_units.systems", "src/systems/mp-units-systems.cpp"),
                ("mp_units.utility", "src/utility/mp-units-utility.cpp"),
                ("mp_units", "src/mp-units.cpp"))


def std_module_source(tc: Toolchain):
    """Where the standard library keeps its module interface - libc++ ships std.cppm next to the
    toolchain, libstdc++ names it in a manifest."""
    if tc.is_clang:
        resource = Path(run([tc.cxx, "-print-resource-dir"]).stdout.strip())
        return resource.parents[2] / "share/libc++/v1/std.cppm"
    manifest = Path(run([tc.cxx, "-print-file-name=libstdc++.modules.json"]).stdout.strip())
    if not manifest.is_absolute() or not manifest.exists():
        sys.exit(f"{tc.cxx} does not ship a libstdc++ module manifest; `import std` needs a newer GCC")
    entry = next(m for m in json.loads(manifest.read_text())["modules"] if m["logical-name"] == "std")
    return (manifest.parent / entry["source-path"]).resolve()


def compile_cmd(tc: Toolchain, repo, out, src, trace=False, ctx: BuildContext = BuildContext()):
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
    if tc.import_std:
        cmd += ["-DMP_UNITS_IMPORT_STD"]
    if tc.modules:
        cmd += ["-DMP_UNITS_MODULES"]
    if (tc.modules or tc.import_std) and not tc.is_clang:
        cmd += ["-fmodules"]  # GCC finds the BMIs through gcm.cache, relative to the working directory
    cmd += list(ctx.flags)
    cmd += tc.extra.split() + [f"-I{d}" for d in include_dirs(repo)]
    cmd += ["-c", str(src), "-o", str(out)]
    return cmd


def trace_counts(trace: Path):
    data = json.loads(trace.read_text())
    counts = {"InstantiateClass": 0, "InstantiateFunction": 0}
    for e in data["traceEvents"]:
        if e.get("ph") == "X" and e["name"] in counts:
            counts[e["name"]] += 1
    return counts


def build_modules(repo: Path, tc: Toolchain, workdir: Path, trace=False):
    """Build the BMIs a configuration needs, once, before any workflow is measured - and record what
    that cost, because it is a real cost users pay that consumer numbers would otherwise hide."""
    if not (tc.modules or tc.import_std):
        return BuildContext()
    workdir.mkdir(parents=True, exist_ok=True)
    flags, steps = [], []
    base = [tc.cxx, f"-std={tc.std}", "-O2", "-DNDEBUG", "-DMP_UNITS_API_CONTRACTS=0",
            "-DMP_UNITS_API_THROWING_CONSTRAINTS=0"]
    if trace:
        # Counting the BMI build matters: under modules the consumer instantiates almost nothing,
        # because the work happened here. Tracing does not change the BMI, so consumers can reuse it.
        base += ["-ftime-trace", "-ftime-trace-granularity=0"]
    if tc.standard_library:
        base += [f"-stdlib={tc.standard_library}"]
    if tc.import_std:
        base += ["-DMP_UNITS_IMPORT_STD"]
    if not tc.is_clang:
        base += ["-fmodules"]
    cwd = None if tc.is_clang else str(workdir)

    def step(name, cmd, artifact):
        try:
            got = compile_once(cmd, cwd=cwd)
        except subprocess.CalledProcessError:
            sys.exit(f"failed to build {name} for {config_key(tc)}:\n  " + " ".join(map(str, cmd)))
        got["mib_on_disk"] = round(artifact.stat().st_size / (1024 * 1024), 1) if artifact.exists() else None
        if trace:
            trace_file = artifact.with_suffix(".json")
            got["counts"] = trace_counts(trace_file) if trace_file.exists() else None
        steps.append({"name": f"bmi/{name}", **got})

    if tc.import_std:
        # The standard library's own module is built with a MINIMAL flag set: it is not part of
        # mp-units, so it must not inherit its configuration macros or -O2. Doing so is not merely
        # untidy - GCC 16 miscompiles consumers of a std module built that way, ICEing in
        # nonnull_arg_p during GIMPLE ealias.
        std_base = [tc.cxx, f"-std={tc.std}"] + (["-ftime-trace", "-ftime-trace-granularity=0"]
                                                  if trace else [])
        if tc.standard_library:
            std_base += [f"-stdlib={tc.standard_library}"]
        if not tc.is_clang:
            std_base += ["-fmodules", "-fsearch-include-path"]
        else:
            std_base += ["-Wno-reserved-module-identifier"]
        source = std_module_source(tc)
        out = workdir / ("std.pcm" if tc.is_clang else "std.o")
        cmd = [*std_base, "--precompile" if tc.is_clang else "-c", str(source)]
        step("std", [*cmd, "-o", str(out)], out)
        if tc.is_clang:
            flags.append(f"-fmodule-file=std={out}")
    if tc.modules:
        includes = [f"-I{d}" for d in include_dirs(repo)]
        for name, rel in MODULE_UNITS:
            if not (repo / rel).exists():
                # The set of module units is a property of the ref: mp_units.utility arrived in 2.6,
                # so comparing against v2.5.0 must build what that tree has, not what master has.
                continue
            out = workdir / (f"{name}.pcm" if tc.is_clang else f"{name}.o")
            cmd = [*base, *includes, *flags]
            cmd += ["-x", "c++-module", "--precompile"] if tc.is_clang else ["-c"]
            step(name, [*cmd, str(repo / rel), "-o", str(out)], out)
            if tc.is_clang:
                flags.append(f"-fmodule-file={name}={out}")
    return BuildContext(tuple(flags), cwd, tuple(steps))


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
        ctx = build_modules(repo, tc, Path(tmp) / "bmi", trace=True)
        for step in ctx.steps:
            results[step["name"]] = step.get("counts")
        for name, src in select_workflows(version, patterns, tc.std).items():
            if src is None:
                results[name] = None
                continue
            out = Path(tmp) / "wf.o"
            trace = Path(tmp) / "wf.json"
            try:
                run(compile_cmd(tc, repo, out, src, trace=True, ctx=ctx), cwd=ctx.cwd)
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


def compile_once(cmd, cwd=None):
    """Wall time and peak RSS of one compile. os.wait4 gives this child's own rusage, so no
    /usr/bin/time dependency and no interference between measurements."""
    started = time.perf_counter_ns()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=cwd)
    _, status, usage = os.wait4(proc.pid, 0)
    ms = (time.perf_counter_ns() - started) // 1_000_000
    if status != 0:
        raise subprocess.CalledProcessError(status, cmd)
    return {"ms": ms, "peak_mib": round(usage.ru_maxrss / 1024, 1)}  # ru_maxrss is KiB on Linux


def measure_time(repos, tc: Toolchain, reps, patterns=None):
    """Interleaved best-of-K wall-clock and peak RSS across checkouts (rep-major, arm-minor)."""
    versions = {ref: detect_version(repo) for ref, repo in repos.items()}
    selections = {ref: select_workflows(versions[ref], patterns, tc.std) for ref in repos}
    names = sorted({n for sel in selections.values() for n in sel})  # BMI rows are added below
    best = {ref: {} for ref in repos}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "wf.o"
        contexts = {ref: build_modules(repo, tc, Path(tmp) / f"bmi-{ref.replace('/', '_')}")
                    for ref, repo in repos.items()}
        for ref, ctx in contexts.items():
            for s in ctx.steps:  # the BMI build is measured once; it is not a per-rep cost
                best[ref][s["name"]] = {k: v for k, v in s.items() if k != "name"}
        names = [*sorted(n for r in best.values() for n in r), *names]
        for name in [n for n in names if not n.startswith("bmi/")]:
            # warmup + applicability
            for ref, repo in repos.items():
                src = selections[ref].get(name)
                if src is None:
                    best[ref][name] = None
                    continue
                try:
                    run(compile_cmd(tc, repo, out, src, ctx=contexts[ref]), cwd=contexts[ref].cwd)
                except subprocess.CalledProcessError:
                    best[ref][name] = "FAIL"
            for _ in range(reps):
                for ref, repo in repos.items():
                    src = selections[ref].get(name)
                    if src is None or best[ref].get(name) == "FAIL":
                        continue
                    got = compile_once(compile_cmd(tc, repo, out, src, ctx=contexts[ref]),
                                       cwd=contexts[ref].cwd)
                    cur = best[ref].get(name)
                    # Best-of-K per metric: the minimum of each is the least contaminated estimate,
                    # and peak RSS barely moves between reps anyway (measured spread <0.1%).
                    best[ref][name] = got if not isinstance(cur, dict) else {
                        k: min(cur[k], got[k]) for k in got if k in cur}
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
    return Toolchain(args.cxx, args.std, args.extra_flags, args.stdlib, args.config_label,
                     args.modules, args.import_std)


def cmd_counts(args):
    refs = args.refs or ["WORKTREE"]
    repos = materialize(args, refs)
    tc = toolchain(args)
    measured = {ref: measure_counts(r, tc, args.workflows) for ref, r in repos.items()}
    if len(refs) == 1:
        results = measured[refs[0]]
        print_table(["inst_class", "inst_func"],
                    [(n, v["InstantiateClass"] if isinstance(v, dict) else v,
                      v["InstantiateFunction"] if isinstance(v, dict) else v)
                     for n, v in sorted(results.items())],
                    lambda v: "n/a" if v is None else str(v))
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
    if isinstance(entry, dict):
        return fmt.format(entry[key]) if entry.get(key) is not None else "-"
    return entry if isinstance(entry, str) else "n/a"  # "FAIL" is not the same as "not applicable"


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


def gate_summary_table(details, median, args, tc: Toolchain):
    """Every workflow with its measured value, its limit and the headroom left - so the distance to
    the bands is visible by observation, not inferred from a single pass/fail line."""
    if not details:
        return
    rows = []
    for name, d in sorted(details.items()):
        delta = d["rel"] * 100
        if delta > args.slack:
            status = "FAILS"
        elif args.advisory_slack is not None and delta > args.advisory_slack:
            status = "advisory"
        elif delta < -args.tighten_notice:
            status = "can tighten"
        else:
            status = "ok"
        rows.append([name + (" (churn-expected)" if d["umbrella"] else ""), str(d["baseline"]),
                     str(d["current"]), f"{delta:+.2f}%", f"{args.slack:g}%",
                     f"{args.slack - delta:+.2f}pp", status])
    # Name the configuration: the same compiler at a different -std produces different counts, so a
    # table without it looks like it contradicts the measurement fleet's numbers.
    lines = [f"### instantiation counts - `{config_key(tc)}`", ""]
    lines += markdown_table(["workflow", "baseline", "current", "delta", "limit", "headroom", ""], rows)
    lines += ["", f"median across non-umbrella workflows: **{median:+.2%}** against a "
                  f"{args.median_alarm:g}% alarm ({args.median_alarm - median * 100:+.2f}pp headroom)",
              "", "`headroom` is how much further a workflow could grow before it fails: negative means "
              "it already has. A re-record resets every headroom to the full band, which is why "
              "`bench.py update --workflows <filters>` exists - it moves only what you name.", ""]
    text = "\n".join(lines)
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(text + "\n")


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
    gate_summary_table(details, median, args, tc)
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
           ("peak_mib", "peak compiler memory (MiB, best of K)"),
           ("mib_on_disk", "BMI size on disk (MiB)"))


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
    metrics = {metric: {} for metric, _ in METRICS}
    for ref in refs:
        for name, entry in sorted(counts[ref].items()):
            metrics["instantiations"].setdefault(name, {})[ref] = "FAIL" if entry == "FAIL" else total(entry)
        for name, entry in sorted(timed[ref].items()):
            # None means the workflow does not apply to this ref (version floor); "FAIL" means it
            # applies and did not compile. Collapsing both into n/a hides a real failure.
            for metric, field in (("time_ms", "ms"), ("peak_mib", "peak_mib"),
                                  ("mib_on_disk", "mib_on_disk")):
                value = entry.get(field) if isinstance(entry, dict) else entry
                if value is not None or metric != "mib_on_disk":
                    metrics[metric].setdefault(name, {})[ref] = value
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


def counterparts(columns, candidates):
    """Map a configuration to the PLAIN build of the same compiler - no modules, no `import std`.

    Always that one, never a partially-stripped intermediate: a modules delta measured against the
    `import std` column would be incremental, and "modules are 60% cheaper" has to mean cheaper than
    how the library is consumed today, not cheaper than one step along the way. If the plain build
    was not measured in the same run, the cell shows no delta rather than a misleading one."""
    plain = {}
    for col in columns:
        stripped = col.replace("-modules", "").replace("-importstd", "")
        if stripped != col and stripped in candidates:
            plain[col] = stripped
    return plain


def fmt_against(value, reference):
    """The value, and how it compares with the same measurement in another configuration."""
    if not isinstance(value, (int, float)) or not isinstance(reference, (int, float)) or not reference:
        return fmt_value(value)
    return f"{fmt_value(value)} ({(value - reference) / reference:+.0%})"


def markdown_table(header, rows):
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    lines = ["| " + " | ".join(h.ljust(w) for h, w in zip(header, widths)) + " |",
             "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for row in rows:
        lines.append("| " + row[0].ljust(widths[0]) + " | "
                     + " | ".join(str(c).rjust(w) for c, w in zip(row[1:], widths[1:])) + " |")
    return lines


BMI_ORDER = ("bmi/std", "bmi/mp_units.core", "bmi/mp_units.systems", "bmi/mp_units.utility",
             "bmi/mp_units")
# How a metric's total row is formed: peak memory is a high-water mark, not something to add up.
TOTALS = {"time_ms": sum, "mib_on_disk": sum, "instantiations": sum, "peak_mib": max}


def metric_table(by_workflow, rows_wanted, columns, refs, totals_metric=None, labels=None, against=None):
    """One table: a row per entry, a column per configuration. With exactly two refs the cell
    carries the change, so a comparison is read rather than computed across columns."""
    present = [c for c in columns if any(c in by_workflow.get(r, {}) or
                                         any(k[0] == c for k in by_workflow.get(r, {})) for r in rows_wanted)]
    if not present:
        return []
    labels = labels or {c: c for c in present}
    header = ["workflow" if not rows_wanted or not rows_wanted[0].startswith("bmi/") else "interface"]
    if len(refs) == 2:
        old, new_ = refs
        header += [labels[c] for c in present]
        rows = [[name.removeprefix("bmi/"),
                 *[fmt_change(by_workflow[name].get((c, old)), by_workflow[name].get((c, new_)))
                   for c in present]] for name in rows_wanted]
        if totals_metric:
            rows.append([totals_label(totals_metric),
                         *[fmt_change(combine_totals(totals_metric, by_workflow, rows_wanted, c, old),
                                      combine_totals(totals_metric, by_workflow, rows_wanted, c, new_))
                           for c in present]])
    else:
        pairs = [(c, ref) for c in present for ref in refs
                 if any((c, ref) in by_workflow.get(r, {}) for r in rows_wanted)]
        header += [f"{labels[c]} @ {ref}" if len(refs) > 1 else labels[c] for c, ref in pairs]
        rows = []
        for name in rows_wanted:
            cells = []
            for col, ref in pairs:
                value = by_workflow[name].get((col, ref))
                other = by_workflow[name].get(((against or {}).get(col), ref))
                cells.append(fmt_against(value, other))
            rows.append([name.removeprefix("bmi/"), *cells])
        if totals_metric:
            rows.append([totals_label(totals_metric),
                         *[fmt_value(combine_totals(totals_metric, by_workflow, rows_wanted, c, ref))
                           for c, ref in pairs]])
    return markdown_table(header, rows)


def shorten_labels(keys):
    """Strip the trailing tokens every column shares - in a modules section, repeating
    `-modules-importstd` on every header buys nothing and costs width."""
    parts = [k.split("-") for k in keys]
    shared = 0
    while all(len(p) > shared + 1 and p[-1 - shared] == parts[0][-1 - shared] for p in parts):
        shared += 1
    labels = {k: "-".join(p[:len(p) - shared]) for k, p in zip(keys, parts)}
    suffix = "-".join(parts[0][len(parts[0]) - shared:]) if shared else ""
    return labels, suffix


def totals_label(metric):
    return "**peak of all**" if TOTALS.get(metric, sum) is max else "**total**"


def combine_totals(metric, by_workflow, rows_wanted, column, ref):
    """Interfaces are built once and shared, so their sum is the number that matters - except peak
    memory, which is a high-water mark and does not add up."""
    combine = TOTALS.get(metric, sum)
    values = [by_workflow.get(r, {}).get((column, ref)) for r in rows_wanted]
    numeric = [v for v in values if isinstance(v, (int, float))]
    return round(combine(numeric), 1) if numeric else None


def render_report(payloads):
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

    module_keys = [k for k in keys if "-modules" in k]
    header_keys = [k for k in keys if "-modules" not in k]
    workflows = sorted({n for m in cells.values() for n in m if not n.startswith("bmi/")})
    interfaces = [n for n in BMI_ORDER if any(n in m for m in cells.values())]

    lines = []
    for metric, title in METRICS:
        by_workflow = cells.get(metric, {})
        if not by_workflow or not header_keys:
            continue
        for family in (*FAMILIES, "other"):
            cols = sorted([k for k in header_keys if family_of(k) == family],
                          key=lambda k: (version_of(k), k))
            rows = [w for w in workflows if any((c, r) in by_workflow.get(w, {}) for c in cols for r in refs)]
            against = counterparts(cols, header_keys) if len(refs) == 1 else None
            table = metric_table(by_workflow, rows, cols, refs, None, None, against) if rows else []
            if table:
                lines += [f"### {title} - {family}" if family != "other" else f"### {title}", "", *table, ""]

    if module_keys:
        lines += ["## C++20 modules", "",
                  "Building the module interfaces is a cost every consumer of a configuration shares, so it is",
                  "reported here in full rather than folded into the consumer numbers below it. Total cost of a",
                  "configuration is the interface build (once) plus its consumers.", ""]
        cols = sorted(module_keys, key=lambda k: (version_of(k), k))
        labels, suffix = shorten_labels(cols)
        if suffix:
            lines += [f"Every column below is `{suffix}`; the headers name only what differs.", ""]
        for metric, title in METRICS:
            by_workflow = cells.get(metric, {})
            rows = [i for i in interfaces if i in by_workflow]
            table = metric_table(by_workflow, rows, cols, refs, metric, labels) if rows else []
            if table:
                lines += [f"### module interfaces - {title}", "", *table, ""]
        # Each modules column is read against the same compiler's header build, when that was
        # measured in the same run: "how much cheaper is this TU as a module consumer".
        against = counterparts(cols, header_keys)
        if against and len(refs) == 1:
            lines += ["In brackets: change against the same compiler's plain build - headers, no "
                      "`import std` - so the number says what modules are worth against how the "
                      "library is consumed today, not against an intermediate configuration. Consumer "
                      "cost only: the interface build above is paid once per configuration, not per "
                      "translation unit.", ""]
        for metric, title in METRICS:
            by_workflow = cells.get(metric, {})
            rows = [w for w in workflows if any((c, r) in by_workflow.get(w, {}) for c in cols for r in refs)]
            table = metric_table(by_workflow, rows, cols, refs, None, labels,
                                 against if len(refs) == 1 else None) if rows else []
            if table:
                lines += [f"### module consumers - {title}", "", *table, ""]

    if not any(line.startswith("###") for line in lines):
        return "no measurements to report"
    legend = ["- A percentage in brackets is the change against the same compiler's PLAIN build"
              " (headers, no `import std`), never against an intermediate configuration; it is absent"
              " when that plain build was not measured in the same run.",
              "- **n/a** - the workflow does not apply to that ref: its `// REQUIRES:` floor (library"
              " version or language standard) is newer.",
              "- **FAIL** - the workflow applies to that ref but did not compile."]
    if len(refs) == 2:
        legend.insert(0, f"- Cells read `{refs[0]} -> {refs[1]} (change)`, oldest library version first.")
    lines = [*legend, "", *lines]
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
    p.add_argument("--modules", action="store_true",
                   help="consume mp-units as C++20 modules (builds the BMIs first and reports what "
                        "that cost); implies the corpus's MP_UNITS_MODULES branch")
    p.add_argument("--import-std", action="store_true",
                   help="use `import std;` instead of standard library headers (builds the std "
                        "module first)")
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
