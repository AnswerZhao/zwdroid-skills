from __future__ import annotations

"""
parse_event_message.py — core module for parsing event log message fields.

Event log messages are text-serialized field lists, e.g.:
  "[0,12345,10094,com.example.app,activity,com.example.app/.MainActivity]"

We use type-guided consumption: the field type determines how many characters
to consume. int/long/float always end at the next comma; string fields calculate
their boundary by counting how many commas must be reserved for remaining fields.

MVP scope: handles single-value, standard list, and string-with-comma cases.
"""

import sys


# ── type casting ─────────────────────────────────────────────────────────────

def _cast(raw: str, ftype: str):
    """Cast a raw string to the field's native type. Falls back to str on error."""
    raw = raw.strip()
    try:
        if ftype in ('int', 'long'):
            return int(raw)
        if ftype == 'float':
            return float(raw)
    except (ValueError, TypeError):
        pass
    return raw  # string or cast failure → keep as str


# ── single-field consumption ──────────────────────────────────────────────────

def _consume_field(content: str, pos: int, field_def: dict, remaining_fields: list):
    """
    Consume one field value from content starting at pos.

    Returns (value, new_pos) where new_pos points to the start of the next field
    (i.e., one past the separator comma, or len(content) if this was the last).

    Strategy by type:
      int / long / float  — consume up to the next comma
      string              — consume up to the comma that leaves enough separators
                            for all remaining fields (type-guided boundary)
      list                — TODO: nested list not supported in MVP; consume rest
      unknown             — treat as int/long/float (comma-delimited)
    """
    ftype = field_def.get('type', 'string')
    rest = content[pos:]

    if ftype in ('int', 'long', 'float', 'unknown'):
        comma = rest.find(',')
        if comma == -1:
            return _cast(rest, ftype), len(content)
        return _cast(rest[:comma], ftype), pos + comma + 1

    if ftype == 'string':
        n_after = len(remaining_fields)
        if n_after == 0:
            # Last field — consume everything remaining
            return rest, len(content)

        # Find all comma positions within `rest`
        commas = [i for i, c in enumerate(rest) if c == ',']

        if len(commas) < n_after:
            # Fewer separators than remaining fields — content is shorter than expected.
            # Take everything; downstream None-filling will pad missing fields.
            # TODO: emit parse_warning here (MVP skips warning collection)
            return rest, len(content)

        # Reserve the last n_after commas for the remaining fields.
        split = commas[len(commas) - n_after]
        return rest[:split], pos + split + 1

    if ftype == 'list':
        # TODO: nested list not supported in MVP — consume rest of content
        return rest, len(content)

    # Fallback
    comma = rest.find(',')
    if comma == -1:
        return rest, len(content)
    return rest[:comma], pos + comma + 1


# ── public API ────────────────────────────────────────────────────────────────

def parse_event_message(msg: str, field_defs: list) -> dict:
    """
    Parse an event log message into a {field_name: value} dict.

    Args:
        msg:        raw event message, e.g. "[0,12345,com.example,...]" or "41"
        field_defs: list of {"name": str, "type": str, ...} dicts from event_log_tags.json

    Returns:
        dict with one key per field_def, plus "_extras" list if there are
        more values than definitions.  Never raises; missing fields → None.
    """
    content = msg.strip()

    # ── Case 1: single-value (no brackets) ───────────────────────────────────
    if not content.startswith('['):
        if not field_defs:
            return {}
        result = {fd['name']: None for fd in field_defs}
        result[field_defs[0]['name']] = _cast(content, field_defs[0].get('type', 'string'))
        return result

    # ── Case 2 & 3: list message ──────────────────────────────────────────────
    # Strip outer brackets
    if content.endswith(']'):
        content = content[1:-1]
    else:
        content = content[1:]  # malformed — best effort

    result = {}
    pos = 0

    for i, fd in enumerate(field_defs):
        if pos >= len(content):
            result[fd['name']] = None  # ran out of input — pad with None
            continue
        remaining = field_defs[i + 1:]
        value, pos = _consume_field(content, pos, fd, remaining)
        result[fd['name']] = value

    # Collect extra values beyond the defined fields
    if pos < len(content):
        tail = content[pos:].lstrip(',')
        if tail:
            result['_extras'] = [v.strip() for v in tail.split(',')]

    return result


# ── smoke tests ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    failures = 0

    def check(label, got, expected):
        global failures
        if got == expected:
            print(f'  [PASS] {label}')
        else:
            print(f'  [FAIL] {label}')
            print(f'         expected: {expected}')
            print(f'         got:      {got}')
            failures += 1

    print('=== parse_event_message smoke tests ===\n')

    # ── 1. single int value ───────────────────────────────────────────────────
    print('1. Single int (am_low_memory style)')
    result = parse_event_message(
        '41',
        [{'name': 'num_processes', 'type': 'int'}],
    )
    check('single int', result, {'num_processes': 41})

    # ── 2. single string value ────────────────────────────────────────────────
    print('2. Single string')
    result = parse_event_message(
        'hello world',
        [{'name': 'message', 'type': 'string'}],
    )
    check('single string', result, {'message': 'hello world'})

    # ── 3. standard list — all ints ───────────────────────────────────────────
    print('3. Standard list — ints only')
    result = parse_event_message(
        '[0,1,2]',
        [
            {'name': 'a', 'type': 'int'},
            {'name': 'b', 'type': 'int'},
            {'name': 'c', 'type': 'int'},
        ],
    )
    check('standard list ints', result, {'a': 0, 'b': 1, 'c': 2})

    # ── 4. standard list — mixed types (am_proc_start style) ─────────────────
    print('4. Standard list — mixed types (am_proc_start)')
    result = parse_event_message(
        '[0,12345,10094,com.example.app,activity,com.example.app/.MainActivity]',
        [
            {'name': 'User', 'type': 'int'},
            {'name': 'PID', 'type': 'int'},
            {'name': 'UID', 'type': 'int'},
            {'name': 'Process Name', 'type': 'string'},
            {'name': 'Type', 'type': 'string'},
            {'name': 'Component', 'type': 'string'},
        ],
    )
    check('am_proc_start', result, {
        'User': 0, 'PID': 12345, 'UID': 10094,
        'Process Name': 'com.example.app',
        'Type': 'activity',
        'Component': 'com.example.app/.MainActivity',
    })

    # ── 5. string field containing one comma ──────────────────────────────────
    print('5. String field with single comma (am_kill style)')
    # am_kill: (User|1|5),(PID|1|5),(Process Name|3),(OomAdj|1|5),(Reason|3)
    result = parse_event_message(
        '[0,12345,com.example.app,900,lmk reason=provider]',
        [
            {'name': 'User', 'type': 'int'},
            {'name': 'PID', 'type': 'int'},
            {'name': 'Process Name', 'type': 'string'},
            {'name': 'OomAdj', 'type': 'int'},
            {'name': 'Reason', 'type': 'string'},
        ],
    )
    check('am_kill simple', result, {
        'User': 0, 'PID': 12345,
        'Process Name': 'com.example.app',
        'OomAdj': 900,
        'Reason': 'lmk reason=provider',
    })

    # ── 6. string field with comma, followed by two more string fields ─────────
    print('6. String with comma + two trailing strings')
    # Pattern: int, str(with comma), str, str
    result = parse_event_message(
        '[0,com.example.app,flg=0x10000000 cmp=com.a/.B,info]',
        [
            {'name': 'User', 'type': 'int'},
            {'name': 'Process', 'type': 'string'},
            {'name': 'Detail', 'type': 'string'},
            {'name': 'Note', 'type': 'string'},
        ],
    )
    check('string+comma+two-strings', result, {
        'User': 0,
        'Process': 'com.example.app',
        'Detail': 'flg=0x10000000 cmp=com.a/.B',
        'Note': 'info',
    })

    # ── 7. fewer values than field definitions ─────────────────────────────────
    print('7. Fewer values than definitions → None padding')
    result = parse_event_message(
        '[1,2]',
        [
            {'name': 'a', 'type': 'int'},
            {'name': 'b', 'type': 'int'},
            {'name': 'c', 'type': 'int'},
        ],
    )
    check('fewer values', result, {'a': 1, 'b': 2, 'c': None})

    # ── 8. more values than field definitions → _extras ───────────────────────
    print('8. More values than definitions → _extras')
    result = parse_event_message(
        '[1,2,3,4,5]',
        [
            {'name': 'a', 'type': 'int'},
            {'name': 'b', 'type': 'int'},
        ],
    )
    check('extra values', result, {'a': 1, 'b': 2, '_extras': ['3', '4', '5']})

    # ── summary ───────────────────────────────────────────────────────────────
    print()
    total = 8
    passed = total - failures
    print(f'Results: {passed}/{total} passed', '✓' if failures == 0 else '✗')
    sys.exit(0 if failures == 0 else 1)
