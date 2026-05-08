#!/usr/bin/env python3
"""Parse a decoded AndroidManifest.xml into a focused JSON summary.

Reads `$WS/resources/AndroidManifest.xml` (or any path passed as arg) and
writes structured JSON to `$WS/manifest_summary.json` (or stdout if --stdout).

Output schema:
{
  "package": str,
  "version_name": str,
  "version_code": int,
  "min_sdk": int,
  "target_sdk": int,
  "compile_sdk": int | None,
  "debuggable": bool,
  "shared_user_id": str | None,
  "main_activity": str | None,
  "permissions": {
    "dangerous": [...],
    "signature": [...],
    "normal": [...],
    "custom_defined": [{"name", "protection_level"}]
  },
  "exported_components": {
    "activities": [...],
    "services": [...],
    "receivers": [...],
    "providers": [...]
  }
}

Each exported component entry:
  {"name": str, "exported": bool, "permission": str|None,
   "intent_filters": [{"actions": [...], "categories": [...]}],
   "extras": {...}}  // misc attrs preserved (authorities, etc.)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ANDROID_NS = "http://schemas.android.com/apk/res/android"
A = f"{{{ANDROID_NS}}}"


# Hand-curated table of well-known dangerous + signature/privileged permissions.
# Anything not listed → "normal" bucket.
# Source: AOSP `frameworks/base/core/res/AndroidManifest.xml` protection levels.
DANGEROUS_PERMS = frozenset({
    "android.permission.READ_EXTERNAL_STORAGE",
    "android.permission.WRITE_EXTERNAL_STORAGE",
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.ACCESS_BACKGROUND_LOCATION",
    "android.permission.READ_CONTACTS",
    "android.permission.WRITE_CONTACTS",
    "android.permission.GET_ACCOUNTS",
    "android.permission.READ_CALENDAR",
    "android.permission.WRITE_CALENDAR",
    "android.permission.READ_PHONE_STATE",
    "android.permission.READ_PHONE_NUMBERS",
    "android.permission.CALL_PHONE",
    "android.permission.READ_CALL_LOG",
    "android.permission.WRITE_CALL_LOG",
    "android.permission.ADD_VOICEMAIL",
    "android.permission.USE_SIP",
    "android.permission.PROCESS_OUTGOING_CALLS",
    "android.permission.ANSWER_PHONE_CALLS",
    "android.permission.RECORD_AUDIO",
    "android.permission.CAMERA",
    "android.permission.BODY_SENSORS",
    "android.permission.SEND_SMS",
    "android.permission.RECEIVE_SMS",
    "android.permission.READ_SMS",
    "android.permission.RECEIVE_WAP_PUSH",
    "android.permission.RECEIVE_MMS",
    "android.permission.ACTIVITY_RECOGNITION",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.READ_MEDIA_IMAGES",
    "android.permission.READ_MEDIA_VIDEO",
    "android.permission.READ_MEDIA_AUDIO",
    "android.permission.BLUETOOTH_SCAN",
    "android.permission.BLUETOOTH_ADVERTISE",
    "android.permission.BLUETOOTH_CONNECT",
    "android.permission.UWB_RANGING",
    "android.permission.NEARBY_WIFI_DEVICES",
})

SIGNATURE_PERMS = frozenset({
    "android.permission.READ_LOGS",
    "android.permission.DUMP",
    "android.permission.WRITE_SECURE_SETTINGS",
    "android.permission.WRITE_SETTINGS",
    "android.permission.SYSTEM_ALERT_WINDOW",
    "android.permission.PACKAGE_USAGE_STATS",
    "android.permission.REQUEST_INSTALL_PACKAGES",
    "android.permission.REQUEST_DELETE_PACKAGES",
    "android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
    "android.permission.MANAGE_EXTERNAL_STORAGE",
    "android.permission.MANAGE_DOCUMENTS",
    "android.permission.MANAGE_OWN_CALLS",
    "android.permission.BIND_ACCESSIBILITY_SERVICE",
    "android.permission.BIND_DEVICE_ADMIN",
    "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE",
    "android.permission.BIND_VPN_SERVICE",
    "android.permission.BIND_INPUT_METHOD",
    "android.permission.CAPTURE_AUDIO_OUTPUT",
    "android.permission.RECEIVE_BOOT_COMPLETED",
    "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
    "android.permission.FOREGROUND_SERVICE_LOCATION",
    "android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK",
    "android.permission.FOREGROUND_SERVICE_PHONE_CALL",
})


def _attr(el: ET.Element, name: str, default=None):
    """Get an `android:`-namespaced attribute."""
    return el.attrib.get(f"{A}{name}", default)


def _as_int(v):
    if v is None:
        return None
    try:
        return int(str(v), 0)  # supports 0x...
    except (ValueError, TypeError):
        return None


def _classify_permission(name: str) -> str:
    if name in DANGEROUS_PERMS:
        return "dangerous"
    if name in SIGNATURE_PERMS:
        return "signature"
    return "normal"


def _is_exported(component: ET.Element, target_sdk: int) -> bool:
    """Determine whether a component is reachable from outside the app."""
    explicit = _attr(component, "exported")
    if explicit is not None:
        return explicit == "true"
    # No explicit attribute. Pre-S (target<31): implicitly exported if has filter.
    has_filter = component.find("intent-filter") is not None
    if target_sdk < 31:
        return has_filter
    # Android 31+: must be explicit if any filter is present.
    return False


def _collect_intent_filters(component: ET.Element) -> list[dict]:
    out = []
    for f in component.findall("intent-filter"):
        actions = [_attr(a, "name") for a in f.findall("action") if _attr(a, "name")]
        cats = [_attr(c, "name") for c in f.findall("category") if _attr(c, "name")]
        data = []
        for d in f.findall("data"):
            data.append({k.removeprefix(A): v for k, v in d.attrib.items() if k.startswith(A)})
        out.append({"actions": actions, "categories": cats, "data": data})
    return out


def _component_entry(c: ET.Element, target_sdk: int, kind: str) -> dict | None:
    """Build entry dict for an exported component; return None if not exported."""
    if not _is_exported(c, target_sdk):
        return None
    name = _attr(c, "name")
    permission = _attr(c, "permission")
    entry = {
        "name": name,
        "exported": True,
        "exported_explicit": _attr(c, "exported") is not None,
        "permission": permission,
        "intent_filters": _collect_intent_filters(c),
    }
    extras = {}
    for k, v in c.attrib.items():
        if not k.startswith(A):
            continue
        attr_name = k.removeprefix(A)
        if attr_name in {"name", "exported", "permission"}:
            continue
        if attr_name in {"authorities", "grantUriPermissions", "process",
                         "directBootAware", "enabled", "label", "icon",
                         "launchMode", "configChanges", "theme",
                         "windowSoftInputMode", "screenOrientation",
                         "permission"}:
            extras[attr_name] = v
    if extras:
        entry["extras"] = extras
    return entry


def _find_main_activity(app: ET.Element) -> str | None:
    for activity in list(app.iter("activity")) + list(app.iter("activity-alias")):
        for f in activity.findall("intent-filter"):
            actions = {_attr(a, "name") for a in f.findall("action")}
            cats = {_attr(c, "name") for c in f.findall("category")}
            if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in cats:
                # For activity-alias, prefer the alias's targetActivity if present.
                return _attr(activity, "targetActivity") or _attr(activity, "name")
    return None


def parse_manifest(xml_path: Path) -> dict:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    if root.tag != "manifest":
        raise ValueError(f"root element is {root.tag!r}, expected 'manifest'")

    package = root.attrib.get("package", "")
    version_name = _attr(root, "versionName") or root.attrib.get("versionName", "")
    version_code = _as_int(_attr(root, "versionCode") or root.attrib.get("versionCode"))
    shared_user_id = _attr(root, "sharedUserId")
    compile_sdk = _as_int(_attr(root, "compileSdkVersion") or root.attrib.get("compileSdkVersion"))

    sdk = root.find("uses-sdk")
    min_sdk = _as_int(_attr(sdk, "minSdkVersion")) if sdk is not None else None
    target_sdk = _as_int(_attr(sdk, "targetSdkVersion")) if sdk is not None else None
    if target_sdk is None:
        target_sdk = min_sdk or 1  # conservative default

    app = root.find("application")
    debuggable = False
    if app is not None:
        debuggable = _attr(app, "debuggable", "false") == "true"

    # --- permissions ---
    perms_dangerous: list[str] = []
    perms_signature: list[str] = []
    perms_normal: list[str] = []
    for up in root.findall("uses-permission"):
        name = _attr(up, "name")
        if not name:
            continue
        bucket = _classify_permission(name)
        if bucket == "dangerous":
            perms_dangerous.append(name)
        elif bucket == "signature":
            perms_signature.append(name)
        else:
            perms_normal.append(name)

    custom_defined = []
    for p in root.findall("permission"):
        custom_defined.append({
            "name": _attr(p, "name"),
            "protection_level": _attr(p, "protectionLevel"),
            "label": _attr(p, "label"),
        })

    # --- exported components ---
    exported = {"activities": [], "services": [], "receivers": [], "providers": []}
    if app is not None:
        for a in list(app.iter("activity")) + list(app.iter("activity-alias")):
            entry = _component_entry(a, target_sdk, "activity")
            if entry:
                exported["activities"].append(entry)
        for s in app.iter("service"):
            entry = _component_entry(s, target_sdk, "service")
            if entry:
                exported["services"].append(entry)
        for r in app.iter("receiver"):
            entry = _component_entry(r, target_sdk, "receiver")
            if entry:
                exported["receivers"].append(entry)
        for p in app.iter("provider"):
            entry = _component_entry(p, target_sdk, "provider")
            if entry:
                # providers also need authorities surfaced
                auth = _attr(p, "authorities")
                if auth:
                    entry.setdefault("extras", {})["authorities"] = auth
                exported["providers"].append(entry)

    main_activity = _find_main_activity(app) if app is not None else None

    return {
        "package": package,
        "version_name": version_name,
        "version_code": version_code,
        "min_sdk": min_sdk,
        "target_sdk": target_sdk,
        "compile_sdk": compile_sdk,
        "debuggable": debuggable,
        "shared_user_id": shared_user_id,
        "main_activity": main_activity,
        "permissions": {
            "dangerous": sorted(perms_dangerous),
            "signature": sorted(perms_signature),
            "normal": sorted(perms_normal),
            "custom_defined": custom_defined,
        },
        "exported_components": exported,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="path to AndroidManifest.xml OR to workspace dir (will look for resources/AndroidManifest.xml)")
    parser.add_argument("--output", help="write JSON here; default: <workspace>/manifest_summary.json or stdout")
    parser.add_argument("--stdout", action="store_true", help="print JSON to stdout instead of writing a file")
    args = parser.parse_args()

    in_path = Path(args.input)
    if in_path.is_dir():
        candidate = in_path / "resources" / "AndroidManifest.xml"
        if not candidate.exists():
            print(f"manifest_summary: not found: {candidate}", file=sys.stderr)
            sys.exit(2)
        xml_path = candidate
        ws = in_path
    else:
        xml_path = in_path
        ws = in_path.parent

    summary = parse_manifest(xml_path)
    blob = json.dumps(summary, indent=2, ensure_ascii=False)

    if args.stdout:
        print(blob)
        return

    out_path = Path(args.output) if args.output else ws / "manifest_summary.json"
    out_path.write_text(blob + "\n")
    print(f"manifest_summary: wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
