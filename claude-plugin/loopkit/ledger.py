"""The append-only, hash-chained ledger: the only record of a run's history.

Each line is one JSON object. Every line stores the sha256 of the previous
line's bytes in `prev`, so edits anywhere in the file break the chain. Lines
are written with a single write() and fsync'd; a trailing fragment without a
newline is an interrupted write and is ignored by readers and removed by the
next append.

The last line has no successor to vouch for it, so the sha256 and seq of the
newest line are also kept in <ledger>.head; a mismatch there means the tail
was edited or truncated.
"""

import fcntl
import json
import os

from .util import LoopkitError, dumps_line, now_iso, sha256_bytes, write_atomic

SCHEMA = 1
GENESIS = '0' * 64

EVENT_TYPES = (
    'run_created', 'batch_start', 'batch_stop', 'promote', 'request_rejected',
    'integrity', 'integrity_warning',
)


class Ledger(object):
    def __init__(self, path, run_name):
        self.path = path
        self.head_path = path + '.head'
        self.run = run_name

    # -- reading ---------------------------------------------------------

    def _complete_lines(self):
        try:
            with open(self.path, 'rb') as f:
                data = f.read()
        except OSError:
            return []
        end = data.rfind(b'\n')
        if end < 0:
            return []
        return data[:end].split(b'\n')

    def read(self):
        """Return (records, problems). Problems describe chain or format breaks."""
        records = []
        problems = []
        prev = GENESIS
        for index, raw in enumerate(self._complete_lines()):
            line_no = index + 1
            try:
                record = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                problems.append('ledger line %d is not valid JSON' % line_no)
                prev = sha256_bytes(raw)
                continue
            if not isinstance(record, dict):
                problems.append('ledger line %d is not an object' % line_no)
            else:
                if record.get('prev') != prev:
                    problems.append('ledger hash chain broken at line %d' % line_no)
                if record.get('seq') != line_no:
                    problems.append('ledger line %d has seq %r' % (line_no, record.get('seq')))
                if record.get('run') != self.run:
                    problems.append('ledger line %d belongs to run %r' % (line_no, record.get('run')))
                records.append(record)
            prev = sha256_bytes(raw)
        problems.extend(self._check_head(len(records) and records[-1].get('seq', 0), prev))
        return records, problems

    def _read_head(self):
        try:
            with open(self.head_path) as f:
                seq, digest = f.read().split()
            return int(seq), digest
        except (OSError, ValueError):
            return None

    def _check_head(self, last_seq, last_hash):
        head = self._read_head()
        if head is None:
            return ['ledger head file is missing'] if last_seq else []
        seq, digest = head
        if seq == last_seq:
            return [] if digest == last_hash else ['ledger last line was modified']
        if seq == last_seq - 1:
            return []  # interrupted between the append and the head update
        if seq > last_seq:
            return ['ledger was truncated (head says seq %d, file ends at %d)' % (seq, last_seq)]
        return ['ledger head is stale (seq %d, file ends at %d)' % (seq, last_seq)]

    def records(self):
        return self.read()[0]

    # -- writing ---------------------------------------------------------

    def append(self, record_type, fields):
        """Append one record and return it. Only the framework calls this."""
        if record_type not in ('candidate', 'event'):
            raise ValueError(record_type)
        if record_type == 'event' and fields.get('event') not in EVENT_TYPES:
            raise ValueError('unknown event %r' % fields.get('event'))
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            size = os.fstat(fd).st_size
            data = b''
            if size:
                os.lseek(fd, 0, os.SEEK_SET)
                chunks = []
                while True:
                    chunk = os.read(fd, 1 << 20)
                    if not chunk:
                        break
                    chunks.append(chunk)
                data = b''.join(chunks)
            end = data.rfind(b'\n')
            if end + 1 != len(data):
                # Drop an interrupted partial line before appending.
                os.ftruncate(fd, end + 1)
                data = data[:end + 1]
            lines = data[:-1].split(b'\n') if data else []
            prev = sha256_bytes(lines[-1]) if lines else GENESIS
            record = {
                'schema': SCHEMA,
                'run': self.run,
                'seq': len(lines) + 1,
                'ts': now_iso(),
                'type': record_type,
                'prev': prev,
            }
            for key, value in fields.items():
                if key in record:
                    raise ValueError('field %r is reserved' % key)
                record[key] = value
            line = dumps_line(record).encode('utf-8')
            if b'\n' in line:
                raise LoopkitError('ledger record would span lines')
            os.lseek(fd, 0, os.SEEK_END)
            payload = line + b'\n'
            written = os.write(fd, payload)
            if written != len(payload):
                raise LoopkitError('short write to ledger')
            os.fsync(fd)
            write_atomic(self.head_path, '%d %s\n' % (record['seq'], sha256_bytes(line)))
            return record
        finally:
            os.close(fd)

    def event(self, name, **fields):
        fields = dict(fields)
        fields['event'] = name
        return self.append('event', fields)


def candidates(records):
    return [r for r in records if r.get('type') == 'candidate']


def events(records, name=None):
    return [r for r in records if r.get('type') == 'event' and (name is None or r.get('event') == name)]


def candidate_map(records):
    return {r['id']: r for r in candidates(records)}


def promoted_ids(records):
    return {e.get('id') for e in events(records, 'promote')}


def next_id(records, prefix):
    numbers = [int(r['id'][1:]) for r in candidates(records) if r.get('id', '').startswith(prefix) and r['id'][1:].isdigit()]
    start = 0 if prefix == 'c' else 1
    return '%s%03d' % (prefix, (max(numbers) + 1) if numbers else start)
