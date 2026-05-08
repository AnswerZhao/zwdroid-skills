from __future__ import annotations

"""
trace_starter.py — trace who started a given package/activity.

Searches ActivityTaskManager START events for the target package within a time
window, extracts caller info, and resolves caller process names from events.jsonl.

Usage:
    python3 scripts/trace_starter.py --package com.flyme.auto.mediacontrol \
        --time "14:08:52" [--window 120] [--work-dir .logcat-analysis/]
"""

import argparse
import json
import os
import re
import sys

# Parse "START u<N> {<pkg>/...}" — captures the package name (explicit component format)
_COMPONENT_RE = re.compile(r'START u\d+ \{([^/}]+)[/}]')
# Parse "pkg=<package>" from intent-style START (e.g. "pkg=com.foo.bar")
_PKG_FIELD_RE = re.compile(r'\bpkg=(\S+?)(?:\s|$|})')
# Parse caller: "from uid <N> pid <N>" optionally followed by "(<pkg>)" or "package <pkg>"
_FROM_RE = re.compile(r'from uid (\d+) pid (\d+)(?:\s+(?:\(([^)]+)\)|package\s+(\S+)))?')


def ts_to_hms(ts):
    if not ts:
        return None
    return ts.split('T')[-1][:8]


def hms_diff_secs(a, b):
    def to_s(h):
        p = h.split(':')
        return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
    try:
        diff = abs(to_s(a) - to_s(b))
        return min(diff, 86400 - diff)
    except Exception:
        return float('inf')


def resolve_pid_to_name(pid_str, work_dir):
    """Look up process name for a PID via events.jsonl (am_proc_start)."""
    events_path = os.path.join(work_dir, 'events.jsonl')
    if not os.path.exists(events_path):
        return None
    with open(events_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get('tag') == 'am_proc_start':
                fields = rec.get('fields') or {}
                if str(fields.get('PID', '')) == pid_str:
                    name = fields.get('Process Name')
                    if name:
                        return name
    return None


def find_starts(work_dir, package, center_hms, window_secs):
    """Return all START records for the target package within the time window."""
    index_path = os.path.join(work_dir, 'index.jsonl')
    if not os.path.exists(index_path):
        print(f"ERROR: {index_path} not found. Run parse_logcat.py first.", file=sys.stderr)
        sys.exit(1)

    hits = []
    with open(index_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if rec.get('tag') not in ('ActivityTaskManager', 'ActivityManager'):
                continue
            msg = rec.get('msg', '')
            if 'START' not in msg:
                continue
            if package not in msg:
                continue

            hms = ts_to_hms(rec.get('ts'))
            if hms and hms_diff_secs(hms, center_hms) > window_secs:
                continue

            m_comp = _COMPONENT_RE.search(msg)
            m_pkg  = _PKG_FIELD_RE.search(msg)
            m_from = _FROM_RE.search(msg)

            # Prefer explicit pkg= field (intent format); fall back to {pkg/activity} format
            target_pkg = (m_pkg.group(1) if m_pkg else None) or \
                         (m_comp.group(1) if m_comp else package)

            # Caller package: group(3) for parens form, group(4) for "package X" form
            caller_pkg = None
            if m_from:
                caller_pkg = m_from.group(3) or m_from.group(4)

            hits.append({
                'ts':          rec.get('ts'),
                'target_pkg':  target_pkg,
                'caller_uid':  m_from.group(1) if m_from else None,
                'caller_pid':  m_from.group(2) if m_from else None,
                'caller_pkg':  caller_pkg,
                'source_file': rec.get('source_file'),
                'line_no':     rec.get('line_no'),
                'msg':         msg,
            })

    return hits


def find_task_restores(work_dir, package, center_hms, window_secs):
    """Return wm_on_create_called events for the target package within the time window."""
    events_path = os.path.join(work_dir, 'events.jsonl')
    if not os.path.exists(events_path):
        return []

    hits = []
    with open(events_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if rec.get('tag') != 'wm_on_create_called':
                continue

            fields = rec.get('fields') or {}
            field_str = ' '.join(str(v) for v in fields.values() if isinstance(v, str))
            if package not in field_str:
                continue

            hms = ts_to_hms(rec.get('ts'))
            if hms and hms_diff_secs(hms, center_hms) > window_secs:
                continue

            component = fields.get('Component Name', '')
            fields_summary = ' '.join(f'{k}={v}' for k, v in fields.items())
            hits.append({
                'ts':          rec.get('ts'),
                'component':   component,
                'source_file': rec.get('source_file'),
                'line_no':     rec.get('line_no'),
                'fields_summary': fields_summary,
            })

    return hits


def format_chain(hits, work_dir):
    """Output unified KV format for new_start hits: one line per field, prefixed `kind=new_start`.
    Field set: kind, ts, target_pkg, caller_pkg, caller_uid, caller_pid, source, log."""
    lines = []
    for h in hits:
        uid = h['caller_uid']
        pid = h['caller_pid']
        pkg = h['caller_pkg']
        if (uid is not None or pid is not None) and not pkg and pid:
            pkg = resolve_pid_to_name(pid, work_dir)
        # Mark system_server callers explicitly
        if uid == '1000' and pkg:
            pkg = f"system_server/{pkg}"

        kvs = [
            ('kind',        'new_start'),
            ('ts',          h['ts']),
            ('target_pkg',  h['target_pkg']),
            ('caller_pkg',  pkg or 'unknown'),
            ('caller_uid',  uid or 'N/A'),
            ('caller_pid',  pid or 'N/A'),
            ('source',      f"{h['source_file']}:{h['line_no']}"),
            ('log',         h['msg'][:200]),
        ]
        lines.append(' '.join(f'{k}={v}' for k, v in kvs))
    return '\n'.join(lines)


def format_task_restores(restores):
    """Output unified KV format for task_restore hits: one line per restore.
    Field set: kind, ts, component, task_id, caller_pkg=N/A, source, note."""
    lines = []
    for r in restores:
        # Extract task_id from fields_summary if present (e.g. "Task=1201370 ...")
        task_id = 'N/A'
        m = re.search(r'\bTask=(\S+)', r.get('fields_summary', ''))
        if m:
            task_id = m.group(1)

        kvs = [
            ('kind',        'task_restore'),
            ('ts',          r['ts']),
            ('component',   r['component'] or 'N/A'),
            ('task_id',     task_id),
            ('caller_pkg',  'N/A'),
            ('source',      f"{r['source_file']}:{r['line_no']}"),
            ('note',        'WindowManager restored existing task; no caller'),
        ]
        lines.append(' '.join(f'{k}={v}' for k, v in kvs))
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Trace who started a given package')
    parser.add_argument('--package',  required=True, help='Target package name (substring match)')
    parser.add_argument('--time',     required=True, help='Center time HH:MM:SS')
    parser.add_argument('--window',   type=int, default=120,
                        help='Search window ±seconds around --time (default 120)')
    parser.add_argument('--work-dir', default='.logcat-analysis/', help='Work directory')
    args = parser.parse_args()

    hits = find_starts(args.work_dir, args.package, args.time, args.window)
    restores = find_task_restores(args.work_dir, args.package, args.time, args.window)

    if not hits and not restores:
        print(f"No START or task restore events found for '{args.package}' within ±{args.window}s of {args.time}")
        print("REMINDER: 写下一轮 query 前，把本轮证据（含阴性结果）Edit 到 .logcat-analysis/analysis_log.md", file=sys.stderr)
        return

    if hits:
        print(format_chain(hits, args.work_dir))
    if restores:
        print(format_task_restores(restores))

    print(f"--- {len(hits)} START event(s), {len(restores)} task restore(s)", file=sys.stderr)
    print("REMINDER: 写下一轮 query 前，把本轮证据 Edit 到 .logcat-analysis/analysis_log.md", file=sys.stderr)


if __name__ == '__main__':
    main()
