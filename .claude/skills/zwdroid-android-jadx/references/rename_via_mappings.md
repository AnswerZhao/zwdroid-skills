# Rename persistence via mapping files

This skill is read-only at the file system level — there's no `rename_class()` API like a GUI plugin would offer. Instead it provides **the same outcome via a different axis**: write user-discovered or AI-inferred renames to a mapping file, then re-decompile with `jadx --mappings-path` to propagate.

The result is more powerful than in-memory renaming:

- **Diff-able**: the mapping file is plain text — `git diff` shows exactly what changed.
- **Shareable**: a teammate can pick up where you left off by checking out the file.
- **Reproducible**: re-running `jadx` against the same input + same mapping always produces the same source tree.
- **Layered**: combine multiple mapping files (e.g. one for inferred names, one for known SDK names) without merge conflicts.

## When to use

- After identifying a class/method/field's true purpose in Phase 4, persist that name so future Phase 4 queries see it.
- When migrating from a ProGuard `mapping.txt` provided by an internal team.
- When building up a manually-curated dictionary across many investigations of the same APK.

## Format: TINY_2 (recommended default)

jadx supports six formats: TINY, TINY_2, ENIGMA, PROGUARD, SRG, JOBF. **Use TINY_2** unless a different format is forced on you. Reasons:

- Human-readable, line-oriented.
- Supports all five granularities the SKILL might write: class / method / field / method-arg / method-local-var.
- Auto-detected by jadx without `-Prename-mappings.format` overrides.
- Universally compatible with mapping toolchains (yarn, mappingio).

### Minimal worked example

Save to `$WS/inferred-names.tiny`:

```
tiny	2	0	intermediary	named
c	com/termux/app/TermuxService	com/termux/app/TermuxSessionManager
	m	(ILcom/termux/terminal/TerminalSession;)V	handleSessionAction	dispatchSessionAction
		p	1	sessionAction	actionCode
	f	Ljava/lang/String;	LOG_TAG	TAG
```

What this says, line by line:
- Header: `tiny<TAB>2<TAB>0<TAB>intermediary<TAB>named` — TINY v2, two name spaces named "intermediary" (decompiler-given) and "named" (your renames).
- `c` — class: rename `com.termux.app.TermuxService` to `com.termux.app.TermuxSessionManager`.
- `m` (indented under `c`) — method: in that class, with descriptor `(I, TerminalSession) -> void`, rename `handleSessionAction` to `dispatchSessionAction`.
- `p` (indented under `m`) — parameter at index 1: rename `sessionAction` to `actionCode`.
- `f` (indented under `c`) — field: of type `String`, rename `LOG_TAG` to `TAG`.

Tabs matter — TINY v2 is tab-delimited. Method/field descriptors use JVM bytecode notation (`I` = int, `Ljava/lang/String;` = String, `[B` = byte[], etc.).

### Apply the mapping

```bash
# Re-decompile into a sibling output dir so the original sources/ stays available.
jadx \
  --mappings-path "$WS/inferred-names.tiny" \
  --deobf \
  -Pkotlin-metadata.class-alias=yes \
  -d "$WS-mapped" \
  "$WS/input.apk"

# The new tree is at $WS-mapped/sources/. Rebuild the xref index for it:
python3 "${SKILL_DIR}/scripts/build_xref_index.py" "$WS-mapped" --rebuild
```

After this, every reference to `TermuxService.handleSessionAction` across the entire codebase appears as `TermuxSessionManager.dispatchSessionAction`. Stack traces, log strings (when they reference the class via `getClass().getName()`), and IDE imports — all consistent.

## Workflow integration

1. **Phase 4 surfaces a discovery** — e.g. AI infers `a.b.c.x` is really `com.real.Foo.bar`.
2. **Append to `$WS/inferred-names.tiny`**:
   - Class entry: one line per class.
   - Member entries: tab-indented under their class.
3. **Re-decompile** to `$WS-mapped/`.
4. **Rebuild xref index** for `$WS-mapped/`.
5. **Future queries** target `$WS-mapped/` (the mapped tree becomes the new working set).

The mapping file lives in the workspace alongside `sources/` and is implicitly versioned by the workspace's sha1 cache key.

## Caveats

- **Re-decompile invalidates the previous xref index.** The `build_xref_index.py` script always operates on `$WS/sources/`, so if you point it at `$WS-mapped/` you get a fresh index. Don't mix paths.
- **Renaming makes the original `sources/` stale**, not deleted. Keep both around if you want to compare.
- **Method descriptors must match the decompiled signature exactly.** A wrong descriptor = silent no-op. To get the descriptor for an existing method:
  ```bash
  # Use jadx with deobf=read to dump the auto-mapping; copy the descriptor from there:
  jadx --deobf-cfg-file "$WS/auto.jobf" --deobf-cfg-file-mode read-or-save \
       --output-format json -d /tmp/jadx-json --no-res "$WS/input.apk"
  # Now /tmp/jadx-json/<pkg>/Foo.json contains methods with their descriptors.
  ```
- **Don't rename Android framework classes** (`android.app.Activity` etc.) — those are external symbols, not in the APK.
- **Tabs vs spaces matter.** Many editors auto-convert. Verify with `cat -A inferred-names.tiny`.

## Comparison with MCP rename APIs

A live GUI tool (e.g. jadx-ai-mcp) offers `rename_class()` / `rename_method()` calls that mutate jadx's in-memory model. This skill's mapping-file approach achieves the same end-state but is:

| Property | MCP in-memory rename | SKILL mapping-file rename |
|---|---|---|
| Persistence across sessions | manual save in jadx-gui | always; file is the state |
| Shareable | screenshot | `git add` |
| Reviewable | no diff view | `git diff` |
| Reverts cleanly | undo stack (lossy) | `git revert` |
| Applies in batches | one-at-a-time IPC | one mapping file = N renames |
| Requires running GUI | yes | no |

For a CLI/batch/remote workflow, the mapping-file axis is strictly better. The trade-off is the small re-decompile time per rename batch (typically <1 minute on Termux-scale APKs); collect renames before re-running rather than re-decompiling per name.
