from __future__ import annotations

"""
select_logs.py — auto-select logcat files covering a given event time window.

Given a directory of logcat files and an event time, outputs the subset of files
whose time range overlaps the analysis window. File paths are printed to stdout
(one per line) for use as --files arguments to parse_logcat.py.

Usage:
    python3 scripts/select_logs.py \
        --dir logs/ \
        --time "14:08:52" \
        [--window-before 600] \
        [--window-after 120] \
        [--pattern "log_logcat*.log"] \
        [--year 2026]

stdout: selected file paths (one per line), suitable for --files
stderr: coverage summary and diagnostics
"""

import argparse
import fnmatch
import os
import re
import sys
from datetime import datetime, timedelta

# Filename timestamp pattern: @YYYYMMDD_HH-MM-SS[-mmm]
_FNAME_TS_RE = re.compile(r'@(\d{4})(\d{2})(\d{2})_(\d{2})-(\d{2})-(\d{2})')

# Threadtime line prefix: MM-DD HH:MM:SS.mmm  PID  TID L TAG: msg
_THREADTIME_RE = re.compile(r'^(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\d+\s+\d+\s+[VDIWEFS]\s+')


def _parse_raw_ts(raw_ts, year):
    try:
        return datetime.strptime(f"{year}-{raw_ts.strip()}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def extract_ts_from_filename(basename, year=None):
    m = _FNAME_TS_RE.search(basename)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except ValueError:
        return None


def read_first_ts(filepath, year):
    """Scan up to first 50 lines for the first valid threadtime timestamp."""
    try:
        with open(filepath, encoding='utf-8', errors='replace') as f:
            for _ in range(50):
                line = f.readline()
                if not line:
                    break
                m = _THREADTIME_RE.search(line)
                if m:
                    return _parse_raw_ts(m.group(1), year)
    except OSError:
        pass
    return None


def read_last_ts(filepath, year, chunk_size=8192):
    """Read last 8KB of file and return the last valid threadtime timestamp."""
    try:
        with open(filepath, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - chunk_size))
            chunk = f.read().decode('utf-8', errors='replace')
        for line in reversed(chunk.splitlines()):
            m = _THREADTIME_RE.search(line)
            if m:
                return _parse_raw_ts(m.group(1), year)
    except OSError:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description='Auto-select logcat files by event time')
    parser.add_argument('--dir',           required=True,            help='Directory containing log files')
    parser.add_argument('--time',          required=True,            help='Event time HH:MM:SS')
    parser.add_argument('--window-before', type=int, default=600,    help='Seconds before event_time (default 600 = 10min)')
    parser.add_argument('--window-after',  type=int, default=120,    help='Seconds after event_time (default 120 = 2min)')
    parser.add_argument('--pattern',       default='log_logcat*.log', help='Filename glob pattern (default log_logcat*.log)')
    parser.add_argument('--year',          type=int, default=None,   help='Year for timestamps (inferred from filenames if omitted)')
    args = parser.parse_args()

    if not os.path.isdir(args.dir):
        print(f"ERROR: Directory not found: {args.dir}", file=sys.stderr)
        sys.exit(1)

    # Collect and sort candidate files by filename (chronological for standard naming)
    candidates = sorted(
        os.path.join(args.dir, f)
        for f in os.listdir(args.dir)
        if fnmatch.fnmatch(f, args.pattern) and os.path.isfile(os.path.join(args.dir, f))
    )
    if not candidates:
        print(f"ERROR: No files matching '{args.pattern}' in {args.dir}", file=sys.stderr)
        sys.exit(1)

    # Resolve year: CLI arg > filename > current year
    year = args.year
    if year is None:
        for path in candidates:
            ts = extract_ts_from_filename(os.path.basename(path))
            if ts:
                year = ts.year
                break
    if year is None:
        year = datetime.now().year

    # Build file info: {path, start, end}
    filename_dates = []
    file_info = []
    for path in candidates:
        basename = os.path.basename(path)
        filename_start = extract_ts_from_filename(basename, year)
        if filename_start is not None:
            filename_dates.append(filename_start)
        start = read_first_ts(path, year) or filename_start
        file_info.append({'path': path, 'start': start, 'end': None})

    file_info.sort(key=lambda fi: (fi['start'] is None, fi['start'] or datetime.max, fi['path']))

    # Estimate end time: prefer the file's actual tail timestamp; fall back to next file start.
    for i, fi in enumerate(file_info):
        tail = read_last_ts(fi['path'], year)
        if tail is not None and (fi['start'] is None or tail > fi['start']):
            fi['end'] = tail
        elif i + 1 < len(file_info) and file_info[i + 1]['start'] is not None:
            fi['end'] = file_info[i + 1]['start']
        else:
            fi['end'] = fi['start']

    # Parse event time against the first known date
    ref_date = filename_dates[0] if filename_dates else next((fi['start'] for fi in file_info if fi['start']), datetime(year, 1, 1))
    try:
        t = datetime.strptime(args.time.strip(), "%H:%M:%S")
    except ValueError:
        try:
            t = datetime.strptime(args.time.strip(), "%H:%M")
        except ValueError:
            print(f"ERROR: Cannot parse --time '{args.time}'. Use HH:MM:SS format.", file=sys.stderr)
            sys.exit(1)
    event_dt     = ref_date.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
    window_start = event_dt - timedelta(seconds=args.window_before)
    window_end   = event_dt + timedelta(seconds=args.window_after)

    # Select files whose [start, end] overlaps [window_start, window_end]
    # Overlap condition: start <= window_end AND (end is None OR end >= window_start)
    selected = [
        fi for fi in file_info
        if fi['start'] is not None
        and fi['start'] <= window_end
        and (fi['end'] is None or fi['end'] >= window_start)
    ]

    if not selected:
        print(
            f"ERROR: No files cover the window "
            f"{window_start.strftime('%H:%M:%S')} – {window_end.strftime('%H:%M:%S')} "
            f"(event={args.time}, before={args.window_before}s, after={args.window_after}s)",
            file=sys.stderr,
        )
        print("\nAvailable file time ranges:", file=sys.stderr)
        for fi in file_info:
            s = fi['start'].strftime('%H:%M:%S') if fi['start'] else '?'
            e = fi['end'].strftime('%H:%M:%S')   if fi['end']   else '?'
            print(f"  {s} – {e}  {os.path.basename(fi['path'])}", file=sys.stderr)
        sys.exit(1)

    # Coverage summary to stderr
    cov_start = min(fi['start'] for fi in selected if fi['start'])
    cov_end   = max((fi['end'] for fi in selected if fi['end']), default=selected[-1]['start'])
    print(
        f"Selected {len(selected)} file(s)  coverage {cov_start.strftime('%H:%M:%S')} – {cov_end.strftime('%H:%M:%S')}  "
        f"(window {window_start.strftime('%H:%M:%S')} – {window_end.strftime('%H:%M:%S')})",
        file=sys.stderr,
    )

    # File paths to stdout — ready for shell substitution or copy-paste into --files
    for fi in selected:
        print(fi['path'])


if __name__ == '__main__':
    main()
