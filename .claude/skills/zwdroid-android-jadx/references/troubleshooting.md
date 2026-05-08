# Troubleshooting — failed or empty decompiles

When `sources/` is empty, tiny, or otherwise broken, work through this list before assuming jadx is at fault.

## Symptom: `sources/` is nearly empty, only a `StubApp` / `ApplicationLoader` class

The APK is **packed** (壳, hardened). Common shells:

| Shell | Telltale package | What to do |
|---|---|---|
| 360 加固 | `com.qihoo.util` / `libjiagu.so` | Out of scope — needs unpacking (e.g. FRIDA-DEXDump) |
| 腾讯乐固 | `com.tencent.StubShell` | Same |
| 百度加固 | `com.baidu.protect` | Same |
| Bangcle | `com.secneo.apkwrapper` | Same |
| 爱加密 | `com.shell.NativeApplication` | Same |

This skill **does not** unpack. Tell the user the APK is hardened and recommend they obtain an unpacked dump (typically via Frida hook on a rooted device, dumping the real `classes.dex` from memory).

## Symptom: jadx exits non-zero, log says "loading errors"

Run with `--show-bad-code -m fallback`:

```bash
jadx --show-bad-code -m fallback -d "$WS/sources" "$WS/input.apk" >> "$WS/decompile.log" 2>&1
```

`fallback` mode produces verbose linear output that's harder to read but rarely fails. Useful for the one method jadx couldn't decompile in the default mode.

## Symptom: jadx OOM or hangs

```bash
JAVA_OPTS="-Xmx6g" jadx -j 4 ...
```

- `-j 4` limits parallel threads (default 16) — most useful on 16 GB machines.
- `-Xmx` raises the JVM heap. 4 GB is enough for most APKs; 8 GB for the largest.

If still hanging on a specific class, identify the culprit from `decompile.log` and skip with `--single-class` for the rest:

```bash
jadx --single-class com.example.SafeClass --single-class-output out.java "$WS/input.apk"
```

## Symptom: dex checksum failure

Some APKs ship with intentionally broken dex checksums to deter naive tools.

```bash
jadx -Pdex-input.verify-checksum=no -d "$WS/sources" "$WS/input.apk"
```

## Symptom: zip security limit hit

For APKs with very many entries:

```bash
JADX_DISABLE_ZIP_SECURITY=true jadx ...
# or raise the limit instead of disabling:
JADX_ZIP_MAX_ENTRIES_COUNT=500000 jadx ...
```

Only use `JADX_DISABLE_ZIP_SECURITY` on artifacts you trust — it disables zip-bomb protections.

## Symptom: AAB (Android App Bundle) instead of APK

jadx accepts `.aab` directly. If it doesn't, extract base module:

```bash
unzip -o app.aab -d aab-unpacked
jadx -d "$WS/sources" aab-unpacked/base/dex/classes.dex
```

## Symptom: only resources, no source

Probably passed `-s` (no source) or input was a `.arsc` / `.xapk` resources-only file. Re-check the input.

## Symptom: classes split across `classes.dex`, `classes2.dex`, ...

Multi-dex is handled automatically when you point jadx at the APK. Only do per-dex decompiling if the APK is broken open and you have loose dex files:

```bash
jadx -d "$WS/sources" classes.dex classes2.dex classes3.dex
```

## When in doubt: read `decompile.log`

Every skipped method, every load error, every deobf decision is in there. `grep -i "error\|warn" "$WS/decompile.log" | head -30` is usually enough to localize the issue.
