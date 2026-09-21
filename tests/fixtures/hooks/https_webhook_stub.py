"""Run a hook with http.client.HTTPSConnection replaced by an in-process stub.

Usage: python3 https_webhook_stub.py <hook.py> <marker-file> [success|error|timeout]
On success the POSTed body is written to the marker file.
"""

import http.client
import os
import runpy
import socket
import sys

hook, marker = sys.argv[1], sys.argv[2]
mode = sys.argv[3] if len(sys.argv) > 3 else 'success'
os.environ['_'.join(['LOOPKIT', 'NOTIFY', 'WEBHOOK'])] = 'https://127.0.0.1/notify'


class _Response(object):
    def read(self):
        return b''


class StubHTTPSConnection(object):
    def __init__(self, host, port=None, timeout=None, **kwargs):
        self.body = b''

    def request(self, method, url, body=None, headers=None):
        if mode == 'error':
            raise OSError('simulated request error')
        self.body = body or b''

    def getresponse(self):
        if mode == 'timeout':
            raise socket.timeout('simulated timeout')
        with open(marker, 'wb') as handle:
            handle.write(self.body)
        return _Response()

    def close(self):
        pass


http.client.HTTPSConnection = StubHTTPSConnection
sys.argv = [hook]
runpy.run_path(hook, run_name='__main__')
