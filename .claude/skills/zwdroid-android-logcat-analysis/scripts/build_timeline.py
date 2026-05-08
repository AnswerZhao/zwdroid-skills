from __future__ import annotations

"""
build_timeline.py — build activity_timeline.json and process_timeline.json from events.jsonl.

Usage:
    python3 scripts/build_timeline.py --work-dir .logcat-analysis/
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

# Strip AAOS vendor-format key=value suffixes absorbed into Component Name
# e.g. "com.example/.Activity userLeaving=true" → "com.example/.Activity"
_COMPONENT_SUFFIX_RE = re.compile(r'\s+\w+=\S+$')

# ── tag → (event_type, timeline)  ─────────────────────────────────────────────

# Activity tags: Android 12+ uses wm_* prefix; older Android used am_* prefix.
# We handle both so the script works with either schema.
ACTIVITY_TAGS = {
    # Android 12+ (wm_*)
    'wm_create_activity':       'create',
    'wm_resume_activity':       'resume',
    'wm_pause_activity':        'pause',
    'wm_destroy_activity':      'destroy',
    'wm_activity_launch_time':  'launch_time',
    # Android < 12 (am_*) — kept for forward-compat if needed
    'am_create_activity':       'create',
    'am_resume_activity':       'resume',
    'am_pause_activity':        'pause',
    'am_destroy_activity':      'destroy',
    'am_activity_launch_time':  'launch_time',
    'am_activity_fully_drawn_time': 'fully_drawn',
    # TODO: wm_activity_fully_drawn_time not in Android 12 schema — add when found
}

PROCESS_TAGS = {
    'am_proc_start': 'start',
    'am_proc_died':  'died',
    'am_kill':       'killed',
}


# ── field extraction helpers ──────────────────────────────────────────────────

def _get(fields, *keys):
    """Return the first non-None value from fields dict among the given keys."""
    for k in keys:
        v = fields.get(k)
        if v is not None:
            return v
    return None


def build_activity_record(event):
    tag    = event['tag']
    fields = event.get('fields') or {}
    etype  = ACTIVITY_TAGS[tag]

    component = _get(fields, 'Component Name', 'Component')
    if component:
        # Strip AAOS vendor-format suffix (e.g. "userLeaving=true") absorbed into Component Name
        component = _COMPONENT_SUFFIX_RE.sub('', component)

    record = {
        'ts':           event.get('ts'),
        'event_type':   etype,
        'tag':          tag,
        'component':    component,
        'token':        fields.get('Token'),
        'source_file':  event.get('source_file'),
        'line_no':      event.get('line_no'),
    }

    if etype in ('launch_time', 'fully_drawn'):
        record['launch_time_ms'] = fields.get('time')

    return record


def build_process_record(event):
    tag    = event['tag']
    fields = event.get('fields') or {}
    etype  = PROCESS_TAGS[tag]

    record = {
        'ts':           event.get('ts'),
        'event_type':   etype,
        'tag':          tag,
        'pid':          _get(fields, 'PID'),
        'process_name': _get(fields, 'Process Name'),
        'source_file':  event.get('source_file'),
        'line_no':      event.get('line_no'),
    }

    if etype == 'start':
        record['uid']  = fields.get('UID')
        record['type'] = fields.get('Type')

    if etype in ('died', 'killed'):
        record['oom_adj'] = fields.get('OomAdj')

    if etype == 'killed':
        record['reason'] = fields.get('Reason')

    return record


# ── main ──────────────────────────────────────────────────────────────────────

def build_timelines(work_dir):
    events_path = os.path.join(work_dir, 'events.jsonl')
    activity_out = os.path.join(work_dir, 'activity_timeline.json')
    process_out  = os.path.join(work_dir, 'process_timeline.json')

    activity = []
    process  = []
    warnings = 0

    with open(events_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                warnings += 1
                continue

            tag = event.get('tag', '')

            if tag in ACTIVITY_TAGS:
                try:
                    activity.append(build_activity_record(event))
                except Exception:
                    warnings += 1

            elif tag in PROCESS_TAGS:
                try:
                    process.append(build_process_record(event))
                except Exception:
                    warnings += 1

    # Sort by ts (None-safe: None sorts last)
    activity.sort(key=lambda r: (r['ts'] is None, r['ts'] or ''))
    process.sort( key=lambda r: (r['ts'] is None, r['ts'] or ''))

    with open(activity_out, 'w', encoding='utf-8') as f:
        json.dump(activity, f, indent=2, ensure_ascii=False)

    with open(process_out, 'w', encoding='utf-8') as f:
        json.dump(process, f, indent=2, ensure_ascii=False)

    return activity, process, warnings


def main():
    parser = argparse.ArgumentParser(description='Build activity and process timelines')
    parser.add_argument('--work-dir', default='.logcat-analysis/', help='Work directory')
    args = parser.parse_args()

    print(f"Reading events from {args.work_dir} ...")
    activity, process, warnings = build_timelines(args.work_dir)

    print(f"\nactivity_timeline.json : {len(activity)} entries")
    if activity:
        print(f"  time range : {activity[0]['ts']} → {activity[-1]['ts']}")
        for r in activity[:3]:
            print(f"  {r['ts']}  {r['event_type']:12s}  {r['component']}")

    print(f"\nprocess_timeline.json  : {len(process)} entries")
    if process:
        print(f"  time range : {process[0]['ts']} → {process[-1]['ts']}")
        for r in process[:3]:
            print(f"  {r['ts']}  {r['event_type']:8s}  pid={r['pid']}  {r['process_name']}")

    if warnings:
        print(f"\nwarnings: {warnings}")

    if not activity and not process:
        print("\n(Both timelines empty — events.jsonl contains no activity/process management tags.)")
        print(" This is expected for app-process logcat; system_server logcat is needed for AMS events.")


if __name__ == '__main__':
    main()
