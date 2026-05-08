#!/usr/bin/env python3
"""Print a class outline (signatures only, no method bodies).

Used to answer "list methods/fields of class X" queries without paying the
cost of reading the full class source.

Usage:
  # Use the workspace's classes.json index to locate the file.
  python3 class_outline.py <workspace_dir> <fqcn>

  # Operate on a single .java file directly.
  python3 class_outline.py --file path/to/Foo.java [--fqcn com.foo.Foo]

  # Operate on a single class extracted via `jadx --single-class`.
  jadx --single-class com.foo.Foo --single-class-output /tmp/Foo.java app.apk
  python3 class_outline.py --file /tmp/Foo.java
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from _java_regex import (  # noqa: E402
    RE_CLASS,
    RE_FIELD,
    RE_METHOD,
    RE_PACKAGE,
    indent_width,
    line_starts,
    offset_to_line,
    strip_comments_and_strings,
)


def _read(path: Path) -> str:
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def _resolve_via_workspace(ws: Path, fqcn: str) -> tuple[Path, str]:
    """Look up fqcn in classes.json; return (java_file, fqcn)."""
    cj = ws / "classes.json"
    if not cj.exists():
        raise FileNotFoundError(f"classes.json not found at {cj}; run build_xref_index.py first")
    data = json.loads(cj.read_text())

    rec = data["classes"].get(fqcn)
    if rec is None:
        # Try by simple name.
        simple = fqcn
        cands = data["by_simple_name"].get(simple, [])
        if len(cands) == 1:
            rec = data["classes"][cands[0]]
            fqcn = cands[0]
        elif len(cands) > 1:
            raise ValueError(f"ambiguous class '{simple}': {cands}")
        else:
            raise ValueError(f"class not found: {fqcn}")
    file_rel = rec["file"]
    return ws / "sources" / file_rel, fqcn


def render_outline(java_path: Path, target_fqcn: str | None = None) -> str:
    src = _read(java_path)
    stripped = strip_comments_and_strings(src)
    starts = line_starts(src)

    pkg_match = RE_PACKAGE.search(stripped)
    package = pkg_match.group(1) if pkg_match else ""

    # Build event list (class/method/field) in source order.
    events: list[tuple[int, str, re.Match]] = []
    for m in RE_CLASS.finditer(stripped):
        events.append((m.start(), "class", m))
    for m in RE_METHOD.finditer(stripped):
        events.append((m.start(), "method", m))
    for m in RE_FIELD.finditer(stripped):
        events.append((m.start(), "field", m))
    events.sort(key=lambda x: x[0])

    # Walk events with indent stack to build per-class records, but ALSO
    # capture the full source-line text for each method/field signature
    # so we can render it.
    @dataclass_lite
    class Cls:
        fqcn: str
        kind: str
        decl_line: int
        decl_text: str
        methods: list[tuple[str, int, str]]  # (name, line, decl_text)
        fields: list[tuple[str, int, str]]
        inner: list  # list[Cls]

    # We use plain dicts to avoid dataclass overhead for this small script.
    classes_by_fqcn: dict[str, dict] = {}
    stack: list[tuple[int, str]] = []  # (indent, fqcn)

    src_lines = src.split("\n")

    for offset, kind_ev, match in events:
        line = offset_to_line(starts, offset)
        ind = indent_width(match.group(1))
        # Pop stack until top has strictly smaller indent.
        while stack and stack[-1][0] >= ind:
            stack.pop()
        # Reconstruct the source-text decl line, trimmed.
        decl_text = src_lines[line - 1].rstrip() if line - 1 < len(src_lines) else ""

        if kind_ev == "class":
            class_kind = match.group(2)
            simple = match.group(3)
            outer = stack[-1][1] if stack else None
            if outer:
                fqcn = f"{outer}${simple}"
            elif package:
                fqcn = f"{package}.{simple}"
            else:
                fqcn = simple
            classes_by_fqcn[fqcn] = {
                "fqcn": fqcn,
                "kind": class_kind,
                "decl_line": line,
                "decl_text": decl_text.lstrip(),
                "methods": [],
                "fields": [],
                "inner": [],
                "outer": outer,
            }
            stack.append((ind, fqcn))

        elif kind_ev == "method":
            if not stack:
                continue
            method_name = match.group(2)
            classes_by_fqcn[stack[-1][1]]["methods"].append(
                (method_name, line, _trim_method_decl(decl_text))
            )

        elif kind_ev == "field":
            if not stack:
                continue
            field_name = match.group(2)
            classes_by_fqcn[stack[-1][1]]["fields"].append(
                (field_name, line, decl_text.strip().rstrip(";").rstrip())
            )

    # Decide what to render.
    if target_fqcn is not None:
        rec = classes_by_fqcn.get(target_fqcn)
        if rec is None:
            # Try by simple name.
            simple = target_fqcn.rsplit(".", 1)[-1].rsplit("$", 1)[-1]
            cands = [c for c in classes_by_fqcn if c.split(".")[-1].split("$")[-1] == simple]
            if len(cands) == 1:
                rec = classes_by_fqcn[cands[0]]
            elif len(cands) > 1:
                print(f"class_outline: ambiguous '{target_fqcn}'; candidates: {cands}", file=sys.stderr)
                return ""
            else:
                print(f"class_outline: class not found in this file: {target_fqcn}", file=sys.stderr)
                return ""
        return _render(rec, classes_by_fqcn)
    # No target — render all top-level classes in order.
    out_chunks = []
    for rec in classes_by_fqcn.values():
        if rec.get("outer") is None:
            out_chunks.append(_render(rec, classes_by_fqcn))
    return "\n".join(out_chunks)


def _render(rec: dict, all_classes: dict) -> str:
    lines = [f"{rec['decl_text']}  (line {rec['decl_line']})"]
    if rec["fields"]:
        lines.append("  fields:")
        for name, line_no, decl in rec["fields"]:
            lines.append(f"    {decl}  // line {line_no}")
    if rec["methods"]:
        lines.append("  methods:")
        for name, line_no, decl in rec["methods"]:
            lines.append(f"    {decl}  // line {line_no}")
    inner = [c for c in all_classes.values() if c.get("outer") == rec["fqcn"]]
    if inner:
        lines.append("  inner classes:")
        for c in inner:
            lines.append(f"    {c['decl_text']}  // line {c['decl_line']}")
    return "\n".join(lines) + "\n"


def _trim_method_decl(text: str) -> str:
    """Cut a method decl line at the opening `{` to drop the body brace."""
    text = text.strip()
    idx = text.find("{")
    if idx >= 0:
        text = text[:idx].rstrip()
    return text


# Tiny shim so the @dataclass_lite decorator is a no-op (we don't actually use it
# above — keeping the source compact without importing dataclasses).
def dataclass_lite(cls):
    return cls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace_or_fqcn", nargs="?", help="workspace dir (then pass fqcn) — or unused if --file is given")
    parser.add_argument("fqcn", nargs="?", help="fully qualified class name to outline")
    parser.add_argument("--file", help="path to a .java file (alternative to workspace+fqcn)")
    args = parser.parse_args()

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"class_outline: file not found: {path}", file=sys.stderr)
            sys.exit(2)
        target = args.fqcn  # may be None — render all
        print(render_outline(path, target_fqcn=target), end="")
        return

    if not args.workspace_or_fqcn or not args.fqcn:
        print("class_outline: usage: class_outline.py <workspace> <fqcn>  OR  --file path.java [--fqcn ...]", file=sys.stderr)
        sys.exit(2)

    ws = Path(args.workspace_or_fqcn)
    fqcn = args.fqcn
    try:
        java_path, resolved = _resolve_via_workspace(ws, fqcn)
    except FileNotFoundError as e:
        print(f"class_outline: {e}\n  Hint: run scripts/build_xref_index.py \"{ws}\" first.", file=sys.stderr)
        sys.exit(2)
    except ValueError as e:
        print(f"class_outline: {e}", file=sys.stderr)
        sys.exit(3)
    print(render_outline(java_path, target_fqcn=resolved), end="")


if __name__ == "__main__":
    main()
