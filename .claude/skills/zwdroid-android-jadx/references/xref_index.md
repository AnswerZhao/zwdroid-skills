# Cross-reference index — schema & query cookbook

After Phase 2 finishes, `scripts/build_xref_index.py` walks `$WS/sources/` and produces four JSON files in `$WS/`. This file is the schema reference and a `jq` cookbook for querying them.

The index is **best-effort, not authoritative**. It uses regex-based heuristics and is intentionally over-inclusive on declarations and post-filtered on references. When a query returns `null` or unexpectedly few hits, fall back to `grep -rn` against `$WS/sources/`.

## Files produced

| File | Purpose | Size (typical) | Build cost |
|---|---|---|---|
| `classes.json` | Class index: FQCN → file/methods/fields | 5–10 MB at 50k classes | included in stage 1 |
| `xrefs.json` | Cross-references: class_refs / method_calls / field_access | 30–80 MB | stage 2 (~70% of total time) |
| `package_layout.json` | Top-level package → file count | <100 KB | free byproduct |
| `index_meta.json` | Build metadata + completeness flag | <1 KB | last write, atomic |

`index_meta.json` MUST exist with `"complete": true` for the index to be considered usable. Anything else → fall back to grep.

## Schema

### `classes.json`

```json
{
  "classes": {
    "com.termux.app.TermuxService": {
      "file": "com/termux/app/TermuxService.java",
      "kind": "class",
      "package": "com.termux.app",
      "outer": null,
      "methods": {
        "handleSessionAction": [432],
        "onCreate": [67]
      },
      "fields": {
        "TAG": 45,
        "mWakeLock": 48
      }
    },
    "com.termux.app.TermuxService$LocalBinder": {
      "file": "com/termux/app/TermuxService.java",
      "kind": "class",
      "package": "com.termux.app",
      "outer": "com.termux.app.TermuxService",
      "methods": {...},
      "fields": {...}
    }
  },
  "by_simple_name": {
    "TermuxService": ["com.termux.app.TermuxService"],
    "Service": ["android.app.Service", "com.foo.Service"]
  }
}
```

- `methods` maps name → list of definition line numbers (a method may have overloads, so multiple lines).
- `fields` maps name → single line number (overloads not meaningful for fields).
- `outer` is the parent FQCN if this is an inner class, else `null`. Inner classes use `$` separator.
- `by_simple_name` lets you resolve a stack-trace fragment like `TermuxService` to its full FQCN(s).

### `xrefs.json`

```json
{
  "class_refs": {
    "com.termux.app.TermuxService": [
      {"file": "com/termux/app/TermuxActivity.java", "lines": [55, 312]},
      {"file": "com/termux/app/RunCommandService.java", "lines": [78]}
    ]
  },
  "method_calls": {
    "TermuxService.handleSessionAction": [
      {"file": "com/termux/app/TermuxService.java", "lines": [352, 463]}
    ]
  },
  "field_access": {
    "TermuxService.mWakeLock": [
      {"file": "com/termux/app/TermuxService.java", "lines": [203, 217]}
    ]
  }
}
```

- `class_refs` is keyed by **FQCN** (no ambiguity) and lists files that mention the class via import / type usage / `new X()`.
- `method_calls` and `field_access` are keyed by **`<SimpleClass>.<member>`** — matches stack-trace style and how LLMs naturally think.

When the same simple name resolves to multiple classes, the index emits an entry for each owner. Cross-check with `classes.json`'s `by_simple_name` to disambiguate.

### `package_layout.json`

```json
{
  "by_top_package": {
    "com.google": 877,
    "kotlinx.coroutines": 453,
    "androidx.core": 239,
    "com.termux": 145
  },
  "total_classes": 6156,
  "total_files": 3413
}
```

Use this to gauge "what's the app's own code vs library bulk" — the package with the matching app `package` from the manifest is the app code; everything else is bundled libraries.

### `index_meta.json`

```json
{
  "index_version": 1,
  "complete": true,
  "built_at": "2026-05-07T14:48:04Z",
  "files_processed": 3405,
  "files_skipped": 8,
  "skipped_reason_counts": {"skip_basename": 8},
  "elapsed_seconds": 3.65
}
```

Skip reason buckets:
- `skip_basename`: `R.java`, `BuildConfig.java` (intentionally skipped, dominate the index with noise).
- `skip_prefix`: `R$styleable.java` etc. (resource ID classes).
- `size:<bytes>`: file > 2 MB (pathological generated code).
- `exc:<type>`: per-file exception. Details in `$WS/index_build.log`.

## `jq` cookbook

Set `WS=./.jadx-workspace/<sha1>` first.

### Does class X exist? Where?

```bash
jq '.classes["com.termux.app.TermuxService"] // .by_simple_name["TermuxService"]' "$WS/classes.json"
```

The `//` operator returns the first non-null result — try FQCN, fall back to simple-name lookup which returns the list of FQCN candidates.

### List methods of a class

```bash
jq '.classes["com.termux.app.TermuxService"].methods | keys' "$WS/classes.json"
```

### List fields of a class

```bash
jq '.classes["com.termux.app.TermuxService"].fields | keys' "$WS/classes.json"
```

### Where is method M called?

```bash
jq '.method_calls["TermuxService.handleSessionAction"]' "$WS/xrefs.json"
```

Returns an array of `{file, lines}` entries. If `null`: the index doesn't know about callers (could be: method has no callers, OR method name is in the denylist, OR method was added after index build). Fall back to grep:

```bash
grep -rn '\bhandleSessionAction\s*(' "$WS/sources/"
```

### Where is class C referenced?

```bash
jq '.class_refs["com.termux.app.TermuxService"]' "$WS/xrefs.json"
```

### Where is field F read or written?

```bash
jq '.field_access["TermuxService.mWakeLock"]' "$WS/xrefs.json"
```

Field accuracy is lower than method accuracy — only **qualified** accesses (`obj.field`) are indexed. Unqualified accesses inside the same class will be missed; use grep when you need every read site.

### Build a one-hop call graph

"Find all callers of M, then for each caller's file:line, find which method contains it":

```bash
# Step 1: callers
jq -r '.method_calls["TermuxService.handleSessionAction"][] | "\(.file):\(.lines | join(","))"' "$WS/xrefs.json"

# For each (file, line) returned, look up which method in that file contains the line:
# (manual; the index doesn't pre-build this. Use classes.json to find methods in a file.)
jq --arg file "com/termux/app/TermuxActivity.java" \
   '.classes | to_entries[] | select(.value.file == $file) | {fqcn: .key, methods: .value.methods}' \
   "$WS/classes.json"
```

### Find the app's own code

If the manifest says `package: com.termux`, list classes whose package starts with that:

```bash
jq --arg pkg "com.termux" '.classes | to_entries[] | select(.value.package | startswith($pkg)) | .key' "$WS/classes.json" | head -30
```

### Find classes that reference Android Activity

```bash
jq '.class_refs["android.app.Activity"]' "$WS/xrefs.json"
```

Note: classes outside the indexed APK (Android framework, JDK) are NOT in the index — they appear as `class_refs` keys only because some bundled class imports them. To list them all, query `class_refs` and filter to keys not in `classes`:

```bash
jq '
  .class_refs as $refs |
  ($refs | keys) as $referenced |
  $referenced[]
' "$WS/xrefs.json" | head
```

## Limitations

| Limitation | Impact | Workaround |
|---|---|---|
| Method overloads collapsed by name | Some queries return more than one method's call sites | Manually disambiguate via classes.json's overload line numbers |
| Unqualified field access not indexed | Inside-class field reads/writes missed | grep `\bfieldName\b` against the class file |
| Reflection (`Class.forName`, `Method.invoke`) not detected | Calls through reflection invisible | Manual review |
| No type resolution | "Foo.bar" matched against any class with `bar` method, even unrelated | False positives possible; cross-check with class_refs |
| Anonymous inner classes | `Outer$1`, `Outer$2` are indexed but clarifying which lambda is which is hard | Read the source |
| `--deobf`-renamed methods | Indexed under their renamed names | Keep mapping consistent across re-decompile (see rename_via_mappings.md) |
| jadx-discovered names change between runs | Index built on old run is stale | Always rebuild after a re-decompile (`build_xref_index.py --rebuild`) |

## Authoritative alternative

If you need a guaranteed-correct class list (no regex heuristics), jadx itself can emit per-class JSON:

```bash
jadx --output-format json --no-res -d /tmp/jadx-json "$WS/input.apk"
```

This produces one JSON file per class with full method/field metadata. It does **not** include cross-references (call graph, use sites) — that's why this skill builds its own index. But for the "does class X exist?" or "what methods does class X have?" questions, jadx JSON is authoritative and our `classes.json` is a heuristic approximation. Reach for the jadx JSON if precision matters more than speed.
