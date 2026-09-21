"""The monitor: a Claude artifact page that displays the ledger.

The page reads two collections from the artifact's database:
- `records`: the ledger in chunks of CHUNK records (doc ids chunk-00000, ...),
  each record carrying the derived fields `loopkit export` computes;
- `meta`: one document, `current`, with the objectives, run status and patterns.

The run session pushes both with the Artifact tool's write_db batch, using the
JSON files `loopkit monitor push` writes, then acknowledges with
`loopkit monitor ack`. Rewriting a chunk is idempotent: derived fields of a
record depend only on the records before it.
"""

import json
import os
import random

from . import ledger as ledger_mod, pareto, paths as paths_mod, report
from .util import LoopkitError, now_iso, write_json_atomic

CHUNK = 50
TEMPLATE = os.path.join(paths_mod.plugin_root(), 'monitor', 'monitor.html')
DB_CAPABILITIES = {'db': {'rules': [{'path': '', 'read': 'view', 'write': 'admin'}]}}


def chunk_id(index):
    return 'chunk-%05d' % index


def payload(config, records, status):
    """(meta, records with derived fields) as the page consumes them."""
    data = report.export(config, records, 0, status)
    records_out = data.pop('records')
    data['chunk_size'] = CHUNK
    data['updated'] = now_iso()
    return data, records_out


# -- the page -------------------------------------------------------------------

def render(title, sample=None):
    """The monitor page, with optional sample data embedded for the setup preview."""
    with open(TEMPLATE, encoding='utf-8') as f:
        html = f.read()
    sample_json = json.dumps(sample, ensure_ascii=False, separators=(',', ':')) if sample else 'null'
    # Keep the embedded JSON from closing the script element.
    sample_json = sample_json.replace('</', '<\\/')
    html = html.replace('__LOOPKIT_TITLE__', title.replace('<', '').replace('&', 'and'))
    return html.replace('/*__LOOPKIT_SAMPLE__*/null', sample_json)


def sample_data(config, run_name):
    """A plausible, clearly labeled sample run shaped like the config, for the setup preview."""
    rng = random.Random(7)
    objectives = config['objectives']
    analysts = [a['name'] for a in config['workflow'].get('analysts', [])] or ['agent']
    constraint = (config.get('constraints') or [{'name': 'constraint'}])[0]['name']
    base = {}
    for index, objective in enumerate(objectives):
        base[objective['name']] = [1.0e6, 40.0, 5.0e3, 0.8][index % 4]
    records = []

    def add(record_type, fields):
        record = {'schema': 1, 'run': run_name, 'seq': len(records) + 1, 'ts': now_iso(),
                  'type': record_type, 'prev': ''}
        record.update(fields)
        records.append(record)
        return record

    def candidate(cid, by, parents, values, status, reason, idea, proposer, iter_no, batch_no, **extra):
        fields = {
            'id': cid, 'by': by, 'iter': iter_no, 'batch': batch_no, 'parents': parents,
            'sha': '%040x' % rng.getrandbits(160), 'idea': idea, 'proposed_by': proposer,
            'files': ['src/placer/%s.cu' % rng.choice(['density', 'wirelength', 'optimizer', 'legalize'])],
            'files_total': 1, 'objectives': values,
            'parent_objectives': by_id[parents[0]]['objectives'] if parents else (
                by_id['c000']['objectives'] if by == 'human' else None),
            'constraints': {constraint: {'pass': status != 'FAILED' or not reason.startswith('constraint'),
                                         'value': 0, 'detail': 'sample'}},
            'extra': {}, 'status': status, 'reason': reason, 'on_front': status == 'KEPT',
            'learned': None if by != 'agent' else '[sample] hypothesis: %s; result: %s; evidence: sample numbers' % (
                idea[9:].lower(), 'confirmed' if status == 'KEPT' else 'disproven'),
            'duration_s': round(rng.uniform(300, 900), 1), 'cost_usd': None,
        }
        fields.update(extra)
        record = add('candidate', fields)
        by_id[cid] = record
        evo.apply(record)
        return record

    evo = pareto.Evolution(config, [])
    by_id = {}
    evo.apply(add('event', {'event': 'run_created'}))
    candidate('c000', 'baseline', [], dict(base), 'BASELINE', None, 'baseline', [], 0, None)

    def moved(values, deltas):
        # deltas are fractional changes of the first two objectives; negative means better.
        out = {}
        for index, objective in enumerate(objectives):
            change = deltas[index] if index < len(deltas) else 0.0
            if objective['direction'] == 'maximize':
                change = -change
            out[objective['name']] = values[objective['name']] * (1 + change)
        return out

    def objective_values(cid):
        return by_id[cid]['objectives']

    # (parents, deltas relative to the first parent, forced failure, idea)
    steps = [
        (['c000'], (-0.026, -0.004), None, 'tighten the density penalty schedule'),
        (['c001'], (-0.025, 0.030), None, 'raise the target density'),
        (['c002'], None, 'constraint', 'squeeze the bin grid'),
        (['c001'], (0.005, -0.060), None, 'fuse the gradient kernels'),
        (['c002'], (0.002, 0.005), None, 'cache net bounding boxes'),
        'human',
        (['c004'], (-0.015, -0.010), None, 'reorder cells for coalesced access'),
        'promote',
        ('merge', None, None, 'merge the fused kernels into the manual tweak'),
        'batch',
        (['h001'], None, 'build', 'use half precision in the density map'),
        (['c006'], (0.003, 0.004), None, 'skip converged clusters'),
        ('last-kept', (-0.012, 0.005), None, 'use a Nesterov step size search'),
        (['c006'], (0.008, -0.040), None, 'overlap transfers with compute'),
        ('last-kept', (0.001, 0.001), None, 'retune the smoothing parameter'),
    ]
    number = 0
    batch_no = 1
    batch_iter = 0
    last_kept = 'c000'
    evo.apply(add('event', {'event': 'batch_start', 'batch': 1, 'text': '[sample prompt 1]',
                            'conditions': {'max_iters': 8, 'targets': [], 'mode': 'any'}}))
    for step in steps:
        if step == 'human':
            values = {o['name']: objective_values('c002')[o['name']] for o in objectives}
            values = moved(values, (-0.010, 0.0))
            second = objectives[1]['name'] if len(objectives) > 1 else None
            if second:
                values[second] = objective_values('c004')[second] * (1.02 if objectives[1]['direction'] == 'minimize' else 0.98)
            verdict, _ = evo.judge(values)
            candidate('h001', 'human', [], values, 'OBSERVED', None, '[sample] my manual tweak', ['human'], None, None,
                      origin={'ref': 'refs/heads/main', 'sha': '%040x' % rng.getrandbits(160),
                              'note': '[sample] my manual tweak'},
                      on_front=verdict == 'KEPT')
            continue
        if step == 'promote':
            verdict, _ = evo.judge(objective_values('h001'))
            evo.apply(add('event', {'event': 'promote', 'id': 'h001', 'on_front': verdict == 'KEPT'}))
            continue
        if step == 'batch':
            evo.apply(add('event', {'event': 'batch_stop', 'batch': 1, 'reason': 'max_iters 8/8'}))
            batch_no, batch_iter = 2, 0
            evo.apply(add('event', {'event': 'batch_start', 'batch': 2, 'text': '[sample prompt 2]',
                                    'conditions': {'max_iters': 10, 'targets': [], 'mode': 'any'}}))
            continue
        parents, deltas, failure, idea = step
        if parents == 'merge':
            parents = ['c006', 'h001']
            deltas = (-0.005, -0.010)
            base_id = 'h001'
        elif parents == 'last-kept':
            parents = [last_kept]
            base_id = last_kept
        else:
            base_id = parents[0]
        number += 1
        batch_iter += 1
        cid = 'c%03d' % number
        proposer = [analysts[number % len(analysts)]]
        text = '[sample] ' + idea
        if failure:
            reason = 'constraint:%s' % constraint if failure == 'constraint' else 'build_failed: sample compile error'
            candidate(cid, 'agent', parents, None, 'FAILED', reason, text, proposer, number, batch_no, batch_iter=batch_iter)
            continue
        values = moved(objective_values(base_id), deltas)
        verdict, detail = evo.judge(values)
        candidate(cid, 'agent', parents, values, verdict, None if verdict == 'KEPT' else detail, text, proposer,
                  number, batch_no, batch_iter=batch_iter)
        if verdict == 'KEPT':
            last_kept = cid
    status = {'run': run_name, 'batch': 2, 'batch_iter': batch_iter, 'max_iters': 10, 'conditions': 'max_iters=10',
              'stopped': None, 'counts': {}, 'front': list(evo.front), 'queue': {'eval': 1, 'promote': 0},
              'integrity_warnings': 0, 'updated': now_iso()}
    for record in ledger_mod.candidates(records):
        status['counts'][record['status']] = status['counts'].get(record['status'], 0) + 1
    meta, records_out = payload(config, records, status)
    meta['sample'] = True
    return {'meta': meta, 'records': records_out}


# -- pushing records -----------------------------------------------------------------

def push_plan(run):
    """Write the documents to push as JSON files; returns (writes, latest_seq, pushed_seq)."""
    records = run.records()
    state = run._state('monitor.json') or {}
    pushed = state.get('pushed_seq', 0)
    latest = records[-1]['seq'] if records else 0
    meta, records_out = payload(run.config, records, run.status_data(records))
    meta['run'] = run.name
    out_dir = os.path.join(run.paths.work, 'monitor')
    os.makedirs(out_dir, exist_ok=True)
    writes = []
    chunks = sorted({(r['seq'] - 1) // CHUNK for r in records_out if r['seq'] > pushed})
    for index in chunks:
        members = [r for r in records_out if (r['seq'] - 1) // CHUNK == index]
        doc = {'first_seq': members[0]['seq'], 'last_seq': members[-1]['seq'], 'records': members}
        path = os.path.join(out_dir, 'records-%s.json' % chunk_id(index))
        write_json_atomic(path, doc)
        if os.path.getsize(path) > 240 * 1024:
            raise LoopkitError('monitor chunk %s is over the 256 KiB document limit' % chunk_id(index))
        writes.append({'op': 'set', 'collection': 'records', 'doc_id': chunk_id(index), 'file_path': path})
    path = os.path.join(out_dir, 'meta-current.json')
    write_json_atomic(path, meta)
    writes.append({'op': 'set', 'collection': 'meta', 'doc_id': 'current', 'file_path': path})
    return writes, latest, pushed


def default_title(repo_name, run_name):
    return '%s %s evolution' % (repo_name, run_name)
