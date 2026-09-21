#!/usr/bin/env python3
"""SessionEnd hook: sends terminal notification and optional webhook when session terminates.

Cleans up session state file after firing. Fails open on any error.
"""

import datetime
import http.client
import json
import math
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from ar_hook_utils import (  # noqa: E402
    find_recent_tsv, is_enabled, js_basename, js_cwd, js_truthy, load_session_state, log,
    now_ms, output, read_tsv_tail, run, safe_parse_stdin, session_state_path,
)

HOOK_NAME = 'stop-notify'


def parse_date_ms(value):
    """new Date(value).getTime(): epoch milliseconds, or NaN when unparseable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        text = str(value).strip()
        if len(text) == 10:  # date-only ISO strings are UTC
            text += 'T00:00:00+00:00'
        parsed = datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))
        return parsed.timestamp() * 1000  # naive date-times are local time, as in JS
    except Exception:
        return float('nan')


def format_duration(started_at):
    if not js_truthy(started_at):
        return 'unknown'
    ms = now_ms() - parse_date_ms(started_at)
    if ms < 0:
        return 'unknown'
    if ms != ms:
        return 'NaNm NaNs'  # matches the JavaScript output for an unparseable date
    total_seconds = math.floor(ms / 1000)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return '%dh %dm' % (hours, minutes)
    return '%dm %ds' % (minutes, seconds)


_METRIC = re.compile(r'[\t|]([0-9.-]+)(?:[\t|]|\Z)')


def build_tsv_summary(project_root):
    tsv_path = find_recent_tsv(project_root, 120)  # look back 2 hours on session end
    if not tsv_path:
        return {'text': 'no iterations recorded', 'iterations': 0}

    tsv = read_tsv_tail(tsv_path, 1)
    if not tsv:
        return {'text': 'no iterations recorded', 'iterations': 0}

    last_row = tsv['rows'][0] if tsv['rows'] else ''
    match = _METRIC.search(last_row)
    metric = match.group(1) if match else 'n/a'

    return {
        'text': '%d iterations, metric: %s' % (tsv['total'], metric),
        'iterations': tsv['total'],
    }


# Characters the WHATWG URL parser leaves unencoded in the path and query.
_PATH_SAFE = "!$%&'()*+,-./:;=@[]^_|~"
_QUERY_SAFE = "!$%&()*+,-./:;=?@[]^_`{|}~"


def post_webhook(webhook_url, payload):
    """POST the payload; bounded by a 2-second socket timeout and never raises."""
    try:
        parsed = urllib.parse.urlsplit(webhook_url)
        if not parsed.scheme or not parsed.netloc:
            return  # new URL() would throw: nothing is sent
        client = http.client.HTTPConnection if parsed.scheme == 'http' else http.client.HTTPSConnection
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        path = urllib.parse.quote(parsed.path or '/', safe=_PATH_SAFE)
        if parsed.query:
            path += '?' + urllib.parse.quote(parsed.query, safe=_QUERY_SAFE)
        connection = client(parsed.hostname, port, timeout=2)
        try:
            connection.request('POST', path, body=body, headers={
                'Content-Type': 'application/json',
                'Content-Length': str(len(body)),
            })
            connection.getresponse().read()
        finally:
            connection.close()
    except Exception:
        pass


def cleanup_session_file(stdin):
    try:
        os.unlink(session_state_path(stdin))
    except Exception:
        pass  # already gone or unwritable


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)

    state = load_session_state(stdin)
    duration = format_duration(state.get('startedAt'))
    project_root = state.get('projectRoot') if js_truthy(state.get('projectRoot')) else js_cwd()
    tsv_summary = build_tsv_summary(project_root)
    project_name = js_basename(project_root)

    notify_text = 'autoresearch;Session completed — %s (%s)' % (project_name, duration)
    result = {'terminalSequence': '\x1b]777;notify;' + notify_text + '\x07'}

    # Optional webhook is bounded and awaited so the process cannot exit early.
    webhook_url = os.environ.get('AR_NOTIFY_WEBHOOK')
    if webhook_url:
        post_webhook(webhook_url, {
            'text': 'autoresearch session completed',
            'project': project_name,
            'branch': state.get('gitBranch') if js_truthy(state.get('gitBranch')) else '',
            'duration': duration,
            'tsv_summary': tsv_summary['text'],
        })

    log(HOOK_NAME, {'projectName': project_name, 'duration': duration, 'iterations': tsv_summary['iterations']})

    cleanup_session_file(stdin)

    output(result)
    sys.exit(0)


if __name__ == '__main__':
    run(HOOK_NAME, main)
