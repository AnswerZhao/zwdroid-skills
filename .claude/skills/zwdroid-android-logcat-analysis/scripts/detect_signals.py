from __future__ import annotations

"""
detect_signals.py — detect known anomaly patterns and produce signals.json.

Rules are hardcoded in Python (no YAML engine per MVP spec).

Usage:
    python3 scripts/detect_signals.py --work-dir .logcat-analysis/
"""

import argparse
import datetime
import json
import os
import re
import sys

# ── severity map ──────────────────────────────────────────────────────────────

HIGH_RULES = {
    'fatal_exception', 'anr_in_process', 'tombstone_written',
    'watchdog', 'force_finishing', 'window_freeze', 'proc_died_foreground',
    'hal_died', 'system_server_restart', 'input_anr',
}
# proc_died_background / selinux_denial / binder_died are medium severity

INFO_RULES = {'user_switch', 'task_auto_restored'}  # lifecycle context events, not anomalies

def severity(rule_id):
    if rule_id in HIGH_RULES:
        return 'high'
    if rule_id in INFO_RULES:
        return 'info'
    return 'medium'


# ── index.jsonl rules ─────────────────────────────────────────────────────────

ANR_RE          = re.compile(r'^ANR in (\S+)')
SKIPPED_RE      = re.compile(r'Skipped (\d+) frames')
# selinux: strip the unique audit(...) timestamp before deduplication
_AUDIT_ID_RE    = re.compile(r'audit\([\d.]+:\d+\)')
_AVC_FIELD_RE   = re.compile(r'\b([a-zA-Z_]+)=("[^"]*"|\S+)')
_AVC_PERM_RE    = re.compile(r'avc:\s+denied\s+\{([^}]+)\}')

# AAOS car_helper/car_user switch tags (appear in index.jsonl, not events.jsonl)
_CAR_USER_SWITCH_TAGS = {
    'car_user_mgr_switch_user_req',
    'car_user_svc_switch_user_req',
    'car_helper_user_switching',
    'car_helper_user_unlocked',
    'car_helper_user_unlocking',
}

def check_index(record):
    """Return list of (rule_id, captures) for a single index.jsonl record."""
    tag   = record.get('tag', '')
    level = record.get('level', '')
    msg   = record.get('msg', '')
    hits  = []

    # fatal_exception
    if tag == 'AndroidRuntime' and level == 'E' and 'FATAL EXCEPTION' in msg:
        hits.append(('fatal_exception', {}))

    # anr_in_process
    if tag == 'ActivityManager' and level == 'E':
        m = ANR_RE.match(msg)
        if m:
            hits.append(('anr_in_process', {'process': m.group(1)}))

    # tombstone_written
    if tag == 'DEBUG' and 'tombstone' in msg.lower():
        hits.append(('tombstone_written', {}))

    # window_freeze
    if 'Window freeze timeout expired' in msg:
        hits.append(('window_freeze', {}))

    # force_finishing
    if 'Force finishing activity' in msg:
        hits.append(('force_finishing', {}))

    # skipped_frames
    if tag == 'Choreographer':
        m = SKIPPED_RE.search(msg)
        if m and int(m.group(1)) >= 30:
            hits.append(('skipped_frames', {'frames': int(m.group(1))}))

    # watchdog — exact match to avoid AAOS ClusterWatchdogPolicy false positives
    if tag == 'Watchdog' and level == 'E':
        hits.append(('watchdog', {}))

    # selinux_denial — avc: denied in any log line; medium severity
    if 'avc: denied' in msg:
        hits.append(('selinux_denial', {'msg': msg[:120]}))

    # hal_died — hwservicemanager E-level indicates HAL communication failure or death
    if tag == 'hwservicemanager' and level == 'E':
        hits.append(('hal_died', {'msg': msg[:120]}))

    # user_switch — AAOS car_helper/car_user lifecycle context (info severity)
    if tag in _CAR_USER_SWITCH_TAGS:
        hits.append(('user_switch', {'tag': tag, 'msg': msg[:120]}))

    # input_anr — InputDispatcher reports app not responding
    if tag == 'InputDispatcher' and level == 'E' and 'Application is not responding' in msg:
        hits.append(('input_anr', {'msg': msg[:120]}))

    # binder_died — binder or HwBinder transaction failure (medium severity)
    msg_lower = msg.lower()
    if 'binder died' in msg_lower or 'hwbinder: transaction failed' in msg_lower:
        hits.append(('binder_died', {'msg': msg[:120]}))

    return hits


def selinux_denial_key(msg):
    """Return a stable, order-insensitive key for duplicate AVC denials."""
    msg_no_audit = _AUDIT_ID_RE.sub('audit(*)', msg)
    perm_match = _AVC_PERM_RE.search(msg_no_audit)
    fields = dict(_AVC_FIELD_RE.findall(msg_no_audit))
    key_parts = [
        perm_match.group(1).strip() if perm_match else '',
        fields.get('scontext', ''),
        fields.get('tcontext', ''),
        fields.get('tclass', ''),
        fields.get('name', ''),
        fields.get('path', ''),
    ]
    if any(key_parts):
        return '|'.join(key_parts)
    return msg_no_audit[:160]


# ── events.jsonl rules ────────────────────────────────────────────────────────

def check_event(record, stop_user_pids=None):
    """Return list of (rule_id, captures) for a single events.jsonl record."""
    tag    = record.get('tag', '')
    fields = record.get('fields') or {}
    hits   = []

    # lowmem_kill
    if tag == 'am_kill':
        hits.append(('lowmem_kill', {
            'pid':          fields.get('PID'),
            'process_name': fields.get('Process Name'),
            'oom_adj':      fields.get('OomAdj'),
            'reason':       fields.get('Reason'),
        }))

    # low_memory_event
    if tag == 'am_low_memory':
        hits.append(('low_memory_event', {
            'num_processes': fields.get('Num Processes'),
        }))

    # proc_died_foreground/background: split by oom_adj
    # negative oom_adj = system persistent process (normal exit, skip)
    # PIDs in stop_user_pids were killed by am_kill reason="stop user X due to finish user";
    # dying at foreground priority during a user switch is expected, not an anomaly.
    if tag == 'am_proc_died':
        oom = fields.get('OomAdj')
        if oom is not None:
            try:
                oom_int = int(oom)
                pid = fields.get('PID')
                captures = {
                    'pid':          pid,
                    'process_name': fields.get('Process Name'),
                    'oom_adj':      oom,
                }
                if 0 <= oom_int < 200:
                    if not (stop_user_pids and pid in stop_user_pids):
                        hits.append(('proc_died_foreground', captures))
                elif oom_int >= 200:
                    hits.append(('proc_died_background', captures))
            except (ValueError, TypeError):
                pass

    # system_server_restart — system_server process died (catastrophic)
    if tag == 'am_proc_died' and fields.get('Process Name') == 'system_server':
        hits.append(('system_server_restart', {
            'pid':     fields.get('PID'),
            'oom_adj': fields.get('OomAdj'),
        }))

    # user_switch — standard Android user lifecycle events (info severity)
    if tag == 'uc_dispatch_user_switch':
        hits.append(('user_switch', {
            'tag':        tag,
            'from_user':  fields.get('oldUserId'),
            'to_user':    fields.get('newUserId'),
        }))

    if tag == 'ssm_user_unlocked':
        hits.append(('user_switch', {
            'tag':     tag,
            'user_id': fields.get('userId'),
        }))

    return hits


# ── main ──────────────────────────────────────────────────────────────────────

def detect(work_dir):
    signals   = []
    hit_count = {
        'fatal_exception': 0, 'anr_in_process': 0, 'tombstone_written': 0,
        'window_freeze': 0, 'force_finishing': 0, 'skipped_frames': 0,
        'watchdog': 0, 'hal_died': 0, 'selinux_denial': 0,
        'lowmem_kill': 0, 'low_memory_event': 0,
        'proc_died_foreground': 0, 'proc_died_background': 0,
        'user_switch': 0,
        'task_auto_restored': 0,
        'system_server_restart': 0, 'input_anr': 0, 'binder_died': 0,
    }

    def add(rule_id, captures, record):
        signals.append({
            'rule_id':  rule_id,
            'severity': severity(rule_id),
            'ts':       record.get('ts'),
            'captures': captures,
            'source': {
                'file':    record.get('source_file'),
                'line_no': record.get('line_no'),
            },
        })
        hit_count[rule_id] += 1

    # Scan index.jsonl
    # selinux_denial: deduplicate by first 80 chars of msg — same AVC rule fires thousands of times
    selinux_seen = set()
    index_path = os.path.join(work_dir, 'index.jsonl')
    with open(index_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for rule_id, captures in check_index(rec):
                if rule_id == 'selinux_denial':
                    key = selinux_denial_key(rec.get('msg', ''))
                    if key in selinux_seen:
                        continue
                    selinux_seen.add(key)
                add(rule_id, captures, rec)

    # Scan events.jsonl
    # stop_user_pids: PIDs killed by "stop user X due to finish user" (user-switch cleanup).
    # am_kill always precedes am_proc_died for the same PID, so a single pass suffices.
    stop_user_pids = set()
    events_path = os.path.join(work_dir, 'events.jsonl')
    with open(events_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get('tag') == 'am_kill':
                reason = (rec.get('fields') or {}).get('Reason') or ''
                if 'stop user' in reason:
                    pid = (rec.get('fields') or {}).get('PID')
                    if pid is not None:
                        stop_user_pids.add(pid)
            for rule_id, captures in check_event(rec, stop_user_pids):
                add(rule_id, captures, rec)

    # Third pass: task_auto_restored
    # For each wm_on_create_called event that falls within 0-60s after a user_switch, emit a signal.
    user_switch_ts_list = [
        s['ts'] for s in signals if s['rule_id'] == 'user_switch' and s['ts'] is not None
    ]

    def _ts_to_seconds(ts):
        # ts format: '2026-04-16T14:08:29.055' — include date so cross-midnight logs are safe
        try:
            date_part, time_part = ts.split('T')
            y, mo, d = date_part.split('-')
            h, m, rest = time_part.split(':')
            s_frac = rest.split('.')
            day_secs = (int(y) * 365 + int(mo) * 31 + int(d)) * 86400
            return day_secs + int(h) * 3600 + int(m) * 60 + int(s_frac[0])
        except Exception:
            return None

    if user_switch_ts_list:
        switch_seconds = [_ts_to_seconds(ts) for ts in user_switch_ts_list]
        switch_seconds = [s for s in switch_seconds if s is not None]
        with open(events_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tag = rec.get('tag')
                # wm_on_create_called: Activity.onCreate() path (new task)
                # wm_task_to_front: task moved to front without onCreate (resume path)
                if tag not in ('wm_on_create_called', 'wm_task_to_front'):
                    continue
                evt_ts = rec.get('ts')
                if not evt_ts:
                    continue
                evt_sec = _ts_to_seconds(evt_ts)
                if evt_sec is None:
                    continue
                for sw_ts, sw_sec in zip(user_switch_ts_list, switch_seconds):
                    diff = evt_sec - sw_sec
                    if 0 <= diff <= 60:
                        fields = rec.get('fields') or {}
                        # wm_on_create_called carries Component Name (activity FQN);
                        # wm_task_to_front carries only Task (numeric task id), no component.
                        if tag == 'wm_on_create_called':
                            captures = {
                                'component': fields.get('Component Name'),
                                'restore_path': 'on_create',
                                'after_user_switch_ts': sw_ts,
                            }
                        else:  # wm_task_to_front
                            captures = {
                                'component': None,
                                'task_id': fields.get('Task'),
                                'restore_path': 'task_to_front',
                                'after_user_switch_ts': sw_ts,
                            }
                        add('task_auto_restored', captures, rec)
                        break  # only one match per event needed

    # Sort by ts (None last), assign sequential IDs
    signals.sort(key=lambda s: (s['ts'] is None, s['ts'] or ''))
    for i, sig in enumerate(signals, 1):
        sig['id'] = f"signal_{i:04d}"

    # Reorder keys for readability
    ordered = []
    for sig in signals:
        ordered.append({
            'id':       sig['id'],
            'rule_id':  sig['rule_id'],
            'severity': sig['severity'],
            'ts':       sig['ts'],
            'captures': sig['captures'],
            'source':   sig['source'],
        })

    out_path = os.path.join(work_dir, 'signals.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)

    high_path = os.path.join(work_dir, 'signals_high.json')
    with open(high_path, 'w', encoding='utf-8') as f:
        json.dump([s for s in ordered if s['severity'] == 'high'], f, indent=2, ensure_ascii=False)

    return ordered, hit_count


def write_summary_md(work_dir, signals, hit_count):
    """Persist the per-rule hit counts + first 10 signals to signals_summary.md.

    stdout-only summary is lost after context compression; this file lets the
    agent recover triage state with a single cat after resume.
    """
    lines = []
    lines.append('# Signals Summary')
    lines.append(f"Generated: {datetime.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Total signals: {len(signals)}")
    lines.append('')
    lines.append('## Per-rule hit counts')
    lines.append('')
    lines.append('| count | rule_id | severity |')
    lines.append('|---:|---|---|')
    for rule, count in hit_count.items():
        lines.append(f"| {count} | {rule} | {severity(rule)} |")
    lines.append('')

    if signals:
        lines.append('## First 10 signals (chronological)')
        lines.append('')
        for sig in signals[:10]:
            cap = sig.get('captures') or {}
            cap_str = ' '.join(f"{k}={v}" for k, v in cap.items()) if cap else '-'
            src = sig.get('source') or {}
            src_str = f"{src.get('file', '?')}:{src.get('line_no', '?')}"
            lines.append(f"- {sig.get('ts', '?')} | {sig['rule_id']} | {sig['severity']} | {src_str} | {cap_str}")
        lines.append('')

    out_path = os.path.join(work_dir, 'signals_summary.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return out_path


def main():
    parser = argparse.ArgumentParser(description='Detect anomaly signals in logcat analysis output')
    parser.add_argument('--work-dir', default='.logcat-analysis/', help='Work directory')
    args = parser.parse_args()

    print(f"Scanning {args.work_dir} ...")
    signals, hit_count = detect(args.work_dir)

    print(f"\nTotal signals: {len(signals)}")
    print("\nPer-rule hit counts:")
    for rule, count in hit_count.items():
        flag = '' if count > 0 else '  ← 0 hits (no such issue in this log, or tag not present)'
        print(f"  {count:4d}  {rule}{flag}")

    if signals:
        print(f"\nFirst 10 signals:")
        for sig in signals[:10]:
            print(json.dumps(sig, indent=2, ensure_ascii=False))

    summary_path = write_summary_md(args.work_dir, signals, hit_count)
    print(f"\nSummary written to {summary_path}", file=sys.stderr)


if __name__ == '__main__':
    main()
