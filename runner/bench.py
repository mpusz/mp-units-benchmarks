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
import collections
import hashlib
import json
import os
import platform
import re
import statistics
import struct
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
            # Instantiations are frontend work. Three more deterministic numbers come free from the
            # same compile and cover what they cannot see: constant evaluation (which a constexpr
            # implementation trades instantiations for), and the object file split into the code that
            # reaches the binary versus the symbol metadata that does not - see object_sizes().
            counts = {"InstantiateClass": 0, "InstantiateFunction": 0, "EvaluateAsConstantExpr": 0}
            for e in data["traceEvents"]:
                if e.get("ph") == "X" and e["name"] in counts:
                    counts[e["name"]] += 1
            counts.update(object_sizes(out))
            results[name] = counts
    return results


def object_sizes(obj: Path) -> dict:
    """Split an object file into the two costs that look identical on disk but have nothing in
    common.

    `code_bytes` is every SHF_ALLOC section - the machine code, constants and unwind tables that
    actually reach the binary. `symbol_bytes` is the rest of the file: symbol table, string table and
    relocations. For text/output_format the split is 142 KiB of code against 289 KiB of metadata -
    two thirds of the file is mangled names, 662 symbols averaging 101 characters. They deserve
    separate rows because they have separate remedies: code shrinks by instantiating fewer copies of
    the write path, metadata shrinks by keeping details out of the object's symbol table.

    ELF is parsed here rather than shelled out to llvm-size, which is not installed on every runner
    that has clang. A non-ELF or truncated file reports None rather than a wrong number.
    """
    blank = {"object_bytes": None, "code_bytes": None, "symbol_bytes": None}
    try:
        raw = obj.read_bytes()
    except OSError:
        return blank
    total = len(raw)
    if raw[:4] != b"\x7fELF" or raw[4] != 2:  # 2 = ELFCLASS64
        return {**blank, "object_bytes": total}
    (shoff,) = struct.unpack_from("<Q", raw, 0x28)
    shentsize, shnum = struct.unpack_from("<HH", raw, 0x3A)
    if not shoff or shoff + shentsize * shnum > total:
        return {**blank, "object_bytes": total}
    code = 0
    for i in range(shnum):
        sh_type, sh_flags, _addr, _off, sh_size = struct.unpack_from("<4xIQQQQ", raw, shoff + i * shentsize)
        if sh_flags & 0x2 and sh_type != 8:  # SHF_ALLOC, and not SHT_NOBITS (occupies no file space)
            code += sh_size
    return {"object_bytes": total, "code_bytes": code, "symbol_bytes": total - code}


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
    """class + function instantiations; "FAIL" when it did not compile, None when it is n/a.

    Those two must stay distinguishable all the way to the table. A workflow excluded by its REQUIRES
    floor is expected; one that applies and fails to build is a finding, and rendering both as `n/a`
    hides it - the REQUIRES floor is per minor version, so a workflow can pass the floor and still fail
    against a mid-development ref of that same version."""
    if isinstance(entry, dict):
        return entry["InstantiateClass"] + entry["InstantiateFunction"]
    return "FAIL" if entry == "FAIL" else None


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
        print_table(["inst_class", "inst_func", "const_eval", "code_B", "syms_B"],
                    [(n, *[v[k] if isinstance(v, dict) else v
                           for k in ("InstantiateClass", "InstantiateFunction", "EvaluateAsConstantExpr",
                                     "code_bytes", "symbol_bytes")])
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
            if all(isinstance(v, int) for v in (totals[0], totals[-1])) and totals[0]:
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


# Every gated number must be bit-deterministic for a pinned compiler AND large enough that a percentage
# band means something. Instantiations are frontend work; constant evaluations are what a constexpr
# implementation trades them for; emitted code is what the optimizer's time tracks.
#
# The third field is a MINIMUM ABSOLUTE MOVEMENT, required on top of the percentage band. Without it a
# percentage on a three-digit number is noise: `code_bytes` has a median of 145 across the corpus, so a
# 1% band resolves to 1.4 bytes and a single instruction trips the gate. A helper that stops folding away
# - the thing this metric exists to catch - moves it by thousands, so the floor costs no sensitivity.
#
# Two metrics are measured and reported but deliberately NOT gated:
#   - peak memory, which correlates 0.97 with instantiations and would only fire when they already had.
#   - `symbol_bytes` (mangled names, string table, relocations). It is linker input and error-message
#     length, not compile-time cost, and its median is 1477 bytes - so adding one symbol name (~40-48
#     bytes) reads as +3%. It once failed 52 workflows on a change that cost +0.08% instantiations, which
#     is the definition of a metric that punishes growth rather than slowness.
GATED = (("instantiations", lambda e: e["InstantiateClass"] + e["InstantiateFunction"], 0),
         ("constant evaluations", lambda e: e.get("EvaluateAsConstantExpr"), 0),
         ("emitted code (bytes)", lambda e: e.get("code_bytes"), 512))


def baseline_deltas(baseline, results, extract):
    """Per-workflow baseline/current/relative-delta for every comparable entry.

    A metric missing from either side is skipped rather than assumed: baselines recorded before a
    metric existed must not read as a change."""
    details = {}
    for name, base in sorted(baseline.items()):
        cur = results.get(name)
        if not isinstance(cur, dict) or not isinstance(base, dict):
            continue  # n/a, FAIL, or a baseline entry whose workflow is gone
        b, c = extract(base), extract(cur)
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)) or not b:
            continue
        details[name] = {"baseline": b, "current": c, "rel": (c - b) / b, "abs": c - b,
                         "umbrella": name.startswith("umbrella/")}
    return details


def slope_from(entries, extract):
    """Marginal cost of one more step, per shape, from a flat {workflow: entry} mapping.

    This is the number a gate on totals cannot see properly. Adding a feature to the library lifts every
    workflow's total by a similar small amount; making the library slower per unit of user code lifts the
    slope. Gating only totals therefore blocks growth and under-reacts to regression - and the slope is
    only ~62% of `broad_256`'s total, so even that workflow dilutes a slope move by a third."""
    out = {}
    for shape in ("narrow", "broad"):
        sizes = {}
        for name, entry in entries.items():
            m = re.fullmatch(rf"scaling/{shape}_(\d+)", name)
            if not m or not isinstance(entry, dict):
                continue
            value = extract(entry)
            if isinstance(value, (int, float)):
                sizes[int(m.group(1))] = value
        if len(sizes) >= 2:
            lo, hi = min(sizes), max(sizes)
            out[shape] = (sizes[hi] - sizes[lo]) / (hi - lo)
    return out


def slope_deltas(baseline, results, extract):
    """Per-shape baseline/current/relative-delta for the scaling slopes, skipping shapes absent
    from either side so a baseline predating the series does not read as change."""
    base, cur = slope_from(baseline, extract), slope_from(results, extract)
    out = {}
    for shape in sorted(base):
        if shape in cur and base[shape]:
            out[shape] = {"baseline": base[shape], "current": cur[shape],
                          "rel": (cur[shape] - base[shape]) / base[shape]}
    return out


def assert_same_config(recorded, tc: Toolchain, where):
    """Numbers from another configuration are not a baseline, they are a different measurement."""
    for field, current in (("cxx", tc.cxx), ("std", tc.std), ("stdlib", tc.standard_library),
                           ("config_label", tc.label), ("extra_flags", " ".join(tc.extra.split()))):
        was = recorded.get(field)
        if was is not None and was != current:
            sys.exit(f"{where} was recorded with {field}={was!r}, this run uses {current!r}; "
                     f"counts are only comparable within one configuration")


def gate_slope_table(slopes, band, tc: Toolchain):
    """The marginal-cost table. Separate from the per-workflow one because it answers a different
    question: not "did this workflow grow" but "did one more line of user code get more expensive"."""
    rows = [[shape, f"{d['baseline']:.1f}", f"{d['current']:.1f}", f"{d['rel'] * 100:+.2f}%",
             f"{band * 100:g}%", f"{(band - d['rel']) * 100:+.2f}pp",
             "FAILS" if d["rel"] > band else "ok"]
            for shape, d in sorted(slopes.items())]
    lines = [f"### marginal cost per step - `{config_key(tc)}`", ""]
    lines += markdown_table(["shape", "baseline", "current", "delta", "limit", "headroom", ""], rows)
    lines += ["", "Instantiations per step, from the `scaling/` series. This is the number that separates "
              "*the library grew* from *the library got slower*: adding a feature lifts every workflow's "
              "total by a similar small amount, while a real regression lifts the slope. `narrow` reuses "
              "quantity types, `broad` composes a new derived unit per step.", ""]
    text = "\n".join(lines)
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(text + "\n")


def gate_summary_table(details, median, args, tc: Toolchain, label="instantiations"):
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
    lines = [f"### {label} - `{config_key(tc)}`", ""]
    lines += markdown_table(["workflow", "baseline", "current", "delta", "limit", "headroom", ""], rows)
    lines += ["", (f"median across non-umbrella workflows: **{median:+.2%}** against a "
                   f"{args.median_alarm:g}% alarm ({args.median_alarm - median * 100:+.2f}pp headroom)"
                   if median is not None else
                   f"gated at the same {args.slack:g}% band as instantiations; the median alarm "
                   f"applies to instantiations only"),
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
    slope_band = (args.slope_slack if args.slope_slack is not None else args.slack) / 100
    measured = measure_counts(repo, tc)
    per_metric = {label: baseline_deltas(baseline, measured, extract) for label, extract, _ in GATED}
    floors = {label: floor for label, _, floor in GATED}
    details = per_metric["instantiations"]

    def past(d, label, band):
        """Both the percentage band and the metric's absolute floor have to be exceeded."""
        return d["rel"] > band and abs(d["abs"]) >= floors[label]

    regressions = {f"{n} [{label}]": {**d, "metric": label} for label, m in per_metric.items()
                   for n, d in m.items() if past(d, label, slack)}
    improvements = {f"{n} [{label}]": {**d, "metric": label} for label, m in per_metric.items()
                    for n, d in m.items() if d["rel"] < -notice and abs(d["abs"]) >= floors[label]}
    # Growth inside the blocking band but past the advisory one: reported, never fatal. This is how
    # a loose gate can still say "this grew" without stopping the change.
    advisory = {f"{n} [{label}]": {**d, "metric": label} for label, m in per_metric.items() for n, d in m.items()
                if advisory_band is not None and past(d, label, advisory_band) and not past(d, label, slack)}
    deltas = [d["rel"] for d in details.values() if not d["umbrella"]]
    median = statistics.median(deltas) if deltas else 0.0
    inst = GATED[0][1]
    slopes = slope_deltas(baseline, measured, inst)
    slope_regressions = {shape: d for shape, d in slopes.items() if d["rel"] > slope_band}
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(
            {"mp_units_version": ".".join(map(str, detect_version(repo))), **git_provenance(repo),
             "cxx": args.cxx, "std": tc.std, "baseline_key": baseline_file.stem.split("instantiations-")[-1],
             "bands": {"slack": args.slack, "median_alarm": args.median_alarm,
                       "tighten_notice": args.tighten_notice, "advisory_slack": args.advisory_slack,
                       "slope_slack": args.slope_slack if args.slope_slack is not None else args.slack},
             "median_non_umbrella": median, "slopes": slopes,
             "slope_regressions": list(slope_regressions), "regressions": list(regressions),
             "improvements": list(improvements), "advisory": list(advisory),
             "workflows": details,
             "metrics": {label: m for label, m in per_metric.items()}}, indent=2) + "\n")
    for label, m in per_metric.items():
        if m:
            gate_summary_table(m, median if label == "instantiations" else None, args, tc, label)
    if slopes:
        gate_slope_table(slopes, slope_band, tc)
    # One annotation, not one per workflow. Fifty-two identical ::error:: lines bury the finding they are
    # reporting, and the per-metric table above already carries every delta with its headroom - so the
    # annotation's job is only to name the worst case and say what to do about it.
    if regressions:
        worst = max(regressions.items(), key=lambda kv: kv[1]["rel"])
        name, d = worst
        by_metric = collections.Counter(v["metric"] for v in regressions.values())
        spread = ", ".join(f"{n} {m}" for m, n in by_metric.most_common())
        gate_summary_line(
            f"{len(regressions)} regression(s) past the {args.slack:g}% band ({spread}); worst is "
            f"{name} {d['baseline']} -> {d['current']} ({d['rel']:+.1%}). See the table above for all of "
            f"them; if intentional, run bench.py update and commit the new baselines in this PR", "error")
    for shape, d in slope_regressions.items():
        gate_summary_line(
            f"marginal cost regression: the {shape} slope went {d['baseline']:.1f} -> {d['current']:.1f} "
            f"instantiations per step ({d['rel']:+.1%}, band {slope_band:.0%}). Every translation unit that "
            f"introduces units pays this, and unlike a total it cannot be explained by the library growing",
            "error")
    if median > alarm:
        gate_summary_line(f"framework-wide regression: median instantiation growth {median:+.1%} "
                          f"across all workflows - this should almost never be rebaselined away", "error")
    if advisory:
        worst = max(advisory.items(), key=lambda kv: kv[1]["rel"])
        gate_summary_line(
            f"{len(advisory)} workflow(s) grew past the {args.advisory_slack:g}% advisory band but within "
            f"the {args.slack:g}% blocking one; worst is {worst[0]} ({worst[1]['rel']:+.1%}). Not fatal "
            f"here, but the benchmarks repo gates tighter and will go red on it", "warning")
    if improvements:
        best = min(improvements.items(), key=lambda kv: kv[1]["rel"])
        gate_summary_line(
            f"{len(improvements)} workflow(s) improved past the {args.tighten_notice:g}% notice band; best "
            f"is {best[0]} ({best[1]['rel']:+.1%}) - baselines can be tightened, run bench.py update in a "
            f"follow-up PR", "warning")
    if not regressions and not slope_regressions and median <= alarm:
        msg = f"compile-cost gate OK (median instantiation delta {median:+.1%})"
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
           ("const_evals", "compile-time constant evaluations (what constexpr code costs instead)"),
           ("code_bytes", "emitted code (bytes reaching the binary - what the optimizer's time tracks)"),
           ("symbol_bytes", "symbol metadata (bytes of mangled names and relocations - linker input)"),
           ("time_ms", "wall time (ms, best of K - only trustworthy on a quiet machine)"),
           ("peak_mib", "peak compiler memory (MiB, best of K)"),
           ("mib_on_disk", "BMI size on disk (MiB)"))


def trace_entities(repo: Path, tc: Toolchain, source: Path, ctx: BuildContext, workdir: Path):
    """Instantiation events grouped by the entity that was instantiated, from one traced compile.

    `counts` answers how many; this answers WHICH, which is what turns "3% slower" into a name.
    Template arguments are collapsed - `quantity<metre, double>` and `quantity<second, int>` are one
    entity - because the interesting unit is the template, not each specialization of it."""
    out = workdir / "attr.o"
    run(compile_cmd(tc, repo, out, source, trace=True, ctx=ctx), cwd=ctx.cwd)
    events = json.loads((workdir / "attr.json").read_text())["traceEvents"]
    tally = {}
    for e in events:
        if e.get("ph") == "X" and e["name"] in ("InstantiateClass", "InstantiateFunction"):
            entity = re.sub(r"<.*", "<>", e.get("args", {}).get("detail", "(unnamed)"))
            tally[entity] = tally.get(entity, 0) + 1
    return tally


def cmd_attribute(args):
    """Diff two measurements by entity, to answer why one costs more than the other.

    Two shapes, because there are two questions. One workflow across two refs: what did a library
    change make more expensive. One ref across two workflows: what does the bigger one instantiate
    that the smaller does not - which is how a scaling series' slope gets attributed."""
    refs = args.refs or ["WORKTREE"]
    repos = materialize(args, refs)
    tc = toolchain(args)
    if not tc.is_clang:
        sys.exit("attribution reads clang's -ftime-trace; another compiler cannot produce it")
    selections = {ref: select_workflows(detect_version(repos[ref]), args.workflows, tc.std) for ref in refs}
    # Oldest library version first, so the delta reads as "what changed on the way to the newer one"
    # rather than depending on the order the refs happened to be typed in.
    ordered = sorted(refs, key=lambda r: tuple(detect_version(repos[r])))
    pairs = [(ref, name, src) for ref in ordered for name, src in sorted(selections[ref].items()) if src]
    if len(pairs) != 2:
        sys.exit(f"attribution compares exactly two measurements, got {len(pairs)}: give either two "
                 f"refs and one workflow, or one ref and two workflows")

    with tempfile.TemporaryDirectory() as tmp:
        tallies, labels = [], []
        for ref, name, source in pairs:
            ctx = build_modules(repos[ref], tc, Path(tmp) / f"bmi-{ref.replace('/', '_')}")
            tallies.append(trace_entities(repos[ref], tc, source, ctx, Path(tmp)))
            # A 40-character sha as a column header makes the table unreadable.
            shown = ref[:9] if len(ref) >= 20 and all(c in "0123456789abcdef" for c in ref) else ref
            labels.append(name if len(refs) == 1 else f"{name} @ {shown}")

    left, right = tallies
    rows, total = [], sum(right.values()) - sum(left.values())
    for entity in sorted(set(left) | set(right), key=lambda k: -(right.get(k, 0) - left.get(k, 0))):
        a, b = left.get(entity, 0), right.get(entity, 0)
        if abs(b - a) >= args.min_delta:
            rows.append([entity, str(a), str(b), f"{b - a:+d}"])
    print(f"### what accounts for the difference: {labels[0]} vs {labels[1]}", "")
    print(f"\nTotal instantiations {sum(left.values())} -> {sum(right.values())} ({total:+d}). Entities "
          f"below are templates with their arguments collapsed, ranked by how much they moved.\n")
    if not rows:
        print(f"No entity moved by at least {args.min_delta} instantiation(s): the two measurements "
              f"instantiate the same templates the same number of times.")
        return
    print("\n".join(markdown_table(["entity", labels[0], labels[1], "delta"], rows[:args.top])))
    if len(rows) > args.top:
        print(f"\n{len(rows) - args.top} further entities moved by at least {args.min_delta}.")


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
            if isinstance(entry, dict):
                metrics["const_evals"].setdefault(name, {})[ref] = entry.get("EvaluateAsConstantExpr")
                for key in ("code_bytes", "symbol_bytes"):
                    metrics[key].setdefault(name, {})[ref] = entry.get(key)
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


def config_shape(key):
    """The configuration with the compiler VERSION removed. Two keys of the same shape differ ONLY in
    the toolchain, which is the only pair whose difference the compiler explains - across shapes a
    stdlib or formatting-backend swap would be attributed to the compiler."""
    return re.sub(r"^([a-z]+)\d+", r"\1", key)


def config_order(key):
    """The order every table in the report uses: header builds before module consumers, then family,
    then compiler version - never the order the measurement artifacts happened to arrive in."""
    family = family_of(key)
    return ("-modules" in key, FAMILIES.index(family) if family in FAMILIES else len(FAMILIES),
            version_of(key), key)


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


def scaling_fit(by_workflow, column, ref):
    """Slope and intercept per shape from the scaling/ series: the marginal cost of one more step,
    and the constant cost of pulling the library in. The whole point of the series is that these two
    move independently - a release can improve the intercept while making the slope worse."""
    fits = {}
    for shape in ("narrow", "broad"):
        sizes = {}
        for name, row in by_workflow.items():
            m = re.fullmatch(rf"scaling/{shape}_(\d+)", name)
            value = row.get((column, ref)) if m else None
            if isinstance(value, (int, float)):
                sizes[int(m.group(1))] = value
        if len(sizes) >= 2:
            lo, hi = min(sizes), max(sizes)
            slope = (sizes[hi] - sizes[lo]) / (hi - lo)
            fits[shape] = (slope, sizes[lo] - slope * lo)
    return fits


def scaling_section(cells, columns, refs, labels=None):
    """A table per metric: what one more operation costs, next to the constant cost."""
    labels = labels or {c: c for c in columns}
    lines = []
    for metric, title in METRICS:
        by_workflow = cells.get(metric, {})
        rows = []
        for shape in ("narrow", "broad"):
            for what, index in (("per step", 0), ("intercept", 1)):
                cols = []
                for column in columns:
                    values = [scaling_fit(by_workflow, column, ref).get(shape) for ref in refs]
                    values = [v[index] if v else None for v in values]
                    cols.append(fmt_change(values[0], values[1]) if len(refs) == 2 else fmt_value(
                        None if values[0] is None else round(values[0], 1)))
                if any(c != "n/a" for c in cols):
                    rows.append([f"{shape} - {what}", *cols])
        if rows:
            # A configuration that cannot produce this metric AT ALL (no GCC gives counts) is not a
            # workflow held back by a REQUIRES floor, which is what the legend says `n/a` means: drop
            # the column rather than print a stripe of n/a that the legend misexplains - and that
            # costs a third of the table's width in every count metric.
            keep = [i for i, c in enumerate(columns) if any(r[i + 1] != "n/a" for r in rows)]
            rows = [[r[0], *(r[i + 1] for i in keep)] for r in rows]
            present = [labels[columns[i]] for i in keep]
            lines += [f"### {title}", "", *markdown_table(["scaling", *present], rows), ""]
    return lines


# One paragraph is ONE line: GitHub renders a single newline inside a paragraph as a line break, so
# source-wrapped prose arrives at the reader ragged. Wrap in the source with implicit concatenation.
PREAMBLE = [
    "<details><summary>How to read this</summary>", "",
    "A **template instantiation** is one unit of work the compiler does when it stamps out a template "
    "for a particular set of types. Counting them is exact and machine-independent, which is why this "
    "is the number the project gates on: the same code and compiler always produce the same count, on "
    "any machine. **Peak memory** is how much RAM the compiler needed, and varies by less than 0.1% "
    "between runs, so it is trustworthy too. **Wall-clock time** is what a developer actually waits "
    "for, but it depends on the machine and its load - times measured on shared CI runners are "
    "indicative only, and are never compared between columns.",
    "",
    "Two costs are worth separating. The **constant cost** is what a file pays merely for using the "
    "library, before it does anything: including the headers, or importing the module. The **marginal "
    "cost** is what each further operation adds - and it matters more, because it is multiplied by the "
    "size of real code. A release can improve one and worsen the other, so they are reported apart.",
    "", "</details>", "",
]


def pct(old, new):
    return None if not isinstance(old, (int, float)) or not isinstance(new, (int, float)) or not old \
        else (new - old) / old


def findings(cells, keys, refs):
    """The few sentences worth reading, ranked. Everything else is in the tables below them.

    Written for someone who has never used the library: each item names the effect and what follows
    from it, rather than quoting a metric at them."""
    out = []
    inst = cells.get("instantiations", {})
    time = cells.get("time_ms", {})
    memory = cells.get("peak_mib", {})
    plain = sorted([k for k in keys if "-modules" not in k and "-importstd" not in k],
                   key=lambda k: (version_of(k), k))
    workflows = sorted(w for w in inst if not w.startswith("bmi/"))

    # 1. anything that did not build at all
    broken = sorted({f"{w} ({k})" for metric in cells.values() for w, row in metric.items()
                     for (k, _), v in row.items() if v == "FAIL"})
    if broken:
        out.append(f"**{len(broken)} measurement(s) failed to compile**, which is a finding rather than "
                   f"a gap: {', '.join(broken[:4])}{' and others' if len(broken) > 4 else ''}.")

    if len(refs) == 2 and plain:
        old, new = refs
        col = plain[-1]
        deltas = {w: pct(inst[w].get((col, old)), inst[w].get((col, new))) for w in workflows}
        moved = {w: d for w, d in deltas.items() if d is not None}
        if moved:
            median = statistics.median(moved.values())
            best = min(moved.items(), key=lambda kv: kv[1])
            worst = max(moved.items(), key=lambda kv: kv[1])
            tail = (f"The largest growth is `{worst[0]}` at {worst[1]:+.0%}." if worst[1] > 0.005
                    else "Nothing in the corpus grew.")
            out.append(f"Compiling the same code against `{new}` instead of `{old}` needs "
                       f"**{abs(median):.0%} {'fewer' if median < 0 else 'more'}** template "
                       f"instantiations for a typical workflow (median across {len(moved)}). The largest "
                       f"improvement is `{best[0]}` at {best[1]:+.0%}. {tail}")

        # 2. the finding totals cannot show: constant cost and marginal cost moving apart
        fits = [scaling_fit(inst, col, ref) for ref in (old, new)]
        if all(f.get("broad") for f in fits):
            (slope_old, base_old), (slope_new, base_new) = (f["broad"] for f in fits)
            ds, db = pct(slope_old, slope_new), pct(base_old, base_new)
            if ds is not None and db is not None and (ds > 0.05 > db or abs(ds - db) > 0.1):
                out.append(f"Constant and marginal cost moved in opposite directions: using the library "
                           f"at all became **{abs(db):.0%} {'cheaper' if db < 0 else 'dearer'}**, while "
                           f"each additional distinct unit type became **{abs(ds):.0%} "
                           f"{'dearer' if ds > 0 else 'cheaper'}** ({slope_old:.0f} -> {slope_new:.0f} "
                           f"instantiations per unit). A file using a handful of units therefore gains "
                           f"from this change, and one using many does not.")

        # 3. is this run's wall clock worth reading at all?
        control = pct(time.get("bmi/std", {}).get((col, old)), time.get("bmi/std", {}).get((col, new)))
        if control is not None and abs(control) > 0.1:
            out.append(f"Treat wall-clock numbers in this run as noise: building the standard library "
                       f"module is identical work in both columns, yet differs by {control:+.0%}. The "
                       f"instantiation counts are unaffected - they are exact.")

    # 4. what consuming the library as modules is worth - from the NEWEST toolchain that measured it,
    # since one statement is all this section gets and the oldest compiler is the least useful one to
    # spend it on.
    for col in sorted([k for k in keys if "-modules" in k], key=lambda k: (version_of(k), k), reverse=True):
        base = counterparts([col], keys).get(col)
        if not base:
            continue
        ref = refs[-1]
        t = [pct(time[w].get((base, ref)), time[w].get((col, ref))) for w in workflows if w in time]
        m = [pct(memory[w].get((base, ref)), memory[w].get((col, ref))) for w in workflows if w in memory]
        t, m = [x for x in t if x is not None], [x for x in m if x is not None]
        build = combine_totals("time_ms", time, [i for i in BMI_ORDER if i in time], col, ref)
        disk = combine_totals("mib_on_disk", cells.get("mib_on_disk", {}),
                              [i for i in BMI_ORDER if i in cells.get("mib_on_disk", {})], col, ref)
        if t and m:
            out.append(f"Consuming the library as C++20 modules (`{col}`) compiles each file "
                       f"**{abs(statistics.median(t)):.0%} {'faster' if statistics.median(t) < 0 else 'slower'}** "
                       f"and uses **{abs(statistics.median(m)):.0%} "
                       f"{'more' if statistics.median(m) > 0 else 'less'} memory**"
                       + (f", after building the module interfaces once: {build / 1000:.0f} s"
                          f"{f' and {disk:.0f} MiB on disk' if disk else ''}." if build else "."))
        break  # one such statement is enough; the section below has the rest

    # 5. how much of any improvement is really the compiler - only across keys of the same SHAPE, so a
    # stdlib or formatting-backend swap in the pair is never attributed to the toolchain.
    shapes = {}
    for k in plain:
        if family_of(k) == "clang":
            shapes.setdefault(config_shape(k), []).append(k)
    group = max(shapes.values(), key=len, default=[])
    if len(group) >= 2 and len(refs) == 1:
        lo, hi = group[0], group[-1]  # plain is version-sorted, so this is oldest vs newest
        d = [pct(inst[w].get((lo, refs[0])), inst[w].get((hi, refs[0]))) for w in workflows]
        d = [x for x in d if x is not None]
        median = statistics.median(d) if d else 0
        if d and abs(median) > 0.02:
            out.append(f"The compiler matters as much as the library: `{hi}` needs "
                       f"**{abs(median):.0%} {'fewer' if median < 0 else 'more'}** instantiations than "
                       f"`{lo}` for identical code, so "
                       + ("part of what users experience as the library improving is their toolchain "
                          "improving." if median < 0 else
                          "a newer toolchain is not automatically a cheaper one, and any comparison of "
                          "library versions has to pin the compiler."))

    # 6. n/a is not failure - but only count real gaps: a configuration that produces no counts at
    # all (any GCC) is not a workflow being skipped, and bmi/ rows exist only under modules.
    gaps = 0
    for metric, per_workflow in cells.items():
        measured = {(k, ref) for row in per_workflow.values() for (k, ref) in row}
        for name, row in per_workflow.items():
            if name.startswith("bmi/"):
                continue
            gaps += sum(1 for pair in measured if pair not in row)
    if gaps:
        out.append(f"{gaps} cell(s) below are `n/a`: the workflow does not apply to that library "
                   f"version or language standard (a declared floor), not a failure.")
    return out


def run_over_run(payloads, previous):
    """What moved since the last CI run, per configuration - the concrete answer to "how much did we
    gain by the last changes", computed instead of asserted.

    Arms are matched by configuration key; each arm's newest measured ref is compared against the
    previous run's newest, because that pair is "the tree CI watched then" vs "the tree it watches
    now". Refs are named in the output so an unchanged tree reads as what it is - a control."""
    prev_by_key = {p.get("config_key") or p["cxx"]: p for p in previous}
    lines, movers, unmatched, skipped = [], [], 0, set()
    for p in sorted(payloads, key=lambda payload: config_order(payload.get("config_key") or payload["cxx"])):
        key = p.get("config_key") or p["cxx"]
        q = prev_by_key.get(key)
        if q is None:
            skipped.add(key)
            continue
        def newest(payload):
            refs = refs_oldest_first([payload])
            return refs[-1] if refs else None
        rn, ro = newest(p), newest(q)
        if rn is None or ro is None:
            skipped.add(key)
            continue
        def corpus(metric):
            """Totals over the workflows BOTH runs measured, so adding or removing one cannot read as
            the library moving. Returns the pair and how many entries only one run had."""
            then_per, now_per = q["metrics"].get(metric, {}), p["metrics"].get(metric, {})
            names = {n for n in set(then_per) | set(now_per) if not n.startswith("bmi/")}
            pairs = {n: (then_per.get(n, {}).get(ro), now_per.get(n, {}).get(rn)) for n in names}
            both = [(a, b) for a, b in pairs.values()
                    if isinstance(a, (int, float)) and isinstance(b, (int, float))]
            if not both:
                return None
            return sum(a for a, _ in both), sum(b for _, b in both), len(names) - len(both)
        def slope(payload, ref):
            """Instantiations per step, and nothing else. This column sits beside the deterministic
            ones: a wall-time slope here would read as the same kind of number while carrying runner
            noise - which is how a GCC arm with no counts came to report a 13% "slope" move."""
            sizes = {}
            for name, by_ref in payload["metrics"].get("instantiations", {}).items():
                m = re.fullmatch(r"scaling/broad_(\d+)", name)
                v = by_ref.get(ref) if m else None
                if isinstance(v, (int, float)):
                    sizes[int(m.group(1))] = v
            if len(sizes) < 2:
                return None
            lo, hi = min(sizes), max(sizes)
            return (sizes[hi] - sizes[lo]) / (hi - lo)
        row = {"key": key,
               "then": q["refs"][ro].get("mp_units_describe", ro), "now": p["refs"][rn].get("mp_units_describe", rn)}
        for metric, name in (("instantiations", "inst"), ("time_ms", "time")):
            got = corpus(metric)
            if got and got[0]:
                a, b, gaps = got
                row[name] = (a, b, (b - a) / a)
                unmatched = max(unmatched, gaps)
        sa, sb = slope(q, ro), slope(p, rn)
        if isinstance(sa, float) and isinstance(sb, float) and sa:
            row["slope"] = (sa, sb, (sb - sa) / sa)
        if "inst" in row or "time" in row:
            lines.append(row)
            if "inst" in row:
                movers.append(row)
    if not lines:
        return None, []
    md = ["## Since the previous run", ""]
    md += markdown_table(
        ["configuration", "measured then -> now", "instantiations", "broad slope", "wall time"],
        [[r["key"], f"`{r['then']}` -> `{r['now']}`",
          f"{r['inst'][0]} -> {r['inst'][1]} ({r['inst'][2]:+.1%})" if "inst" in r else "n/a",
          f"{r['slope'][0]:.1f} -> {r['slope'][1]:.1f} ({r['slope'][2]:+.1%})" if "slope" in r else "n/a",
          f"{r['time'][0]} -> {r['time'][1]} ms ({r['time'][2]:+.1%})" if "time" in r else "n/a"]
         for r in lines])
    # `n/a` here is not a REQUIRES floor: it is a configuration that cannot produce the metric at all,
    # and saying so is the difference between "GCC gives no counts" and "something failed".
    md += ["", "Corpus totals and slopes over the workflows both runs measured (`bmi/*` excluded); "
           "`n/a` in a deterministic column means the configuration produces no counts (any GCC), not "
           "that a measurement is missing. Both count columns are instantiations - the slope is "
           "instantiations per step. Wall time compares different runner sessions, so treat its column "
           "as direction only - the deterministic columns are the finding."
           + (f" {unmatched} workflow(s) were measured by only one of the two runs and are excluded from "
              f"every column." if unmatched else "")
           + (f" {len(skipped)} configuration(s) have nothing to compare against - "
              f"{', '.join(f'`{k}`' for k in sorted(skipped, key=config_order))} "
              f"{'was' if len(skipped) == 1 else 'were'} not measured by the previous run." if skipped else ""),
           ""]
    finding = None
    if movers:
        # By key, never on the tuple: two arms that tie on the delta - which is what an unchanged tree
        # measured twice produces - would have max() fall through to comparing the row dicts and raise.
        r = max(movers, key=lambda row: abs(row["inst"][2]))
        rel = r["inst"][2]
        counted, quiet = len(movers), len(lines) - len(movers)
        no_counts = (f" (the {quiet} configuration(s) without counts are compared on wall time only, which "
                     f"this section does not treat as a finding)" if quiet else "")
        moved = max((x for x in lines if "slope" in x), key=lambda x: abs(x["slope"][2]), default=None)
        worst = moved["slope"] if moved else None
        if abs(rel) < 0.001 and (worst is None or abs(worst[2]) < 0.001):
            finding = (f"Nothing moved since the previous run: on all {counted} configuration(s) that produce "
                       f"deterministic counts, both the corpus total and the marginal cost per step are within "
                       f"0.1% of last time, so the tables below describe the same library state{no_counts}.")
        elif abs(rel) < 0.001:
            # Totals flat while the slope moves is the case the totals CANNOT show: the slope is a
            # fraction of any one file's cost, and it is the part that multiplies with real code.
            finding = (f"Every corpus total is within 0.1% of the previous run, but the marginal cost of a "
                       f"unit-composing line moved: {worst[0]:.1f} -> {worst[1]:.1f} instantiations per step "
                       f"({worst[2]:+.1%}) on `{moved['key']}`. Totals hide a slope move because the slope is "
                       f"only a part of any one file's cost - it is the part that grows with the size of "
                       f"real code.")
        else:
            direction = "cheaper" if rel < 0 else "more expensive"
            finding = (f"Since the previous run (`{r['then']}` -> `{r['now']}`), the corpus got "
                       f"**{abs(rel):.1%} {direction}** on `{r['key']}`"
                       + (f" and the marginal cost of a unit-composing line went "
                          f"{r['slope'][0]:.1f} -> {r['slope'][1]:.1f} instantiations per step "
                          f"({r['slope'][2]:+.1%})" if "slope" in r else "") + ".")
    return "\n".join(md), [finding] if finding else []


def render_report(payloads, previous=None):
    comparison_md, comparison_findings = (None, [])
    if previous:
        comparison_md, comparison_findings = run_over_run(payloads, previous)
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

    # By family first, not by version number alone: sorting on the version interleaves the families the
    # moment two of them share one (a clang 16 column would land between gcc 15 and gcc 16).
    series = scaling_section(cells, sorted([*header_keys, *module_keys], key=config_order), refs)
    if series:
        lines += ["## Marginal cost of user code", "",
                  "From the scaling/ series: `per step` is what one more operation costs, `intercept` is "
                  "the constant cost of pulling the library in. They move independently - a release can "
                  "improve the intercept while making the slope worse, and only `broad` (a distinct unit "
                  "per step) exercises the second. `narrow` reuses five types, as production code does.", ""]
        lines += series

    if module_keys:
        lines += ["## C++20 modules", "",
                  "Building the module interfaces is a cost every consumer of a configuration shares, so it is "
                  "reported here in full rather than folded into the consumer numbers below it. Total cost of a "
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
    story = comparison_findings + findings(cells, keys, refs)
    if story:
        story = ["## What changed", "", *[f"{i}. {s}" for i, s in enumerate(story, 1)], ""]
    comparison = [comparison_md, ""] if comparison_md else []
    lines = [*story, *comparison, *PREAMBLE, "<details><summary>All measurements</summary>", "", *legend, "",
             *lines, "</details>", ""]
    lines += ["<details><summary>How this was measured</summary>", ""] + notes + [
        "", "Instantiation counts are bit-deterministic for a pinned compiler and peak memory varies by "
        "<0.1% between runs, so both are comparable across every column above. Wall time is not: each "
        "configuration is measured on its own runner, and CI runners differ in CPU and in load, so "
        "compare time only within a column, never between columns. Presentation-quality timings need a "
        "quiet machine and `bench.py time`, which interleaves the arms.", "", "</details>"]
    return "\n".join(lines)


def cmd_summary(args):
    """Render one combined report from several `report --output` files (e.g. one per compiler)."""
    payloads = [json.loads(Path(f).read_text()) for f in args.reports]
    previous = [json.loads(Path(f).read_text()) for f in (args.previous or [])]
    text = render_report(payloads, previous or None)
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
    g.add_argument("--slope-slack", type=float, metavar="PCT",
                   help="band for the marginal cost per step from the scaling/ series (default: --slack). "
                        "Keep this tighter than --slack: a total can grow because the library gained a "
                        "feature, but the slope only grows when user code got more expensive")

    r = sub.add_parser("report", help="all metrics this compiler can produce, as markdown + JSON")
    r.add_argument("refs", nargs="*", help="git refs to measure (default: WORKTREE)")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--workflows", nargs="*")
    r.add_argument("--output", help="write the JSON payload (feed several of these to `summary`)")
    r.add_argument("--worktree-cache", default=str(ROOT / ".worktrees"))

    sub.add_parser("key", help="print the baseline file this configuration resolves to")

    a = sub.add_parser("attribute", help="diff two measurements by entity, to explain a difference")
    a.add_argument("refs", nargs="*", help="one or two git refs (default: WORKTREE)")
    a.add_argument("--workflows", nargs="*", help="one or two workflows (substring filters)")
    a.add_argument("--top", type=int, default=20, help="entities to show (default 20)")
    a.add_argument("--min-delta", type=int, default=1, help="ignore entities moving less than this")
    a.add_argument("--worktree-cache", default=str(ROOT / ".worktrees"))

    s = sub.add_parser("summary", help="merge report JSONs into one markdown report (+ job summary)")
    s.add_argument("reports", nargs="+", help="JSON files written by `report --output`")
    s.add_argument("--previous", nargs="*", metavar="JSON",
                   help="the previous run's report JSONs: adds a 'Since the previous run' section "
                        "comparing corpus totals and the scaling slope per configuration, so the "
                        "summary says what the last changes bought instead of describing one point")

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
    elif args.cmd == "attribute":
        cmd_attribute(args)
    elif args.cmd == "key":
        print(baseline_path(args, toolchain(args)))


if __name__ == "__main__":
    main()
