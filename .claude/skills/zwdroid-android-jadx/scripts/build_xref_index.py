#!/usr/bin/env python3
"""Build a cross-reference index from a jadx-decompiled workspace.

Reads $WS/sources/ and emits four JSON files in $WS/:
  - classes.json         : class index (FQCN -> file/methods/fields)
  - xrefs.json           : class_refs / method_calls / field_access maps
  - package_layout.json  : top-level package -> file count
  - index_meta.json      : version + completeness flag (atomic last-write)

Designed to be run at the tail of Phase 2 in the zwdroid-android-jadx skill.

Usage:
  python3 build_xref_index.py <workspace_dir>
  python3 build_xref_index.py <workspace_dir> --rebuild

Best-effort: per-file errors are logged to $WS/index_build.log and the file
is skipped. The script writes index_meta.json last and atomically; absence
or `complete: false` signals the SKILL to fall back to grep.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from collections import defaultdict
from multiprocessing import Pool, cpu_count
from pathlib import Path

# Add this script's dir to sys.path so worker subprocesses can import _java_regex.
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from _java_regex import (  # noqa: E402
    extract_definitions,
    extract_references,
)

INDEX_VERSION = 1
SKIP_BASENAMES = {"R.java", "BuildConfig.java"}
SKIP_PREFIXES = ("R$",)  # files like R$styleable.java
MAX_FILE_BYTES = 2 * 1024 * 1024  # skip files larger than 2 MB

# Globals set in worker init for stage 2.
_W_SIMPLE_TO_FQCNS: dict[str, list[str]] = {}
_W_METHOD_NAMES: set[str] = set()
_W_FIELD_NAMES: set[str] = set()


def should_skip_file(path: Path) -> str | None:
    """Return reason string if file should be skipped, else None."""
    if path.name in SKIP_BASENAMES:
        return "skip_basename"
    if path.name.startswith(SKIP_PREFIXES):
        return "skip_prefix"
    if path.name == "package-info.java":
        # Allow but caller treats specially.
        return None
    return None


def _read_text(path: Path) -> str:
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


# --------------------------- stage 1: definitions ---------------------------

def _stage1_worker(args):
    """Per-file worker for stage 1. Returns (rel_file, classes_or_None, error)."""
    sources_root, rel_path_str = args
    rel = Path(rel_path_str)
    abs_path = Path(sources_root) / rel
    try:
        size = abs_path.stat().st_size
        if size > MAX_FILE_BYTES:
            return rel_path_str, None, f"size:{size}"
        skip = should_skip_file(rel)
        if skip:
            return rel_path_str, None, skip
        text = _read_text(abs_path)
        records = extract_definitions(rel_path_str, text)
        # Convert ClassRecord dataclasses to plain dicts for picklability speed.
        out = [
            {
                "fqcn": r.fqcn,
                "file": r.file,
                "kind": r.kind,
                "package": r.package,
                "outer": r.outer,
                "methods": r.methods,
                "fields": r.fields,
            }
            for r in records
        ]
        return rel_path_str, out, None
    except Exception as e:
        return rel_path_str, None, f"exc:{type(e).__name__}:{e}"


# --------------------------- stage 2: references ---------------------------

def _stage2_init(simple_to_fqcns, method_names, field_names):
    global _W_SIMPLE_TO_FQCNS, _W_METHOD_NAMES, _W_FIELD_NAMES
    _W_SIMPLE_TO_FQCNS = simple_to_fqcns
    _W_METHOD_NAMES = method_names
    _W_FIELD_NAMES = field_names


def _stage2_worker(args):
    sources_root, rel_path_str = args
    rel = Path(rel_path_str)
    abs_path = Path(sources_root) / rel
    try:
        size = abs_path.stat().st_size
        if size > MAX_FILE_BYTES:
            return rel_path_str, None, f"size:{size}"
        skip = should_skip_file(rel)
        if skip:
            return rel_path_str, None, skip
        text = _read_text(abs_path)
        refs = extract_references(
            rel_path_str,
            text,
            _W_SIMPLE_TO_FQCNS,
            _W_METHOD_NAMES,
            _W_FIELD_NAMES,
        )
        # Pre-pickle as plain types.
        return (
            rel_path_str,
            {
                "class_refs": {k: sorted(v) for k, v in refs.class_refs.items()},
                "method_calls": {k: sorted(v) for k, v in refs.method_calls.items()},
                "field_access": {k: sorted(v) for k, v in refs.field_access.items()},
            },
            None,
        )
    except Exception as e:
        return rel_path_str, None, f"exc:{type(e).__name__}:{e}"


# --------------------------- orchestration ---------------------------

def discover_files(sources_root: Path) -> list[str]:
    rels: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(sources_root):
        for fname in filenames:
            if fname.endswith(".java"):
                rel = Path(dirpath, fname).relative_to(sources_root)
                rels.append(str(rel))
    rels.sort()
    return rels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", help="path to jadx workspace dir, e.g. ./.jadx-workspace/<sha1>")
    parser.add_argument("--rebuild", action="store_true", help="ignore existing index_meta.json")
    parser.add_argument("--quiet", action="store_true", help="reduce stdout chatter")
    parser.add_argument("--processes", type=int, default=0, help="worker processes (default: cpu_count)")
    args = parser.parse_args()

    ws = Path(args.workspace).resolve()
    sources = ws / "sources"
    if not sources.is_dir():
        print(
            f"build_xref_index: sources/ not found at {sources}\n"
            f"  Hint: run jadx -d \"{ws}\" \"<input.apk>\" first, or pass a different workspace path.",
            file=sys.stderr,
        )
        sys.exit(2)

    meta_path = ws / "index_meta.json"
    if meta_path.exists() and not args.rebuild:
        try:
            existing = json.loads(meta_path.read_text())
            if existing.get("complete") and existing.get("index_version") == INDEX_VERSION:
                if not args.quiet:
                    print(f"build_xref_index: skipping; existing index complete (built_at={existing.get('built_at')}). Pass --rebuild to force.", file=sys.stderr)
                sys.exit(0)
        except Exception:
            pass  # corrupted meta, proceed to rebuild

    log_path = ws / "index_build.log"
    log = open(log_path, "w")
    def _log(msg):
        log.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        log.flush()

    t0 = time.time()
    procs = args.processes or max(1, cpu_count())
    if not args.quiet:
        print(f"build_xref_index: scanning {sources} (procs={procs})", file=sys.stderr)

    rels = discover_files(sources)
    total_files = len(rels)
    if total_files == 0:
        _log("no .java files found")
        # Write a minimal meta and exit gracefully.
        meta = {
            "index_version": INDEX_VERSION,
            "complete": True,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "files_processed": 0,
            "files_skipped": 0,
            "skipped_reason_counts": {},
            "elapsed_seconds": round(time.time() - t0, 2),
        }
        _atomic_write_json(meta_path, meta)
        return

    skipped_reasons: dict[str, int] = defaultdict(int)
    classes: dict[str, dict] = {}              # fqcn -> record
    by_simple_name: dict[str, list[str]] = defaultdict(list)
    package_files: dict[str, int] = defaultdict(int)

    # Stage 1
    if not args.quiet:
        print(f"build_xref_index: stage 1 (definitions) — {total_files} files", file=sys.stderr)
    with Pool(processes=procs) as pool:
        args_iter = ((str(sources), rel) for rel in rels)
        for rel_path_str, recs, err in pool.imap_unordered(_stage1_worker, args_iter, chunksize=64):
            if err is not None and recs is None:
                # Distinguish skip reasons from actual exceptions.
                kind = err.split(":", 1)[0]
                skipped_reasons[kind] += 1
                if kind == "exc":
                    _log(f"stage1 fail {rel_path_str}: {err}")
                continue
            # Track package layout from successful files.
            top_pkg = ""
            for r in recs or []:
                pkg = r["package"]
                if pkg:
                    top_pkg = ".".join(pkg.split(".")[:2]) if "." in pkg else pkg
                    break
            if top_pkg:
                package_files[top_pkg] += 1
            for r in (recs or []):
                fqcn = r["fqcn"]
                if fqcn in classes:
                    # Duplicate FQCN (can happen with anonymous inner $1, $2 collisions). Merge.
                    existing = classes[fqcn]
                    for mname, lines in r["methods"].items():
                        existing["methods"].setdefault(mname, [])
                        existing["methods"][mname].extend(lines)
                    for fname, line in r["fields"].items():
                        existing["fields"].setdefault(fname, line)
                else:
                    classes[fqcn] = r
                    simple = fqcn.split(".")[-1].split("$")[-1]
                    by_simple_name[simple].append(fqcn)

    # Build name sets for stage 2 post-filter.
    method_names: set[str] = set()
    field_names: set[str] = set()
    for r in classes.values():
        method_names.update(r["methods"].keys())
        field_names.update(r["fields"].keys())

    if not args.quiet:
        print(f"build_xref_index: stage 1 done — {len(classes)} classes, {len(method_names)} method names, {len(field_names)} field names ({time.time()-t0:.1f}s)", file=sys.stderr)

    # Populate method/field owner lookups for stage 2's keying.
    _populate_owners(classes)

    # Write classes.json early so partial progress survives.
    classes_path = ws / "classes.json"
    _atomic_write_json(classes_path, {
        "classes": classes,
        "by_simple_name": dict(by_simple_name),
    })

    pkg_layout = {
        "by_top_package": dict(sorted(package_files.items(), key=lambda kv: -kv[1])),
        "total_classes": len(classes),
        "total_files": total_files,
    }
    _atomic_write_json(ws / "package_layout.json", pkg_layout)

    # Stage 2
    if not args.quiet:
        print(f"build_xref_index: stage 2 (references) — {total_files} files", file=sys.stderr)

    class_refs: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))   # fqcn -> file -> [lines]
    method_calls: dict[str, list[dict]] = defaultdict(list)
    field_access: dict[str, list[dict]] = defaultdict(list)

    # Plain dict for pickle to workers — defaultdict pickles fine but be explicit.
    simple_to_fqcns_plain = dict(by_simple_name)

    init_args = (simple_to_fqcns_plain, method_names, field_names)
    with Pool(processes=procs, initializer=_stage2_init, initargs=init_args) as pool:
        args_iter = ((str(sources), rel) for rel in rels)
        for rel_path_str, refs, err in pool.imap_unordered(_stage2_worker, args_iter, chunksize=64):
            if err is not None and refs is None:
                kind = err.split(":", 1)[0]
                skipped_reasons[kind] += 1
                if kind == "exc":
                    _log(f"stage2 fail {rel_path_str}: {err}")
                continue
            # class_refs: keyed by FQCN
            for fqcn, lines in refs["class_refs"].items():
                class_refs[fqcn][rel_path_str].extend(lines)
            # method_calls: re-key by SimpleClass.method.
            # We produce <SimpleName>.<method> entries for *every* class that defines that method.
            # That's at most a handful per method due to the def-name set filter.
            for method, lines in refs["method_calls"].items():
                owners = _METHOD_OWNERS.get(method)
                if not owners:
                    continue
                for owner_simple in owners:
                    method_calls[f"{owner_simple}.{method}"].append({"file": rel_path_str, "lines": lines})
            for fname, lines in refs["field_access"].items():
                owners = _FIELD_OWNERS.get(fname)
                if not owners:
                    continue
                for owner_simple in owners:
                    field_access[f"{owner_simple}.{fname}"].append({"file": rel_path_str, "lines": lines})

    # Compact lines lists per (key, file): merge if same file appears twice (rare but possible).
    def _flatten(d):
        out: dict[str, list[dict]] = {}
        for key, file_to_lines in d.items():
            out[key] = [
                {"file": fpath, "lines": sorted(set(lines))}
                for fpath, lines in file_to_lines.items()
            ]
        return out

    # Merge method_calls / field_access entries that share the same file under one row.
    def _merge_per_file(d: dict[str, list[dict]]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for key, entries in d.items():
            by_file: dict[str, set[int]] = defaultdict(set)
            for e in entries:
                by_file[e["file"]].update(e["lines"])
            out[key] = [
                {"file": fpath, "lines": sorted(lines)}
                for fpath, lines in sorted(by_file.items())
            ]
        return out

    xrefs_out = {
        "class_refs": _flatten(class_refs),
        "method_calls": _merge_per_file(method_calls),
        "field_access": _merge_per_file(field_access),
    }
    _atomic_write_json(ws / "xrefs.json", xrefs_out)

    elapsed = round(time.time() - t0, 2)
    files_processed = total_files - sum(skipped_reasons.values())
    meta = {
        "index_version": INDEX_VERSION,
        "complete": True,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files_processed": files_processed,
        "files_skipped": sum(skipped_reasons.values()),
        "skipped_reason_counts": dict(skipped_reasons),
        "elapsed_seconds": elapsed,
    }
    _atomic_write_json(meta_path, meta)
    log.close()

    if not args.quiet:
        print(f"build_xref_index: done — {files_processed} indexed, {sum(skipped_reasons.values())} skipped, {elapsed}s", file=sys.stderr)
        print(f"  outputs: classes.json, xrefs.json, package_layout.json, index_meta.json (in {ws})", file=sys.stderr)


# Module-level lookups populated before stage 2.
_METHOD_OWNERS: dict[str, list[str]] = {}
_FIELD_OWNERS: dict[str, list[str]] = {}


def _atomic_write_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)


# Entrypoint with method/field owner index built between stages.
def _populate_owners(classes: dict[str, dict]):
    global _METHOD_OWNERS, _FIELD_OWNERS
    method_owners: dict[str, list[str]] = defaultdict(list)
    field_owners: dict[str, list[str]] = defaultdict(list)
    for fqcn, rec in classes.items():
        simple = fqcn.split(".")[-1].split("$")[-1]
        for m in rec["methods"]:
            method_owners[m].append(simple)
        for fld in rec["fields"]:
            field_owners[fld].append(simple)
    _METHOD_OWNERS = dict(method_owners)
    _FIELD_OWNERS = dict(field_owners)


# Patch main to populate owners after stage 1. We do this by monkey-patching here
# in a later-pass pattern is overkill; instead, restructure main to call _populate_owners.
# Simpler: redefine main to insert _populate_owners after classes is built.
# (Inlining: we re-export main here intentionally with the populate step.)

def _main_with_owners():
    # Trampoline pattern not needed — we inline the call in main above by adjusting call order.
    # See main() — _populate_owners is called explicitly there.
    main()


if __name__ == "__main__":
    main()
