---
name: zwdroid-android-jadx
description: Use this skill whenever the user is investigating an Android binary artifact — APK, dex, jar, aar, class, or aab — without source code, especially to cross-reference logcat output with decompiled code to track down a bug, a crash, or unexplained behavior. Trigger on phrases like "decompile this APK / jar / dex", "反编译这个 apk", "logcat 定位 bug", "find why the Activity goes transparent / crashes / freezes", "Android 崩溃堆栈反混淆 / 还原", "看看这个 jar 里做了什么", "这个 SDK 干了什么", "查 APK 的权限/exported 组件", "对比两个 APK 版本", "识别 APK 内嵌库", "stack trace 是混淆的", "no source for this app". Use proactively whenever the user mentions an `.apk`, `.dex`, `.jar`, `.aar`, `.aab`, or `.class` file alongside any question about behavior, crashes, manifests, permissions, or bundled libraries — even if they don't say the word "decompile". Do not use for native `.so` analysis, runtime hooking (Frida/Xposed), repackaging (apktool), or pure resource diffs.
---

# jadx — Android decompile & log-to-source workflow

Turn an Android binary (APK / dex / jar / aar / class) plus optional logcat into a focused source-code investigation.

The hardest part is **not** running jadx — that's one command. The hardest part is **not drowning** in the resulting source. A non-trivial APK decompiles to hundreds of MB of Java; reading the whole thing is impossible and unnecessary. This skill exists to keep the investigation narrow and evidence-based.

## When to use

Use whenever the user is asking about Android-app behavior or bugs and source code is unavailable:

- **APK + logcat** → trace what code ran around a log line.
- **Crash stack trace with obfuscated names** → find the real method.
- **Third-party `.jar` / `.aar` / SDK** → understand what the library actually does.
- **AndroidManifest audit** → permissions, exported components, intent filters.
- **Two APK versions** → localize a behavior change.
- **Identify bundled libraries** → "which OkHttp version is in here?".

Skip when the task is:

- Native `.so` analysis (use ghidra/radare2).
- Runtime hooking / dynamic analysis (Frida, Xposed).
- Repackaging or modifying the APK (apktool).
- Pure resource diff with no source involvement (aapt2 dump).

## Mental model

Four principles drive every step:

1. **Cache by content hash.** Reuse decompiled output across sessions. Re-running jadx on the same APK is wasteful and slow.
2. **Decompile once, query many times.** Phase 2 is bulky and slow; Phase 4 is small and fast and runs many times.
3. **Index first, grep second, single-class last.** A pre-built `xrefs.json` answers "who calls X / where is C used" in one `jq` lookup; grep is the fallback when the index misses; `--single-class` extraction is the final step before reading code into context.
4. **Never read the full source tree.** The user's context is precious. If you find yourself about to `cat sources/*.java` or to read a dozen files, stop — that's the wrong direction.

## Workspace conventions

For each artifact, set up a workspace at `./.jadx-workspace/` relative to the user's current working directory (or a path they specify):

```
./.jadx-workspace/
├── <sha1-prefix-12>/                # cache key = first 12 chars of input sha1
│   ├── input.apk                    # symlink to the original artifact
│   ├── sources/                     # full decompiled .java (Phase 2)
│   ├── resources/                   # decoded XML, assets (Phase 2)
│   ├── decompile.log                # jadx stdout/stderr
│   ├── meta.json                    # {sha1, jadx_version, time, src_path}
│   ├── classes.json                 # class index (Phase 2.x)
│   ├── xrefs.json                   # cross-reference index (Phase 2.x)
│   ├── package_layout.json          # top-level package → file count (Phase 2.x)
│   ├── index_meta.json              # index completeness flag (Phase 2.x)
│   ├── index_build.log              # index-builder log (Phase 2.x)
│   ├── manifest_summary.json        # structured manifest (Phase 3)
│   ├── manifest_summary.md          # human-readable manifest summary (Phase 3)
│   ├── inferred-names.tiny          # AI-inferred renames (optional, see references/rename_via_mappings.md)
│   └── sources-mapped/              # re-decompile output after applying inferred-names.tiny (optional)
└── reports/
    └── <yyyymmdd-HHMM>-<topic>.md   # Phase 5 attribution reports — workspace-level, not per-sha1
```

The 12-char sha1 prefix is the cache key — same APK, same workspace, no rework. Reports live at workspace root so investigations of related APKs share one report folder.

## Phase 1 — Prepare & cache

Three steps: dependency check (fail-fast on missing hard deps) → compute artifact sha1 → look up cache.

### 1.1 Pre-flight (jadx + Java only)

One inline check — Java is the only dep with a cryptic native error. Everything else (Python, shasum, unzip, jq, coreutils) fails-fast with a clear `command not found` and is left to the natural error path.

```bash
command -v jadx >/dev/null 2>&1 && java -version >/dev/null 2>&1 \
  || { echo "Missing jadx or Java 11+. macOS: brew install jadx (pulls openjdk). Linux: pacman -S jadx / apt install jadx default-jdk. Or grab a release zip from https://github.com/skylot/jadx/releases" >&2; exit 1; }
```

If the user's platform isn't covered above, point them at https://github.com/skylot/jadx#install and stop. Don't attempt installs without explicit authorization. `jq` is a soft dep — the index queries in Phase 4 fall back to `python3 -c 'import json; ...'` if absent.

### 1.2 Compute the artifact sha1

```bash
SHA1=$(shasum -a 1 path/to/app.apk | awk '{print substr($1,1,12)}')
WS="./.jadx-workspace/$SHA1"
```

### 1.3 Cache check

If `$WS/meta.json` exists and `$WS/sources/` is non-empty, the artifact has already been decompiled. **Skip Phase 2's full decompile** and tell the user "reusing cached decompile from <date>".

**Sub-case — sources cached but index missing:** if `$WS/meta.json` exists but `$WS/index_meta.json` is missing or has `"complete": false`, run **only Phase 2.x** (the index builder, ~5–30s). Don't re-run jadx — the slow part is already done.

**Sub-case — sources cached but stale jadx version:** compare `meta.json.jadx_version` with the current `jadx --version`. Mismatch is usually fine (decompiled .java doesn't depend on the jadx that produced it) but flag it to the user so they can choose to `--rebuild`.

## Phase 2 — Full decompile (on-demand)

**Skip when:**
- The cache from Phase 1 is hot.
- The question can be answered by **targeted extraction only** — e.g. "is library X bundled?", "what does class Y do?". For these, use `--single-class` directly against the APK without populating `sources/`.
- **Manifest-only audits** ("what permissions / exported components / main activity?") — use the `--no-src` variant below and skip Phase 2.x entirely.

**Run when:**
- You expect to grep across the whole codebase repeatedly (typical for log-driven bug hunts).
- The user explicitly wants a browseable source tree.

For non-trivial APKs (>10 MB) full decompile takes 1–10 minutes. **Run as a background bash task** so the conversation stays responsive.

```bash
ln -sfn "$(realpath path/to/app.apk)" "$WS/input.apk"

jadx \
  --deobf \
  -Pkotlin-metadata.class-alias=yes \
  --comments-level info \
  --log-level error \
  -d "$WS" \
  "$WS/input.apk" \
  > "$WS/decompile.log" 2>&1 &
```

`-d "$WS"` lets jadx create `$WS/sources/` and `$WS/resources/` automatically — don't pass `-d "$WS/sources"`, that produces a doubled `sources/sources/` path.

Default flags chosen for the common case:
- `--deobf` — most production APKs are obfuscated; without this you'd be reading `a.b.c` everywhere.
- `kotlin-metadata.class-alias=yes` — restores Kotlin class names from `@Metadata` annotations.
- `--comments-level info` — keep useful jadx-emitted comments without the debug noise.
- `--log-level error` — quiet stderr; the file log captures full progress.

When the task completes:
1. Verify `$WS/sources/` contains at least one `.java` file. An empty tree usually means the APK is hardened — see `references/troubleshooting.md`.
2. Write `$WS/meta.json`:
   ```json
   {"sha1": "...", "src_path": "...", "jadx_version": "...", "decompiled_at": "..."}
   ```

**Manifest-only variant** (for Phase 3 alone, no source decompile needed):

```bash
jadx --no-src --log-level error -d "$WS" "$WS/input.apk" > "$WS/decompile.log" 2>&1
```

This produces just `$WS/resources/AndroidManifest.xml` and other resource artifacts in seconds.

For tricky cases (encrypted/packed APK, ProGuard mapping, encrypted strings) see `references/deobfuscation.md`.

### 2.x — Build cross-reference index (automatic tail step)

Right after a successful full decompile (skip for `--no-src` and skip on cache hit), run the index builder:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/build_xref_index.py "$WS"
```

Typical run takes 3–10 seconds on Termux-scale APKs and produces four JSON files in `$WS/`:

- `classes.json` — class index (FQCN → file/methods/fields)
- `xrefs.json` — `class_refs` / `method_calls` / `field_access` lookups
- `package_layout.json` — top-level package → file count
- `index_meta.json` — completion flag + build time

These power the Phase 4 "Strategy D" path below. The index is **best-effort**: if `index_meta.json` is missing or `complete: false`, fall back to grep — see `references/xref_index.md` for details.

## Phase 3 — Manifest & resource scan

Run the manifest summary script to convert `AndroidManifest.xml` into a structured JSON summary. Read that JSON instead of the raw XML — it's an order of magnitude smaller, pre-classified, and lets you skip directly to high-signal items:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/manifest_summary.py "$WS"
# Output: $WS/manifest_summary.json
```

The JSON contains:

- `package`, `version_name`, `version_code`, `min_sdk`, `target_sdk`, `debuggable`, `shared_user_id`, `main_activity`
- `permissions`: bucketed into `dangerous` / `signature` / `normal` / `custom_defined` (the script ships a curated table of well-known dangerous and signature permissions).
- `exported_components`: `activities` / `services` / `receivers` / `providers`, each with `name`, `permission`, and `intent_filters`. Implicit-export rules (intent filter + targetSdk < 31) are applied automatically.

For the user-facing summary, write `$WS/manifest_summary.md` distilling the JSON to ~100 lines with prose context (top risks, exported-component findings). Do **not** copy the JSON into the report verbatim — interpret it.

For top-level package layout (the "this is app code vs library bulk" view), read `$WS/package_layout.json` produced by Phase 2.x — no need to run `find | wc -l` manually.

For resource cross-reference: a logcat line like `setBackgroundResource(0x7f0801b2)` resolves via `$WS/resources/res/values/public.xml` (or the generated `R.java`).

This phase is cheap — always run it during the first investigation of an APK, even if Phase 4 is the eventual goal. It primes the model with the right vocabulary (package roots, component names) for Phase 4.

## Phase 4 — Log-driven search (the core)

This is where the skill earns its keep. Given the user's signal — a logcat line, an exception, a behavioral description, a stack frame — narrow to 1–3 candidate classes, extract them via `--single-class`, then read.

**Preferred path: Strategy D (index-driven) below**, when a full Phase 2 decompile + index has been built. Strategies A/B/C are fallbacks for index misses or when only `--single-class` outputs exist.

### Strategy D — index-driven navigation (preferred)

If Phase 2.x produced `$WS/xrefs.json`, the cross-reference questions become O(1) `jq` lookups instead of grep walks. See `references/xref_index.md` for the full schema and cookbook; the load-bearing queries are:

```bash
# Does class X exist? Where?
jq '.classes["com.termux.app.TermuxService"] // .by_simple_name["TermuxService"]' "$WS/classes.json"

# All methods in a class (no full-source read needed)
jq '.classes["com.termux.app.TermuxService"].methods | keys' "$WS/classes.json"

# Where is method M called?
jq '.method_calls["TermuxService.handleSessionAction"]' "$WS/xrefs.json"

# Where is class C referenced?
jq '.class_refs["com.termux.app.TermuxService"]' "$WS/xrefs.json"

# Where is field F read or written?
jq '.field_access["TermuxService.mWakeLock"]' "$WS/xrefs.json"
```

**Reading the output**: each xref query returns an array of `{file, lines}` records, e.g.

```json
[
  {"file": "com/termux/app/TermuxService.java", "lines": [352, 432, 463]}
]
```

`file` is workspace-relative (resolve as `$WS/sources/<file>`). `lines` is sorted, deduplicated. A `null` result means the index has no entry — fall back to grep.

For "list methods of class" and "list fields of class" questions, also consider the class outline script — it returns full method/field declarations (signatures + line numbers) for a class without reading the body:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/class_outline.py "$WS" com.termux.app.TermuxService
```

Use the outline before pulling the full class via `--single-class` — often the outline alone answers the question.

### Strategy A — match a literal log string

Logcat lines often contain string constants hardcoded in source. Pick a **distinctive, stable, app-specific** substring and grep:

```bash
grep -rln --include='*.java' -F 'BackgroundChanged' "$WS/sources/"
```

Choosing terms:
- Distinctive enough that hits stay below ~10.
- Not a number, timestamp, or generic Android string.
- A stable fragment from inside the constant if the log was assembled with `+`.

### Strategy B — start from the named entry

If the log mentions an Activity / Fragment / Service name, jump straight there. Find its lifecycle methods and the most recent UI/event handler before the bad behavior.

### Strategy C — reverse a stack trace

For frames like `at a.b.c.x(SourceFile:42)`:

1. **With a ProGuard mapping**: re-decompile passing `--mappings-path mapping.txt`. This changes the output, so use a different sha1 suffix or a `mapped/` subdirectory.
2. **Without a mapping**: locate `$WS/sources/a/b/c.java` directly. Method `x` at line ~42 is the target. Read just that file.
3. **AI-inferred names**: when you've reasoned out a class/method's true purpose during investigation, persist it to a TINY_2 mapping file and re-decompile. See `references/rename_via_mappings.md` — the SKILL's non-interactive equivalent of a GUI rename API. Better than in-memory rename because the mapping file is git-able and shareable across sessions.

### Extract a single class — the context-saving move

Once you have a candidate fully-qualified class name, do **not** read it from `$WS/sources/`. Re-extract it cleanly via `--single-class`, which writes one tidy file:

```bash
jadx --single-class com.example.foo.BarActivity \
     --single-class-output /tmp/BarActivity.java \
     "$WS/input.apk"
```

Then `Read /tmp/BarActivity.java`. If you need 2–3 classes, do this 2–3 times. **Never** loop over a directory.

### Distillation

Before writing the report, ask: which 5–20 lines actually answer the user's question? Cite those by `path:line`. Discard the rest.

## Phase 5 — Attribution report

When the investigation reaches a conclusion, write a durable report to `$WS/../reports/<yyyymmdd-HHMM>-<topic>.md`:

```markdown
# <bug or question in plain words>

**Inputs:** apk=<basename> sha1=<prefix> logcat=<path or excerpt>

## Finding
<one paragraph in plain language; what is happening and why>

## Evidence
- `sources/com/example/foo/BarActivity.java:142` — `setBackgroundColor(0)` is called from `onUserAction`.
- logcat T+12.4s: `D/Bar: applying transparent bg` — string emitted from line 141.
- Trigger chain: `MainController.handleStr()` → `bar.fadeOut()` → `setAlpha(0f)`.

## Open questions
- Does the alpha animation also affect siblings?
- Is the timing tied to a specific server response?
```

The report is the deliverable. Future sessions can read it without redoing the work.

## Tips & gotchas

- **Multi-dex APK**: jadx merges `classes.dex`, `classes2.dex`, … automatically. No special flag needed.
- **Encrypted/packed APK** (360 加固, 腾讯乐固, Bangcle): `sources/` may be tiny or empty. Look for a single `StubApp` class. See `references/troubleshooting.md`.
- **Kotlin code**: with `kotlin-metadata` (default on) names are mostly restored; lambdas can still look mechanical.
- **Resource obfuscation** (AndResGuard): add `--use-headers-for-detect-resource-extensions` and pass `--deobf-res-name-source code` to fall back on R-class names.
- **Very large APK + low memory**: add `-j 4` to limit threads, and prepend `JAVA_OPTS="-Xmx4g"` if jadx OOMs.
- **Don't dump** the full `sources/` tree into your reply. Always grep + extract.
- **Don't re-decompile** if `meta.json` shows a recent run with the same sha1 and same jadx version.
- **Index miss is not a wall** — when `jq` returns `null` for a method-call or field-access query, fall back to `grep -rn '\bmethodName\s*('` against `$WS/sources/`. The index is a speed-up, not a precondition.

## Reference files

- `references/troubleshooting.md` — recover from failed/empty decompiles (hardened APKs, OOM, multi-dex edge cases).
- `references/deobfuscation.md` — ProGuard mappings, R8, name-mangling defenses, encrypted strings.
- `references/library_signatures.md` — discriminator classes/strings to identify and version-bracket bundled libraries (Guava, OkHttp, Retrofit, kotlinx, etc.). Read when answering "which library version is bundled?".
- `references/xref_index.md` — JSON schema and `jq` query cookbook for the index built in Phase 2.x. Read when planning Strategy D queries.
- `references/rename_via_mappings.md` — TINY_2 mapping-file workflow for persisting AI-inferred class/method/field/variable renames across decompiles. Read when stack-trace reverse or interactive rename comes up.

## Available scripts

Invoke all scripts via `${CLAUDE_SKILL_DIR}`. Claude Code executes bash commands in the project root, not the skill directory, so relative paths resolve to the wrong location.

- **`${CLAUDE_SKILL_DIR}/scripts/build_xref_index.py <ws>`** — Phase 2.x. Builds `classes.json` / `xrefs.json` / `package_layout.json` / `index_meta.json` in `$WS/`. Pass `--rebuild` to ignore cached index. Pass `--quiet` to suppress progress lines.
- **`${CLAUDE_SKILL_DIR}/scripts/manifest_summary.py <ws>`** — Phase 3. Emits structured `manifest_summary.json` from `$WS/resources/AndroidManifest.xml`. Pass `--stdout` to print JSON instead of writing to a file.
- **`${CLAUDE_SKILL_DIR}/scripts/class_outline.py <ws> <fqcn>`** — Phase 4. Prints method/field signatures for a class without reading the body. Pass `--file path.java` to operate on a single extracted class instead of the workspace.

All Python scripts use **stdlib only** (no PyPI dependencies). Run any script with `--help` for full usage.
