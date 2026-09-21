"""The loopkit config (.loopkit/config.json): validation, variable expansion, command screening."""

import math
import numbers
import os
import re

from .util import LoopkitError, read_json

CONFIG_PATH = '.loopkit/config.json'
SCHEMA = 1

_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_ANALYST = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
_VAR = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')

# Variables the framework expands inside score.command and precheck.command.
FRAMEWORK_VARS = (
    'LOOPKIT_EVAL_DIR', 'LOOPKIT_BUILD_DIR', 'LOOPKIT_AGENT_BUILD_DIR', 'LOOPKIT_RESULT',
    'LOOPKIT_WORKTREE',
)


def _is_number(value):
    return isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(value)


def _check_named_list(config, key, errors, required, extra_check=None):
    items = config.get(key, [] if not required else None)
    if items is None:
        errors.append('%s: required' % key)
        return []
    if not isinstance(items, list) or (required and not items):
        errors.append('%s: must be a %slist' % (key, 'non-empty ' if required else ''))
        return []
    names = []
    for index, item in enumerate(items):
        where = '%s[%d]' % (key, index)
        if not isinstance(item, dict):
            errors.append('%s: must be an object' % where)
            continue
        name = item.get('name')
        if not isinstance(name, str) or not _NAME.match(name):
            errors.append('%s.name: must match %s' % (where, _NAME.pattern))
            continue
        if name in names:
            errors.append('%s.name: duplicate name %r' % (where, name))
        names.append(name)
        if 'description' in item and not isinstance(item['description'], str):
            errors.append('%s.description: must be a string' % where)
        if extra_check:
            extra_check(item, where, errors)
    return names


def _check_objective(item, where, errors):
    if item.get('direction') not in ('minimize', 'maximize'):
        errors.append('%s.direction: must be "minimize" or "maximize"' % where)
    tolerance = item.get('tolerance')
    if not isinstance(tolerance, dict) or len(tolerance) != 1 or not (set(tolerance) <= {'relative', 'absolute'}):
        errors.append('%s.tolerance: must be {"relative": x} or {"absolute": x}' % where)
    else:
        value = list(tolerance.values())[0]
        if not _is_number(value) or value < 0:
            errors.append('%s.tolerance: must be a finite number >= 0' % where)
    if 'unit' in item and not isinstance(item['unit'], str):
        errors.append('%s.unit: must be a string' % where)


def _check_command(section, where, errors, allow_empty=False):
    command = section.get('command')
    if not isinstance(command, list) or (not command and not allow_empty) or not all(isinstance(a, str) for a in command):
        errors.append('%s.command: must be a non-empty list of strings' % where)
        return
    for arg in command:
        for var in _VAR.findall(arg):
            if var.startswith('LOOPKIT_') and var not in FRAMEWORK_VARS:
                errors.append('%s.command: unknown variable ${%s}' % (where, var))


def validate(config):
    """Return a list of human-readable problems; empty when the config is valid."""
    errors = []
    if not isinstance(config, dict):
        return ['config: must be a JSON object']
    if config.get('schema') != SCHEMA:
        errors.append('schema: must be %d' % SCHEMA)
    if 'source' in config and not isinstance(config['source'], str):
        errors.append('source: must be a string')

    objectives = _check_named_list(config, 'objectives', errors, True, _check_objective)
    constraints = _check_named_list(config, 'constraints', errors, False)
    extra = _check_named_list(config, 'extra', errors, False)
    seen = set()
    for name in objectives + constraints + extra:
        if name in seen:
            errors.append('name %r is used by more than one of objectives/constraints/extra' % name)
        seen.add(name)

    score = config.get('score')
    if not isinstance(score, dict):
        errors.append('score: required object')
    else:
        _check_command(score, 'score', errors)
        timeout = score.get('timeout_s', 1800)
        if not _is_number(timeout) or timeout <= 0:
            errors.append('score.timeout_s: must be a positive number')
        env = score.get('env', {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            errors.append('score.env: must map strings to strings')
        elif any(k.startswith('LOOPKIT_') or k == 'CUDA_VISIBLE_DEVICES' for k in env):
            errors.append('score.env: LOOPKIT_* and CUDA_VISIBLE_DEVICES are set by the framework')

    assets = config.get('eval_assets')
    if not isinstance(assets, list) or not assets or not all(isinstance(a, str) and a for a in assets):
        errors.append('eval_assets: must be a non-empty list of paths')
    else:
        for asset in assets:
            if not os.path.isabs(asset) and ('..' in asset.replace('\\', '/').split('/')):
                errors.append('eval_assets: %r must not contain ".."' % asset)

    scope = config.get('scope')
    if not isinstance(scope, dict):
        errors.append('scope: required object')
    else:
        include = scope.get('include')
        exclude = scope.get('exclude', [])
        if not isinstance(include, list) or not include or not all(isinstance(p, str) and p for p in include):
            errors.append('scope.include: must be a non-empty list of globs')
        if not isinstance(exclude, list) or not all(isinstance(p, str) and p for p in exclude):
            errors.append('scope.exclude: must be a list of globs')

    workflow = config.get('workflow')
    if not isinstance(workflow, dict):
        errors.append('workflow: required object')
    else:
        mode = workflow.get('mode')
        if mode not in ('single', 'multi'):
            errors.append('workflow.mode: must be "single" or "multi"')
        if mode == 'multi':
            analysts = workflow.get('analysts')
            if not isinstance(analysts, list) or not analysts:
                errors.append('workflow.analysts: multi mode needs at least one analyst')
            else:
                names = []
                for index, analyst in enumerate(analysts):
                    where = 'workflow.analysts[%d]' % index
                    if not isinstance(analyst, dict):
                        errors.append('%s: must be an object' % where)
                        continue
                    name = analyst.get('name')
                    if not isinstance(name, str) or not _ANALYST.match(name):
                        errors.append('%s.name: must match %s' % (where, _ANALYST.pattern))
                    elif name in names or name == 'agent':
                        errors.append('%s.name: %r is duplicated or reserved' % (where, name))
                    else:
                        names.append(name)
                    if not isinstance(analyst.get('focus'), str) or not analyst.get('focus'):
                        errors.append('%s.focus: required string' % where)
                    if 'web' in analyst and not isinstance(analyst['web'], bool):
                        errors.append('%s.web: must be true or false' % where)
            decider = workflow.get('decider')
            if not isinstance(decider, dict) or not isinstance(decider.get('principles'), str):
                errors.append('workflow.decider.principles: required string in multi mode')
            if 'critic' in workflow and not isinstance(workflow['critic'], bool):
                errors.append('workflow.critic: must be true or false')

    precheck = config.get('precheck')
    if precheck is not None:
        if not isinstance(precheck, dict):
            errors.append('precheck: must be an object')
        else:
            _check_command(precheck, 'precheck', errors)
            attempts = precheck.get('max_fix_attempts', 3)
            if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
                errors.append('precheck.max_fix_attempts: must be an integer >= 0')
            timeout = precheck.get('timeout_s', 540)
            if not _is_number(timeout) or timeout <= 0:
                errors.append('precheck.timeout_s: must be a positive number')

    run = config.get('run', {})
    if not isinstance(run, dict):
        errors.append('run: must be an object')
    elif run.get('gpu') is not None and not (isinstance(run['gpu'], str) and re.match(r'^[0-9A-Za-z,:-]+$', run['gpu'])):
        errors.append('run.gpu: must be a device list such as "1" or "0,1", or null')
    return errors


def load(path):
    try:
        config = read_json(path)
    except OSError as exc:
        raise LoopkitError('cannot read %s: %s' % (path, exc.strerror))
    except ValueError as exc:
        raise LoopkitError('%s is not valid JSON: %s' % (path, exc))
    errors = validate(config)
    if errors:
        raise LoopkitError('invalid loopkit config %s:\n  - %s' % (path, '\n  - '.join(errors)))
    return config


def objective_map(config):
    return {o['name']: o for o in config['objectives']}


def expand(argv, variables):
    """Expand ${LOOPKIT_*} framework variables; other text is passed through untouched."""
    def replace(match):
        name = match.group(1)
        return variables.get(name, match.group(0))
    return [_VAR.sub(replace, arg) for arg in argv]


# ---------------------------------------------------------------------------
# Command screening, ported from the old orchestrate.sh screen-cmd. It flags
# commands that destroy data, pipe remote code into an interpreter, exfiltrate,
# or carry credentials. It is a review aid for init, not a sandbox.
# ---------------------------------------------------------------------------

_WS = r'[ \t\n\r\f\v]'
_P = r'(?:[^ \t\n\r\f\v]*/)?'  # optional path prefix: /bin/rm, ./rm
_INTERP = r'(?:sh|bash|zsh|dash|fish|ksh|python[0-9.]*|perl|ruby|node|php)'
_SCREEN_RULES = [
    ('pipes a download into an interpreter',
     lambda c: re.search(r'(?:curl|wget)[^|]*\|%s*%s%s(?:%s|$)' % (_WS, _P, _INTERP, _WS), c)),
    ('pipes a download through xargs into an interpreter',
     lambda c: re.search(r'(?:curl|wget)[^|]*\|.*xargs.*%s%s%s(?:%s|$)' % (_WS, _P, _INTERP.replace('fish|', ''), _WS), c)),
    ('pipes output to netcat',
     lambda c: re.search(r'\|%s*%s(?:nc|ncat|netcat)(?:%s|$)' % (_WS, _P, _WS), c)),
    ('writes to a raw block device',
     lambda c: re.search(r'(?:of=|>%s*)/dev/(?:sd|hd|vd|nvme|disk|mapper|loop|xvd|mmcblk|md|dm-)' % _WS, c)),
    ('formats a filesystem',
     lambda c: re.search(r'(?:^|%s)%s(?:mkfs|mke2fs)' % (_WS, _P), c)),
    ('mass-deletes with find -delete',
     lambda c: re.search(r'(?:^|%s)%sfind(?:%s|$)' % (_WS, _P, _WS), c)
     and re.search(r'%s-delete(?:%s|$)' % (_WS, _WS), c)),
    ('shreds files',
     lambda c: re.search(r'(?:^|%s)%sshred(?:%s|$)' % (_WS, _P, _WS), c)),
    ('truncates files to zero size',
     lambda c: re.search(r'(?:^|%s)%struncate(?:%s|$)' % (_WS, _P, _WS), c)
     and re.search(r'(?:-s%s*0|--size%s*=?%s*0)(?:%s|$)' % (_WS, _WS, _WS, _WS), c)),
    ('recursively removes all permissions',
     lambda c: re.search(r'(?:^|%s)%schmod(?:%s|$)' % (_WS, _P, _WS), c)
     and re.search(r'(?:-R|--recursive)(?:%s|$)' % _WS, c)
     and re.search(r'(?:^|%s)(?:000|00|0)(?:%s|$)' % (_WS, _WS), c)),
    ('contains a fork bomb',
     lambda c: ':(){ :|:' in c or re.search(r':\(\)\{', c)),
    ('contains an AWS access key id',
     lambda c: re.search(r'AKIA[0-9A-Z]{16}', c)),
    ('contains a PASSWORD= assignment',
     lambda c: re.search(r'PASSWORD%s*=' % _WS, c)),
    ('contains a private key',
     lambda c: re.search(r'BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY', c)),
]


def _rm_recursive_force(command):
    if not re.search(r'(?:^|%s)%srm(?:%s|$)' % (_WS, _P, _WS), command):
        return False
    recursive = re.search(r'(?:^|%s)-[a-zA-Z]*[rR]|--recursive' % _WS, command)
    force = re.search(r'(?:^|%s)-[a-zA-Z]*[fF]|--force' % _WS, command)
    return bool(recursive and force)


def _remote_database(command):
    """A postgres URL whose host is not local and whose database is not *_test or *_ci."""
    for url in re.findall(r'postgres(?:ql)?://[^ \t\n\r\f\v]+', command):
        match = re.match(r'postgres(?:ql)?://(?:[^@/]+@)?([^/:]+)[:/]?([^?]*)', url)
        if not match:
            continue
        host = match.group(1)
        dbname = match.group(2).split('/')[-1]
        if host in ('localhost', '127.0.0.1') or '.' not in host:
            continue
        if not re.search(r'_test$|_ci$', dbname):
            return True
    return False


def screen(command):
    """Return reasons a shell string looks dangerous; empty when nothing was found."""
    if isinstance(command, (list, tuple)):
        command = ' '.join(command)
    reasons = []
    if _rm_recursive_force(command):
        reasons.append('removes files recursively with force (rm -rf)')
    for reason, rule in _SCREEN_RULES:
        if rule(command):
            reasons.append(reason)
    if _remote_database(command):
        reasons.append('connects to a non-local database that is not *_test or *_ci')
    return reasons
