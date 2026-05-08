# Deobfuscation & name restoration

Most production Android apps ship through R8 / ProGuard, which renames classes, methods, fields, and (sometimes) string literals to short opaque names. jadx has good defaults but the user can usually go further.

## The three layers

1. **jadx auto-deobf** (`--deobf`) — invents stable readable names for short identifiers. Already in the default flags. Mappings persist in `<input>.jobf` next to the APK.
2. **User-supplied mapping** (`--mappings-path`) — a real ProGuard / Tiny / Enigma mapping that restores **original** names. Use whenever the user has it.
3. **Kotlin metadata** (`-Pkotlin-metadata.*`) — for Kotlin code, the compiler embeds an annotation with original class/field/method names. Default flags turn this on.

These compose: with all three, even a heavily obfuscated Kotlin app reads almost like the original source.

## Importing a ProGuard mapping

If the user has `mapping.txt` from a build:

```bash
jadx \
  --mappings-path /path/to/mapping.txt \
  --deobf \
  -d "$WS/sources" \
  "$WS/input.apk"
```

This restores the original fully-qualified names. If you previously decompiled this APK without the mapping, the cached `sources/` is now stale — decompile into a sibling directory like `$WS/sources-mapped/` and update `meta.json` to record the mapping was applied.

Supported mapping formats (auto-detected):
- `.txt` — ProGuard
- `.tiny` / `.tiny2` — Tiny
- `.mapping` — Enigma file
- `.srg`, `.csrg`, `.tsrg`, `.tsrg2`, `.xsrg`, `.jam`
- `.jobf` — JADX-native (what `--deobf` writes)
- IntelliJ Migration Map XML, Recaf simple

If auto-detection fails: `-Prename-mappings.format=PROGUARD_FILE`.

## Persisting jadx auto-names across runs

By default jadx writes its invented names to `<input>.jobf`. If the input is a symlink or read-only, point this elsewhere:

```bash
jadx \
  --deobf \
  --deobf-cfg-file "$WS/auto.jobf" \
  --deobf-cfg-file-mode read-or-save \
  -d "$WS/sources" \
  "$WS/input.apk"
```

Subsequent runs read the same names, so cross-session consistency is preserved.

## Resource name obfuscation (AndResGuard etc.)

Symptoms: `R.layout.a`, `R.string.b`, resource IDs without symbolic names.

```bash
jadx \
  --deobf \
  --deobf-res-name-source code \
  --use-headers-for-detect-resource-extensions \
  -d "$WS/sources" \
  "$WS/input.apk"
```

`--deobf-res-name-source code` falls back to R-class field names; `--use-headers-for-detect-resource-extensions` recovers file extensions when filenames were stripped.

## String encryption

R8 doesn't encrypt strings, but third-party tools (DexGuard, custom transformers) do. Symptoms:

- Code is full of calls to `a.b.c.decrypt("xx==")` or similar.
- Search for log strings returns nothing useful.

This skill does **not** decrypt strings. Options for the user:

- Run the app under Frida and dump the decryption function's outputs at runtime.
- Manually identify the decryptor and re-implement it (often a simple XOR or AES).
- For investigation purposes only: identify *call sites* (which classes use the decryptor) and inspect those — the encrypted-string class is usually centralized.

## Use source name as class alias

If a Kotlin file's compiled name differs from its `.kt` filename, `--use-source-name-as-class-name-alias if-better` sometimes produces clearer names than `class-alias=yes` alone.

## Renaming policy

The default renaming behavior fixes invalid Java identifiers, removes non-printable characters, and adapts to filesystem case sensitivity. If you only want jadx to rename for validity (not to "improve" already-readable names):

```bash
jadx --rename-flags 'valid,printable' ...
```

To disable all jadx renames (useful when comparing two APK versions byte-by-byte):

```bash
jadx --rename-flags 'none' ...
```

## Sanity check after deobfuscation

After a deobf run, sample-check 3 classes from `sources/`:
- Do package names look human (not `a.b.c`)?
- Do methods have meaningful names or still `a()` `b()`?
- Do string searches actually find log lines from the user's logcat?

If most are still `a` `b` `c`, deobf failed. Most likely the mapping wasn't applied (wrong format, wrong file) — re-check `decompile.log` for `mapping` entries.
