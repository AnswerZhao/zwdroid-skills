from __future__ import annotations

"""
query_by_time.py — query index.jsonl for log lines within a time window.

Usage:
    python3 scripts/query_by_time.py --start "14:08:25" --end "14:08:40" [--tags "ActivityManager,WindowManager"] [--level E] [--keyword "mediacontrol"] [--work-dir .logcat-analysis/]

--tags accepts comma-separated tag names (case-sensitive).
--keyword filters by case-insensitive substring match on the msg field.
Time format: HH:MM:SS (matched against the HH:MM:SS portion of each record's ts).
"""

import argparse
import json
import os
import sys
from datetime import datetime


def ts_to_hms(ts):
    """Extract HH:MM:SS from ISO timestamp like 2024-12-14T16:10:33.138"""
    if not ts:
        return None
    # ts may be "2024-12-14T16:10:33.138" or just a time string
    t = ts.split('T')[-1]  # take the time portion
    return t[:8]            # HH:MM:SS


def _parse_iso_like(value):
    """Parse an ISO timestamp, accepting optional millisecond precision."""
    if not value or 'T' not in value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _hms_in_window(hms, start_hms, end_hms):
    """Compare HH:MM:SS values, supporting windows that cross midnight."""
    if start_hms <= end_hms:
        return start_hms <= hms <= end_hms
    return hms >= start_hms or hms <= end_hms


def record_in_window(ts, start, end):
    """Return whether record timestamp is within a HH:MM:SS or ISO datetime window."""
    if not ts:
        return False

    start_dt = _parse_iso_like(start)
    end_dt = _parse_iso_like(end)
    if start_dt and end_dt:
        rec_dt = _parse_iso_like(ts)
        return bool(rec_dt and start_dt <= rec_dt <= end_dt)

    hms = ts_to_hms(ts)
    if not hms:
        return False
    return _hms_in_window(hms, ts_to_hms(start) or start, ts_to_hms(end) or end)


def fetch_context_lines(work_dir, main_results, context_n):
    """Second pass: collect lines within ±context_n of each main result (same source_file)."""
    if context_n <= 0 or not main_results:
        return []

    ranges = {}
    main_keys = set()
    for rec in main_results:
        sf  = rec.get('source_file')
        lno = rec.get('line_no')
        if sf is None or lno is None:
            continue
        main_keys.add((sf, lno))
        if sf not in ranges:
            ranges[sf] = set()
        for n in range(lno - context_n, lno + context_n + 1):
            if n >= 0:
                ranges[sf].add(n)

    if not ranges:
        return []

    index_path = os.path.join(work_dir, 'index.jsonl')
    ctx_results = []
    with open(index_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            sf  = rec.get('source_file')
            lno = rec.get('line_no')
            if sf not in ranges or lno not in ranges[sf]:
                continue
            if (sf, lno) in main_keys:
                continue
            rec['_is_context'] = True
            ctx_results.append(rec)
    return ctx_results


def query(work_dir, start_hms, end_hms, tags=None, pids=None, level_filter=None, keyword=None, max_lines=200):
    index_path = os.path.join(work_dir, 'index.jsonl')
    if not os.path.exists(index_path):
        print(f"ERROR: {index_path} not found. Run parse_logcat.py first.", file=sys.stderr)
        sys.exit(1)

    tag_set = set(tags) if tags else None
    pid_set = {str(p) for p in pids} if pids else None
    level_set = {v.strip() for v in level_filter.split(',')} if level_filter else None
    kw = keyword.lower() if keyword else None
    results = []

    with open(index_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not record_in_window(rec.get('ts'), start_hms, end_hms):
                continue
            if tag_set and rec.get('tag', '') not in tag_set:
                continue
            if pid_set and str(rec.get('pid', '')) not in pid_set:
                continue
            if level_set and rec.get('level', '') not in level_set:
                continue
            if kw and kw not in rec.get('msg', '').lower():
                continue

            results.append(rec)
            if len(results) >= max_lines:
                break

    return results


def query_events(work_dir, start_hms, end_hms, tags=None, pids=None, max_lines=200):
    """Return events.jsonl records within the time window."""
    events_path = os.path.join(work_dir, 'events.jsonl')
    if not os.path.exists(events_path):
        return []

    tag_set = set(tags) if tags else None
    pid_set = {str(p) for p in pids} if pids else None
    results = []

    with open(events_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not record_in_window(rec.get('ts'), start_hms, end_hms):
                continue
            if tag_set and rec.get('tag', '') not in tag_set:
                continue
            if pid_set and str(rec.get('pid', '')) not in pid_set:
                continue
            rec['_source'] = 'event'
            results.append(rec)
            if len(results) >= max_lines:
                break

    return results


def format_event_line(rec):
    src    = rec.get('source_file', '?')
    lno    = rec.get('line_no', '?')
    ts     = rec.get('ts', '?')
    tag    = rec.get('tag', '?')
    pid    = rec.get('pid', '?')
    fields = rec.get('fields', {})
    if isinstance(fields, dict):
        summary = ', '.join(f"{k}={v}" for k, v in fields.items())
    else:
        summary = str(fields)
    if len(summary) > 120:
        summary = summary[:117] + '...'
    return f"[EVENT] [{src} L{lno}] {ts} {tag}({pid}): {summary}"


def format_md(results):
    lines = []
    for rec in results:
        if rec.get('_source') == 'event':
            lines.append(format_event_line(rec))
            continue
        src  = rec.get('source_file', '?')
        lno  = rec.get('line_no', '?')
        ts   = rec.get('ts', '?')
        lvl  = rec.get('level', '?')
        tag  = rec.get('tag', '?')
        pid  = rec.get('pid', '?')
        msg  = rec.get('msg', '')
        line = f"[{src} L{lno}] {ts} {lvl}/{tag}({pid}): {msg}"
        lines.append('  ' + line if rec.get('_is_context') else line)
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Query log lines by time window')
    parser.add_argument('--start',    required=True, help='Start time HH:MM:SS')
    parser.add_argument('--end',      required=True, help='End time HH:MM:SS')
    parser.add_argument('--tags',     default=None,  help='Comma-separated tag filter')
    parser.add_argument('--pids',     default=None,  help='Comma-separated PID filter (e.g. 2082,23387)')
    parser.add_argument('--level',    default=None,  help='Filter by log level (e.g. E or E,W)')
    parser.add_argument('--keyword',  default=None,  help='Case-insensitive substring filter on msg')
    parser.add_argument('--max-lines',      type=int, default=200,   help='Max lines to return')
    parser.add_argument('--context',        type=int, default=0,     help='Show ±N context lines around each match')
    parser.add_argument('--include-events', action='store_true',     help='Also include events.jsonl structured events')
    parser.add_argument('--work-dir', default='.logcat-analysis/', help='Work directory')
    args = parser.parse_args()

    tags = [t.strip() for t in args.tags.split(',')] if args.tags else None
    pids = [p.strip() for p in args.pids.split(',')] if args.pids else None

    results      = query(args.work_dir, args.start, args.end, tags, pids, args.level, args.keyword, args.max_lines)
    ctx_results  = fetch_context_lines(args.work_dir, results, args.context)
    evt_results  = query_events(args.work_dir, args.start, args.end, tags, pids, args.max_lines) if args.include_events else []

    combined = results + ctx_results + evt_results
    combined.sort(key=lambda r: (r.get('ts') or '', r.get('source_file', ''), r.get('line_no', 0)))

    if not combined:
        print(f"No results for {args.start}–{args.end}" + (f" tags={args.tags}" if args.tags else ""))
        print("REMINDER: 写下一轮 query 前，把本轮证据（含阴性结果）Edit 到 .logcat-analysis/analysis_log.md", file=sys.stderr)
        return

    print(format_md(combined))
    parts = [f"{len(results)} index"]
    if ctx_results:
        parts.append(f"{len(ctx_results)} context")
    if evt_results:
        parts.append(f"{len(evt_results)} events")
    print(f"\n--- {len(combined)} lines ({', '.join(parts)})", file=sys.stderr)
    print("REMINDER: 写下一轮 query 前，把本轮证据 Edit 到 .logcat-analysis/analysis_log.md", file=sys.stderr)


if __name__ == '__main__':
    main()
