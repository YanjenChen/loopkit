"""Run a hook against a local HTTP webhook server.

Usage: python3 webhook_smoke.py <hook.py>
Prints 'webhook received' once the hook has POSTed the expected payload and exited 0.
"""

import http.server
import json
import os
import subprocess
import sys
import threading

if len(sys.argv) < 2:
    sys.exit(2)
hook = sys.argv[1]
received = {'ok': False}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode('utf-8')
        self.send_response(204)
        self.end_headers()
        try:
            received['ok'] = json.loads(body).get('text') == 'autoresearch session completed'
        except ValueError:
            received['ok'] = False

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = dict(os.environ)
env['_'.join(['AR', 'NOTIFY', 'WEBHOOK'])] = 'http://127.0.0.1:%d/notify' % server.server_address[1]
try:
    child = subprocess.run([sys.executable, '-B', hook], input=json.dumps({'session_id': 'webhook-smoke'}).encode(),
                           env=env, stdout=subprocess.DEVNULL, timeout=3)
finally:
    server.shutdown()
if child.returncode != 0 or not received['ok']:
    sys.exit(1)
print('webhook received')
