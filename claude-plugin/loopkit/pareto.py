"""Objective comparison with tolerance, dominance, KEPT/REVERTED, and the front derived from the ledger."""

from . import ledger as ledger_mod

ELIGIBLE_STATUSES = ('BASELINE', 'KEPT', 'REVERTED')


def compare(objective, a, b):
    """How value a compares with value b: 'better', 'worse' or 'same' (within tolerance)."""
    tolerance = objective['tolerance']
    if 'relative' in tolerance:
        limit = tolerance['relative'] * max(abs(a), abs(b))
    else:
        limit = tolerance['absolute']
    diff = a - b
    if abs(diff) <= limit:
        return 'same'
    improved = diff < 0 if objective['direction'] == 'minimize' else diff > 0
    return 'better' if improved else 'worse'


def comparisons(objectives, a, b):
    return [compare(o, a[o['name']], b[o['name']]) for o in objectives]


def dominates(objectives, a, b):
    results = comparisons(objectives, a, b)
    return 'worse' not in results and 'better' in results


def judge(objectives, values, front):
    """Decide whether a constraint-passing candidate joins the front.

    front: list of (id, objective values). KEPT needs the candidate to beat
    every front member in at least one objective. Returns
    ('KEPT', [ids it removes]) or ('REVERTED', 'dominated_by:<id>' / 'same_as:<id>').
    """
    dominated_by = None
    same_as = None
    for member_id, member_values in front:
        results = comparisons(objectives, values, member_values)
        if 'better' in results:
            continue
        if 'worse' in results:
            dominated_by = dominated_by or member_id
        else:
            same_as = same_as or member_id
    if dominated_by:
        return 'REVERTED', 'dominated_by:%s' % dominated_by
    if same_as:
        return 'REVERTED', 'same_as:%s' % same_as
    removed = [member_id for member_id, member_values in front if dominates(objectives, values, member_values)]
    return 'KEPT', removed


class Evolution(object):
    """Replays the ledger: the front after every record, parents, promotions."""

    def __init__(self, config, records):
        self.objectives = config['objectives']
        self.front = []            # ids, in the order they joined
        self.front_after = {}      # seq -> list of ids
        self.candidates = {}       # id -> record
        self.promoted = set()
        self.parent_counts = {}    # id -> times chosen as a parent
        for record in records:
            self.apply(record)

    def _join(self, candidate_id):
        values = self.candidates[candidate_id]['objectives']
        self.front = [m for m in self.front
                      if not dominates(self.objectives, values, self.candidates[m]['objectives'])]
        self.front.append(candidate_id)

    def apply(self, record):
        if record.get('type') == 'candidate':
            cid = record['id']
            self.candidates[cid] = record
            for parent in record.get('parents') or []:
                self.parent_counts[parent] = self.parent_counts.get(parent, 0) + 1
            if record.get('status') in ('BASELINE', 'KEPT'):
                self._join(cid)
        elif record.get('event') == 'promote':
            cid = record.get('id')
            if cid in self.candidates and cid not in self.promoted:
                self.promoted.add(cid)
                if record.get('on_front'):
                    self._join(cid)
        self.front_after[record.get('seq')] = list(self.front)

    def front_values(self):
        return [(m, self.candidates[m]['objectives']) for m in self.front]

    def judge(self, values):
        return judge(self.objectives, values, self.front_values())

    def eligible_parent(self, candidate_id):
        record = self.candidates.get(candidate_id)
        if not record:
            return False
        if record.get('by') == 'human':
            return record.get('status') == 'OBSERVED' and candidate_id in self.promoted
        return record.get('status') in ELIGIBLE_STATUSES

    def default_parent(self):
        """The front member chosen as a parent least often; ties go to the newest."""
        best = None
        for member in self.front:
            key = (self.parent_counts.get(member, 0), -self.candidates[member]['seq'])
            if best is None or key < best[0]:
                best = (key, member)
        return best[1] if best else None

    def best_so_far(self, seq):
        """Single objective only: the best eligible value among records up to seq."""
        if len(self.objectives) != 1:
            return None
        objective = self.objectives[0]
        values = [self.candidates[m]['objectives'][objective['name']] for m in self.front_after.get(seq, [])]
        if not values:
            return None
        return min(values) if objective['direction'] == 'minimize' else max(values)


def load(config, records):
    return Evolution(config, records)


def promoted(records):
    return ledger_mod.promoted_ids(records)
