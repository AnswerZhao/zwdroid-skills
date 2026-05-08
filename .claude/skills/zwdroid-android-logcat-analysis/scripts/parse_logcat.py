from __future__ import annotations

"""
parse_logcat.py — parse threadtime-format logcat files into events.jsonl + index.jsonl.

Usage:
    python3 scripts/parse_logcat.py --files a.log b.log --output-dir .logcat-analysis/ --year 2024
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.parse_event_message import parse_event_message

# threadtime pattern: MM-DD HH:MM:SS.mmm  PID  TID L TAG: msg
THREADTIME = re.compile(
    r'^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s+'
    r'(\d+)\s+(\d+)\s+'
    r'([VDIWEFS])\s+'
    r'([^:]+):\s?'
    r'(.*)$'
)

# Heuristic: event-buffer tags look like lowercase_with_underscores
EVENT_TAG_PATTERN = re.compile(r'^[a-z][a-z0-9_]+$')


def load_event_tags(data_dir):
    path = os.path.join(data_dir, 'event_log_tags.json')
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    # Strip _meta, keep only tag entries
    return {k: v for k, v in raw.items() if not k.startswith('_')}


def parse_ts(raw_ts: str, year: int):
    """Convert 'MM-DD HH:MM:SS.mmm' to 'YYYY-MM-DDTHH:MM:SS.mmm'. Returns None on failure."""
    try:
        dt = datetime.strptime(f"{year}-{raw_ts.strip()}", "%Y-%m-%d %H:%M:%S.%f")
        # Format with 3-digit milliseconds
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    except ValueError:
        return None


def parse_files(files, output_dir, year, tag_dict):
    os.makedirs(output_dir, exist_ok=True)

    events_path = os.path.join(output_dir, 'events.jsonl')
    index_path  = os.path.join(output_dir, 'index.jsonl')

    stats = {
        'total_lines': 0,
        'events_count': 0,
        'index_count': 0,
        'skipped_lines': 0,   # non-matching threadtime
        'parse_errors': 0,    # utf-8 decode failures
    }
    unknown_event_tags = set()   # event-looking tags not in dict
    ts_first = None
    ts_last  = None

    with open(events_path, 'w', encoding='utf-8') as ef, \
         open(index_path,  'w', encoding='utf-8') as ix:

        for src_file in files:
            basename = os.path.basename(src_file)
            try:
                fh = open(src_file, encoding='utf-8', errors='replace')
            except OSError as e:
                print(f"  ERROR opening {src_file}: {e}", file=sys.stderr)
                continue

            with fh:
                for line_no, raw_line in enumerate(fh, 1):
                    stats['total_lines'] += 1

                    # Detect replacement character — count as parse error
                    if '�' in raw_line:
                        stats['parse_errors'] += 1

                    line = raw_line.rstrip('\n\r')
                    m = THREADTIME.match(line)
                    if not m:
                        stats['skipped_lines'] += 1
                        continue

                    raw_ts, pid, tid, level, tag, msg = m.groups()
                    tag = tag.strip()
                    pid = int(pid)
                    tid = int(tid)
                    ts  = parse_ts(raw_ts, year)

                    # Track time range
                    if ts:
                        if ts_first is None or ts < ts_first:
                            ts_first = ts
                        if ts_last is None or ts > ts_last:
                            ts_last = ts

                    if tag in tag_dict:
                        # Structured event
                        entry = tag_dict[tag]
                        fields = parse_event_message(msg, entry['fields'])
                        record = {
                            'ts':          ts,
                            'pid':         pid,
                            'tid':         tid,
                            'tag':         tag,
                            'tag_id':      entry['tag_id'],
                            'fields':      fields,
                            'source_file': basename,
                            'line_no':     line_no,
                        }
                        ef.write(json.dumps(record, ensure_ascii=False) + '\n')
                        stats['events_count'] += 1
                    else:
                        # Free-text index
                        record = {
                            'ts':          ts,
                            'pid':         pid,
                            'tid':         tid,
                            'level':       level,
                            'tag':         tag,
                            'msg':         msg,
                            'source_file': basename,
                            'line_no':     line_no,
                        }
                        ix.write(json.dumps(record, ensure_ascii=False) + '\n')
                        stats['index_count'] += 1

                        # Track tags that look like event tags but aren't in our dict
                        if EVENT_TAG_PATTERN.match(tag):
                            unknown_event_tags.add(tag)

    sources = {
        'input_files': [os.path.basename(f) for f in files],
        'year': year,
        'time_range': {
            'start': ts_first,
            'end':   ts_last,
        },
        'stats': {
            'total_lines':         stats['total_lines'],
            'events_count':        stats['events_count'],
            'index_count':         stats['index_count'],
            'skipped_lines':       stats['skipped_lines'],
            'parse_errors':        stats['parse_errors'],
            'unknown_event_tags':  sorted(unknown_event_tags),
        },
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }

    sources_path = os.path.join(output_dir, 'sources.json')
    with open(sources_path, 'w', encoding='utf-8') as f:
        json.dump(sources, f, indent=2, ensure_ascii=False)

    # Generate analysis_log.md template (skip if exists — preserve user progress)
    write_analysis_log_template(output_dir)

    return sources


ANALYSIS_LOG_TEMPLATE = """# Logcat 分析进度日志

> **用途**：记录分析过程的阶段性进度，让任何中断（上下文压缩、会话切断、人工暂停）后都能从此续接。
> **更新时机**：每次形成/调整假设、拿到关键证据、排除某方向、切换调查路径时，立即 Edit 此文件追加。
> **续接方式**：开始任何新一轮分析前，先完整读一遍本文件，再决定下一步。

## 分析上下文
- event_time: <待填写，如 14:08:52>
- target: <待填写，package_name 或 process_name>
- 时间窗口: <待填写，如 14:00:00 ~ 14:10:00>

## 当前假设
<信号 → 怀疑对象 → 关键证据 [source_file:line_no]，按因果链推进>

- 暂无

## 已确认的事实
<带 source_file:line_no 引用的硬证据，按时间顺序>

- 暂无

## 待查问题
<下一步要执行的具体 query 或验证动作；每次更新"已确认的事实"后回头扫一遍这里，已答的删除>

- 暂无

## 已排除的方向（可选，遇到才填）
<已查证为无关的假设/进程/路径，避免重复调查；如果分析路径直白没有走过弯路，此段可保持空>

- 暂无
"""


def write_analysis_log_template(output_dir):
    """Write empty analysis_log.md template if not present. Never overwrite."""
    log_path = os.path.join(output_dir, 'analysis_log.md')
    if os.path.exists(log_path):
        return
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write(ANALYSIS_LOG_TEMPLATE)


def main():
    parser = argparse.ArgumentParser(description='Parse logcat threadtime files')
    parser.add_argument('--files', nargs='+', required=True, help='Input log file(s)')
    parser.add_argument('--output-dir', default='.logcat-analysis/', help='Output directory')
    parser.add_argument('--year', type=int, default=datetime.now().year, help='Year for timestamps')
    args = parser.parse_args()

    # Locate data dir relative to this script's project root
    script_dir  = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_dir    = os.path.join(project_root, 'data')

    print(f"Loading event tag dictionary from {data_dir} ...")
    tag_dict = load_event_tags(data_dir)
    print(f"  {len(tag_dict)} tags loaded")

    print(f"Parsing {len(args.files)} file(s) → {args.output_dir}")
    sources = parse_files(args.files, args.output_dir, args.year, tag_dict)

    s = sources['stats']
    print(f"\nDone.")
    print(f"  total lines : {s['total_lines']:,}")
    print(f"  events      : {s['events_count']:,}")
    print(f"  index       : {s['index_count']:,}")
    print(f"  skipped     : {s['skipped_lines']:,}")
    print(f"  parse errors: {s['parse_errors']:,}")
    print(f"  unknown event tags: {len(s['unknown_event_tags'])}")
    print(f"  time range  : {sources['time_range']['start']} → {sources['time_range']['end']}")
    log_path = os.path.join(args.output_dir, 'analysis_log.md')
    if os.path.exists(log_path):
        print(f"\n  analysis_log: {log_path} (ready for incremental updates)")


if __name__ == '__main__':
    main()
