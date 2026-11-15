"""Pure Helix command-line tool exposed to benchmark models, with explicit caller attribution."""
import hashlib
import json
import time
from datetime import datetime, timezone

from tooling import usage_log


TOOL = {
    'type': 'function',
    'function': {
        'name': 'helix',
        'description': 'Call the Helix CLI. Allowed argv: ["--help"], ["std"] or ["std", MODULE], '
        '["schema"], ["fmt", "-"], ["run", "-"]. For fmt/run, stdin is the complete YAML program. '
        'Run is confined to this attempt directory with embedded std: imports and no host effect grants. '
        'Use run to test examples before returning your final solve module. No shell commands.',
        'parameters': {'type': 'object', 'properties': {
            'args': {'type': 'array', 'items': {'type': 'string'}},
            'stdin': {'type': 'string'},
        }, 'required': ['args'], 'additionalProperties': False},
    },
}


def execute(ctx, directory, payload, identity, index):
    """Record the model's requested CLI operation even when denied; no inherited implementer identity."""
    args = payload.get('args', []) if isinstance(payload, dict) else []
    source = payload.get('stdin') if isinstance(payload, dict) else None
    started, clock = datetime.now(timezone.utc), time.perf_counter()
    command = []
    path = None
    error = None
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        error = 'args must be an array of strings'
    elif args in (['--help'], ['std'], ['schema']):
        command = args
    elif len(args) == 2 and args[0] == 'std' and args[1].replace('_', '').isalnum():
        command = args
    elif args in (['fmt', '-'], ['run', '-']) and isinstance(source, str):
        if args[0] == 'run':
            path = directory / f'tool-{index:03d}.yaml'
            path.write_text(source)
            command = ['sandbox', str(directory), str(path)]
        else:
            command = args
    else:
        error = 'Unsupported CLI arguments. Use --help, std [MODULE], schema, fmt -, or run - with stdin YAML.'
    if error:
        code, stdout, stderr = 2, '', error
    else:
        result = ctx.helix(*command, stdin=source if command == ['fmt', '-'] else None)
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    duration = (time.perf_counter() - clock) * 1000
    record = {
        'started_at': started.isoformat(timespec='milliseconds'), 'duration_ms': round(duration),
        'command': args[0] if args and isinstance(args[0], str) else 'invalid',
        'argv': json.dumps(args), 'cwd': str(directory), 'exit_code': code,
        'program_path': str(path) if path else None, 'program': source if isinstance(source, str) else None,
        'program_sha256': hashlib.sha256(source.encode()).hexdigest() if isinstance(source, str) else None,
        'stdin_program': int(isinstance(source, str)), 'stdout': stdout, 'stderr': stderr,
        'agent': 'openrouter:' + identity['model'], 'caller_role': 'benchmark-model', **identity,
    }
    observation = {'args': args, 'executed_argv': command, 'exit_code': code, 'stdout': stdout,
                   'stderr': stderr, 'duration_ms': duration, 'identity': identity}
    (directory / f'tool-{index:03d}.json').write_text(json.dumps(observation, ensure_ascii=False, indent=2) + '\n')
    # Log explicitly even when the surrounding regression harness disables developer telemetry.
    usage_log.append(ctx.root, ctx.binary, record)
    return {**observation, 'stdout': stdout[:16000], 'stderr': stderr[:16000],
            'output_truncated': len(stdout) > 16000 or len(stderr) > 16000}
