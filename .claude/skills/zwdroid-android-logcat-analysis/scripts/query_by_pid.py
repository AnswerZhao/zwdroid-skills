from __future__ import annotations

"""
query_by_pid.py — query index.jsonl and events.jsonl for log lines from a specific PID.

Usage:
    python3 scripts/query_by_pid.py --pid 1234 [--level E] [--keyword "mediacontrol"] [--max-lines 100] [--context N] [--work-dir .logcat-analysis/]
"""

import argparse
import json
import os
import sys


def query_index(work_dir, pid, level_filter=None, keyword=None):
    index_path = os.path.join(work_dir, 'index.jsonl')
    if not os.path.exists(index_path):
        print(f"ERROR: {index_path} not found. Run parse_logcat.py first.", file=sys.stderr)
        sys.exit(1)

    pid_int = int(pid)
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
            if rec.get('pid') != pid_int:
                continue
            if level_filter and rec.get('level', '') != level_filter:
                continue
            if kw and kw not in rec.get('msg', '').lower():
                continue
            rec['_source'] = 'index'
            results.append(rec)

    return results


def fetch_context_lines(work_dir, main_results, context_n):
    """Second pass: collect lines within ±context_n of each main result (same source_file)."""
    if context_n <= 0 or not main_results:
        return []

    # Build per-file line_no ranges to fetch
    ranges = {}  # source_file -> set of line_nos
    main_keys = set()
    for rec in main_results:
        sf = rec.get('source_file')
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
            sf = rec.get('source_file')
            lno = rec.get('line_no')
            if sf not in ranges or lno not in ranges[sf]:
                continue
            if (sf, lno) in main_keys:
                continue  # already in main results
            rec['_source'] = 'index'
            rec['_is_context'] = True
            ctx_results.append(rec)

    return ctx_results


def query_events(work_dir, pid):
    events_path = os.path.join(work_dir, 'events.jsonl')
    if not os.path.exists(events_path):
        return []

    pid_int = int(pid)
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
            fields = rec.get('fields', {})
            if rec.get('pid') == pid_int or (isinstance(fields, dict) and fields.get('PID') == pid_int):
                rec['_source'] = 'event'
                results.append(rec)

    return results


def format_index_line(rec):
    src = rec.get('source_file', '?')
    lno = rec.get('line_no', '?')
    ts  = rec.get('ts', '?')
    lvl = rec.get('level', '?')
    tag = rec.get('tag', '?')
    pid = rec.get('pid', '?')
    msg = rec.get('msg', '')
    return f"[{src} L{lno}] {ts} {lvl}/{tag}({pid}): {msg}"


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


def main():
    parser = argparse.ArgumentParser(description='Query log lines by PID')
    parser.add_argument('--pid',      required=True, help='Process ID to query')
    parser.add_argument('--level',    default=None,  help='Filter by log level (e.g. E, W)')
    parser.add_argument('--keyword',  default=None,  help='Case-insensitive substring filter on msg')
    parser.add_argument('--max-lines',type=int, default=200, help='Max lines to return')
    parser.add_argument('--context',  type=int, default=0,   help='Show ±N context lines around each match (index only)')
    parser.add_argument('--work-dir', default='.logcat-analysis/', help='Work directory')
    args = parser.parse_args()

    index_results = query_index(args.work_dir, args.pid, args.level, args.keyword)
    event_results = query_events(args.work_dir, args.pid)
    ctx_results   = fetch_context_lines(args.work_dir, index_results, args.context)

    combined = index_results + event_results + ctx_results
    combined.sort(key=lambda r: (r.get('source_file', ''), r.get('line_no', 0)))
    combined = combined[:args.max_lines]

    if not combined:
        print(f"No results for pid={args.pid}" + (f" level={args.level}" if args.level else ""))
        print("REMINDER: 写下一轮 query 前，把本轮证据（含阴性结果）Edit 到 .logcat-analysis/analysis_log.md", file=sys.stderr)
        return

    lines = []
    for rec in combined:
        if rec['_source'] == 'event':
            lines.append(format_event_line(rec))
        elif rec.get('_is_context'):
            lines.append('  ' + format_index_line(rec))
        else:
            lines.append(format_index_line(rec))

    print('\n'.join(lines))
    ctx_count = len(ctx_results)
    print(f"\n--- {len(combined)} lines ({len(index_results)} index + {len(event_results)} events" +
          (f" + {ctx_count} context" if ctx_count else "") + ")", file=sys.stderr)
    print("REMINDER: 写下一轮 query 前，把本轮证据 Edit 到 .logcat-analysis/analysis_log.md", file=sys.stderr)


if __name__ == '__main__':
    main()
