"""Real subprocess acceptance for the bounded command adapter; no external hosts."""
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

import yaml


def verify(h):
    h = replace(h, timeout=max(h.timeout, 20))
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='long-commands-') as directory:
        base = Path(directory)
        program = base / 'program.yaml'
        executable = sys.executable
        grants = ['--allow-command', executable]

        def source(script):
            return json.dumps({'$call': ['effect', 'command', {
                'program': executable, 'args': {'$literal': ['-c', script]},
            }]})

        def prepare(script):
            program.write_text(source(script))

        def run(script, *flags):
            prepare(script)
            result = h.helix('perform', *grants, *flags, str(program))
            state = yaml.safe_load(result.stderr)
            assert isinstance(state, dict), result
            return result, state

        def failed(script, kind, *flags):
            result, state = run(script, *flags)
            assert result.returncode == 1 and state['error']['kind'] == kind, (result, state)
            return state

        def records(path):
            return [json.loads(line) for line in path.read_text().splitlines(keepends=True)
                    if line.endswith('\n')] if path.exists() else []

        # Reject invalid policy before source loading, spawning, or progress-file creation.
        progress = base / 'invalid.jsonl'
        marker = base / 'invalid-mutated'
        prepare(f'open({str(marker)!r}, "w").write("bad")')
        invalid = [('--command-timeout-ms', '0'), ('--command-timeout-ms', '86400001'),
                   ('--command-timeout-ms', '-1'), ('--command-output-limit', '0'),
                   ('--command-output-limit', '16777217'), ('--command-attempts', '0'),
                   ('--command-attempts', '33'), ('--command-retry-delay-ms', '60001'),
                   ('--command-retry-exit-code', '0'), ('--command-retry-exit-code', '256')]
        for flag, value in invalid:
            result = h.helix('perform', *grants, flag, value, '--command-progress', str(progress), str(program))
            assert result.returncode == 2 and not marker.exists() and not progress.exists(), result
        result = h.helix('perform', '--command-progress', str(progress), str(program))
        assert result.returncode == 1 and not marker.exists() and not progress.exists(), result
        occupied = base / 'occupied.jsonl'
        occupied.write_text('preserve')
        failed(f'open({str(marker)!r}, "w").write("bad")', 'io', '--command-progress', str(occupied))
        assert occupied.read_text() == 'preserve' and not marker.exists()

        # The deadline can exceed the old ten-second ceiling; heartbeat output stays separate.
        progress = base / 'long.jsonl'
        result = h.helix('perform', '--allow-command', '/bin/sh',
                         '--command-timeout-ms', '14000', '--command-progress', str(progress),
                         'tests/cases/long_commands.yaml')
        state = yaml.safe_load(result.stderr)
        assert result.returncode == 0 and result.stdout == '' and state['root'] == {
            'code': 0, 'stdout': 'complete\n', 'stderr': ''}, (result, state)
        events = records(progress)
        assert events[0]['event'] == 'starting' and events[-1]['event'] == 'finished', events
        assert any(event['event'] == 'heartbeat' for event in events), events
        assert all(event['version'] == 1 and event['effect_id'] == events[0]['effect_id'] for event in events)
        assert all(set(event) == {'version', 'effect_id', 'attempt', 'event', 'elapsed_ms',
                                  'stdout_bytes', 'stderr_bytes', 'code'} for event in events)
        assert 'complete' not in progress.read_text()

        failed('import time; time.sleep(2)', 'timeout', '--command-timeout-ms', '300')
        result, state = run('import os; os.write(1, b"a" * 8192); os.write(2, b"b" * 8192)',
                            '--command-output-limit', '8192')
        assert result.returncode == 0 and len(state['root']['stdout']) == len(state['root']['stderr']) == 8192
        failed('import os\nwhile True: os.write(1, b"x" * 4096); os.write(2, b"y" * 4096)',
               'resource', '--command-output-limit', '8192', '--command-timeout-ms', '2000')

        # Each attempt is externally counted; classifications never retry uncertain failures.
        counter = base / 'attempts'
        count = f'import pathlib\np=pathlib.Path({str(counter)!r})\nn=int(p.read_text())+1 if p.exists() else 1\np.write_text(str(n))\n'
        retry = ['--command-attempts', '3', '--command-retry-exit-code', '75', '--command-retry-delay-ms', '30']
        result, state = run(count + 'import sys; sys.exit(75 if n < 3 else 0)', *retry)
        assert result.returncode == 0 and state['root']['code'] == 0 and counter.read_text() == '3', state
        counter.unlink()
        result, state = run(count + 'import sys; sys.exit(75)', *retry)
        assert result.returncode == 0 and state['root']['code'] == 75 and counter.read_text() == '3', state
        counter.unlink()
        result, state = run(count + 'import sys; sys.exit(76)', *retry)
        assert result.returncode == 0 and state['root']['code'] == 76 and counter.read_text() == '1', state
        counter.unlink()
        result, state = run(count + 'raise SystemExit(75)')
        assert result.returncode == 0 and state['root']['code'] == 75 and counter.read_text() == '1'
        counter.unlink()
        result, state = run(count + 'import os, signal; os.kill(os.getpid(), signal.SIGTERM)', *retry)
        assert result.returncode == 0 and state['root']['code'] < 0 and counter.read_text() == '1'
        for suffix, kind, options in [
            ('import time; time.sleep(2)', 'timeout', ['--command-timeout-ms', '300']),
            ('import os; os.write(1, b"\\xff")', 'io', []),
            ('import os; os.write(1, b"x" * 64)', 'resource', ['--command-output-limit', '32']),
        ]:
            counter.unlink()
            failed(count + suffix, kind, *retry, *options)
            assert counter.read_text() == '1', (kind, counter.read_text())
        counter.unlink()
        failed(count + 'import sys; sys.exit(75)', 'timeout', *retry,
               '--command-timeout-ms', '100', '--command-retry-delay-ms', '500')
        assert counter.read_text() == '1'
        counter.unlink()
        failed(count + 'import os; os.write(1,b"x"*20); raise SystemExit(75)', 'resource',
               *retry, '--command-output-limit', '32')
        assert counter.read_text() == '2', counter.read_text()

        # Process tests observe actual liveness, not the adapter's own completion report.
        def alive(pid):
            try:
                stat = Path(f'/proc/{pid}/stat').read_text()
                return stat.rsplit(')', 1)[1].split()[0] != 'Z'
            except FileNotFoundError:
                return False

        def await_condition(predicate, seconds=3):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(0.01)
            assert predicate(), 'timed out waiting for subprocess evidence'

        pids = base / 'pids'
        def tree_script(exit_parent=False):
            return ('import os, time\nchild=os.fork()\n'
                    f'if child:\n open({str(pids)!r}, "w").write(str(os.getpid())+" "+str(child))\n'
                    + (' os._exit(0)\n' if exit_parent else '') + 'time.sleep(30)\n')

        def assert_tree_dead():
            ids = [int(pid) for pid in pids.read_text().split()]
            try:
                await_condition(lambda: not any(alive(pid) for pid in ids))
            finally:
                for pid in ids:
                    if alive(pid):
                        os.kill(pid, signal.SIGKILL)
            pids.unlink()

        for exit_parent in [False, True]:
            try:
                failed(tree_script(exit_parent), 'process' if exit_parent else 'timeout', '--command-timeout-ms', '300')
            finally:
                if pids.exists():
                    assert_tree_dead()

        # Background descendants closing their inherited output still cannot outlive completion.
        script = tree_script(True).replace('time.sleep(30)', 'os.close(1); os.close(2); time.sleep(30)')
        try:
            result, state = run(script, '--command-timeout-ms', '1000')
            assert result.returncode == 0 or state['error']['kind'] == 'process', state
        finally:
            if pids.exists():
                assert_tree_dead()

        def cancelled(script, signal_number, wait_for, *flags, catch=False, wrapper=False):
            prepare(script)
            if catch:
                command = json.loads(program.read_text())
                program.write_text(json.dumps({'$call': ['try', command, {'$call': ['fn', ['error'],
                    ({'$call': ['effect', 'file_write', {'path': str(marker), 'text': 'bad'}]}
                     if catch == 'effect' else 'caught')]}]}))
            prefix = [sys.executable, str(h.root / 'manage.py')] if wrapper else [str(h.binary)]
            with subprocess.Popen([*prefix, 'perform', *grants, *flags, str(program)],
                                  cwd=h.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, start_new_session=True) as controller:
                try:
                    await_condition(wait_for, seconds=60 if wrapper else 3)
                    controller.send_signal(signal_number)
                    stdout, stderr = controller.communicate(timeout=3)
                    state = yaml.safe_load(stderr)
                    assert controller.returncode == 1 and stdout == '', state
                    if catch:
                        assert state['status'] == ('signaled' if catch == 'effect' else 'finished') and not marker.exists(), state
                    else:
                        assert state['error']['kind'] == 'cancelled', state
                finally:
                    if controller.poll() is None:
                        controller.kill()
                        controller.communicate()
                    if pids.exists():
                        assert_tree_dead()

        for signal_number in [signal.SIGINT, signal.SIGTERM]:
            cancelled(tree_script(), signal_number, lambda: pids.exists() and pids.stat().st_size > 0, '--command-timeout-ms', '5000')
        for signum in [signal.SIGINT, signal.SIGTERM]:
            cancelled(tree_script(), signum, lambda: pids.exists() and pids.stat().st_size > 0,
                      '--command-timeout-ms', '5000', wrapper=True)
        cancelled(tree_script(), signal.SIGTERM, lambda: pids.exists() and pids.stat().st_size > 0,
                  '--allow-write', str(base), '--command-timeout-ms', '5000', catch='effect')
        cancelled(tree_script(), signal.SIGINT, lambda: pids.exists() and pids.stat().st_size > 0,
                  '--command-timeout-ms', '5000', catch='value')
        counter.unlink()
        progress = base / 'retry-cancel.jsonl'
        cancelled(count + 'raise SystemExit(75)', signal.SIGTERM,
                  lambda: any(event['event'] == 'retry' for event in records(progress)),
                  *retry, '--command-retry-delay-ms', '5000', '--command-progress', str(progress))
        assert counter.read_text() == '1'
