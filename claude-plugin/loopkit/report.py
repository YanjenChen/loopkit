"""Text and JSON views of the ledger: ITER/HUMAN lines, summary, show, lineage, export."""

import collections
import json
import os

from . import batch as batch_mod, ledger as ledger_mod, pareto
from .util import fmt5, fmt_pct, truncate

TREND = 8


def short_ref(ref):
    if not ref:
        return 'detached'
    for prefix in ('refs/heads/', 'refs/remotes/', 'refs/tags/'):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def objective_parts(config, values, base_values):
    """`hpwl 1.0231e6 (-0.80% better)` for each objective, compared with base_values."""
    parts = []
    for objective in config['objectives']:
        name = objective['name']
        value = values[name]
        text = '%s %s%s' % (name, fmt5(value), objective.get('unit', ''))
        if base_values and name in base_values:
            base = base_values[name]
            delta = fmt_pct((value - base) / abs(base)) if base else 'n/a'
            text += ' (%s %s)' % (delta, pareto.compare(objective, value, base))
        parts.append(text)
    return parts


def iter_line(config, record, batch_iter, max_iters, front_size):
    head = 'ITER %d%s' % (batch_iter, '/%d' % max_iters if max_iters else '')
    lineage = '%s<-%s' % (record['id'], '+'.join(record['parents']))
    if record['status'] == 'FAILED':
        return ' | '.join([head, lineage, 'FAILED', record.get('reason') or 'failed'])
    parts = [head, lineage] + objective_parts(config, record['objectives'], record.get('parent_objectives'))
    parts.append(record['status'])
    parts.append('front=%d' % front_size if record['status'] == 'KEPT' else (record.get('reason') or ''))
    return ' | '.join(parts)


def human_line(config, record):
    origin = record.get('origin') or {}
    where = '%s@%s' % (short_ref(origin.get('ref')), record['sha'][:7])
    if record['status'] == 'FAILED':
        return ' | '.join(['HUMAN %s' % record['id'], where, 'FAILED', record.get('reason') or 'failed'])
    parts = ['HUMAN %s' % record['id'], where] + objective_parts(config, record['objectives'], record.get('parent_objectives'))
    parts += ['OBSERVED', 'front:%s' % ('yes' if record.get('on_front') else 'no')]
    return ' | '.join(parts)


def record_line(config, record, records=None):
    """The one-line summary of any candidate, as printed when it was recorded."""
    if record.get('by') == 'human':
        return human_line(config, record)
    if record.get('by') == 'baseline':
        values = record.get('objectives')
        if not values:
            return 'BASELINE c000 | FAILED | %s' % (record.get('reason') or '')
        return ' | '.join(['BASELINE c000'] + objective_parts(config, values, None) + ['BASELINE'])
    batch_iter = record.get('batch_iter') or record.get('iter') or 0
    evo = pareto.Evolution(config, [r for r in (records or []) if r['seq'] <= record['seq']]) if records else None
    front = len(evo.front) if evo else 0
    return iter_line(config, record, batch_iter, None, front)


def _lineage_id(record):
    parents = record.get('parents') or []
    return '%s<-%s' % (record['id'], '+'.join(parents)) if parents else record['id']


def _values(config, record):
    if not record.get('objectives'):
        return ''
    return ' '.join('%s=%s' % (o['name'], fmt5(record['objectives'][o['name']])) for o in config['objectives'])


def summary(config, records, pending_requests, full=False):
    evo = pareto.Evolution(config, records)
    cands = ledger_mod.candidates(records)
    current = batch_mod.current(records)
    idea_len = 400 if full else 90
    learned_len = 400 if full else 110
    lines = []
    objectives = ', '.join('%s(%s%s, tol %s)' % (
        o['name'], 'min' if o['direction'] == 'minimize' else 'max',
        (', ' + o['unit']) if o.get('unit') else '',
        ('%g%%' % (o['tolerance']['relative'] * 100)) if 'relative' in o['tolerance'] else '%g' % o['tolerance']['absolute'])
        for o in config['objectives'])
    constraints = ', '.join(c['name'] for c in config.get('constraints', [])) or 'none'
    lines.append('OBJECTIVES %s | constraints: %s' % (objectives, constraints))
    if current:
        done = batch_mod.iterations(records, current['batch'])
        max_iters = (current['conditions'] or {}).get('max_iters')
        state = ('STOPPED: %s' % current['stopped']) if current['stopped'] else 'running'
        lines.append('BATCH %d | iter %d%s | %s | stop: %s' % (
            current['batch'], done, '/%d' % max_iters if max_iters else '', state, batch_mod.describe(current['conditions'])))
    else:
        lines.append('BATCH none yet')
    lines.append('FRONT (%d): %s' % (len(evo.front), ' | '.join(
        '%s %s' % (m, _values(config, evo.candidates[m])) for m in evo.front)))
    lines.append('DEFAULT PARENT: %s' % evo.default_parent())
    lines.append('')
    lines.append('CANDIDATES (id<-parents status values | idea | learned)')
    for record in cands:
        cid = record['id']
        status = record.get('status')
        if record.get('by') == 'human':
            status += ' promoted' if cid in evo.promoted else ''
            origin = record.get('origin') or {}
            head = '  %s %s %s@%s' % (cid, status, short_ref(origin.get('ref')), record['sha'][:7])
        else:
            head = '  %s %s' % (_lineage_id(record), status)
        if cid in evo.front:
            head += ' *front*'
        if status == 'FAILED' or record.get('status') == 'FAILED':
            head += ' [%s]' % truncate(record.get('reason'), 80)
        else:
            head += ' ' + _values(config, record)
            if record.get('status') == 'REVERTED':
                head += ' [%s]' % record.get('reason')
        if record.get('idea'):
            head += ' | ' + truncate(record['idea'], idea_len)
        if record.get('learned'):
            head += ' | ' + truncate(record['learned'], learned_len)
        lines.append(head)
    tried = collections.OrderedDict()
    for record in cands:
        if record.get('by') == 'agent':
            for parent in record.get('parents') or []:
                tried.setdefault(parent, []).append(record)
    if tried:
        lines.append('')
        lines.append('TRIED FROM EACH PARENT')
        for parent, children in tried.items():
            lines.append('  %s: %s' % (parent, '; '.join(
                '%s %s %s' % (c['id'], c['status'], truncate(c.get('idea'), 50)) for c in children)))
    lines.append('')
    lines.extend(patterns(records))
    evals = sum(1 for r in pending_requests if r.get('kind') == 'eval')
    promotes = sum(1 for r in pending_requests if r.get('kind') == 'promote')
    lines.append('QUEUE: %d eval request(s), %d promote request(s)' % (evals, promotes))
    warnings = ledger_mod.events(records, 'integrity_warning')
    if warnings:
        lines.append('INTEGRITY WARNINGS: %d (latest: %s)' % (len(warnings), ', '.join(warnings[-1].get('changed') or [])))
    return '\n'.join(lines)


def pattern_data(records):
    agent = [r for r in ledger_mod.candidates(records) if r.get('by') == 'agent']
    by_proposer = collections.OrderedDict()
    failures = collections.Counter()
    files = collections.Counter()
    areas = collections.OrderedDict()
    for record in agent:
        kept = record.get('status') == 'KEPT'
        for name in record.get('proposed_by') or ['agent']:
            stats = by_proposer.setdefault(name, [0, 0])
            stats[0] += kept
            stats[1] += 1
        if record.get('status') == 'FAILED':
            failures[(record.get('reason') or 'failed').split(':')[0].split(' ')[0]] += 1
        seen_areas = set()
        for path in record.get('files') or []:
            files[path] += 1
            area = path.split('/')[0] if '/' in path else '.'
            if area not in seen_areas:
                seen_areas.add(area)
                stats = areas.setdefault(area, [0, 0])
                stats[0] += kept
                stats[1] += 1
    return {
        'proposers': [{'name': k, 'kept': v[0], 'total': v[1]} for k, v in by_proposer.items()],
        'failures': [{'reason': k, 'count': v} for k, v in failures.most_common()],
        'files': [{'path': k, 'count': v} for k, v in files.most_common(5)],
        'areas': [{'area': k, 'kept': v[0], 'total': v[1]} for k, v in sorted(areas.items(), key=lambda kv: -kv[1][1])[:5]],
        'trend': [r.get('status') for r in agent[-TREND:]],
    }


def patterns(records):
    data = pattern_data(records)
    if not data['trend']:
        return ['PATTERNS: no agent iterations yet']
    lines = ['PATTERNS']
    lines.append('  kept by proposer: ' + ', '.join('%s %d/%d' % (p['name'], p['kept'], p['total']) for p in data['proposers']))
    lines.append('  kept by area: ' + ', '.join('%s %d/%d' % (a['area'], a['kept'], a['total']) for a in data['areas']))
    if data['failures']:
        lines.append('  failures: ' + ', '.join('%s %d' % (f['reason'], f['count']) for f in data['failures']))
    lines.append('  most changed: ' + ', '.join('%s (%d)' % (f['path'], f['count']) for f in data['files']))
    lines.append('  last %d: %s' % (len(data['trend']), ' '.join(data['trend'])))
    return lines


def show(record, log_path=None, log_lines=80):
    text = json.dumps(record, ensure_ascii=False, indent=2)
    if log_path:
        try:
            with open(log_path, encoding='utf-8', errors='replace') as f:
                tail = f.readlines()[-log_lines:]
            text += '\n\n--- score log (last %d lines of %s) ---\n%s' % (len(tail), log_path, ''.join(tail))
        except OSError:
            text += '\n\n(no score log at %s)' % log_path
    return text


def lineage(records, candidate_id):
    by_id = ledger_mod.candidate_map(records)
    if candidate_id not in by_id:
        return None
    order = []
    seen = set()
    queue = [candidate_id]
    while queue:
        cid = queue.pop(0)
        if cid in seen or cid not in by_id:
            continue
        seen.add(cid)
        order.append(by_id[cid])
        queue.extend(by_id[cid].get('parents') or [])
    lines = []
    for record in order:
        lines.append('%s %s%s | %s | %s' % (
            _lineage_id(record), record.get('status'),
            (' [%s]' % record['reason']) if record.get('reason') else '',
            truncate(record.get('idea'), 120), truncate(record.get('learned'), 160)))
    return '\n'.join(lines)


def export(config, records, since=0, status=None):
    """Records after `since`, each with derived fields, for the monitor."""
    evo = pareto.Evolution(config, [])
    batch_iters = collections.Counter()
    out = []
    for record in records:
        evo.apply(record)
        derived = {'front': list(evo.front), 'best': evo.best_so_far(record['seq'])}
        if record.get('type') == 'candidate':
            derived['line'] = record_line(config, record) if record.get('by') != 'agent' else None
            if record.get('by') == 'agent':
                batch_iters[record.get('batch')] += 1
                derived['batch_iter'] = batch_iters[record.get('batch')]
                derived['line'] = iter_line(config, record, derived['batch_iter'], None, len(evo.front))
            derived['promoted'] = record['id'] in evo.promoted
        if record['seq'] > since:
            item = dict(record)
            item['derived'] = derived
            out.append(item)
    return {
        'schema': 1,
        'run': records[0]['run'] if records else None,
        'objectives': [{k: o.get(k) for k in ('name', 'direction', 'unit', 'tolerance', 'description')} for o in config['objectives']],
        'constraints': [c['name'] for c in config.get('constraints', [])],
        'extra': [e['name'] for e in config.get('extra', [])],
        'since': since,
        'latest_seq': records[-1]['seq'] if records else 0,
        'status': status or {},
        'patterns': pattern_data(records),
        'records': out,
    }


def log_path(run_paths, candidate_id):
    return os.path.join(run_paths.artifact_dir(candidate_id), 'score.txt')
