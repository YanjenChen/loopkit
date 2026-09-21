"""Batches and stop conditions.

A batch is one /goal or /loop execution. The agent turns the stop conditions
written in the prompt into structured conditions and registers them at the
head of every iteration; the framework decides when the batch stops.

Structured conditions:
  {"max_iters": 20,
   "plateau": 5,
   "targets": [{"objective": "hpwl", "op": "<=", "value": 1.0e6}],
   "mode": "any"}

plateau N stops the batch once its last N iterations produced no KEPT
candidate: the /goal evaluator only reads the transcript and cannot tell.
"""

import json
import numbers

from . import ledger as ledger_mod
from .util import LoopkitError, fmt5

OPS = {
    '<=': lambda a, b: a <= b,
    '<': lambda a, b: a < b,
    '>=': lambda a, b: a >= b,
    '>': lambda a, b: a > b,
}


def normalize_text(text):
    return ' '.join((text or '').split())


def parse_conditions(raw, config):
    """Validate structured conditions; returns (conditions, warnings)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            raise LoopkitError('--conditions is not valid JSON: %s' % exc)
    if not isinstance(raw, dict):
        raise LoopkitError('--conditions must be a JSON object')
    unknown = set(raw) - {'max_iters', 'plateau', 'targets', 'mode'}
    if unknown:
        raise LoopkitError('--conditions has unknown keys: %s' % ', '.join(sorted(unknown)))
    warnings = []
    max_iters = raw.get('max_iters')
    if max_iters is not None and (not isinstance(max_iters, int) or isinstance(max_iters, bool) or max_iters < 1):
        raise LoopkitError('max_iters must be a positive integer')
    plateau = raw.get('plateau')
    if plateau is not None and (not isinstance(plateau, int) or isinstance(plateau, bool) or plateau < 1):
        raise LoopkitError('plateau must be a positive integer')
    objectives = {o['name']: o for o in config['objectives']}
    targets = []
    for target in raw.get('targets') or []:
        if not isinstance(target, dict):
            raise LoopkitError('each target must be an object')
        name = target.get('objective')
        if name not in objectives:
            raise LoopkitError('target objective %r is not one of: %s' % (name, ', '.join(objectives)))
        op = target.get('op')
        if op not in OPS:
            raise LoopkitError('target op must be one of %s' % ', '.join(OPS))
        value = target.get('value')
        if not isinstance(value, numbers.Real) or isinstance(value, bool):
            raise LoopkitError('target value for %s must be a number' % name)
        direction = objectives[name]['direction']
        if (direction == 'minimize') != (op in ('<=', '<')):
            warnings.append('target %s%s%s points the worse way for a %s objective' % (name, op, fmt5(value), direction))
        targets.append({'objective': name, 'op': op, 'value': value})
    mode = raw.get('mode', 'any')
    if mode not in ('any', 'all'):
        raise LoopkitError('mode must be "any" or "all"')
    conditions = {'max_iters': max_iters, 'targets': targets, 'mode': mode}
    if plateau is not None:
        conditions['plateau'] = plateau
    return conditions, warnings


def describe(conditions):
    if not conditions:
        return 'none'
    parts = []
    if conditions.get('max_iters'):
        parts.append('max_iters=%d' % conditions['max_iters'])
    if conditions.get('plateau'):
        parts.append('plateau=%d' % conditions['plateau'])
    targets = conditions.get('targets') or []
    if targets:
        text = ', '.join('%s%s%s' % (t['objective'], t['op'], fmt5(t['value'])) for t in targets)
        if len(targets) > 1:
            text += ' (%s)' % conditions.get('mode', 'any')
        parts.append(text)
    return '; '.join(parts) or 'none'


def current(records):
    """The latest batch: {'batch', 'text', 'conditions', 'seq', 'stopped'} or None."""
    batch = None
    for record in ledger_mod.events(records):
        if record.get('event') == 'batch_start':
            batch = {'batch': record['batch'], 'text': record.get('text', ''),
                     'conditions': record.get('conditions') or {}, 'seq': record['seq'], 'stopped': None}
        elif record.get('event') == 'batch_stop' and batch and record.get('batch') == batch['batch']:
            batch['stopped'] = record.get('reason') or 'stopped'
    return batch


def iterations(records, batch_no):
    return sum(1 for r in ledger_mod.candidates(records) if r.get('by') == 'agent' and r.get('batch') == batch_no)


def statuses(records, batch_no):
    """Statuses of this batch's agent iterations, in order."""
    return [r.get('status') for r in ledger_mod.candidates(records) if r.get('by') == 'agent' and r.get('batch') == batch_no]


def check(conditions, iterations_done, evolution, batch_statuses=()):
    """Return the stop reason, or None to keep going."""
    max_iters = conditions.get('max_iters')
    if max_iters and iterations_done >= max_iters:
        return 'max_iters %d/%d' % (iterations_done, max_iters)
    plateau = conditions.get('plateau')
    if plateau and len(batch_statuses) >= plateau and 'KEPT' not in list(batch_statuses)[-plateau:]:
        return 'plateau: no KEPT in the last %d iterations' % plateau
    targets = conditions.get('targets') or []
    if not targets:
        return None
    front = [(m, evolution.candidates[m]['objectives']) for m in evolution.front]

    def met_by(target):
        return [m for m, values in front if OPS[target['op']](values[target['objective']], target['value'])]

    if conditions.get('mode') == 'all':
        for member, values in front:
            if all(OPS[t['op']](values[t['objective']], t['value']) for t in targets):
                return 'targets %s by %s' % (describe({'targets': targets}), member)
        return None
    for target in targets:
        members = met_by(target)
        if members:
            return 'target %s%s%s by %s' % (target['objective'], target['op'], fmt5(target['value']), members[-1])
    return None


def decide_start(records, text, conditions):
    """How a `batch start` call relates to the latest batch.

    Returns 'start' (record a new batch), 'continue' (same batch still running)
    or 'stopped' (the same prompt again after LOOPKIT-STOP: stay stopped).
    A running batch continues when either the prompt text or the structured
    conditions match, so small wording drift never resets the count.
    """
    batch = current(records)
    if batch is None:
        return 'start', None
    same_text = normalize_text(batch['text']) == normalize_text(text)
    same_conditions = (batch['conditions'] or {}) == (conditions or {})
    if batch['stopped']:
        return ('stopped' if same_text else 'start'), batch
    if same_text or same_conditions:
        return 'continue', batch
    return 'start', batch
