#!/usr/bin/env python3
"""SessionEnd hook: when a run session ends, sends a terminal notification and an optional webhook.

The summary (batch progress and front) comes from the run's ledger. Set
LOOPKIT_NOTIFY_WEBHOOK to also POST it as JSON. Outside a run session it does
nothing. Fails open on any error.
"""

import http.client
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from hook_utils import (  # noqa: E402
    is_enabled, js_truthy, log, output, run, run_context, run_status_text, safe_parse_stdin,
)

HOOK_NAME = 'stop-notify'

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


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)
    run_paths = run_context(stdin)
    if run_paths is None:
        sys.exit(0)

    status = run_status_text(run_paths, recent=0)
    lines = status.split('\n')
    headline = lines[1] if len(lines) > 1 else lines[0]
    notify_text = 'loopkit;Run session ended - %s' % headline
    result = {'terminalSequence': '\x1b]777;notify;' + notify_text + '\x07'}

    # Optional webhook is bounded and awaited so the process cannot exit early.
    webhook_url = os.environ.get('LOOPKIT_NOTIFY_WEBHOOK')
    if webhook_url:
        post_webhook(webhook_url, {'text': 'loopkit run session ended', 'run': run_paths.name, 'status': status})

    log(HOOK_NAME, {'action': 'notify'})
    output(result)
    sys.exit(0)


if __name__ == '__main__':
    run(HOOK_NAME, main)
