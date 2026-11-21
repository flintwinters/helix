"""Pure Helix command-line tool exposed to benchmark models, with explicit caller attribution."""
import hashlib
import json
import time
import threading
from pathlib import Path
from datetime import datetime, timezone

from tooling import usage_log


TOOL = {
    'type': 'function',
    'function': {
        'name': 'helix',
        'description': 'Call the Helix CLI. Allowed argv: ["--help"], ["help", COMMAND], '
        '["docs"], ["docs", DOC] (its section tree), ["docs", DOC, SECTION], ["builtins"], ["builtins", NAME], '
        '["std"], ["std", MODULE] (its signatures), '
        '["std", "MODULE.EXPORT"] (one export\'s source), ["std", MODULE, "--source"], '
        '["schema"], ["check", FILE], ["fmt", FILE], ["run", FILE]. Append "--json" to help, docs, builtins or std '
        'lookups for structured output. FILE may be - with stdin YAML. '
        'fmt prints canonical block-style tag source; format candidates with it before saving. '
        'Run is confined to this attempt directory with embedded std: imports and no host effect grants. '
        'Run public_test.yaml to invoke candidate.solve on the provided example. Running candidate.yaml alone only defines the function. No shell commands.',
        'parameters': {'type': 'object', 'properties': {
            'args': {'type': 'array', 'items': {'type': 'string'}},
            'stdin': {'type': 'string'},
        }, 'required': ['args'], 'additionalProperties': False},
    },
}


_LOG_LOCK = threading.Lock()
FILES = {
    'type': 'function', 'function': {
        'name': 'workspace',
        'description': 'Read, write or list files in your working directory. Save your solve module as candidate.yaml in !call/!ref/!literal block style (as printed by helix fmt). LANGUAGE.md, LIBRARY.md and public_test.yaml are read-only. Read supports one-based start_line and line_count for progressive documentation discovery.',
        'parameters': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['read', 'write', 'list']},
            'path': {'type': 'string'}, 'content': {'type': 'string'},
            'start_line': {'type': 'integer', 'minimum': 1},
            'line_count': {'type': 'integer', 'minimum': 1},
        }, 'required': ['action'], 'additionalProperties': False},
    },
}
SUBMIT = {
    'type': 'function', 'function': {
        'name': 'submit',
        'description': 'Submit the saved candidate.yaml and helpers exactly as tested. First run helix with args ["run", "public_test.yaml"]. Any subsequent file edit requires rerunning that test. Hidden tests are evaluated after submission.',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
}
TOOLS = [TOOL, FILES, SUBMIT]
READ_ONLY = {'LANGUAGE.md', 'LIBRARY.md', 'public_test.yaml'}


def working_directory(directory):
    workspace = directory / 'workspace'
    return workspace if workspace.is_dir() else directory


def local_path(directory, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError('path must be relative to the workspace')
    root = working_directory(directory).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('path escapes the workspace')
    return path


def fingerprint(directory):
    root = working_directory(directory)
    files = [(str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest())
             for path in sorted(root.rglob('*')) if path.is_file()]
    return hashlib.sha256(json.dumps(files).encode()).hexdigest()


def workspace_tool(directory, payload, state):
    action = payload.get('action')
    if action not in ('read', 'write', 'list') and (action is not None or 'args' in payload):
        spelled = payload['args'] if isinstance(payload.get('args'), list) else [action]
        raise ValueError(f'workspace only reads, writes and lists files (action read, write or list); '
                         f'{json.dumps(spelled)} is a Helix CLI request: call the helix tool with args {json.dumps(spelled)}')
    if action == 'list':
        return {'files': [str(path.relative_to(working_directory(directory)))
                          for path in sorted(working_directory(directory).rglob('*')) if path.is_file()]}
    path = local_path(directory, payload.get('path'))
    if action == 'write':
        if path.name in READ_ONLY:
            raise ValueError('the supplied documentation and public test are read-only')
        content = payload.get('content')
        if not isinstance(content, str):
            raise ValueError('write requires string content')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        state.pop('validated', None)
        return {'written': payload['path'], 'bytes': len(content.encode())}
    if action == 'read':
        start, count = payload.get('start_line', 1), payload.get('line_count', 120)
        if type(start) is not int or type(count) is not int or start < 1 or count < 1:
            raise ValueError('start_line and line_count must be positive integers')
        lines = path.read_text().splitlines(keepends=True)
        text = ''.join(lines[start - 1:start - 1 + count])
        return {'path': payload['path'], 'start_line': start, 'total_lines': len(lines),
                'content': text[:16000], 'output_truncated': len(text) > 16000}
    raise ValueError('action must be read, write or list')


def submit_tool(directory, state):
    path = local_path(directory, 'candidate.yaml')
    if not path.is_file():
        raise ValueError('write candidate.yaml before submitting')
    if state.get('validated') != fingerprint(directory):
        raise ValueError('run public_test.yaml successfully against the current saved files before submitting')
    return {'submitted': True, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def tool(ctx, directory, name, payload, identity, index, state):
    if name == 'helix':
        return execute(ctx, directory, payload, identity, index, state)
    try:
        if not isinstance(payload, dict):
            raise ValueError('tool arguments must be an object')
        if name == 'workspace':
            result = workspace_tool(directory, payload, state)
        elif name == 'submit':
            result = submit_tool(directory, state)
        else:
            raise ValueError('unknown tool')
    except (ValueError, OSError) as error:
        result = {'error': str(error)}
    event = {'tool': name, 'arguments': payload, 'result': result, 'identity': identity}
    (directory / f'tool-{index:03d}.json').write_text(json.dumps(event, ensure_ascii=False, indent=2) + '\n')
    return result


# Read-only lookups and how many names each accepts; names are identifiers, never paths.
LOOKUPS = {'help': 1, 'docs': 2, 'builtins': 1, 'std': 1}


def lookup(args):
    """Whether argv is a read-only documentation lookup: `help`, `docs`, `builtins` or `std`
    with identifier-like names, then optionally `--json` (or `--source` for one std module)."""
    if not args or args[0] not in LOOKUPS:
        return False
    names, flags = args[1:], []
    while names and names[-1].startswith('--'):
        flags.insert(0, names.pop())
    plain = all(name.replace('_', '').replace('-', '').replace('.', '').isalnum() for name in names)
    allowed = (['--json'], ['--source']) if args[0] == 'std' and len(names) == 1 else (['--json'],)
    return len(names) <= LOOKUPS[args[0]] and plain and flags in ([], *allowed)


def execute(ctx, directory, payload, identity, index, state=None):
    """Record the model's requested CLI operation even when denied; no inherited implementer identity."""
    workspace = working_directory(directory)
    args = payload.get('args', []) if isinstance(payload, dict) else []
    source = payload.get('stdin') if isinstance(payload, dict) else None
    started, clock = datetime.now(timezone.utc), time.perf_counter()
    command = []
    path = None
    error = None
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        error = 'args must be an array of strings'
        args = []
    elif args in (['--help'], ['schema']) or lookup(args):
        command = args
    elif len(args) == 2 and args[0] in ('fmt', 'run', 'check'):
        try:
            if args[1] == '-':
                if not isinstance(source, str):
                    raise ValueError('stdin YAML is required for -')
                if args[0] == 'run':
                    path = workspace / f'.tool-{index:03d}.yaml'
                    path.write_text(source)
                else:
                    command = args
            else:
                path = local_path(directory, args[1])
                source = path.read_text()
            if path is not None:
                command = ['sandbox', str(workspace), str(path)] if args[0] == 'run' else [args[0], str(path)]
        except (ValueError, OSError) as failure:
            error = str(failure)
    else:
        error = ('Unsupported CLI arguments. Use --help, help [COMMAND], docs [DOC [SECTION]], builtins [NAME], '
                 'std [MODULE[.EXPORT]] [--source], schema, check FILE, fmt FILE, or run FILE; '
                 'add --json to a lookup for structured output.')
    if error:
        code, stdout, stderr = 2, '', error
    else:
        result = ctx.helix(*command, stdin=source if command in (['fmt', '-'], ['check', '-']) else None, cwd=workspace)
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    if state is not None and args == ['run', 'public_test.yaml']:
        state.pop('validated', None)
        if code == 0:
            state['validated'] = fingerprint(directory)
    duration = (time.perf_counter() - clock) * 1000
    record = {
        'started_at': started.isoformat(timespec='milliseconds'), 'duration_ms': round(duration),
        'command': args[0] if args and isinstance(args[0], str) else 'invalid',
        'argv': json.dumps(args), 'cwd': str(workspace), 'exit_code': code,
        'program_path': str(path) if path else None, 'program': source if isinstance(source, str) else None,
        'program_sha256': hashlib.sha256(source.encode()).hexdigest() if isinstance(source, str) else None,
        'stdin_program': int(args[-1:] == ['-'] and isinstance(source, str)), 'stdout': stdout, 'stderr': stderr,
        'agent': 'openrouter:' + identity['model'], 'caller_role': 'benchmark-model', **identity,
    }
    observation = {'args': args, 'executed_argv': command, 'exit_code': code, 'stdout': stdout,
                   'stderr': stderr, 'duration_ms': duration, 'identity': identity}
    (directory / f'tool-{index:03d}.json').write_text(json.dumps(observation, ensure_ascii=False, indent=2) + '\n')
    # Log explicitly even when the surrounding regression harness disables developer telemetry.
    with _LOG_LOCK:
        usage_log.append(ctx.root, ctx.binary, record)
    return {**observation, 'stdout': stdout[:16000], 'stderr': stderr[:16000],
            'output_truncated': len(stdout) > 16000 or len(stderr) > 16000}
