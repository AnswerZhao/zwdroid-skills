"""Shared regex helpers for parsing jadx-decompiled Java sources.

Used by build_xref_index.py and class_outline.py.

Requires Python 3.9+. The `from __future__ import annotations` line keeps
modern annotation syntax (`str | None`, `dict[str, int]`) parseable on
3.9 without runtime evaluation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# ---------------------------------------------------------------------------
# Pass 0 — strip comments and string literals (preserve length & line breaks)
# ---------------------------------------------------------------------------

# Order in alternation matters: longest first so re.sub picks the right one.
# - Triple-quoted strings (Java 15+ text blocks) — match before regular strings
# - Block comments /* ... */ (with DOTALL so newlines inside are kept length-wise)
# - Line comments // ...
# - String literals "..." with escape support
# - Char literals '...'
_STRIP_PATTERN = re.compile(
    r'"""(?:\\.|[^"\\]|"(?!""))*?"""'   # Java 15+ text blocks
    r'|/\*[\s\S]*?\*/'                  # block comments
    r'|//[^\n]*'                        # line comments
    r'|"(?:\\.|[^"\\\n])*"'             # double-quoted strings
    r"|'(?:\\.|[^'\\\n])*'",            # single-quoted (char) literals
)


def _replacement(match: re.Match) -> str:
    """Replace each matched span with same-length spaces, preserving newlines."""
    s = match.group(0)
    # Keep newlines so line numbers stay correct; everything else becomes space.
    return "".join(" " if ch != "\n" else "\n" for ch in s)


def strip_comments_and_strings(src: str) -> str:
    """Return src with all comments and string/char literals replaced by spaces.

    Length and line offsets are preserved so callers can map matches back to
    line numbers via the original source's line_starts array.
    """
    return _STRIP_PATTERN.sub(_replacement, src)


# ---------------------------------------------------------------------------
# Pass 1 — definitions
# ---------------------------------------------------------------------------

# Note all these patterns are line-anchored (re.MULTILINE) and tolerant of
# leading whitespace. They run on the comment-stripped source.

RE_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)

RE_IMPORT = re.compile(
    r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", re.MULTILINE
)

# class / interface / enum / @interface / record
# Captures: indent, kind, name
_CLASS_MODIFIERS = (
    r"(?:public|private|protected|static|final|abstract|sealed|non-sealed|strictfp)\s+"
)
RE_CLASS = re.compile(
    r"^([ \t]*)"                     # indent (no \n!)
    rf"(?:{_CLASS_MODIFIERS})*"      # any number of modifiers
    r"(class|interface|enum|@interface|record)\s+"
    r"(\w+)",                        # name
    re.MULTILINE,
)

# A method line — opening brace or semicolon (abstract methods).
# Reject lines whose "name" is actually a control-flow keyword.
_METHOD_KEYWORD_DENYLIST = {
    "if", "for", "while", "switch", "try", "catch",
    "synchronized", "do", "return", "throw", "static", "new",
}
_METHOD_MODIFIERS = (
    r"(?:public|private|protected|static|final|abstract|synchronized|"
    r"native|strictfp|default)\s+"
)
RE_METHOD = re.compile(
    r"^([ \t]*)"                                 # indent (no \n!)
    rf"(?:{_METHOD_MODIFIERS})*"                 # modifiers
    r"(?:<[^>]+>[ \t]+)?"                        # generic <T extends Foo>
    r"(?:[\w.<>\[\],?][\w.<>\[\],?\s]*?)[ \t]+"  # return type (lazy, must start with non-space)
    r"(\w+)[ \t]*"                               # method name
    r"\([^)]*\)[ \t]*"                           # params (no nested parens)
    r"(?:throws\s+[\w.,\s]+)?[ \t]*"             # throws clause
    r"[{;]",                                     # body or abstract semicolon
    re.MULTILINE,
)

# Field declarations. We restrict to lines that look like field decls vs.
# local variables by requiring at least one access modifier or a `static`/`final`.
_FIELD_MODIFIERS = (
    r"(?:public|private|protected|static|final|volatile|transient)\s+"
)
RE_FIELD = re.compile(
    r"^([ \t]*)"                                 # indent (no \n!)
    rf"(?:{_FIELD_MODIFIERS})+"                  # at least one modifier (mandatory!)
    r"(?:[\w.<>\[\],?][\w.<>\[\],?\s]*?)[ \t]+"  # type (must start with non-space)
    r"(\w+)[ \t]*"                               # name
    r"(?:=|;|,)",                                # init / end / multi-decl
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Pass 2 — references
# ---------------------------------------------------------------------------

# Match any CamelCase identifier — post-filter against known class names.
RE_CLASS_MENTION = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")

# Method call: any name followed by `(`, where the name is NOT preceded by
# `.` or another word char (avoid matching class names: `new Foo(` is matched
# but `Foo` won't be in the method-name set; declarations like `void foo(`
# are filtered because the preceding char is whitespace and the def-name
# set membership check drops them via the keyword denylist below).
# This catches BOTH qualified (`obj.method(`) and unqualified (`method(`)
# calls — the unqualified case is critical for same-class self-calls and
# recursive calls.
RE_METHOD_CALL = re.compile(r"(?:(?<=[.])|(?<![\w.]))(\w+)\s*\(")

# Field access — qualified only (`.name` not followed by `(`).
# Unqualified field access conflicts with local variables; we keep this
# narrow to maintain precision over recall.
RE_FIELD_ACCESS = re.compile(r"\.(\w+)\b(?!\s*\()")

# Common noise names to drop from the method/field reference index.
METHOD_NAME_DENYLIST = frozenset({
    "toString", "equals", "hashCode", "length", "size",
    "get", "set", "valueOf", "name", "ordinal",
    "compareTo", "intValue", "longValue", "doubleValue", "floatValue",
    "isEmpty", "iterator", "values", "keySet", "entrySet",
    "add", "remove", "contains", "clear", "put", "next",
    "this", "super",  # constructor calls
    # Java keywords / control-flow that look like calls
    "if", "while", "for", "switch", "synchronized", "return",
    "throw", "throws", "catch", "do", "else", "case",
    "new", "instanceof",
})

FIELD_NAME_DENYLIST = frozenset({
    "length", "size", "TYPE", "INSTANCE", "Companion",
    "out", "err", "in",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def line_starts(src: str) -> list[int]:
    """Byte offsets at the start of each line (1-indexed lines)."""
    starts = [0]
    for i, ch in enumerate(src):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def offset_to_line(starts: list[int], offset: int) -> int:
    """Return 1-indexed line number for a byte offset."""
    # Binary search; bisect_right gives the next line, so subtract 1 then +1
    # to get 1-indexed.
    import bisect
    return bisect.bisect_right(starts, offset)


def indent_width(s: str) -> int:
    """Width of leading whitespace, counting tabs as 4 spaces."""
    n = 0
    for ch in s:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += 4
        else:
            break
    return n


# ---------------------------------------------------------------------------
# Definition extraction
# ---------------------------------------------------------------------------

@dataclass
class ClassRecord:
    fqcn: str
    file: str               # workspace-relative path
    kind: str               # class / interface / enum / @interface / record
    package: str
    outer: str | None
    methods: dict[str, list[int]] = field(default_factory=dict)  # name -> line numbers
    fields: dict[str, int] = field(default_factory=dict)         # name -> line


def extract_definitions(rel_file: str, src: str) -> list[ClassRecord]:
    """Extract class / method / field definitions from a single Java source.

    `rel_file` is the workspace-relative path string stored in the output.
    `src` is the original (unstripped) text. We strip internally.
    """
    stripped = strip_comments_and_strings(src)
    starts = line_starts(src)

    # --- package ---
    m = RE_PACKAGE.search(stripped)
    package = m.group(1) if m else ""

    # --- classes (with indent stack) ---
    # Each stack entry: (indent_width, ClassRecord)
    stack: list[tuple[int, ClassRecord]] = []
    classes: list[ClassRecord] = []

    # Collect all class matches in source order.
    class_matches = list(RE_CLASS.finditer(stripped))
    method_matches = list(RE_METHOD.finditer(stripped))
    field_matches = list(RE_FIELD.finditer(stripped))

    # Sort interleaved so we can apply them in source order while
    # maintaining the indent stack.
    events: list[tuple[int, str, re.Match]] = []
    for m in class_matches:
        events.append((m.start(), "class", m))
    for m in method_matches:
        events.append((m.start(), "method", m))
    for m in field_matches:
        events.append((m.start(), "field", m))
    events.sort(key=lambda x: x[0])

    for offset, kind_ev, match in events:
        line = offset_to_line(starts, offset)
        indent_str = match.group(1)
        ind = indent_width(indent_str)

        # Pop stack until top has strictly smaller indent (this is parent).
        while stack and stack[-1][0] >= ind:
            stack.pop()

        if kind_ev == "class":
            class_kind = match.group(2)
            simple_name = match.group(3)
            outer = stack[-1][1].fqcn if stack else None
            if outer:
                fqcn = f"{outer}${simple_name}"
            elif package:
                fqcn = f"{package}.{simple_name}"
            else:
                fqcn = simple_name
            rec = ClassRecord(
                fqcn=fqcn,
                file=rel_file,
                kind=class_kind,
                package=package,
                outer=outer,
            )
            classes.append(rec)
            stack.append((ind, rec))

        elif kind_ev == "method":
            if not stack:
                continue  # method outside any class — skip
            method_name = match.group(2)
            if method_name in _METHOD_KEYWORD_DENYLIST:
                continue
            stack[-1][1].methods.setdefault(method_name, []).append(line)

        elif kind_ev == "field":
            if not stack:
                continue
            field_name = match.group(2)
            if field_name in _METHOD_KEYWORD_DENYLIST:
                continue
            # Don't overwrite earlier line if same name appears twice (rare in Java).
            stack[-1][1].fields.setdefault(field_name, line)

    return classes


# ---------------------------------------------------------------------------
# Reference extraction
# ---------------------------------------------------------------------------

@dataclass
class FileRefs:
    file: str
    class_refs: dict[str, set[int]] = field(default_factory=dict)     # fqcn -> {lines}
    method_calls: dict[str, set[int]] = field(default_factory=dict)   # SimpleClass.method -> {lines} (we store just method->lines here; resolution to class happens later)
    field_access: dict[str, set[int]] = field(default_factory=dict)


def extract_references(
    rel_file: str,
    src: str,
    simple_to_fqcns: dict[str, list[str]],
    method_names: set[str],
    field_names: set[str],
) -> FileRefs:
    """Extract references from a single source file.

    Returns:
      class_refs keyed by FQCN (resolved via simple_to_fqcns).
      method_calls keyed by simple-name (caller resolves to <SimpleClass>.<method> later).
      field_access keyed by simple-name.
    """
    stripped = strip_comments_and_strings(src)
    starts = line_starts(src)
    refs = FileRefs(file=rel_file)

    # --- class mentions ---
    for m in RE_CLASS_MENTION.finditer(stripped):
        name = m.group(1)
        cands = simple_to_fqcns.get(name)
        if not cands:
            continue
        line = offset_to_line(starts, m.start())
        for fqcn in cands:
            refs.class_refs.setdefault(fqcn, set()).add(line)

    # --- method calls ---
    for m in RE_METHOD_CALL.finditer(stripped):
        name = m.group(1)
        if name in METHOD_NAME_DENYLIST or name not in method_names:
            continue
        line = offset_to_line(starts, m.start())
        refs.method_calls.setdefault(name, set()).add(line)

    # --- field access ---
    for m in RE_FIELD_ACCESS.finditer(stripped):
        name = m.group(1)
        if name in FIELD_NAME_DENYLIST or name not in field_names:
            continue
        # Don't double-count if the same span is also a method call (already excluded by lookahead).
        line = offset_to_line(starts, m.start())
        refs.field_access.setdefault(name, set()).add(line)

    return refs
