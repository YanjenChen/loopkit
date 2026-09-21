"""Shared helpers: errors, JSON I/O, atomic writes, hashing, time."""

import datetime
import hashlib
import json
import os
import re
import tempfile


class LoopkitError(Exception):
    """A user-facing error. The CLI prints it without a traceback and exits 2."""


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec='seconds')


def dumps_line(obj):
    """Compact single-line JSON, the ledger and queue encoding."""
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_atomic(path, data):
    """Write bytes or text to path via a temp file and rename, so readers never see a partial file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data.encode('utf-8') if isinstance(data, str) else data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    fsync_dir(directory)


def write_json_atomic(path, obj):
    write_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


_SAFE_NAME = re.compile(r'[^A-Za-z0-9_-]')


def sanitize_name(text):
    return _SAFE_NAME.sub('-', text)


def truncate(text, limit):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:max(0, limit - 3)] + '...'


def fmt5(value):
    """A number with 5 significant digits: 41.200, 1.0231e6, 3.2100e-4."""
    if value == 0:
        return '0.0000'
    magnitude = abs(value)
    if 1e-3 <= magnitude < 1e5:
        digits_before = len(str(int(magnitude))) if magnitude >= 1 else 0
        if magnitude >= 1:
            decimals = max(0, 5 - digits_before)
        else:
            decimals = 4 - int(_floor_log10(magnitude))
        text = '%.*f' % (decimals, value)
        # Rounding can carry into a new digit (99999.5 -> 100000); fall through to scientific.
        if len(text.lstrip('-').split('.')[0].lstrip('0')) <= 5:
            return text
    mantissa, exponent = ('%.4e' % value).split('e')
    return '%se%d' % (mantissa, int(exponent))


def _floor_log10(x):
    exponent = 0
    while x >= 10:
        x /= 10.0
        exponent += 1
    while x < 1:
        x *= 10.0
        exponent -= 1
    return exponent


def fmt_pct(fraction):
    """A signed percentage with 2 significant digits: -0.80%, +1.2%, +12%."""
    pct = fraction * 100.0
    if pct == 0:
        return '+0.0%'
    decimals = max(0, 1 - _floor_log10(abs(pct)))
    text = '%+.*f' % (decimals, pct)
    return text + '%'
