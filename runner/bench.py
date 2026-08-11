#!/usr/bin/env python3
"""mp-units compile-time benchmark runner.

Measures the compile-time cost of idiomatic mp-units workflows across library versions.

Subcommands:
  time     interleaved wall-clock A/B across two or more git refs (best-of-K, rep-major
           interleaving so machine-load drift hits all arms equally)
  counts   deterministic frontend counts from one traced clang compile: -ftime-trace events
           (instantiations, constant evaluations) plus the AST census from -print-stats
           (declarations, types) - all bit-stable for a pinned compiler, so a noisy CI
           machine measures them as well as a quiet one
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
import contextlib
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


def compile_cmd(tc: Toolchain, repo, out, src, trace=False, ctx: BuildContext = BuildContext(),
                quote_dir=None):
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
        # come from the same compile as the time/memory numbers. `-print-stats` rides along because
        # it changes neither the object file nor the trace, and its numbers are byte-identical to a
        # clean compile's - so the AST counts cost no second invocation. It must NOT be paired with
        # `-fsyntax-only` instead: skipping codegen drops a handful of decls and types (16 and 10 on
        # isq/kind_safe_interfaces), which would make these counts describe a different compile than
        # the instantiation counts beside them.
        cmd += ["-ftime-trace", "-ftime-trace-granularity=0", "-Xclang", "-print-stats"]
    if tc.import_std:
        cmd += ["-DMP_UNITS_IMPORT_STD"]
    if tc.modules:
        cmd += ["-DMP_UNITS_MODULES"]
    if (tc.modules or tc.import_std) and not tc.is_clang:
        cmd += ["-fmodules"]  # GCC finds the BMIs through gcm.cache, relative to the working directory
    cmd += list(ctx.flags)
    cmd += tc.extra.split() + [f"-I{d}" for d in include_dirs(repo)]
    if quote_dir is not None:
        # An include twin is compiled from a temporary directory, so the workflow's own quoted
        # includes ("scaling_workload.h") need the original directory on the quote path.
        cmd += ["-iquote", str(quote_dir)]
    cmd += ["-c", str(src), "-o", str(out)]
    return cmd


def trace_counts(trace: Path):
    data = json.loads(trace.read_text())
    counts = {"InstantiateClass": 0, "InstantiateFunction": 0}
    for e in data["traceEvents"]:
        if e.get("ph") == "X" and e["name"] in counts:
            counts[e["name"]] += 1
    return counts


# The entity census: how many things the headers in this TU DEFINE, counted off the same traced
# compile as every other count. Defining a unit/spec/constant means deriving a struct from one of
# these scaffolding class templates, and clang emits exactly one InstantiateClass event per distinct
# specialization of them (verified on six umbrella TUs: events == distinct specializations, every
# time), so counting distinct `detail` strings is bit-deterministic and costs nothing. The census is
# what lets a gate tell the library GROWING from the library GETTING SLOWER: an umbrella that grew by
# 40 constants priced at the recorded per-constant rate is expected growth; the same total moving with
# the census unchanged is a regression. It is counted from the compiler rather than from the source or
# the documentation because the compiler pays per specialization, not per spelling - si.h SPELLS 24
# prefix templates and INSTANTIATES 673 prefixed units for the symbol matrix.
CENSUS_KINDS = (("named_unit", "mp_units::named_unit<"),
                ("prefixed_unit", "mp_units::prefixed_unit<"),
                ("quantity_spec", "mp_units::quantity_spec<"),
                ("named_constant", "mp_units::named_constant<"),
                ("point_origin", "mp_units::absolute_point_origin<"),
                ("point_origin", "mp_units::relative_point_origin<"))


def census_delta(old: dict, new: dict):
    return {k: new.get(k, 0) - old.get(k, 0) for k in {*old, *new}
            if new.get(k, 0) != old.get(k, 0)}


def workflow_preamble(src: Path):
    """Split a workflow into its include preamble and everything after it.

    The preamble is the run of comments, blank lines and preprocessor directives (plus the `import`
    lines living inside their #ifdef branches) before the first line of C++ - i.e. the include set
    the TU pays for whether or not the code below it uses any of it. The corpus writes preambles
    exactly this way by convention, and the empty-main twin built from one is what turns a workflow's
    total into a use-cost."""
    text = src.read_text()
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith(("//", "#", "import ")):
            return "".join(lines[:i]), "".join(lines[i:])
    return text, ""


def twin_name(preamble: str):
    """Row name for an include set: the included headers' stems, so `include/cstdio+si` reads as
    what it is. Distinct preambles that collide on the name get a digest suffix in twin_sources."""
    headers = re.findall(r'#\s*include\s*[<"]([^>"]+)[>"]', preamble)
    stems = sorted({Path(h).stem for h in headers if h != "mp-units/compat_macros.h"})
    return "include/" + "+".join(stems)


def twin_sources(selection):
    """The include twins a selection needs: one empty-main TU per distinct include set.

    Returns ({workflow: twin row name}, {twin row name: (source text, quote dir)}). A workflow whose
    body is nothing but an empty main (umbrella/, control/) IS its include set and gets no twin - the
    subtraction would leave nothing by construction. Twin identity is the preamble's DIRECTIVES, not
    its text: two workflows of one family differ in their leading comments and share every include,
    and measuring that set twice is the same number twice. Shared by `measure_counts` (traced twins
    give the use-cost basis) and `measure_time` (untraced twins give the inclusion wall time the
    safety ladder and the price list's include rows are read in)."""
    by_workflow, sources, idents = {}, {}, {}
    for name, src in selection.items():
        if src is None:
            continue
        preamble, body = workflow_preamble(src)
        if re.sub(r"\s+", "", body) == "intmain(){}":
            continue
        directives = "\n".join(line.strip() for line in preamble.splitlines()
                               if line.strip().startswith(("#", "import "))) + "\n"
        ident = (directives, str(src.parent))
        if ident not in idents:
            key = twin_name(directives)
            if key in sources:  # same stems, different directives or directory
                key += "-" + hashlib.sha1("|".join(ident).encode()).hexdigest()[:4]
            idents[ident] = key
            sources[key] = (directives + "\nint main() {}\n", src.parent)
        by_workflow[name] = idents[ident]
    return by_workflow, sources


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
        # Counting the BMI build matters: under modules the consumer instantiates almost nothing and
        # declares almost nothing - a `si_lean_umbrella` consumer reports 1 function declaration against
        # 8269 with headers - because the work happened here. Leaving these rows out would price modules
        # as free. Tracing does not change the BMI, so consumers can reuse it.
        base += ["-ftime-trace", "-ftime-trace-granularity=0", "-Xclang", "-print-stats"]
    if tc.standard_library:
        base += [f"-stdlib={tc.standard_library}"]
    if tc.import_std:
        base += ["-DMP_UNITS_IMPORT_STD"]
    if not tc.is_clang:
        base += ["-fmodules"]
    cwd = None if tc.is_clang else str(workdir)

    def step(name, cmd, artifact):
        stats_file = artifact.with_suffix(".stats") if trace else None
        try:
            got = compile_once(cmd, cwd=cwd, stderr_to=stats_file)
        except subprocess.CalledProcessError:
            sys.exit(f"failed to build {name} for {config_key(tc)}:\n  " + " ".join(map(str, cmd)))
        got["mib_on_disk"] = round(artifact.stat().st_size / (1024 * 1024), 1) if artifact.exists() else None
        if trace:
            trace_file = artifact.with_suffix(".json")
            got["counts"] = ({**trace_counts(trace_file), **ast_stats(stats_file.read_text())}
                             if trace_file.exists() else None)
        steps.append({"name": f"bmi/{name}", **got})

    if tc.import_std:
        # The standard library's own module is built with a MINIMAL flag set: it is not part of
        # mp-units, so it must not inherit its configuration macros or -O2. Doing so is not merely
        # untidy - GCC 16 miscompiles consumers of a std module built that way, ICEing in
        # nonnull_arg_p during GIMPLE ealias.
        std_base = [tc.cxx, f"-std={tc.std}"] + (["-ftime-trace", "-ftime-trace-granularity=0",
                                                  "-Xclang", "-print-stats"] if trace else [])
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


def measure_one(tc: Toolchain, repo, src, ctx: BuildContext, tmp: Path, quote_dir=None):
    """Every deterministic count from ONE traced compile of `src`.

    Instantiations are frontend work. More deterministic numbers come free from the same compile and
    cover what they cannot see: constant evaluation (which a constexpr implementation trades
    instantiations for), the entity census (what the included headers DEFINE - see CENSUS_KINDS), the
    object file split into the code that reaches the binary versus the symbol metadata that does not
    - see object_sizes() - and the size of the AST that was built, which is the only one of them that
    can see a declaration that was never instantiated (see ast_stats)."""
    out = tmp / "wf.o"
    trace = tmp / "wf.json"
    proc = run(compile_cmd(tc, repo, out, src, trace=True, ctx=ctx, quote_dir=quote_dir), cwd=ctx.cwd)
    data = json.loads(trace.read_text())
    counts = {"InstantiateClass": 0, "InstantiateFunction": 0, "EvaluateAsConstantExpr": 0}
    census, seen = {}, set()
    for e in data["traceEvents"]:
        if e.get("ph") != "X":
            continue
        if e["name"] in counts:
            counts[e["name"]] += 1
        # Census on class instantiations only: a member function of a scaffolding template would
        # match the prefix too, and it is a use, not a definition.
        if e["name"] == "InstantiateClass":
            detail = e.get("args", {}).get("detail", "")
            for kind, prefix in CENSUS_KINDS:
                if detail.startswith(prefix) and detail not in seen:
                    seen.add(detail)
                    census[kind] = census.get(kind, 0) + 1
    counts["census"] = census
    counts.update(object_sizes(out))
    counts.update(ast_stats(proc.stderr))
    return counts


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
        selection = select_workflows(version, patterns, tc.std)
        by_workflow, twin_specs = twin_sources(selection)
        for key, (text, quote_dir) in twin_specs.items():
            twin_src = Path(tmp) / "twin.cpp"
            twin_src.write_text(text)
            try:
                results[key] = measure_one(tc, repo, twin_src, ctx, Path(tmp), quote_dir=quote_dir)
            except subprocess.CalledProcessError as exc:
                print(f"::error::{key} (an include twin) failed to compile: "
                      f"{exc.stderr.splitlines()[:1]}")
                results[key] = "FAIL"
        for name, src in selection.items():
            if src is None:
                results[name] = None
                continue
            try:
                counts = measure_one(tc, repo, src, ctx, Path(tmp))
            except subprocess.CalledProcessError as exc:
                print(f"::error::{name} failed to compile: {exc.stderr.splitlines()[:1]}")
                results[name] = "FAIL"
                continue
            if name in by_workflow:
                counts["twin"] = by_workflow[name]
            results[name] = counts
    return results


# What clang's `-Xclang -print-stats` reports about the AST the frontend built, as opposed to the work
# it did building it. `Function decls` is the one with a mechanism behind it: a hidden friend declared
# inside a class template is redeclared by EVERY specialization of that template, so moving it into a
# non-template interface base removes `friends x specializations` declarations while instantiating
# nothing differently. Such a change moves InstantiateClass + InstantiateFunction by exactly 0 and
# EvaluateAsConstantExpr by single digits out of hundreds of thousands (findings.md §22) - every other
# metric here is blind to it. `Total bytes` is deliberately not parsed: across the same arms it moved by
# ±0.03% with no consistent sign, because fewer declarations are offset by the added base subobject.
# `types_total` is RETIRED, not merely unrendered: it ranked 0.997 with `decls_total`, moved the
# same direction with smaller magnitude on every arm that moved anything, and no design decision in
# the library adds types without adding declarations - a metric with no consumer is storage, not
# measurement. Old baselines still carry it; comparisons skip a metric absent from either side.
STATS_FIELDS = (("decls_total", re.compile(r"^\s*(\d+) decls total\.", re.M)),
                ("function_decls", re.compile(r"^\s*(\d+) Function decls,", re.M)))


def ast_stats(stderr: str) -> dict:
    """Parse the `-print-stats` block. A field clang stops printing reports None rather than 0, so a
    future rename degrades to "metric absent" - which comparisons skip - instead of "dropped to zero"."""
    return {field: int(m.group(1)) if (m := pattern.search(stderr)) else None
            for field, pattern in STATS_FIELDS}


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


def compile_once(cmd, cwd=None, pin=None, stderr_to=None):
    """Wall time and peak RSS of one compile. os.wait4 gives this child's own rusage, so no
    /usr/bin/time dependency and no interference between measurements.

    `pin` binds the compiler to one CPU via taskset. On a machine with any jitter this is the single
    most effective control available: measured on WSL2 it halved the within-arm spread, from 23-31%
    down to 8-12%. It does not make a noisy host trustworthy - see the spread report in `cmd_time`.

    `stderr_to` redirects the compiler's diagnostics to a FILE - the caller wants `-print-stats` output.
    A pipe would be the obvious choice and is the wrong one here: os.wait4() reaps the child before
    anything reads it, so a compiler that fills the pipe buffer would block forever."""
    if pin is not None:
        cmd = ["taskset", "-c", str(pin), *cmd]
    started = time.perf_counter_ns()
    with open(stderr_to, "w") if stderr_to else contextlib.nullcontext(subprocess.DEVNULL) as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err, cwd=cwd)
        _, status, usage = os.wait4(proc.pid, 0)
    ms = (time.perf_counter_ns() - started) // 1_000_000
    if status != 0:
        raise subprocess.CalledProcessError(status, cmd)
    return {"ms": ms, "peak_mib": round(usage.ru_maxrss / 1024, 1)}  # ru_maxrss is KiB on Linux


def measure_time(repos, tc: Toolchain, reps, patterns=None, pin=None, samples=None):
    """Interleaved best-of-K wall-clock and peak RSS across checkouts (rep-major, arm-minor).

    `samples`, if given a dict, collects every individual timing so the caller can report the spread.
    A best-of-K number without its spread is unreadable: a 2% difference between arms means nothing
    on a host whose own repeats vary by 20%, and only the raw samples can say which case you are in."""
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
        # The include twins are timed like workflows: the price list's inclusion rows and the safety
        # ladder read wall time, and only an untraced compile of the twin itself provides it. They
        # are excluded from every corpus total (see corpus_pair) - a twin is a component of the
        # workflows beside it, and summing both counts the includes twice.
        twin_files = {}
        for ref in repos:
            per_ref = {}
            for key, (text, quote_dir) in twin_sources(selections[ref])[1].items():
                f = Path(tmp) / f"twin-{hashlib.sha1((ref + key).encode()).hexdigest()[:12]}.cpp"
                f.write_text(text)
                per_ref[key] = (f, quote_dir)
            twin_files[ref] = per_ref
        names = sorted({*names, *(k for per_ref in twin_files.values() for k in per_ref)})
        names = [*sorted(n for r in best.values() for n in r), *names]

        def source_for(ref, name):
            if name.startswith("include/"):
                return twin_files[ref].get(name)
            src = selections[ref].get(name)
            return None if src is None else (src, None)

        for name in [n for n in names if not n.startswith("bmi/")]:
            # warmup + applicability
            for ref, repo in repos.items():
                found = source_for(ref, name)
                if found is None:
                    best[ref][name] = None
                    continue
                src, quote_dir = found
                try:
                    run(compile_cmd(tc, repo, out, src, ctx=contexts[ref], quote_dir=quote_dir),
                        cwd=contexts[ref].cwd)
                except subprocess.CalledProcessError:
                    best[ref][name] = "FAIL"
            for _ in range(reps):
                for ref, repo in repos.items():
                    found = source_for(ref, name)
                    if found is None or best[ref].get(name) == "FAIL":
                        continue
                    src, quote_dir = found
                    got = compile_once(compile_cmd(tc, repo, out, src, ctx=contexts[ref],
                                                   quote_dir=quote_dir),
                                       cwd=contexts[ref].cwd, pin=pin)
                    if samples is not None:
                        samples.setdefault(ref, {}).setdefault(name, []).append(got["ms"])
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
    """A GitHub annotation. It is NOT mirrored into the step summary: the verdict block carries the
    same facts there in order, and the mirror used to land the conclusions BELOW three hundred rows
    of evidence tables - the reader met the tables before the reason they were red."""
    print(f"::{kind}::{text}")


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
        print_table(["inst_class", "inst_func", "const_eval", "entities", "code_B", "syms_B",
                     "fn_decls", "decls"],
                    [(n, *([sum(v.get("census", {}).values()) or None if k == "entities" else v.get(k)
                            for k in ("InstantiateClass", "InstantiateFunction", "EvaluateAsConstantExpr",
                                      "entities", "code_bytes", "symbol_bytes",
                                      "function_decls", "decls_total")]
                           if isinstance(v, dict) else [v] * 8))
                     for n, v in sorted(results.items())],
                    lambda v: "n/a" if v is None else str(v))
        if price := price_list_lines(results, tc):
            print()
            print("\n".join(price))
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
    samples = {}
    best = measure_time(repos, toolchain(args), args.reps, args.workflows, pin=args.pin, samples=samples)
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
    time_spread_report(samples, comparable, args)


def time_spread_report(samples, comparable, args):
    """What the wall-clock numbers above are worth on THIS host.

    Prints each arm's own repeat-to-repeat spread next to the differences between arms, and says
    outright when the latter is smaller than the former. Without this, `time` invites exactly the
    mistake the project keeps making: reading a single-digit delta off a host that cannot resolve one."""
    if not samples or args.reps < 2:
        return
    per_arm = {}
    for ref, by_wf in samples.items():
        tot = [sum(by_wf[n][i] for n in comparable if len(by_wf.get(n, [])) > i)
               for i in range(args.reps)]
        tot = [v for v in tot if v > 0]
        if len(tot) >= 2:
            per_arm[ref] = sorted(tot)
    if len(per_arm) < 1:
        return
    print(f"\nHow trustworthy is that? (corpus total per rep, {args.reps} reps"
          + (f", pinned to CPU {args.pin}" if args.pin is not None else ", unpinned") + ")")
    rows = [[ref, f"{v[0]:.0f}", f"{statistics.median(v):.0f}", f"{v[-1]:.0f}",
             f"{100 * (v[-1] - v[0]) / v[0]:.1f}%"] for ref, v in per_arm.items()]
    for line in markdown_table(["arm", "best", "median", "worst", "own spread"], rows):
        print(line)
    noise = max(100 * (v[-1] - v[0]) / v[0] for v in per_arm.values())
    if len(per_arm) > 1:
        firsts = list(per_arm)
        base = per_arm[firsts[0]]
        gap = max(abs(100 * (per_arm[r][0] - base[0]) / base[0]) for r in firsts[1:])
        verdict = ("BELOW the noise floor - this host cannot resolve it, and the difference must not be "
                   "reported as a result" if gap < noise else
                   "above the noise floor, so it is worth reading")
        print(f"\nLargest between-arm difference {gap:.1f}% against a {noise:.1f}% noise floor: {verdict}.")
    else:
        print(f"\nNoise floor on this host: {noise:.1f}%. A difference smaller than that is not a result.")
    print("Wall time is never gated for this reason (see CLAUDE.md); counts are. To lower the floor: "
          "an idle machine, `--pin`, and a native kernel - a VM's scheduler jitter dominates everything else.")


# Every gated number must be bit-deterministic for a pinned compiler AND large enough that a percentage
# band means something. Instantiations are frontend work; constant evaluations are what a constexpr
# implementation trades them for; emitted code is what the optimizer's time tracks.
#
# The third field is a MINIMUM ABSOLUTE MOVEMENT, required on top of the percentage band. Without it a
# percentage on a three-digit number is noise: `code_bytes` has a median of 145 across the corpus, so a
# 1% band resolves to 1.4 bytes and a single instruction trips the gate. A helper that stops folding away
# - the thing this metric exists to catch - moves it by thousands, so the floor costs no sensitivity.
#
# `function declarations` is the exception that proves the floor's purpose: its smallest value in the
# corpus is 8269, so even CI's 1% band resolves to 83 declarations, and repeats are bit-identical. A floor
# of 50 there would sit below the band on every workflow and never once bind - decoration, not a
# threshold. It gets 0, the same as the other two frontend counters, and earns it the same way. Revisit if
# a workflow with three-digit declaration counts is ever added; nothing that includes the library is.
#
# Four metrics are measured but deliberately NOT gated:
#   - peak memory, which correlates 0.97 with instantiations and would only fire when they already had.
#   - `symbol_bytes` (mangled names, string table, relocations), which is also no longer rendered - see
#     UNRENDERED. Gating it would gate the SAME variable `code_bytes` already gates: both are counts of
#     emitted template instantiations wearing different clothes, which is why they moved on identical
#     workflow sets across v2.5.0 -> master. The tempting reading - "names explode, so gate names" - does
#     not survive the decomposition: two thirds of the metric is fixed-size records per symbol, per
#     relocation and per section, and shortening every mangled name in text/output_format could reach at
#     most 67 KB of its 440 KB. Emitting fewer instantiations reaches all of it.
#   - `decls_total` and `types_total`, the other two `-print-stats` numbers. Both see the mechanisms the
#     gated metrics see, and neither sees one of its own SHARPLY enough to gate. Measured on the two A/B
#     arms that isolate a mechanism each: moving friends out of class templates moved `function_decls`
#     -2.1% to -3.3% while `decls_total` moved -0.3% to -0.8%, and forcing the CRTP fallback on
#     isq/kind_safe_interfaces moved instantiations +2.2% while `decls_total` moved +3.4%. So each of them
#     is a mixture of the two sharp metrics, diluted by 75k TemplateTypeParm declarations that no design
#     decision controls. The one thing only `decls_total` can see - a non-function member added to a
#     widely-specialized class template - it sees at 1/23rd the resolution: one member alias on `quantity`
#     is 238 declarations out of 310759, i.e. 0.08%, which no band will ever catch. `types_total` is
#     weaker still (rank 0.997 with `decls_total`, smaller move in the same direction on both arms) and is
#     therefore UNRENDERED as well - the §19 argument, unchanged.
# The floors were re-derived when the gate moved from totals to use-costs (2026-08): the smallest
# gated numbers fell from 8269 (fn decls, si_lean total) to 17 (fn decls, narrow_016 over its twin)
# and 42 (instantiations, output_printf over its twin - printf really is that cheap), so a percentage
# band alone would fail on single-count movements. The floors are set where the corpus says movement
# stops meaning anything: findings §22 measured 2-12 constant evaluations of drift on arms that moved
# nothing else, and every mechanism worth catching moves by hundreds (a hidden friend costs
# friends x specializations; one broad-slope step is 125). At CI's 1% band a floor of 8/16 binds only
# below the corpus's 25th percentile of use-costs, so the mid and large rows keep full percentage
# sensitivity; on totals these floors sit far under the band's own resolution (83+ declarations) and
# never bind at all.
GATED = (("instantiations", lambda e: e["InstantiateClass"] + e["InstantiateFunction"], 8),
         ("constant evaluations", lambda e: e.get("EvaluateAsConstantExpr"), 16),
         ("function declarations", lambda e: e.get("function_decls"), 16),
         ("emitted code (bytes)", lambda e: e.get("code_bytes"), 512))

# Which gated metrics ALSO get a slope gate, and the noun their per-step figure is measured in. The
# slope is the one number a purely additive library change cannot move: a new class or function costs a
# workflow the same whether it uses 16 or 256 unit types, so it lifts the intercept and leaves the
# per-step cost alone. That is why declarations belong here - it makes the growth-tolerant half of the
# declaration gate explicit rather than relying on the total's band being loose enough.
# `emitted code` is excluded because it does not scale with user code at all (87 bytes flat across the
# whole series, so there is no slope to speak of). `constant evaluations` is a candidate - it scales at
# ~585 per step on `broad` - and is left out until that slope has been reviewed across configurations:
# putting a band on a number nobody has looked at is how a gate goes red for a reason no one can explain.
SLOPE_GATED = (("instantiations", "instantiations"), ("function declarations", "declarations"))


# Where each entity kind's marginal price comes from: a pair of workflows whose include sets differ in
# (almost) nothing but that kind. Order matters - a later axis subtracts the contribution of the kinds
# priced before it (the si pair carries ~10 constants alongside its ~528 prefixed units), so the purest
# axis goes first. A kind a pair adds that no earlier axis priced is tolerated only because its count is
# noise against the axis kind (a couple of point origins against hundreds of constants).
RATE_AXES = (("named_constant", "umbrella/codata_2022_umbrella", "umbrella/codata_umbrella"),
             ("prefixed_unit", "umbrella/si_lean_umbrella", "umbrella/si_umbrella"),
             ("quantity_spec", "control/core_only", "umbrella/isq_space_and_time_umbrella"),
             # si/units.h is the definitions alone - no symbols, no prefix matrix - so this axis
             # prices the named unit itself; the symbol tax shows up as si_units -> si_lean in the
             # price list's include rows rather than contaminating the rate.
             ("named_unit", "control/core_only", "umbrella/si_units_umbrella"))


def entity_rates(results, extract):
    """What one more entity of each kind costs in this metric, derived from the measured corpus.

    Recorded into the baseline file by `update` and used by `check` to price census growth: an
    umbrella that gained entities is expected to grow by count x rate, and only the residual above
    that expectation gates. The rates are per configuration and per metric because they are
    measurements, not constants - a different compiler or standard prices a constant differently."""
    rates = {}
    for kind, lo_name, hi_name in RATE_AXES:
        lo, hi = results.get(lo_name), results.get(hi_name)
        if not isinstance(lo, dict) or not isinstance(hi, dict):
            continue
        vlo, vhi = extract(lo), extract(hi)
        if not isinstance(vlo, (int, float)) or not isinstance(vhi, (int, float)):
            continue
        dc = census_delta(lo.get("census") or {}, hi.get("census") or {})
        steps = dc.pop(kind, 0)
        if steps <= 0:
            continue
        # An axis is only as pure as its side kinds are priced: attributing an unpriced kind's cost
        # to the axis kind produced a "named unit" rate of 91 when a filtered run had no
        # quantity_spec axis to subtract. A trace of an unpriced kind is noise; more is a bad axis.
        if any(k not in rates and abs(d) > max(2, 0.05 * steps) for k, d in dc.items()):
            continue
        priced = sum(rates.get(k, 0) * d for k, d in dc.items())
        rates[kind] = round((vhi - vlo - priced) / steps, 2)
    return rates


def gated_deltas(baseline, results, extract, rates):
    """Per-workflow baseline/current/relative-delta on the basis the gate reads, for every
    comparable entry.

    The basis is what makes a red gate mean SLOWER rather than BIGGER. `use` subtracts the
    workflow's own include twin from both sides, so a system header gaining entities moves both
    sides equally and cancels - the gate prices only the code the workflow itself writes.
    `residual` (umbrella/, whose workflows ARE their include sets) replaces the baseline with the
    expected value under census growth priced at the recorded per-kind rates - an unchanged census
    makes it the plain total, so no sensitivity is lost on the common run. `total` is the fallback
    when neither is available: control/ rows by design, and any entry whose baseline predates twins
    and censuses. A metric missing from either side is skipped rather than assumed: baselines
    recorded before a metric existed must not read as a change."""
    def twin_value(entry, pool):
        twin = pool.get(entry.get("twin")) if isinstance(entry, dict) else None
        v = extract(twin) if isinstance(twin, dict) else None
        return v if isinstance(v, (int, float)) else None

    details = {}
    for name, base in sorted(baseline.items()):
        if name.startswith(("bmi/", "include/")):
            continue  # an include set is gated through the workflows that subtract it, not twice
        cur = results.get(name)
        if not isinstance(cur, dict) or not isinstance(base, dict):
            continue  # n/a, FAIL, or a baseline entry whose workflow is gone
        b_tot, c_tot = extract(base), extract(cur)
        if not isinstance(b_tot, (int, float)) or not isinstance(c_tot, (int, float)) or not b_tot:
            continue
        basis, b, c = "total", b_tot, c_tot
        if name.startswith("umbrella/"):
            if isinstance(base.get("census"), dict):
                dc = census_delta(base["census"], cur.get("census") or {})
                # A kind without a recorded rate is priced at this workflow's own baseline average -
                # imperfect, but the unpriced kinds are the rare ones (point origins).
                avg = b_tot / max(1, sum(base["census"].values()))
                b = b_tot + sum(d * rates.get(k, avg) for k, d in dc.items())
                basis = "residual"
        else:
            bt, ct = twin_value(base, baseline), twin_value(cur, results)
            if bt is not None and ct is not None and b_tot - bt > 0:
                b, c, basis = b_tot - bt, c_tot - ct, "use"
        if b <= 0:
            continue
        details[name] = {"baseline": round(b, 1), "current": round(c, 1), "rel": (c - b) / b,
                         "abs": c - b, "basis": basis, "total_baseline": b_tot, "total_current": c_tot,
                         "umbrella": name.startswith("umbrella/")}
    return details


# The scaling/ series' shapes: narrow/broad measure the marginal cost of USING quantity types, the
# define_* shapes the marginal cost of DEFINING entities. The define shapes are immune to library
# growth by construction (their entities live in the workflow), so their slopes can carry the
# tightest bands the counts' determinism allows and are never rebaselined for growth.
SCALING_SHAPES = ("narrow", "broad", "typed_broad", "specs_broad",
                  "define_units", "define_specs", "define_constants")


def slope_from(entries, extract):
    """Marginal cost of one more step, per shape, from a flat {workflow: entry} mapping.

    This is the number a gate on totals cannot see properly. Adding a feature to the library lifts every
    workflow's total by a similar small amount; making the library slower per unit of user code lifts the
    slope. Gating only totals therefore blocks growth and under-reacts to regression - and the slope is
    only ~62% of `broad_256`'s total, so even that workflow dilutes a slope move by a third."""
    out = {}
    for shape in SCALING_SHAPES:
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
    from either side so a baseline predating the series does not read as change.

    A zero baseline is a real number here, not a gap: define_specs costs 0.0 instantiations per
    step under the deducing-this API, and that zero is exactly what the shape protects. The delta
    is therefore taken against max(base, 1) - one whole count per step - so a regression from
    nothing to one instantiation per step reads as +100% and trips any band, instead of being
    skipped to dodge the division."""
    base, cur = slope_from(baseline, extract), slope_from(results, extract)
    out = {}
    for shape in sorted(base):
        if shape in cur:
            out[shape] = {"baseline": base[shape], "current": cur[shape],
                          "rel": (cur[shape] - base[shape]) / max(base[shape], 1.0)}
    return out


def assert_same_config(recorded, tc: Toolchain, where):
    """Numbers from another configuration are not a baseline, they are a different measurement."""
    for field, current in (("cxx", tc.cxx), ("std", tc.std), ("stdlib", tc.standard_library),
                           ("config_label", tc.label), ("extra_flags", " ".join(tc.extra.split()))):
        was = recorded.get(field)
        if was is not None and was != current:
            sys.exit(f"{where} was recorded with {field}={was!r}, this run uses {current!r}; "
                     f"counts are only comparable within one configuration")


def price_list_rows(results):
    """The handful of numbers the corpus exists to produce, from one measured results dict - each
    denominated in something a reader can multiply by their own code: an include, an entity, a step.

    Returned as (category, what, value, source) rows - category is one of "include", "define",
    "use" or "ladder", which is how the compact report splits the list into one table per question
    while `check` and `counts` keep printing it whole and `report` carries it in its payload."""
    inst = GATED[0][1]

    def val(name):
        v = results.get(name)
        return inst(v) if isinstance(v, dict) else None

    rows = []
    core = val("control/core_only")
    if core:
        rows.append(["include", "the core framework (defines nothing)", str(core), "control/core_only"])
    for name in sorted(n for n in results if n.startswith("umbrella/")):
        entry = results[name]
        if not isinstance(entry, dict) or not entry.get("census"):
            continue
        entities = sum(entry["census"].values())
        v = inst(entry)
        if not entities or not isinstance(v, (int, float)):
            continue
        dominant = max(entry["census"], key=entry["census"].get)
        over_core = f" ({(v - core) / entities:.1f}/entity over core)" if core and v > core else ""
        rows.append(["include", f"{name.removeprefix('umbrella/').removesuffix('_umbrella')} - "
                     f"{entities} entities, mostly {dominant}", f"{v}{over_core}", name])
    # "As the system ships one": these rates price an entity WITH its ecosystem - an SI named unit
    # brings its symbol table, a CODATA constant its uncertainty payload. The synthetic define_*
    # slope rows price the bare definition; the difference between the two is the ecosystem.
    nouns = {"named_constant": "measured constant (CODATA)", "prefixed_unit": "prefixed unit",
             "quantity_spec": "quantity spec (ISQ)", "named_unit": "named unit (SI)"}
    for kind, rate in entity_rates(results, inst).items():
        lo, hi = next((l, h) for k, l, h in RATE_AXES if k == kind)
        rows.append(["define", nouns.get(kind, kind), f"{rate:.1f}",
                     f"{hi.split('/', 1)[1]} minus {lo.split('/', 1)[1]}"])
    slopes = slope_from(results, inst)
    for cat, shape, label in (
            ("use", "broad", "each distinct derived quantity composed (simple quantities)"),
            ("use", "typed_broad", "the same with TYPED quantities (adds level-5 checking)"),
            ("use", "specs_broad", "a bare spec expression, no quantities (constraint algebra)"),
            ("use", "narrow", "each further line using already-instantiated quantity types"),
            ("define-bare", "define_units", "named unit (SI)"),
            ("define-bare", "define_specs", "quantity spec (ISQ)"),
            ("define-bare", "define_constants", "measured constant (CODATA)")):
        if shape in slopes:
            rows.append([cat, label, f"{slopes[shape]:.1f}/step", f"scaling/{shape} slope"])
    for facility, wf in (("std::printf", "text/output_printf"), ("operator<<", "text/output_ostream"),
                         ("std::format", "text/output_format"), ("std::println", "text/output_println")):
        if (u := use_cost_of(results, wf)) is not None:
            rows.append(["print", f"via `{facility}`", f"{u:,}", wf])

    ladder = [use_cost_of(results, f"safety/{r}") for r in ("raw_doubles", "simple_quantities",
                                                            "typed_quantities", "affine_quantities")]
    if all(v is not None for v in ladder):
        raw, simple, typed, affine = ladder
        rows += [["ladder", "write the safety-ladder profile with raw doubles", str(raw),
                  "safety/raw_doubles"],
                 ["ladder", "the same at safety levels 1-4 (simple quantities)",
                  f"{simple} (+{simple - raw})", "safety/simple_quantities"],
                 ["ladder", "add level 5, quantity safety (typed quantities)",
                  f"{typed} (+{typed - simple})", "safety/typed_quantities"],
                 ["ladder", "add level 6, point/delta safety (affine)",
                  f"{affine} (+{affine - typed})", "safety/affine_quantities"]]
    return rows


def use_cost_of(results, name):
    """A workflow's instantiations over its own include twin, from one measured results dict."""
    inst = GATED[0][1]
    entry = results.get(name)
    twin = results.get(entry.get("twin", "")) if isinstance(entry, dict) else None
    v, t = (inst(entry) if isinstance(entry, dict) else None,
            inst(twin) if isinstance(twin, dict) else None)
    return v - t if isinstance(v, int) and isinstance(t, int) else None


# The include sets the PAGE shows, in reading order: the framework alone, then SI from definitions
# to the full umbrella, then one shallow ISQ chapter against the whole of ISQ, then the CODATA tiers.
# Representative by design - the corpus measures every ISQ chapter and gains more as the library
# grows, and listing all of them turned this table into the thing the page exists to replace.
# ...and how each one is NAMED for a reader: the header a user would write, never the workflow's file
# name - `si_lean`, `isq_space_and_time` and friends are this suite's internal labels, and a page that
# prints them asks its reader to learn the corpus before reading the numbers.
PAGE_INCLUDE_ROWS = (("control/core_only", "the core framework alone (`framework.h`)"),
                     ("umbrella/si_units_umbrella", "SI unit definitions (`si/units.h`)"),
                     ("umbrella/si_lean_umbrella", "lean SI (`si/core.h`)"),
                     ("umbrella/si_umbrella", "full SI (`si.h`)"),
                     ("umbrella/isq_space_and_time_umbrella",
                      "one ISQ chapter (`isq/space_and_time.h`)"),
                     ("umbrella/isq_umbrella", "full ISQ (`isq.h`)"),
                     ("umbrella/codata_lean_umbrella",
                      "essential CODATA constants (`codata/codata2022_essential.h`)"),
                     ("umbrella/codata_2022_umbrella", "one CODATA adjustment (`codata/codata2022.h`)"),
                     ("umbrella/codata_umbrella", "all CODATA adjustments (`codata.h`)"))

PRICE_LIST_NOTE = ("Chapter and system rows include everything their header pulls in, so a chapter's own "
                   "cost is its row minus the chapters it includes (mechanics includes space_and_time). "
                   "Per-entity definition prices come from pairs of rows whose include sets differ in almost "
                   "nothing but that kind; the per-step prices are the slopes of the scaling/ series and are "
                   "what multiplies with the size of real user code.")


def price_list_lines(results, tc: Toolchain):
    """The price list as markdown, for `check` and single-ref `counts` output - one table, rows
    grouped by category in include -> define -> use -> ladder order."""
    rows = price_list_rows(results)
    if not rows:
        return []
    order = {"include": 0, "define": 1, "define-bare": 2, "use": 3, "print": 4, "ladder": 5}
    verbs = {"include": "include ", "define": "define one ", "define-bare": "define one bare ",
             "use": "", "print": "print quantities ", "ladder": ""}
    shown = [[verbs[cat] + what, value, source]
             for cat, what, value, source in sorted(rows, key=lambda r: order[r[0]])]
    return [f"### price list - `{config_key(tc)}`", "",
            *markdown_table(["what one thing costs", "instantiations", "measured from"], shown),
            "", PRICE_LIST_NOTE, ""]


def census_growth_lines(details_by_metric, baseline, results):
    """One line per umbrella whose entity census moved: what was added, and what that growth was
    priced at. This is the transparency the residual basis owes the reader - a gate that silently
    prices growth invites the question the table must answer."""
    inst_details = details_by_metric.get("instantiations", {})
    out = []
    for name, d in sorted(inst_details.items()):
        if d.get("basis") != "residual":
            continue
        base, cur = baseline.get(name), results.get(name)
        if not isinstance(base, dict) or not isinstance(cur, dict):
            continue
        dc = census_delta(base.get("census") or {}, cur.get("census") or {})
        if not dc:
            continue
        moved = ", ".join(f"{v:+d} {k}" for k, v in sorted(dc.items()))
        priced = d["baseline"] - d["total_baseline"]
        out.append(f"- `{name}` census moved ({moved}): growth priced at {priced:+.0f} instantiations, "
                   f"and the residual above that is what gates ({d['rel']:+.2%}).")
    if out:
        out = ["### census changes", "",
               "The library gained or lost defined entities since the baselines were recorded. That is "
               "growth, not slowness: the gate charges it at the per-entity rates recorded with the "
               "baselines and gates only the remainder.", *out, ""]
    return out


def entity_diff_lines(left, right, label_left, label_right, top, min_delta=1):
    """Markdown for an entity-level diff of two instantiation tallies, biggest mover first."""
    rows = []
    for entity in sorted(set(left) | set(right), key=lambda k: -(right.get(k, 0) - left.get(k, 0))):
        a, b = left.get(entity, 0), right.get(entity, 0)
        if abs(b - a) >= min_delta:
            rows.append([entity, str(a), str(b), f"{b - a:+d}"])
    total = sum(right.values()) - sum(left.values())
    lines = [f"Total instantiations {sum(left.values())} -> {sum(right.values())} ({total:+d}). Entities "
             f"below are templates with their arguments collapsed, ranked by how much they moved.", ""]
    if not rows:
        return lines + [f"No entity moved by at least {min_delta} instantiation(s): the two measurements "
                        f"instantiate the same templates the same number of times.", ""]
    lines += markdown_table(["entity", label_left, label_right, "delta"], rows[:top])
    if len(rows) > top:
        lines += ["", f"{len(rows) - top} further entities moved by at least {min_delta}."]
    return lines + [""]


def name_the_offenders(args, tc: Toolchain, repo: Path, recorded, offenders):
    """Turn "slower" into a name, without being asked: for the worst offenders, diff instantiation
    events by entity between the tree the baselines were recorded from and the tree being checked.

    This is the report's obligation: a gate that says only "3% slower" leaves the finding to whoever
    reruns `attribute` by hand, and nobody does that from a red CI page. Runs only when something
    moved, costs two traced compiles per named workflow, and degrades to a warning when the baseline
    commit is not reachable (shallow clone)."""
    sha = recorded.get("mp_units_sha")
    if not sha or not offenders:
        return []
    try:
        base_repo = checkout(repo, sha, Path(args.worktree_cache).resolve())
    except (subprocess.CalledProcessError, FileNotFoundError):
        # CI checks out mp-units shallow, so the baseline commit is usually absent - but GitHub
        # serves arbitrary commits by sha, so one targeted fetch usually repairs it in seconds.
        try:
            run(["git", "-C", str(repo), "fetch", "--depth", "1", "origin", sha])
            base_repo = checkout(repo, sha, Path(args.worktree_cache).resolve())
        except (subprocess.CalledProcessError, FileNotFoundError):
            gate_summary_line(f"cannot attribute the movement: baseline commit {sha[:9]} is not "
                              f"reachable in {repo} and fetching it failed - deepen the clone to get "
                              f"named offenders", "warning")
            return [f"(attribution skipped: baseline commit `{sha[:9]}` is not reachable in this "
                    f"clone and fetching it failed)", ""]
    selections = {r: select_workflows(detect_version(r), None, tc.std) for r in (base_repo, repo)}
    lines = ["### what got slower, by entity", "",
             f"Instantiation events grouped by entity, baseline tree (`{sha[:9]}`) against the checked "
             f"tree, for the workflows that moved the most. This is `bench.py attribute`, run for you.", ""]
    named = 0
    with tempfile.TemporaryDirectory() as tmp:
        contexts = {id(r): build_modules(r, tc, Path(tmp) / f"bmi-{i}")
                    for i, r in enumerate((base_repo, repo))}
        for name in offenders:
            src_base, src_cur = selections[base_repo].get(name), selections[repo].get(name)
            if src_base is None or src_cur is None:
                continue
            try:
                then = trace_entities(base_repo, tc, src_base, contexts[id(base_repo)], Path(tmp))
                now = trace_entities(repo, tc, src_cur, contexts[id(repo)], Path(tmp))
            except subprocess.CalledProcessError:
                continue
            lines += [f"#### {name}", "", *entity_diff_lines(then, now, "baseline", "current", top=10)]
            named += 1
    return lines if named else []


def gate_slope_table(slopes, band, tc: Toolchain):
    """The marginal-cost table. Separate from the per-workflow one because it answers a different
    question: not "did this workflow grow" but "did one more line of user code get more expensive"."""
    rows = [[f"{label} - {shape}", f"{d['baseline']:.1f}", f"{d['current']:.1f}",
             f"{d['rel'] * 100:+.2f}%", f"{band * 100:g}%", f"{(band - d['rel']) * 100:+.2f}pp",
             "FAILS" if d["rel"] > band else "ok"]
            for label, per_shape in slopes.items() for shape, d in sorted(per_shape.items())]
    lines = [f"### marginal cost per step - `{config_key(tc)}`", ""]
    lines += markdown_table(["metric - shape", "baseline", "current", "delta", "limit", "headroom", ""], rows)
    lines += ["", "Per step of the `scaling/` series. This is the number that separates "
              "*the library grew* from *the library got slower*: adding a feature lifts every workflow's "
              "total by a similar small amount, while a real regression lifts the slope. `narrow` reuses "
              "quantity types, `broad` composes a new derived unit per step, and the `define_*` shapes "
              "define one entity per step - immune to library growth by construction, since their "
              "entities live in the workflow itself.", ""]
    return lines


def gate_summary_table(details, median, args, tc: Toolchain, label="instantiations"):
    """Every workflow with its measured value, its limit and the headroom left - so the distance to
    the bands is visible by observation, not inferred from a single pass/fail line."""
    if not details:
        return []
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
        rows.append([name, d.get("basis", "total"), str(d["baseline"]),
                     str(d["current"]), f"{delta:+.2f}%", f"{args.slack:g}%",
                     f"{args.slack - delta:+.2f}pp", status])
    # Name the configuration: the same compiler at a different -std produces different counts, so a
    # table without it looks like it contradicts the measurement fleet's numbers.
    lines = [f"### {label} - `{config_key(tc)}`", ""]
    lines += markdown_table(["workflow", "basis", "baseline", "current", "delta", "limit", "headroom", ""],
                            rows)
    lines += ["", (f"median across non-umbrella workflows: **{median:+.2%}** against a "
                   f"{args.median_alarm:g}% alarm ({args.median_alarm - median * 100:+.2f}pp headroom)"
                   if median is not None else
                   f"gated at the same {args.slack:g}% band as instantiations; the median alarm "
                   f"applies to instantiations only"), ""]
    return lines


# Printed ONCE under the evidence tables, not once per metric - four copies of the same two
# paragraphs were a third of the old output's length.
EVIDENCE_LEGEND = [
    "`basis` is what the numbers on each row ARE, chosen so that red means slower rather "
    "than bigger: `use` is the workflow minus its own include twin (a system header gaining "
    "entities moves both sides equally and cancels), `residual` compares an umbrella against "
    "its baseline plus census growth priced at the recorded per-entity rates (equal to the "
    "plain total whenever the census did not move), and `total` is the raw number, used only "
    "where nothing better exists.",
    "", "`headroom` is how much further a workflow could grow before it fails: negative means "
    "it already has. A re-record resets every headroom to the full band, which is why "
    "`bench.py update --workflows <filters>` exists - it moves only what you name.", ""]


def cmd_check(args):
    repo = Path(args.repo).resolve()
    tc = toolchain(args)
    baseline_file = baseline_path(args, tc)
    if not baseline_file.exists():
        sys.exit(f"no baselines for this configuration ({baseline_file.name}); run `update` first")
    recorded = json.loads(baseline_file.read_text())
    assert_same_config(recorded, tc, baseline_file.name)
    baseline = recorded["results"]
    rates_by_metric = recorded.get("entity_rates", {})
    slack, alarm, notice = args.slack / 100, args.median_alarm / 100, args.tighten_notice / 100
    advisory_band = args.advisory_slack / 100 if args.advisory_slack is not None else None
    slope_band = (args.slope_slack if args.slope_slack is not None else args.slack) / 100
    measured = measure_counts(repo, tc)
    per_metric = {label: gated_deltas(baseline, measured, extract, rates_by_metric.get(label, {}))
                  for label, extract, _ in GATED}
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
    extract_of = {label: extract for label, extract, _ in GATED}
    slopes = {label: s for label, _ in SLOPE_GATED
              if (s := slope_deltas(baseline, measured, extract_of[label]))}
    slope_regressions = {(label, shape): d for label, per_shape in slopes.items()
                         for shape, d in per_shape.items() if d["rel"] > slope_band}
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(
            {"mp_units_version": ".".join(map(str, detect_version(repo))), **git_provenance(repo),
             "cxx": args.cxx, "std": tc.std, "baseline_key": baseline_file.stem.split("instantiations-")[-1],
             "bands": {"slack": args.slack, "median_alarm": args.median_alarm,
                       "tighten_notice": args.tighten_notice, "advisory_slack": args.advisory_slack,
                       "slope_slack": args.slope_slack if args.slope_slack is not None else args.slack},
             # Keyed by metric since more than one is slope-gated: a consumer reading `slopes["broad"]`
             # would now be reading a metric name, so the nesting is deliberate rather than incidental.
             "median_non_umbrella": median, "slopes": slopes,
             "slope_regressions": [f"{shape} [{label}]" for label, shape in slope_regressions],
             "regressions": list(regressions),
             "improvements": list(improvements), "advisory": list(advisory),
             "entity_rates": rates_by_metric,
             "workflows": details,
             "metrics": {label: m for label, m in per_metric.items()}}, indent=2) + "\n")
    failed = bool(regressions) or bool(slope_regressions) or median > alarm
    units = dict(SLOPE_GATED)

    # GitHub ANNOTATIONS first - one per finding, never one per workflow (fifty-two identical
    # ::error:: lines bury the finding they report). Their prose is repeated in the verdict block
    # below, which is what the step summary shows; the annotations exist for the checks UI.
    if regressions:
        worst = max(regressions.items(), key=lambda kv: kv[1]["rel"])
        by_metric = collections.Counter(v["metric"] for v in regressions.values())
        spread = ", ".join(f"{n} {m}" for m, n in by_metric.most_common())
        gate_summary_line(
            f"{len(regressions)} regression(s) past the {args.slack:g}% band ({spread}); worst is "
            f"{worst[0]} {worst[1]['baseline']} -> {worst[1]['current']} ({worst[1]['rel']:+.1%}). "
            f"If intentional, run bench.py update and commit the new baselines in this PR", "error")
    for (label, shape), d in slope_regressions.items():
        gate_summary_line(
            f"marginal cost regression: the {shape} slope went {d['baseline']:.1f} -> {d['current']:.1f} "
            f"{units[label]} per step ({d['rel']:+.1%}, band {slope_band:.0%}). Every translation unit that "
            f"introduces units pays this, and unlike a total it cannot be explained by the library growing",
            "error")
    if median > alarm:
        gate_summary_line(f"framework-wide regression: median instantiation growth {median:+.1%} "
                          f"across the non-umbrella workflows, measured on each one's gated basis "
                          f"(use-cost where a twin exists) - this should almost never be rebaselined "
                          f"away", "error")
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
    if not failed:
        gate_summary_line(f"compile-cost gate OK (median instantiation delta {median:+.1%})"
                          + (f"; {len(improvements)} workflow(s) improved - consider tightening baselines"
                             if improvements else ""), "notice")

    # The VERDICT block: what happened and why, before any evidence. The CI page used to open with
    # three hundred rows of tables and state the conclusion underneath them - a reader met the
    # evidence before the finding it was evidence for.
    verdict = [f"## compile-cost gate: {'FAILED' if failed else 'OK'} - `{config_key(tc)}`", ""]
    moved_rows = [[key, d.get("basis", "total"), f"{d['baseline']} -> {d['current']}",
                   f"{d['rel']:+.2%}", "FAILS"]
                  for key, d in sorted(regressions.items(), key=lambda kv: -kv[1]["rel"])]
    moved_rows += [[f"scaling/{shape} slope [{label}]", "slope",
                    f"{d['baseline']:.1f} -> {d['current']:.1f} per step", f"{d['rel']:+.2%}", "FAILS"]
                   for (label, shape), d in sorted(slope_regressions.items(), key=lambda kv: -kv[1]["rel"])]
    shown = len(moved_rows)
    moved_rows += [[key, d.get("basis", "total"), f"{d['baseline']} -> {d['current']}",
                    f"{d['rel']:+.2%}", "advisory"]
                   for key, d in sorted(advisory.items(), key=lambda kv: -kv[1]["rel"])[:15 - min(shown, 15)]]
    if moved_rows:
        verdict += markdown_table(["what moved", "basis", "value", "delta", ""], moved_rows) + [""]
    verdict += [f"- median across non-umbrella workflows (gated basis): **{median:+.2%}** against the "
                f"{args.median_alarm:g}% alarm.",
                f"- {len(regressions)} regression(s) past the {args.slack:g}% band, "
                f"{len(slope_regressions)} slope regression(s) past {slope_band:.0%}, "
                f"{len(advisory)} advisory, {len(improvements)} improved past the "
                f"{args.tighten_notice:g}% tighten notice."
                + (" If the growth is intentional, run `bench.py update` and commit the new baselines."
                   if failed else ""), ""]

    # Anything past the advisory band gets NAMED, not just measured: attribution against the tree
    # the baselines were recorded from, so the finding arrives with the failure instead of waiting
    # for someone to rerun `attribute` by hand.
    movers = {}
    for key, d in {**regressions, **advisory}.items():
        wf = key.rsplit(" [", 1)[0]
        movers[wf] = max(movers.get(wf, 0), d["rel"])
    for (label, shape), d in slope_regressions.items():
        sizes = [n for n in baseline if re.fullmatch(rf"scaling/{shape}_\d+", n)]
        if sizes:  # the largest workflow of the shape carries most of the slope
            movers[max(sizes, key=lambda n: int(n.rsplit("_", 1)[1]))] = d["rel"]
    named = name_the_offenders(args, tc, repo, recorded,
                               sorted(movers, key=movers.get, reverse=True)[:args.attribute_top]) \
        if movers and args.attribute_top else []

    # Emission order: verdict, the names, what growth was priced at, the price list, the slopes -
    # then the full per-workflow evidence, collapsed (a <details> block in the summary, a foldable
    # ::group:: in the raw log), because its job is to be checkable, not to be scrolled past.
    visible = [*verdict, *named, *census_growth_lines(per_metric, baseline, measured),
               *price_list_lines(measured, tc),
               *(gate_slope_table(slopes, slope_band, tc) if slopes else [])]
    evidence = []
    for label, m in per_metric.items():
        if m:
            evidence += gate_summary_table(m, median if label == "instantiations" else None, args, tc, label)
    if evidence:
        evidence += EVIDENCE_LEGEND
    print("\n".join(visible))
    if evidence:
        print("::group::per-workflow evidence (value vs baseline on the gated basis, with headroom)")
        print("\n".join(evidence))
        print("::endgroup::")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write("\n".join(visible) + "\n")
            if evidence:
                # A 270-row table is consulted by diff tools, never by scrolling: the page points at
                # the artifact and the raw log keeps a foldable copy for spot checks.
                f.write("Per-workflow evidence - every metric's value vs baseline on the gated basis, "
                        "with the headroom to its band - is in `results/report.json` in this run's "
                        "artifact; the raw log of this step carries the same tables in a collapsed "
                        "group.\n")
    return 1 if failed else 0


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
    # The per-entity prices `check` uses to tell growth from slowness, recorded with the numbers they
    # price so the two can never drift apart. Derived from the merged results, so a filtered update
    # keeps rates consistent with whatever mix of old and new entries the file now holds.
    data["entity_rates"] = {label: rates for label, extract, _ in GATED
                            if (rates := entity_rates(results, extract))}
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
           ("function_decls", "function declarations (what a declaration-only change moves, and nothing "
                              "else does)"),
           ("decls_total", "declarations in the AST (every kind, for context under the row above)"),
           ("code_bytes", "emitted code (bytes reaching the binary - what the optimizer's time tracks)"),
           ("symbol_bytes", "symbol metadata (bytes of mangled names and relocations - linker input)"),
           ("time_ms", "wall time (ms, best of K - only trustworthy on a quiet machine)"),
           ("peak_mib", "peak compiler memory (MiB, best of K)"),
           ("mib_on_disk", "BMI size on disk (MiB)"))

# Measured, kept in the JSON, and NOT rendered: a metric that is a second shadow of one already shown.
# `symbol_bytes` ranks the corpus 0.98 with `code_bytes` and moved on exactly the same workflows across
# v2.5.0 -> master on all eight counting configurations - never once alone. Decomposing text/output_format
# says why: of its 293 KB, only 105 KB is names at all, the other 188 KB being 24 B per symbol, 24 B per
# relocation and 64 B per section - all counts of emitted entities, which is what `code_bytes` tracks. For
# the other 26 workflows it is a ~1.3 KB floor of ELF scaffolding that says nothing about the library. It
# stays in the payload because the number is free and `counts` still prints it while investigating.
#
# `types_total` joins it on the same test: rank 0.997 with `decls_total` across the corpus, and on both
# arms that move declarations at all - the hidden-friend conversions and the CRTP fallback - it moved in
# the same direction and by less. A type is created by declaring something; the library has no design
# decision that adds types without declarations, so the row would repeat the one above it.
UNRENDERED = ("symbol_bytes",)
REPORTED = tuple(m for m in METRICS if m[0] not in UNRENDERED)


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
    print(f"### what accounts for the difference: {labels[0]} vs {labels[1]}\n")
    print("\n".join(entity_diff_lines(left, right, labels[0], labels[1], args.top, args.min_delta)))


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
    twins = {ref: {name: entry["twin"] for name, entry in counts[ref].items()
                   if isinstance(entry, dict) and "twin" in entry} for ref in refs}
    for ref in refs:
        for name, entry in sorted(counts[ref].items()):
            metrics["instantiations"].setdefault(name, {})[ref] = "FAIL" if entry == "FAIL" else total(entry)
            if isinstance(entry, dict):
                metrics["const_evals"].setdefault(name, {})[ref] = entry.get("EvaluateAsConstantExpr")
                for key in ("code_bytes", "symbol_bytes", "function_decls", "decls_total"):
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
               # Hostnames cannot identify a machine (see same_machine); only runs that share this
               # explicitly-passed tag may have their wall clocks compared against each other.
               "machine_tag": args.machine_tag,
               # A filtered run measures a different corpus: its totals may not sit beside a full
               # run's in any table, though its same-machine PAIRS remain the point of measuring it.
               "subset": bool(args.workflows),
               "refs": {ref: {"mp_units_version": ".".join(map(str, detect_version(repos[ref]))),
                              **git_provenance(repos[ref])} for ref in refs},
               # The corpus reduced to its answers, carried per ref so `summary` can lead with it,
               # plus each workflow's include-twin row so use-costs stay derivable from the payload.
               "price_list": {ref: rows for ref in refs if (rows := price_list_rows(counts[ref] or {}))},
               "twins": twins,
               "metrics": metrics}
    # The page is what a human reads; the full tables are for the artifact and for diffing. Emitting
    # the page to stdout keeps `report > page.md` the natural CI idiom.
    print(render_compact([payload]))
    if args.full_output:
        full = Path(args.full_output)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(render_report([payload]) + "\n")
        print(f"full tables written to {full}", file=sys.stderr)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"report written to {out}", file=sys.stderr)


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
    """One cell telling the whole story: where it was, where it is, and by how much it moved.

    The percentage is dropped - not the cell - when there is no base worth dividing by: zero, a
    negative per-step slope (which is measurement noise, not a cost), or a base that rounds away at
    the printed precision. Those produced cells like `-0.1 -> 0.0 (-106.1%)`, and dropping the pair
    instead of the ratio lost the fact that a metric grew from nothing at all."""
    if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
        return f"{fmt_value(old)} -> {fmt_value(new)}"
    cell = f"{fmt_value(old)} -> {fmt_value(new)}"
    if old <= 0 or fmt_value(float(old)) in ("0.0", "-0.0"):
        return cell
    return f"{cell} ({(new - old) / old:+.1%})"


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


def fmt_against(value, reference, abs_unit=None):
    """The value, and how it compares with the same measurement in another configuration.

    `abs_unit` adds the absolute difference in that unit before the percentage. Wall-time cells
    need it because a percentage over denominators that vary per workflow answers no question a
    reader has - "each TU compiles 900 ms faster under modules" is the finding, "-55%" is not
    (review feedback from an expert reader who could not extract the former from the latter)."""
    if not isinstance(value, (int, float)) or not isinstance(reference, (int, float)) or not reference:
        return fmt_value(value)
    delta = f"{value - reference:+.0f}{abs_unit}, " if abs_unit else ""
    return f"{fmt_value(value)} ({delta}{(value - reference) / reference:+.0%})"


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


def metric_table(by_workflow, rows_wanted, columns, refs, totals_metric=None, labels=None, against=None,
                 abs_unit=None):
    """One table: a row per entry, a column per configuration. With exactly two refs the cell
    carries the change, so a comparison is read rather than computed across columns."""
    present = [c for c in columns if any(c in by_workflow.get(r, {}) or
                                         any(k[0] == c for k in by_workflow.get(r, {})) for r in rows_wanted)]
    if not present:
        return []
    labels = labels or {c: c for c in present}
    header = ["workflow" if not rows_wanted or not rows_wanted[0].startswith("bmi/") else "interface"]
    note = None
    if len(refs) == 2:
        old, new_ = refs
        header += [labels[c] for c in present]
        rows, flat = [], []
        for name in rows_wanted:
            values = [(by_workflow[name].get((c, old)), by_workflow[name].get((c, new_))) for c in present]
            def identical(pair):
                a, b = pair
                return (a is None and b is None) or (isinstance(a, (int, float)) and a == b)
            # A row that did not move on ANY column is a row of zeros in a table about what moved -
            # `emitted code` printed 24 of 30 workflows as `84 -> 84 (+0.0%)`. Counted, not listed. The
            # test is on values, so a `FAIL` on both sides stays visible and an `n/a -> 150` is a change.
            # Never applied to a table with a totals row, where dropping rows stops the total adding up.
            if not totals_metric and values and all(map(identical, values)) \
                    and any(v[0] is not None for v in values):
                flat.append(name)
                continue
            rows.append([name.removeprefix("bmi/"), *[fmt_change(a, b) for a, b in values]])
        if flat:
            note = (f"{len(flat)} entr{'y' if len(flat) == 1 else 'ies'} did not move on any column and "
                    f"{'is' if len(flat) == 1 else 'are'} not listed"
                    + (f": {', '.join(flat)}." if len(flat) <= 6 else f" ({len(rows)} did move)."))
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
                cells.append(fmt_against(value, other, abs_unit))
            rows.append([name.removeprefix("bmi/"), *cells])
        if totals_metric:
            rows.append([totals_label(totals_metric),
                         *[fmt_value(combine_totals(totals_metric, by_workflow, rows_wanted, c, ref))
                           for c, ref in pairs]])
    if not rows:
        return []
    return markdown_table(header, rows) + (["", note] if note else [])


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
    for shape in SCALING_SHAPES:
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
    for metric, title in REPORTED:
        by_workflow = cells.get(metric, {})
        rows = []
        measured = [False] * len(columns)
        for shape in ("narrow", "broad"):
            for what, index in (("per step", 0), ("intercept", 1)):
                cols, anything, moved = [], False, len(refs) < 2
                for i, column in enumerate(columns):
                    values = [scaling_fit(by_workflow, column, ref).get(shape) for ref in refs]
                    values = [v[index] if v else None for v in values]
                    moved = moved or (len(values) == 2 and values[0] != values[1])
                    # Emptiness is decided on the VALUES, never on the rendered cell: a two-ref cell
                    # renders as `n/a -> n/a`, which no `!= "n/a"` test recognises, and that is how a
                    # BMI-only metric came to print a whole table of nothing but n/a.
                    if any(v is not None for v in values):
                        measured[i] = anything = True
                    cols.append(fmt_change(values[0], values[1]) if len(refs) == 2 else fmt_value(
                        None if values[0] is None else round(values[0], 1)))
                # In a comparison, a line that is identical on every configuration says only "this
                # metric does not scale with user code" - which the single-ref report already says, and
                # which was four fifths of the emitted-code and symbol-metadata tables here.
                if anything and moved:
                    rows.append([f"{shape} - {what}", *cols])
        # A configuration that cannot produce this metric AT ALL (no GCC gives counts) is not a
        # workflow held back by a REQUIRES floor, which is what the legend says `n/a` means: drop the
        # column rather than print a stripe of n/a that the legend misexplains - and that costs a
        # third of the table's width in every count metric.
        keep = [i for i, ok in enumerate(measured) if ok]
        if rows and keep:
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
    "**Declarations** count something instantiations cannot see. Before the compiler can decide whether "
    "to *use* a function it must first *build* it: read its signature, its constraints and its template "
    "parameters into memory. A function written inside a class template is built again for every version "
    "of that class the program uses - a few hundred to a few thousand times in these workflows - even "
    "when nothing ever calls it. Moving such a function out of the template removes all those copies "
    "while the compiler stamps out exactly the same templates as before, so the instantiation count above "
    "does not move at all and the declaration count falls by a few percent. Both numbers are exact and "
    "machine-independent, and the project gates on both.",
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


def findings(cells, keys, refs, tags=None):
    """The few sentences worth reading, ranked. Everything else is in the tables below them.

    Written for someone who has never used the library: each item names the effect and what follows
    from it, rather than quoting a metric at them."""
    out = []
    inst = cells.get("instantiations", {})
    time = cells.get("time_ms", {})
    memory = cells.get("peak_mib", {})
    plain = sorted([k for k in keys if "-modules" not in k and "-importstd" not in k],
                   key=lambda k: (version_of(k), k))
    workflows = sorted(w for w in inst if not w.startswith(("bmi/", "include/")))

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

        # 2. the finding the instantiation count is structurally unable to make. Reported only when the
        # two disagree: a declaration-side change (a hidden friend leaving a class template, which is
        # redeclared by every specialization of it) instantiates nothing differently, so the item above
        # says "nothing moved" about work that really did disappear.
        decls = cells.get("function_decls", {})
        dmoved = {w: d for w in workflows
                  if (d := pct(decls.get(w, {}).get((col, old)), decls.get(w, {}).get((col, new))))
                  is not None}
        if moved and dmoved:
            dmedian = statistics.median(dmoved.values())
            if abs(dmedian) > 0.005 and abs(dmedian - median) > 0.005:
                out.append(f"A typical workflow declares **{abs(dmedian):.1%} "
                           f"{'fewer' if dmedian < 0 else 'more'} functions** against `{new}` than "
                           f"against `{old}` (median across {len(dmoved)}), while instantiating "
                           f"{abs(median):.1%} {'fewer' if median < 0 else 'more'} templates. Those are "
                           f"different kinds of work and they move independently: a function declared "
                           f"inside a class template is declared again by every specialization of that "
                           f"template, whether or not anything ever calls it, so declarations can be "
                           f"removed in bulk without changing a single instantiation.")

        # 3. the finding totals cannot show: constant cost and marginal cost moving apart
        fits = [scaling_fit(inst, col, ref) for ref in (old, new)]
        if all(f.get("broad") for f in fits):
            (slope_old, base_old), (slope_new, base_new) = (f["broad"] for f in fits)
            ds, db = pct(slope_old, slope_new), pct(base_old, base_new)
            if ds is not None and db is not None and (ds > 0.05 > db or abs(ds - db) > 0.1):
                # Which way a file comes out is the CROSSOVER, not a dichotomy: total cost is
                # intercept + slope * units, so the intercept saving buys a file that many units
                # before the steeper slope eats it. Asserting "a file using many units does not gain"
                # was false at +0.8% per unit - the crossover was ~4500 unit types, and the largest
                # workflow in this corpus uses 256.
                biggest = max((int(m.group(1)) for w in workflows
                               for m in [re.fullmatch(r"scaling/broad_(\d+)", w)] if m), default=0)
                saved, extra = base_old - base_new, slope_new - slope_old  # per file, and per unit type
                scale = (f" The largest workflow measured here uses {biggest} distinct unit types."
                         if biggest else "")
                if saved > 0 and extra > 0:  # cheaper to start, dearer per unit: small files win
                    verdict = (f"A file gains until it uses about {saved / extra:,.0f} distinct unit types "
                               f"and pays beyond that.{scale}")
                elif saved < 0 and extra < 0:  # dearer to start, cheaper per unit: large files win
                    verdict = (f"A file pays until it uses about {saved / extra:,.0f} distinct unit types "
                               f"and gains beyond that.{scale}")
                elif extra <= 0:
                    verdict = "Both parts got cheaper, so every file gains whatever its size."
                else:
                    verdict = "Both parts got dearer, so every file pays whatever its size."
                out.append(f"Constant and marginal cost moved in opposite directions: using the library "
                           f"at all became **{abs(db):.0%} {'cheaper' if db < 0 else 'dearer'}**, while "
                           f"each additional distinct unit type became **{abs(ds):.0%} "
                           f"{'dearer' if ds > 0 else 'cheaper'}** ({slope_old:.0f} -> {slope_new:.0f} "
                           f"instantiations per unit). {verdict}")

        # 4. is this run's wall clock worth reading at all?
        control = pct(time.get("bmi/std", {}).get((col, old)), time.get("bmi/std", {}).get((col, new)))
        if control is not None and abs(control) > 0.1:
            out.append(f"Treat wall-clock numbers in this run as noise: building the standard library "
                       f"module is identical work in both columns, yet differs by {control:+.0%}. The "
                       f"instantiation counts are unaffected - they are exact.")

    # 5. what consuming the library as modules is worth - from the NEWEST toolchain that measured it,
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
        # A wall-clock claim needs both arms on ONE machine (see same_machine): the fleet's arms each
        # get their own runner, and comparing those produced a "modules are faster" belief that
        # dissolved under a controlled measurement. Without a shared tag this speaks in
        # instantiations only, which are exact everywhere.
        i = [pct(inst[w].get((base, ref)), inst[w].get((col, ref))) for w in workflows if w in inst]
        i = [x for x in i if x is not None]
        tagged = (tags or {}).get(col) and (tags or {}).get(col) == (tags or {}).get(base)
        once = (f", after building the module interfaces once: {build / 1000:.0f} s"
                f"{f' and {disk:.0f} MiB on disk' if disk else ''}." if build else ".")
        if t and m and tagged:
            out.append(f"Consuming the library as C++20 modules (`{col}`) compiles each file "
                       f"**{abs(statistics.median(t)):.0%} {'faster' if statistics.median(t) < 0 else 'slower'}** "
                       f"and uses **{abs(statistics.median(m)):.0%} "
                       f"{'more' if statistics.median(m) > 0 else 'less'} memory** on one machine"
                       + once)
        elif i:
            out.append(f"Consuming the library as C++20 modules (`{col}`) needs "
                       f"**{abs(statistics.median(i)):.0%} "
                       f"{'fewer' if statistics.median(i) < 0 else 'more'} instantiations** per file"
                       + once
                       + " Its wall clock is not reported here: this run measured the two arms on "
                         "different machines, and that comparison is noise (a control workflow using "
                         "no mp-units at all has measured 40% apart across runners).")
        break  # one such statement is enough; the section below has the rest

    # 6. how much of any improvement is really the compiler - only across keys of the same SHAPE, so a
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

    # 7. n/a is not failure - counted in WORKFLOWS per ref, not in cells: the same absent workflow
    # shows up once per metric per configuration, so a cell count read as "224 cells" when the fact
    # was "4 workflows do not exist at v2.5.0". A configuration that produces no counts at all (any
    # GCC) is not a workflow being skipped, and bmi/ rows exist only under modules.
    absent = {}
    for metric, per_workflow in cells.items():
        measured = {(k, ref) for row in per_workflow.values() for (k, ref) in row}
        for name, row in per_workflow.items():
            if name.startswith("bmi/"):
                continue
            for key, ref in measured:
                if (key, ref) not in row:
                    absent.setdefault(ref, set()).add(name)
    if absent:
        per_ref = ", ".join(f"{len(names)} at `{ref}`" for ref, names in
                            sorted(absent.items(), key=lambda kv: refs.index(kv[0]) if kv[0] in refs else 0))
        out.append(f"Some workflows do not exist on every ref measured ({per_ref}) - their `// REQUIRES:` "
                   f"floor is newer than that ref, so they read `n/a` and are excluded from both sides of "
                   f"every total above.")
    return out


def corpus_pair(then_payload, then_ref, now_payload, now_ref, metric):
    """A metric's corpus total on each side, over the entries BOTH sides measured (`bmi/*` excluded).

    The intersection is the whole point: a workflow that exists on only one side - a `REQUIRES` floor
    the older ref does not meet, or a workflow added since the previous run - would otherwise land in
    one total and read as the library moving. On this run the difference was not cosmetic: summing each
    side's own set said -6.7% where the intersection says -19.0%. Returns (then, now, entries dropped)."""
    then_per, now_per = then_payload["metrics"].get(metric, {}), now_payload["metrics"].get(metric, {})
    names = {n for n in set(then_per) | set(now_per) if not n.startswith(("bmi/", "include/"))}
    pairs = [(then_per.get(n, {}).get(then_ref), now_per.get(n, {}).get(now_ref)) for n in names]
    both = [(a, b) for a, b in pairs if isinstance(a, (int, float)) and isinstance(b, (int, float))]
    if not both:
        return None
    return sum(a for a, _ in both), sum(b for _, b in both), len(names) - len(both)


def broad_slope(payload, ref):
    """Instantiations per step from the scaling/broad series, and nothing else. This number is printed
    beside the deterministic columns: a wall-time slope there would read as the same kind of number
    while carrying runner noise - which is how a GCC arm with no counts reported a 13% "slope" move."""
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


SUMMARY_METRICS = (("instantiations", "inst"), ("function_decls", "decls"), ("time_ms", "time"))


def comparison_rows(cell_pairs):
    """The four change columns every per-configuration summary carries, from rows that already hold
    (then, now, relative) triples.

    Declarations are a column here and not only in the tables below, because this is the one table a
    reader is guaranteed to see: a change that moves declarations and nothing else would otherwise be
    summarized as "nothing moved" directly above a report full of cells saying it did."""
    return [[f"{r['inst'][0]} -> {r['inst'][1]} ({r['inst'][2]:+.1%})" if "inst" in r else "n/a",
             f"{r['decls'][0]} -> {r['decls'][1]} ({r['decls'][2]:+.1%})" if "decls" in r else "n/a",
             f"{r['slope'][0]:.1f} -> {r['slope'][1]:.1f} ({r['slope'][2]:+.1%})" if "slope" in r else "n/a",
             f"{r['time'][0]} -> {r['time'][1]} ms ({r['time'][2]:+.1%})" if "time" in r else "n/a"]
            for r in cell_pairs]


def payload_rows(payloads):
    """Payloads in the report's own column order, keyed."""
    return [(p.get("config_key") or p["cxx"], p) for p in
            sorted(payloads, key=lambda q: config_order(q.get("config_key") or q["cxx"]))]


def range_summary(payloads, refs):
    """What the RANGE this run measured costs, per configuration - the answer to "what did these
    library changes buy", when the run measured two refs itself.

    This is the section a dispatch with `compare_ref` needs and the reason run_over_run must not speak
    for such a run: comparing this run's newest ref against the previous run's newest compares master
    with master, reports "nothing moved", and contradicts the range the run exists to measure. Both
    sides of every row here come from ONE runner session, measured interleaved rep-major, so even the
    wall-time column is a real A/B rather than two sessions subtracted."""
    old, new = refs[0], refs[-1]
    lines, unmatched, partial = [], 0, set()
    for key, p in payload_rows(payloads):
        if old not in p.get("refs", {}) or new not in p.get("refs", {}):
            partial.add(key)  # named below: an arm that measured one end of the range must not vanish
            continue
        row = {"key": key}
        for metric, name in SUMMARY_METRICS:
            got = corpus_pair(p, old, p, new, metric)
            if got and got[0]:
                a, b, gaps = got
                row[name] = (a, b, (b - a) / a)
                unmatched = max(unmatched, gaps)
        sa, sb = broad_slope(p, old), broad_slope(p, new)
        if isinstance(sa, float) and isinstance(sb, float) and sa:
            row["slope"] = (sa, sb, (sb - sa) / sa)
        if "inst" in row or "time" in row:
            lines.append(row)
    if not lines:
        return None, []
    md = [f"## What `{old}` -> `{new}` costs, per configuration", ""]
    md += markdown_table(["configuration", "instantiations", "declarations", "broad slope", "wall time"],
                         [[r["key"], *cols] for r, cols in zip(lines, comparison_rows(lines))])
    md += ["", f"Corpus totals over the workflows BOTH refs compile (`bmi/*` excluded"
           + (f"; {unmatched} workflow(s) exist on only one of the two refs and are left out of both "
              f"sides, so an added workflow cannot read as growth" if unmatched else "") + "). Every row "
           "measured its two refs in one session, interleaved, so the wall-time column is an A/B on one "
           "machine - still machine-dependent, so compare down a column and never across; the count "
           "columns are exact. `declarations` counts function declarations rather than work done, and is "
           "the only column that moves when a function is declared in fewer places without anything "
           "being instantiated differently. `n/a` means the configuration produces no counts at all "
           "(any GCC)."
           + (f" {len(partial)} configuration(s) measured only one end of the range and have no row: "
              f"{', '.join(f'`{k}`' for k in sorted(partial, key=config_order))}." if partial else ""), ""]
    counted = [r for r in lines if "inst" in r]
    findings_out = []
    if counted:
        best = min(counted, key=lambda r: r["inst"][2])
        worst = max(counted, key=lambda r: r["inst"][2])
        slopes = [r for r in lines if "slope" in r]
        tail = ""
        if slopes:
            sb_, sw = min(slopes, key=lambda r: r["slope"][2]), max(slopes, key=lambda r: r["slope"][2])
            tail = (f" The marginal cost per step went the other way on every configuration that "
                    f"produces counts, from {sb_['slope'][2]:+.1%} to {sw['slope'][2]:+.1%} (worst on "
                    f"`{sw['key']}`)." if sb_["slope"][2] > 0 else
                    f" The marginal cost per step ranges from {sb_['slope'][2]:+.1%} on `{sb_['key']}` to "
                    f"{sw['slope'][2]:+.1%} on `{sw['key']}`.")
        if worst["inst"][2] < 0:
            findings_out.append(
                f"Every one of the {len(counted)} configuration(s) that produce counts needs fewer "
                f"instantiations for `{new}` than for `{old}`, from {worst['inst'][2]:+.1%} on "
                f"`{worst['key']}` to {best['inst'][2]:+.1%} on `{best['key']}`, so this is the library "
                f"changing and not one toolchain's quirk.{tail}")
        else:
            findings_out.append(
                f"The corpus total did not move the same way everywhere: {best['inst'][2]:+.1%} on "
                f"`{best['key']}` but {worst['inst'][2]:+.1%} on `{worst['key']}`, so a single number for "
                f"`{old}` -> `{new}` would be wrong for someone - read the row for your configuration."
                f"{tail}")
    return "\n".join(md), findings_out


def run_over_run(payloads, previous):
    """What moved since the last CI run, per configuration - the control for a run that measured ONE
    ref, and only for such a run: when the run measured a range, `range_summary` speaks instead.

    Arms are matched by configuration key; each arm's newest measured ref is compared against the
    previous run's newest, because that pair is "the tree CI watched then" vs "the tree it watches
    now". Refs are named in the output so an unchanged tree reads as what it is - a control."""
    prev_by_key = {p.get("config_key") or p["cxx"]: p for p in previous}
    lines, movers, unmatched, skipped = [], [], 0, set()
    for key, p in payload_rows(payloads):
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
        then_info, now_info = q["refs"][ro], p["refs"][rn]
        row = {"key": key,
               "then": then_info.get("mp_units_describe", ro), "now": now_info.get("mp_units_describe", rn),
               # Compared on the SHA, never on the describe: `git describe` needs the tags to be
               # fetched, so one run called this commit `v2.5.0-695-g6111211d` and the next called it
               # `6111211`, and the column read as a tree change while every count said otherwise.
               "same_tree": bool(then_info.get("mp_units_sha")) and
                            then_info.get("mp_units_sha") == now_info.get("mp_units_sha"),
               "sha": (now_info.get("mp_units_sha") or "")[:7]}
        for metric, name in SUMMARY_METRICS:
            got = corpus_pair(q, ro, p, rn, metric)
            if got and got[0]:
                a, b, gaps = got
                row[name] = (a, b, (b - a) / a)
                unmatched = max(unmatched, gaps)
        sa, sb = broad_slope(q, ro), broad_slope(p, rn)
        if isinstance(sa, float) and isinstance(sb, float) and sa:
            row["slope"] = (sa, sb, (sb - sa) / sa)
        if "inst" in row or "time" in row:
            lines.append(row)
            if "inst" in row:
                movers.append(row)
    if not lines:
        return None, []
    if all(r["same_tree"] for r in lines):
        # Same commit on both sides: every deterministic column is 0.0% BY CONSTRUCTION, so the table
        # would be a dozen rows of zeros whose only varying column is the one its own caption tells you
        # to ignore. What such a run does establish is the runner's noise floor - and that is a sentence.
        noise = max((abs(r["time"][2]) for r in lines if "time" in r), default=None)
        return None, [f"The previous run measured this same commit (`{lines[0]['sha']}`), so this run is a "
                      f"control: every count below is identical to last time by construction, and there is "
                      f"no table for it."
                      + (f" What it does measure is the CI runners' noise - identical work took up to "
                         f"{noise:.0%} longer or shorter than last time, which is the floor any wall-clock "
                         f"number here has to clear before it means anything." if noise else "")]
    # One pair on every row is a column of one repeated fact: state it once instead.
    pairs = {(r["then"], r["now"]) for r in lines}
    md = ["## Since the previous run", ""]
    md += markdown_table(
        ["configuration", *(["measured then -> now"] if len(pairs) > 1 else []),
         "instantiations", "declarations", "broad slope", "wall time"],
        [[r["key"], *([f"`{r['then']}` -> `{r['now']}`"] if len(pairs) > 1 else []), *cols]
         for r, cols in zip(lines, comparison_rows(lines))])
    # `n/a` here is not a REQUIRES floor: it is a configuration that cannot produce the metric at all,
    # and saying so is the difference between "GCC gives no counts" and "something failed".
    md += ["", (f"Measured `{lines[0]['then']}` -> `{lines[0]['now']}` on every configuration. "
                if len(pairs) == 1 else "")
           + "Corpus totals and slopes over the workflows both runs measured (`bmi/*` excluded); "
           "`n/a` in a deterministic column means the configuration produces no counts (any GCC), not "
           "that a measurement is missing. The slope is instantiations per step; `declarations` counts "
           "function declarations, which move without any instantiation when a function is declared in "
           "fewer places. Wall time compares different runner sessions, so treat its column "
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


def price_row_4(row):
    """Payloads written before categories carry (what, value, source) rows; normalize to 4 fields."""
    return row if len(row) == 4 else ["", *row]


def price_list_section(payloads, refs):
    """One price-list table per configuration that carried one, matched by row label across refs.

    This leads the report because it is the report's answer; every table below it is evidence. Only
    configurations that produce counts (clang) have one - the rows are count-denominated."""
    lines = []
    for key, p in payload_rows(payloads):
        per_ref = p.get("price_list") or {}
        cols = [r for r in refs if r in per_ref]
        if not cols:
            continue
        values, order = {}, []
        for ref in cols:
            for _cat, what, value, _source in map(price_row_4, per_ref[ref]):
                if what not in values:
                    values[what] = {}
                    order.append(what)
                values[what][ref] = value
        header = ["what one thing costs (instantiations)", *(cols if len(cols) > 1 else ["value"])]
        rows = [[what, *[values[what].get(r, "n/a") for r in cols]] for what in order]
        lines += [f"### price list - `{key}`", "", *markdown_table(header, rows), ""]
    if lines:
        lines = ["## Price list", "", PRICE_LIST_NOTE, "", *lines]
    return lines


def prepare(payloads, previous=None):
    """The shared reduction both renderers start from: refs oldest-first, one key per payload, the
    (metric, workflow, (key, ref)) -> value cell store, the provenance notes, and the comparison
    section (range for a two-ref run, previous-run control for a single-ref one)."""
    refs = refs_oldest_first(payloads)
    # A run that measured a range answers for that range. Comparing its newest ref against the previous
    # run's newest would compare master with master, report "nothing moved", and lead a report whose
    # every cell reads `v2.5.0 -> master` - the previous run is the control for a SINGLE-ref run only.
    comparison_md, comparison_findings = (None, [])
    if len(refs) >= 2:
        comparison_md, comparison_findings = range_summary(payloads, refs)
    elif previous:
        comparison_md, comparison_findings = run_over_run(payloads, previous)
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
    return refs, keys, cells, notes, comparison_md, comparison_findings


def bar(value, vmax, width=10, ch="█", decimals=None):
    """A magnitude as repeated blocks in a code span, the number beside it. Only full-width glyphs:
    the partial-width ones (and the light-shade track) render ragged in enough fonts that review
    screenshots showed ghosting. Zero-length bars render as no bar at all.

    `decimals` pins the precision, which a column of numbers needs: letting each cell choose printed
    `125` next to `119.8` and `7 s` next to `12.5 s`, and a column whose decimal point wanders is
    read one cell at a time."""
    if not isinstance(value, (int, float)) or not isinstance(vmax, (int, float)) or vmax <= 0:
        return fmt_value(value)
    if decimals is not None:
        shown = f"{value:,.{decimals}f}"
    else:
        shown = f"{value:,}" if isinstance(value, int) or value == int(value) else f"{value:,.1f}"
        shown = shown.removesuffix(".0") if "." in shown else shown
    # At least one block for anything nonzero: a bare number in a column of bars reads as a missing
    # measurement, when what it means is "too small to draw at this scale".
    n = max(1, round(width * value / vmax)) if value > 0 else 0
    return f"`{ch * n}` {shown}".strip() if n else shown


def same_machine(p, q):
    """Whether two payloads' wall clocks may be compared. Hostnames cannot decide this: every
    GitHub runner reports the same generic name while the actual CPUs differ by model and vendor
    (one run's fleet spanned Xeon 8573C, EPYC 7763 and EPYC 9V74 under a single hostname). Only an
    explicit shared `--machine-tag`, set by a job that runs both measurements on one VM, counts."""
    return bool(p.get("machine_tag")) and p.get("machine_tag") == q.get("machine_tag")


def config_family_rows(keys):
    """Configuration keys grouped for the per-configuration table: plain builds sorted by family and
    version, each followed by its own -importstd/-modules variants, indented."""
    plain = sorted([k for k in keys if "-modules" not in k and "-importstd" not in k], key=config_order)
    ordered = []
    for base in plain:
        ordered.append((base, base))
        stem = base
        for suffix in ("-importstd", "-modules-importstd", "-modules"):
            variant = stem + suffix if stem + suffix in keys else None
            if variant and variant not in [k for k, _ in ordered]:
                label = "&nbsp;&nbsp;└ " + ("import std" if suffix == "-importstd" else "modules")
                ordered.append((variant, label))
    for k in sorted(keys, key=config_order):  # variants whose plain build was not measured
        if k not in [key for key, _ in ordered]:
            ordered.append((k, k))
    return ordered


def render_compact(payloads, previous=None):
    """The page: what changed, one row per configuration, the price list split by question, the
    modules interfaces, and the safety ladder - two screens that answer "what does using this cost
    and did it move", with every per-workflow cell left to the full report in the artifact.

    Every caption states what its metric IS and how it was measured, because the page is read by
    people who were not in the room. Wall-clock rows are compared only within one machine: the
    per-configuration table's time bars are softened (each row is its own runner), the ladder
    renders only when a headers payload and a modules payload share a host."""
    refs, keys, cells, notes, comparison_md, comparison_findings = prepare(payloads, previous)
    newest = refs[-1] if refs else None
    inst_cells = cells.get("instantiations", {})
    time_cells = cells.get("time_ms", {})
    info = next((p["refs"][newest] for p in payloads if newest in p.get("refs", {})), {}) if newest else {}
    lines = [f"# Compile cost - mp-units {info.get('mp_units_describe', newest or '?')} "
             f"({len(keys)} configuration{'s' if len(keys) != 1 else ''})", ""]

    tags = {(p.get("config_key") or p["cxx"]): p.get("machine_tag") for p in payloads}
    story = comparison_findings + findings(cells, keys, refs, tags)
    if story:
        lines += ["## What changed", "", *[f"{i}. {s}" for i, s in enumerate(story, 1)], ""]
    if comparison_md:
        lines += [comparison_md, ""]

    # one row per configuration - full-corpus runs only, since the column is a corpus total
    full_keys = [(p.get("config_key") or p["cxx"]) for p in payloads if not p.get("subset")]
    per_config = []
    for key, label in config_family_rows(full_keys):
        tot = sum(v for w, row in inst_cells.items() if not w.startswith(("bmi/", "include/"))
                  and isinstance((v := row.get((key, newest))), int))
        slopes = scaling_fit(inst_cells, key, newest)
        slope = slopes.get("broad", (None,))[0]
        wall = sum(v for w, row in time_cells.items() if not w.startswith(("bmi/", "include/"))
                   and isinstance((v := row.get((key, newest))), (int, float)))
        per_config.append((label, tot or None, slope, wall or None))
    if per_config:
        one_machine = len({p.get("machine_tag") for p in payloads if not p.get("subset")}) == 1 \
            and all(p.get("machine_tag") for p in payloads if not p.get("subset"))
        imax = max((t for _, t, _, _ in per_config if t), default=0)
        smax = max((s for _, _, s, _ in per_config if s), default=0)
        wmax = max((w for _, _, _, w in per_config if w), default=0)
        rows = [[label, bar(t, imax) if t else "n/a",
                 bar(s, smax, 12, decimals=1) if s else "n/a",
                 bar(w / 1000, wmax / 1000, 10, "▒", decimals=0) + " s" if w else "n/a"]
                for label, t, s, w in per_config]
        # A range run keeps this table too - the comparison section above carries the deltas, this
        # one the absolute picture across the fleet, which is what the page opens with either way.
        lines += ["## Per configuration"
                  + (f" - values for `{newest}`" if len(refs) > 1 else ""), "",
                  *markdown_table(["configuration", "instantiations", "broad slope /step",
                                   "wall clock"], rows), "",
                  "> **Instantiations** count the templates the compiler stamps out for the whole corpus "
                  "(`InstantiateClass` + `InstantiateFunction` from clang's `-ftime-trace`) - "
                  "bit-deterministic for a pinned compiler, so any two rows are exactly comparable. The "
                  "**broad slope** comes from the `scaling/` series as (cost at 256 steps - cost at 16 "
                  "steps) / 240: what one more distinct derived unit costs in user code. **Wall clock** "
                  "is an untraced compile of the whole corpus, best-of-K; "
                  + ("every arm here shares one machine, so its bars are a fair comparison too"
                     if one_machine else
                     "each configuration runs on its own CI machine, so its softer bars say "
                     "direction, not magnitude")
                  + ". Variants are "
                  "indented under their compiler; the bars carry the cross-compiler comparison even "
                  "where rows are not adjacent.", ""]

    # the price list, one table per question, from the newest gate-capable payload
    # The price list speaks for ONE configuration, so it must be the most representative one: the
    # newest plain build that produced counts - how the library is consumed today, on the best
    # compiler available - not whichever payload happens to sort first (that was clang 17).
    candidates = [(k, p) for k, p in payload_rows(payloads) if newest in (p.get("price_list") or {})]
    price_payload = next(
        (p for _, p in sorted(candidates, reverse=True,
                              key=lambda kp: (("-modules" not in kp[0] and "-importstd" not in kp[0]),
                                              version_of(kp[0])))), None)
    if price_payload:
        key = price_payload.get("config_key") or price_payload["cxx"]  # heading names its source
        rows4 = [price_row_4(r) for r in price_payload["price_list"][newest]]
        price_lines = []

        # INCLUDING - a curated ladder of include sets, small to large within each family. The page
        # deliberately does not list every ISQ chapter: the corpus measures nine and gains more as
        # the library grows, while the reader needs the shape of the cost, which one shallow chapter
        # against the whole of ISQ shows exactly as the codata tiers do. Every chapter still lives in
        # the artifact's full tables, still gates, and still prices the rates.
        by_source = {s: (what, value) for _c, what, value, s in rows4 if _c == "include"}
        incl_rows = []
        for wf, label in PAGE_INCLUDE_ROWS:
            if wf not in by_source:
                continue
            what, value = by_source[wf]
            iv = int(m.group()) if (m := re.match(r"\d+", value)) else None
            entities = re.search(r"(\d+) entities", what)  # the generic row names the census
            shown = label + (f" - {entities.group(1)} entities" if entities else "")
            incl_rows.append([shown, iv, value[len(str(iv)):] if iv is not None else "",
                              time_cells.get(wf, {}).get((key, newest))])
        if incl_rows:
            imax = max((r[1] for r in incl_rows if r[1]), default=0)
            mmax = max((r[3] for r in incl_rows if isinstance(r[3], (int, float))), default=0)
            table = [[what, bar(iv, imax) + tail if iv else "n/a",
                      bar(ms / 1000, mmax / 1000, 10, "\u2592", decimals=1) + " s"
                      if isinstance(ms, (int, float)) else "n/a"]
                     for what, iv, tail, ms in incl_rows]
            price_lines += [f"### including headers - `{key}`", "",
                            *markdown_table(["what a TU pays before its first line of code",
                                             "instantiations", "wall clock"], table), "",
                            "> One traced compile of an empty-main TU per include set - the constant "
                            "cost a file pays whatever its size, both columns from one machine. The "
                            "chapters and tiers shown are representative; every measured include set "
                            "is in the run artifact's full tables.", ""]

        # DEFINING - one row per entity kind, cheapest first, with both prices side by side: what the
        # system charges for one as it ships it, and what the bare definition costs on its own. The
        # gap between the columns IS the ecosystem, which is the finding the two numbers exist for.
        shipped = {what: v for c, what, v, _s in rows4 if c == "define"}
        bare = {what: v for c, what, v, _s in rows4 if c == "define-bare"}
        def as_float(v):
            try:
                return float(str(v).split("/")[0])
            except ValueError:
                return None
        kinds = sorted(set(shipped) | set(bare), key=lambda k: as_float(shipped.get(k)) or 0)
        if kinds:
            table = [[k, shipped.get(k, "n/a"),
                      bare.get(k, "n/a").removesuffix("/step") if k in bare else "-"] for k in kinds]
            price_lines += [f"### defining entities - `{key}`", "",
                            *markdown_table(["what one definition costs", "as its system ships it",
                                             "the bare definition"], table), "",
                            "> Marginal prices, not averages of mixed kinds: the shipped rate comes "
                            "from a pair of umbrella rows whose entity censuses differ in (almost) "
                            "only that kind, with previously-priced kinds subtracted; the bare price "
                            "is a `scaling/define_*` slope, where the entity is defined in the "
                            "workflow itself. The difference between the columns is the ecosystem an "
                            "entity arrives with - symbol tables, kind resolution, equations, "
                            "uncertainty payloads. The quantity spec's **0.0 is real, not a gap**: "
                            "under the deducing-this API a leaf spec derives from "
                            "`quantity_spec<parent>`, so every sibling leaf shares that one "
                            "specialization and the next one instantiates nothing new - it still "
                            "costs declarations and one constant evaluation, which the gate watches "
                            "separately.", ""]

        # WRITING - composing quantities, then printing them: two different questions that shared one
        # table until a reader asked why they were together.
        for cat, title, what_col, caption in (
                ("use", f"composing quantities - `{key}`",
                 "what user code costs, over the headers it includes",
                 "> Every row is a workflow MINUS an empty-main twin with its exact includes, so the "
                 "header cost cancels and only the written code remains. The first two rows are the "
                 "SAME computation mirrored line for line, differing only in whether references "
                 "carry a quantity spec - so their ratio is the price of safety level 5 per distinct "
                 "derived quantity."),
                ("print", f"printing quantities - `{key}`", "output facility",
                 "> The `text/` family shares one workload header and differs only in the output "
                 "facility, so these differences price the facility alone. Use-cost, as above.")):
            cat_rows = [r for r in rows4 if r[0] == cat]
            if not cat_rows:
                continue
            table = [[what, value] for _c, what, value, _s in cat_rows]
            if cat == "use":
                slopes_here = {s.split("/")[-1].removesuffix(" slope"): v
                               for _c, _w, v, s in cat_rows if s.endswith(" slope")}
                b, tb = slopes_here.get("broad"), slopes_here.get("typed_broad")
                if b and tb:  # the pair exists to be subtracted; do it for the reader
                    bf, tf = float(b.split("/")[0]), float(tb.split("/")[0])
                    idx = next(i for i, r in enumerate(table) if "TYPED" in r[0]) + 1
                    table.insert(idx, ["\u2192 the level-5 tax per derived quantity",
                                       f"**+{tf - bf:.1f}/step, {tf:.1f} / {bf:.1f} = "
                                       f"{tf / bf:.2f}\u00d7**"])
            price_lines += [f"### {title}", "",
                            *markdown_table([what_col, "instantiations"], table), "", caption, ""]

        if price_lines:
            lines += ["## Price list", "", *price_lines]

    # C++20 modules: the interfaces, then what consuming them buys
    lines += modules_compact_section(payloads, refs, cells)
    lines += ladder_compact_section(payloads, refs)

    lines += ["---", "",
              "**Full data:** this run's `compile-cost-report` artifact carries this page as markdown, "
              "the complete per-workflow x per-metric x per-configuration tables, and one JSON per "
              "arm for machine diffing. This page is the summary of that artifact, never a substitute "
              "for it.", "",
              "<details><summary>How this was measured</summary>", "", *notes, "", "</details>"]
    return "\n".join(lines)


def modules_compact_section(payloads, refs, cells):
    """The per-interface breakdown (the abstraction lives in `mp_units.systems`), then per compiler
    what consuming the module buys - instantiations trusted, wall clock cross-runner-guarded."""
    newest = refs[-1] if refs else None
    rows_bmi, per_compiler = [], []
    # One breakdown table, from the NEWEST modules arm: the interfaces evolve with the compiler, and
    # the reader wants today's numbers, not the oldest supported toolchain's.
    subsets = {(q.get("config_key") or q["cxx"]) for q in payloads if q.get("subset")}
    modules_keys = sorted((k for k, _ in payload_rows(payloads) if "-modules" in k),
                          key=lambda k: (k not in subsets, version_of(k), k), reverse=True)
    for key, p in sorted(payload_rows(payloads),
                         key=lambda kp: modules_keys.index(kp[0]) if kp[0] in modules_keys else 99):
        if "-modules" not in key:
            continue
        tm, disk = p["metrics"].get("time_ms", {}), p["metrics"].get("mib_on_disk", {})
        mem, inst = p["metrics"].get("peak_mib", {}), p["metrics"].get("instantiations", {})
        interfaces = [w for w in BMI_ORDER if isinstance(tm.get(w, {}).get(newest), (int, float))]
        if interfaces and not rows_bmi:
            for w in interfaces:
                iv = inst.get(w, {}).get(newest)
                rows_bmi.append([w.removeprefix("bmi/"),
                                 f"{tm[w][newest] / 1000:.1f} s",
                                 f"{disk.get(w, {}).get(newest, 0) or 0:.1f} MiB",
                                 f"{mem.get(w, {}).get(newest, 0) or 0:,.0f} MiB",
                                 f"{iv:,}" if isinstance(iv, int) else "n/a"])
            def col(metric, combine=sum):
                vals = [v for w in interfaces if isinstance((v := metric.get(w, {}).get(newest)),
                                                            (int, float))]
                return combine(vals) if vals else None
            rows_bmi.append(["**total**", f"**{(col(tm) or 0) / 1000:.1f} s**",
                             f"**{col(disk) or 0:.1f} MiB**",
                             f"peak **{col(mem, max) or 0:,.0f} MiB**", f"**{col(inst) or 0:,}**"])
            bmi_key = key
        # Against the PLAIN build of the same compiler - headers, no `import std` - never against an
        # intermediate configuration: "modules are worth X" has to mean X against how the library is
        # consumed today. `counterparts` strips both tokens, which is also what makes the A/B job's
        # pair (`...-ab` vs `...-modules-importstd-ab`) resolve at all.
        plain_key = counterparts([key], [k for k, _ in payload_rows(payloads)]).get(key)
        plain = next((q for k2, q in payload_rows(payloads) if k2 == plain_key), None)
        if plain is None:
            continue
        def corpus(payload, metric):
            per = payload["metrics"].get(metric, {})
            vals = [v for w, row in per.items() if not w.startswith(("bmi/", "include/"))
                    and isinstance((v := row.get(newest)), (int, float))]
            return sum(vals) if vals else None
        mi, hi = corpus(p, "instantiations"), corpus(plain, "instantiations")
        mt, ht = corpus(p, "time_ms"), corpus(plain, "time_ms")
        build = sum(v for w in BMI_ORDER if isinstance((v := tm.get(w, {}).get(newest)), (int, float)))
        wall = (f"{(mt - ht) / ht:+.0%}" + ("" if same_machine(p, plain) else " (different runners)")
                if mt and ht else "n/a")
        per_compiler.append([key, f"{build / 1000:.1f} s" if build else "n/a",
                             f"**{(mi - hi) / hi:+.1%}**" if mi and hi else "n/a", wall])
    per_compiler.sort(key=lambda r: config_order(r[0]))
    out = []
    if rows_bmi:
        out += [f"## C++20 modules", "", f"### module interfaces - `{bmi_key}`", "",
                *markdown_table(["interface", "build", "on disk", "peak memory", "instantiations"],
                                rows_bmi), "",
                "> Each interface is built once per configuration; `mp_units.systems` is where the "
                "abstraction lives - it includes every system umbrella (the full CODATA tables among "
                "them), which is most of the build time, the disk footprint, the peak memory and the "
                "instantiations of the whole set.", ""]
    if per_compiler:
        out += [*markdown_table(["compiler", "interface build (once)",
                                 "consumer instantiations vs headers", "consumer wall clock"],
                                per_compiler), "",
                "> Consumer instantiations are deterministic and exactly comparable; a consumer "
                "wall-clock delta is only meaningful when both arms ran on one machine, and is "
                "labelled when they did not.", ""]
    return out


def ladder_compact_section(payloads, refs):
    """The safety ladder, time-first, three questions - rendered only from payload pairs that share
    a HOST, because its wall-clock columns compare headers against `import mp_units;` and a
    cross-runner comparison of those is exactly the mistake this suite exists to prevent."""
    newest = refs[-1] if refs else None
    rungs = (("raw doubles", "safety/raw_doubles"), ("levels 1-4", "safety/simple_quantities"),
             ("+ level 5", "safety/typed_quantities"), ("+ level 6", "safety/affine_quantities"))
    pair = None
    for key, p in payload_rows(payloads):
        if "-modules" in key:
            continue
        mod = next((q for k2, q in payload_rows(payloads) if "-modules" in k2
                    and same_machine(p, q)), None)
        if mod and all(w in p["metrics"].get("time_ms", {}) for _, w in rungs):
            pair = (p, mod)
            break
    if not pair:
        return []
    plain, mod = pair

    def t(payload, name):
        v = payload["metrics"].get("time_ms", {}).get(name, {}).get(newest)
        return v if isinstance(v, (int, float)) else None

    def use_inst(payload, name):
        inst = payload["metrics"].get("instantiations", {})
        twin = (payload.get("twins") or {}).get(newest, {}).get(name)
        v, tw = inst.get(name, {}).get(newest), inst.get(twin, {}).get(newest) if twin else None
        return v - tw if isinstance(v, int) and isinstance(tw, int) else None

    def twin_t(payload, name):
        twin = (payload.get("twins") or {}).get(newest, {}).get(name)
        return t(payload, twin) if twin else None

    def bar_rows(value_of, ch, unit="", decimals=None):
        """One table's rows with bars scaled to the table's own maximum, shared across both
        columns - the columns carry the same unit, and cross-column length comparison is the point."""
        values = {(wf, i): value_of(side, wf)
                  for _, wf in rungs for i, side in enumerate((plain, mod))}
        vmax = max((v for v in values.values() if isinstance(v, (int, float))), default=0)
        return [[label, *[(bar(values[(wf, i)], vmax, 10, ch, decimals) + unit
                           if values[(wf, i)] is not None else "n/a")
                          for i in (0, 1)]] for label, wf in rungs]

    def use_t(payload, name):
        """Wall clock of the code the user wrote: the whole TU minus its inclusion twin. A difference
        of two best-of-K measurements, so a few milliseconds here is the noise floor, not a signal -
        but it keeps this table in the same unit as the two around it, and gives the raw-doubles rung
        a nonzero baseline that ratios can actually be taken against."""
        whole, twin = t(payload, name), twin_t(payload, name)
        return max(0, whole - twin) if whole is not None and twin is not None else None

    incl = bar_rows(twin_t, "▒", " ms", decimals=0)
    use = bar_rows(use_t, "▒", " ms", decimals=0)
    total = bar_rows(t, "▒", " ms", decimals=0)
    # The counts stay beside the times: they are what the gate reads and the only exactly comparable
    # column, while the time is what the user waits for.
    for row, (_label, wf) in zip(use, rungs):
        row.append(" -> ".join(f"{v:,}" if (v := use_inst(side, wf)) is not None else "n/a"
                               for side in (plain, mod)))
    if all(row[1] == "n/a" for row in incl + total):
        return []
    return ["## The safety ladder", "",
            "One small engineering computation, written four times with identical std includes and "
            "identical printed output - only the safety level differs. Both columns of every table "
            f"were measured on ONE machine (`{plain.get('host', '?')}`), which is what makes the "
            "headers-vs-import comparison fair.", "",
            "### 1. getting the library into the TU", "",
            *markdown_table(["rung", "headers", "import mp_units;"], incl), "",
            "> An empty-main TU with each rung's exact includes, untraced, best-of-K: the constant "
            "cost paid whatever the file's size.", "",
            "### 2. the code the user writes", "",
            *markdown_table(["rung", "headers", "import mp_units;",
                             "instantiations (headers -> import)"], use), "",
            "> Each rung MINUS its inclusion twin: the wall clock of the code the user wrote, in the "
            "same unit as the tables around it, with the deterministic counts beside it. Those "
            "counts are exactly comparable and are what the gate reads; the times are differences of "
            "two best-of-K measurements, so single-digit milliseconds there are the noise floor. The "
            "raw-doubles rung instantiates exactly 0 templates - a TU computing with `double` has "
            "none - so read the library rungs' counts as ABSOLUTE additions rather than as a ratio "
            "against zero; between two library rungs a ratio is meaningful again, which is what the "
            "level-5 tax in the price list is.", "",
            "### 3. what the user actually waits for", "",
            *markdown_table(["rung", "whole TU (headers)", "whole TU (import)"], total), "",
            "> The whole translation unit, compiled as a user would compile it. Wall clock and "
            "instantiations answer different questions - a modules consumer can instantiate almost "
            "nothing and still pay seconds of lazy BMI deserialization at the first use of a name - "
            "which is why this section shows both, from one machine.", ""]


def render_report(payloads, previous=None):
    refs, keys, cells, notes, comparison_md, comparison_findings = prepare(payloads, previous)

    module_keys = [k for k in keys if "-modules" in k]
    header_keys = [k for k in keys if "-modules" not in k]
    workflows = sorted({n for m in cells.values() for n in m if not n.startswith("bmi/")})
    interfaces = [n for n in BMI_ORDER if any(n in m for m in cells.values())]

    lines = []
    for metric, title in REPORTED:
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
        for metric, title in REPORTED:
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
                      "library is consumed today, not against an intermediate configuration. Wall-time "
                      "cells carry the ABSOLUTE ms difference first, because each workflow's own total "
                      "is a different denominator and a column of percentages over varying denominators "
                      "answers no question a reader has. Consumer "
                      "cost only: the interface build above is paid once per configuration, not per "
                      "translation unit.", ""]
        for metric, title in REPORTED:
            by_workflow = cells.get(metric, {})
            rows = [w for w in workflows if any((c, r) in by_workflow.get(w, {}) for c in cols for r in refs)]
            table = metric_table(by_workflow, rows, cols, refs, None, labels,
                                 against if len(refs) == 1 else None,
                                 abs_unit=" ms" if metric == "time_ms" else None) if rows else []
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
    tags = {(p.get("config_key") or p["cxx"]): p.get("machine_tag") for p in payloads}
    story = comparison_findings + findings(cells, keys, refs, tags)
    if story:
        story = ["## What changed", "", *[f"{i}. {s}" for i, s in enumerate(story, 1)], ""]
    comparison = [comparison_md, ""] if comparison_md else []
    prices = price_list_section(payloads, refs)
    lines = [*story, *comparison, *prices, *PREAMBLE, "<details><summary>All measurements</summary>", "", *legend, "",
             *lines, "</details>", ""]
    lines += ["<details><summary>How this was measured</summary>", ""] + notes + [
        "", "Instantiation counts are bit-deterministic for a pinned compiler and peak memory varies by "
        "<0.1% between runs, so both are comparable across every column above. Wall time is not: each "
        "configuration is measured on its own runner, and CI runners differ in CPU and in load, so "
        "compare time only within a column, never between columns. Presentation-quality timings need a "
        "quiet machine and `bench.py time`, which interleaves the arms.", "", "</details>"]
    return "\n".join(lines)


def cmd_summary(args):
    """Render the combined PAGE from several `report --output` files (e.g. one per compiler): the
    two-screen summary goes to stdout and the job summary, the full per-workflow tables only where
    `--full-output` says - a page nobody can read is not a report, and the full tables' consumers
    are diff tools and `attribute`, which read the artifact."""
    payloads = [json.loads(Path(f).read_text()) for f in args.reports]
    previous = [json.loads(Path(f).read_text()) for f in (args.previous or [])]
    text = render_compact(payloads, previous or None)
    print(text)
    if args.full_output:
        full = Path(args.full_output)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(render_report(payloads, previous or None) + "\n")
        print(f"full tables written to {full}", file=sys.stderr)
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
    t.add_argument("--pin", type=int, metavar="CPU",
                   help="bind each compile to this CPU via taskset. Halves the within-arm spread on a "
                        "jittery host (23-31%% -> 8-12%% measured on WSL2) and costs nothing on a quiet "
                        "one; pair it with a high --reps")
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
    g.add_argument("--attribute-top", type=int, default=3, metavar="N",
                   help="name the offenders: for the N workflows that moved the most past the advisory "
                        "band, diff instantiation events by entity against the tree the baselines were "
                        "recorded from (default 3; 0 disables). Costs two traced compiles per workflow, "
                        "paid only when something moved")
    g.add_argument("--worktree-cache", default=str(ROOT / ".worktrees"),
                   help="where to materialize the baseline commit for attribution")

    r = sub.add_parser("report", help="all metrics this compiler can produce: the two-screen page "
                                      "on stdout, JSON + full tables via flags")
    r.add_argument("refs", nargs="*", help="git refs to measure (default: WORKTREE)")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--workflows", nargs="*")
    r.add_argument("--output", help="write the JSON payload (feed several of these to `summary`)")
    r.add_argument("--full-output", metavar="MD",
                   help="write the complete per-workflow tables as markdown (the artifact copy; "
                        "stdout carries the readable page)")
    r.add_argument("--machine-tag", default="",
                   help="opaque label marking runs whose wall clocks may be compared with each "
                        "other; hostnames cannot do this (every CI runner reports the same name "
                        "over different CPUs), so same-machine sections render only across reports "
                        "sharing a tag")
    r.add_argument("--worktree-cache", default=str(ROOT / ".worktrees"))

    sub.add_parser("key", help="print the baseline file this configuration resolves to")

    a = sub.add_parser("attribute", help="diff two measurements by entity, to explain a difference")
    a.add_argument("refs", nargs="*", help="one or two git refs (default: WORKTREE)")
    a.add_argument("--workflows", nargs="*", help="one or two workflows (substring filters)")
    a.add_argument("--top", type=int, default=20, help="entities to show (default 20)")
    a.add_argument("--min-delta", type=int, default=1, help="ignore entities moving less than this")
    a.add_argument("--worktree-cache", default=str(ROOT / ".worktrees"))

    s = sub.add_parser("summary", help="merge report JSONs into the two-screen page (+ job summary)")
    s.add_argument("reports", nargs="+", help="JSON files written by `report --output`")
    s.add_argument("--full-output", metavar="MD",
                   help="write the complete per-workflow tables as markdown (the artifact copy; "
                        "stdout carries the readable page)")
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
