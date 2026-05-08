# Library identification & version bracketing

When the user asks "is library X bundled?" or "which version of X is in here?", you don't need a full decompile. The fingerprinting workflow is:

1. **Confirm presence** by class-name search inside the dex files.
2. **Bracket the version** by checking presence/absence of discriminator classes that landed in known releases.
3. **Pin the version** with one targeted `--single-class` extract that surfaces a unique import or constant.

This file lists discriminators for the common libraries embedded in Android APKs. Use it as a starting point, not a closed set — confirm any conclusion against the library's release notes when stakes are high.

## Workflow

```bash
# Step 1: extract dex files (no decompile needed for fingerprinting)
mkdir -p /tmp/dex-peek && cd /tmp/dex-peek
unzip -o /path/to/app.apk 'classes*.dex'

# Step 2: confirm library presence — list its classes
strings classes*.dex | grep -E '^Lcom/google/common/' | sort -u | head -20

# Step 3: check for discriminator classes
for marker in CollectCollectors ExecutionSequencer InternalFutureFailureAccess ClosingFuture; do
  hits=$(strings classes*.dex | grep -c "Lcom/google/common/.*${marker};")
  echo "$marker: $hits"
done
```

Only after the dex-string scan narrows the version range should you reach for `jadx --single-class` to pull *one* class for confirmation (e.g. to read its `@VisibleForTesting` annotation style, package-info, or a `VERSION` constant).

## Caveat: Android-flavor vs JRE-flavor

Some libraries (Guava, kotlinx-coroutines) ship two flavors. The Android flavor strips a few classes (`@GwtCompatible` machinery, `Service` framework etc.). When a discriminator is *absent*, ask whether it's because the version is older OR because it's the Android flavor.

## Caveat: shading / repackaging

R8 / ProGuard can rename library classes (`com.google.common.collect.ImmutableList` → `c.b.a.a.a`) — but only when the consuming app has `minifyEnabled true`. APKs that bundle libraries unminified (most do) keep original FQCNs. If you find no `Lcom/google/common/` strings at all but the app clearly uses Guava-style API, suspect shading and switch to method-signature heuristics.

---

## Guava (`com.google.common.*`)

Major-version discriminators (presence flips the lower bound, absence flips the upper bound):

| Discriminator | Introduced | Notes |
|---|---|---|
| `com.google.common.collect.CollectCollectors` | 25.1 | Java-8 collectors split out of immutable classes |
| `com.google.common.util.concurrent.ExecutionSequencer` | 26.0 | |
| `com.google.common.util.concurrent.internal.InternalFutureFailureAccess` | 27.0 | New `failureaccess` artifact split |
| `com.google.common.graph.AbstractBaseGraph` | **23.0** | Present from 23.0 onward — NOT a 27.0 marker (a common misread) |
| `com.google.common.util.concurrent.ClosingFuture` | 28.0 | |
| `com.google.common.collect.ImmutableLongArray` | 25.0 | |
| `com.google.common.hash.Hashing.murmur3_32_fixed` | 30.1 | search the literal method name |
| Annotation `com.google.common.annotations.ParametricNullness` | 31.0 | Java 8 nullness rewrite |
| Annotation `com.google.common.annotations.ElementTypesAreNonnullByDefault` | 31.0 | Same wave |

Nullness-import style inside any Guava class (extract one with `--single-class`):

| Import | Used in Guava |
|---|---|
| `javax.annotation.Nullable` | ≤ 26 |
| `org.checkerframework.checker.nullness.compatqual.NullableDecl` | 27.x — 30.x |
| `org.checkerframework.checker.nullness.qual.Nullable` | 31.x onward |

**Worked example** (the disagreement we saw in iteration-1):
- `CollectCollectors` present + `ExecutionSequencer` absent → 25.1, **not** 26+.
- `AbstractBaseGraph` is **not** a 27.0 marker — it's been there since 23.0. Ignore it as a discriminator.
- Conclusion in the iteration-1 case: Guava 25.1 (the baseline answer was correct; the with-skill answer was misled by an incorrect claim about `AbstractBaseGraph`).

## OkHttp (`okhttp3.*` / `com.squareup.okhttp.*`)

| Discriminator | Introduced |
|---|---|
| Package `okhttp3.*` (vs `com.squareup.okhttp.*`) | 3.0 — old package was retired |
| `okhttp3.internal.http2.Http2Connection` | 3.x |
| `okhttp3.internal.connection.RealConnection` | 3.x — refactored from `com.squareup.okhttp.internal.io.RealConnection` |
| `okhttp3.brotli.BrotliInterceptor` | 4.0 |
| `okhttp3.coroutines.*` (Kotlin extensions) | 5.0-alpha |
| Conscrypt-only TLS hooks: `okhttp3.internal.platform.ConscryptPlatform` | 3.13 |
| `okhttp3.internal.tls.OkHostnameVerifier` (vs `internal.HostnameVerifier`) | 3.7 |

Quickest version pinning — extract `okhttp3.OkHttp` (a constants class) via `--single-class` and read the `VERSION` field directly:

```bash
jadx --single-class okhttp3.OkHttp --single-class-output /tmp/OkHttp.java app.apk
grep VERSION /tmp/OkHttp.java
```

## Retrofit (`retrofit2.*`)

Retrofit doesn't have a `VERSION` constant, so use class-set discriminators:

| Discriminator | Introduced |
|---|---|
| `retrofit2.Retrofit` | 2.0 (entire `retrofit2` package is 2.0+; older `retrofit.RestAdapter` is 1.x) |
| `retrofit2.adapter.rxjava2.*` | 2.3 |
| `retrofit2.adapter.rxjava3.*` | 2.9 |
| `retrofit2.converter.kotlinx.serialization.*` | 2.9 |
| `retrofit2.KotlinExtensions` (Coroutines `await()` etc.) | 2.6 |
| `retrofit2.adapter.guava.GuavaCallAdapterFactory` | always present |

## kotlinx-coroutines (`kotlinx.coroutines.*`)

Best version evidence comes from the embedded Kotlin metadata. Extract any coroutines class with `--single-class` and look at the `kotlin.Metadata` annotation:

```bash
jadx --single-class kotlinx.coroutines.BuildersKt --single-class-output /tmp/BuildersKt.java app.apk
head -10 /tmp/BuildersKt.java
```

Module presence as cross-check:

| Discriminator | Introduced |
|---|---|
| `kotlinx.coroutines.flow.Flow` | 1.2 |
| `kotlinx.coroutines.flow.StateFlow` | 1.4 |
| `kotlinx.coroutines.flow.SharedFlow` | 1.4 |
| `kotlinx.coroutines.test.TestScope` | 1.6 |
| `kotlinx.coroutines.android.HandlerDispatcher` | always present in -android variant |

## Glide (`com.bumptech.glide.*`) / Picasso (`com.squareup.picasso.*`)

| Discriminator | Introduced |
|---|---|
| `com.bumptech.glide.GlideBuilder` | 4.x — refactored constructor API |
| `com.bumptech.glide.RequestManager.error()` | 4.x |
| `com.bumptech.glide.module.AppGlideModule` | 4.x — annotation-processed module config |
| `com.squareup.picasso.Picasso.LoadedFrom` | 2.x |
| `com.squareup.picasso3.*` (new package) | 3.x |

## Gson (`com.google.gson.*`)

`com.google.gson.Gson` has a `getVersion()` style — but easier: extract `com.google.gson.Gson` with `--single-class` and search for the `VERSION` constant:

```bash
jadx --single-class com.google.gson.Gson --single-class-output /tmp/Gson.java app.apk
grep VERSION /tmp/Gson.java
```

## When the library has no obvious version markers

Fall back to:
1. `META-INF/MANIFEST.MF` — sometimes preserved in the APK (`unzip -p app.apk META-INF/<lib>.MF`).
2. The library's `BuildConfig` / `R` classes — may contain a `VERSION_NAME`.
3. Pixel-counting: list all the library's classes, compare to release-archive class lists from Maven Central.

Don't claim a version more precisely than your evidence supports — "Guava 25.x" is honest; "Guava 25.1" is only honest if you saw `CollectCollectors$ImmutableMap_Collector` (added 25.1 specifically).
