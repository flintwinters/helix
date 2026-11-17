"""Crash/uncertainty acceptance through the public durable host CLI."""
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
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='durable-effects-') as directory:
        base = Path(directory)
        counter, marker, output = base / 'counter', base / 'marker', base / 'output'
        first = f'from pathlib import Path; p=Path({str(counter)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x")'
        second = f'import os,time; open({str(marker)!r},"w").write(str(os.getpid())); time.sleep(30)'
        payload = {'program': sys.executable, 'args': ['-c', second]}
        source = base / 'program.yaml'
        source.write_text(json.dumps([
            {'$call': ['effect', 'command', {'program': sys.executable, 'args': ['-c', first]}]},
            {'$call': ['effect', 'command', payload]},
            {'$call': ['effect', 'file_write', {'path': str(output), 'text': 'done'}]},
        ]))
        checkpoint = base / 'execution.json'
        args = [str(h.binary), 'perform', '--allow-command', sys.executable,
                '--command-timeout-ms', '60000', '--checkpoint', str(checkpoint), str(source)]
        child_pid = None
        process = subprocess.Popen(args, cwd=h.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 20
            while not marker.exists() or not marker.read_text():
                assert process.poll() is None, process.communicate()
                assert time.monotonic() < deadline, 'durable controller never reached the uncertain effect'
                time.sleep(0.02)
            child_pid = int(marker.read_text())
            assert counter.read_text() == 'x'
            doc = json.loads(checkpoint.read_text())
            assert doc['phase'] == 'in_flight'
            # Inspection works while a controller owns the journal, without
            # modifying it, dispatching effects or weakening exclusive resume.
            before_inspect = checkpoint.read_bytes()
            inspected = h.helix('checkpoint', str(checkpoint))
            assert inspected.returncode == 0, inspected
            report = yaml.safe_load(inspected.stdout)
            assert report['format'] == 'helix.recovery.v1'
            assert report['action'] == 'observe_and_reconcile' and not report['resume_allowed']
            assert report['pending_effect']['id'] == 2
            assert report['pending_effect']['payload'] == payload
            assert report['pending_effect']['location']
            assert [e['id'] for e in report['effects']] == [1]
            assert report['effects'][0]['status'] == 'finished'
            assert checkpoint.read_bytes() == before_inspect
            # A second controller cannot use this journal while its owner is alive.
            busy = h.helix('perform', '--resume-checkpoint', str(checkpoint))
            assert busy.returncode != 0 and 'another controller' in busy.stderr, busy
            os.kill(process.pid, signal.SIGKILL)
            os.killpg(child_pid, signal.SIGKILL)
            process.communicate(timeout=10)
            refused = h.helix('perform', '--allow-command', sys.executable, '--resume-checkpoint', str(checkpoint))
            assert refused.returncode != 0 and 'uncertain' in refused.stderr, refused
            assert counter.read_text() == 'x' and not output.exists()

            outcome = base / 'observed.json'
            observed = {'request': {'id': 2, 'operation': 'command', 'payload': payload},
                        'outcome': {'ok': {'code': 0, 'stdout': '', 'stderr': ''}}}
            wrong = json.loads(json.dumps(observed)); wrong['request']['id'] = 1
            outcome.write_text(json.dumps(wrong))
            rejected = h.helix('reconcile', str(checkpoint), str(outcome))
            assert rejected.returncode != 0 and json.loads(checkpoint.read_text())['phase'] == 'in_flight'
            # The test independently observed the only intended mutation before
            # killing the sleeper. Accept that observed postcondition explicitly.
            outcome.write_text(json.dumps(observed))
            reconciled = h.helix('reconcile', str(checkpoint), str(outcome))
            assert reconciled.returncode == 0, reconciled
            assert json.loads(checkpoint.read_text())['phase'] == 'ready'
            report = yaml.safe_load(h.helix('checkpoint', str(checkpoint)).stdout)
            assert report['action'] == 'resume_with_fresh_grants' and report['resume_allowed']

            denied_branch = base / 'denied.json'
            denied_branch.write_text(checkpoint.read_text())
            denied = h.helix('perform', '--resume-checkpoint', str(denied_branch))
            assert denied.returncode != 0 and 'capability' in denied.stderr and not output.exists(), denied
            resumed = h.helix('perform', '--allow-write', str(base), '--resume-checkpoint', str(checkpoint))
            assert resumed.returncode == 0 and output.read_text() == 'done', resumed
            assert counter.read_text() == 'x', 'completed command repeated after disk restoration'
            before = output.stat().st_mtime_ns
            repeated = h.helix('perform', '--resume-checkpoint', str(checkpoint))
            assert repeated.returncode == 0 and output.stat().st_mtime_ns == before
            assert yaml.safe_load(resumed.stderr)['root'] == yaml.safe_load(repeated.stderr)['root']
            assert checkpoint.stat().st_mode & 0o077 == 0
            report = yaml.safe_load(h.helix('checkpoint', str(checkpoint)).stdout)
            assert report['action'] == 'complete' and report['pending_effect'] is None
            assert [e['id'] for e in report['effects']] == [1, 2, 3]
            invalid = base / 'invalid.json'
            invalid.write_text('{"format":"helix-effects","version":999}')
            assert h.helix('checkpoint', str(invalid)).returncode != 0
            assert not (base / 'invalid.json.lock').exists()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            if child_pid is not None:
                try:
                    os.killpg(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.communicate(timeout=10)

        # Host bookkeeping that affects the observable result survives too.
        exit_source, exit_checkpoint = base / 'exit.yaml', base / 'exit.json'
        exit_source.write_text('{$call: [effect, exit, 7]}\n')
        exited = h.helix('perform', '--checkpoint', str(exit_checkpoint), str(exit_source))
        assert exited.returncode == 7, exited
        restored = h.helix('perform', '--resume-checkpoint', str(exit_checkpoint))
        assert restored.returncode == 7, restored
        existing = h.helix('perform', '--checkpoint', str(exit_checkpoint), str(exit_source))
        assert existing.returncode != 0 and 'already exists' in existing.stderr, existing

        # Reconciliation preserves deterministic host progress as well as VM data.
        random_program = '[{$call: [effect, random, null]}, {$call: [effect, random, null]}]'
        saved = h.helix('snapshot', '-', '--steps', '100', stdin=random_program)
        assert saved.returncode == 0, saved
        random_checkpoint = base / 'random.json'
        random_checkpoint.write_text(json.dumps({'format': 'helix-effects', 'version': 1,
            'phase': 'in_flight', 'authority': 'caller-required', 'host': {'exit_code': 0, 'random_seed': 7},
            'snapshot': yaml.safe_load(saved.stdout)}))
        seed = (7 * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        first_value = seed >> 1
        next_seed = (seed * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        observed = {'request': {'id': 1, 'operation': 'random', 'payload': None}, 'outcome': {'ok': first_value + 1}}
        random_outcome = base / 'random-outcome.json'
        random_outcome.write_text(json.dumps(observed))
        assert h.helix('reconcile', str(random_checkpoint), str(random_outcome)).returncode != 0
        observed['outcome']['ok'] = first_value
        random_outcome.write_text(json.dumps(observed))
        assert h.helix('reconcile', str(random_checkpoint), str(random_outcome)).returncode == 0
        result = h.helix('perform', '--allow-random', '--resume-checkpoint', str(random_checkpoint))
        assert result.returncode == 0 and yaml.safe_load(result.stderr)['root'] == [first_value, next_seed >> 1], result

        # A graceful cancellation records its failed command and an unstarted
        # successor. Explicit resume acknowledges the event without replaying it.
        cancel_source, cancel_checkpoint = base / 'cancel.yaml', base / 'cancel.json'
        cancel_marker, cancel_output = base / 'cancel-marker', base / 'cancel-output'
        script = f'import time; open({str(cancel_marker)!r},"w").write("started"); time.sleep(30)'
        cancel_source.write_text(json.dumps([
            {'$call': ['try', {'$call': ['effect', 'command', {'program': sys.executable, 'args': ['-c', script]}]},
                       {'$call': ['fn', ['failure'], None]}]},
            {'$call': ['effect', 'file_write', {'path': str(cancel_output), 'text': 'resumed'}]},
        ]))
        cancelled = subprocess.Popen([str(h.binary), 'perform', '--allow-command', sys.executable,
            '--command-timeout-ms', '60000', '--checkpoint', str(cancel_checkpoint), str(cancel_source)],
            cwd=h.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 20
            while not cancel_marker.exists():
                assert cancelled.poll() is None, cancelled.communicate()
                assert time.monotonic() < deadline
                time.sleep(0.02)
            cancelled.send_signal(signal.SIGTERM)
            _, stderr = cancelled.communicate(timeout=10)
            assert cancelled.returncode == 1 and yaml.safe_load(stderr)['status'] == 'signaled', stderr
            resumed = h.helix('perform', '--allow-write', str(base), '--resume-checkpoint', str(cancel_checkpoint))
            assert resumed.returncode == 0 and cancel_output.read_text() == 'resumed', resumed
        finally:
            if cancelled.poll() is None:
                os.killpg(cancelled.pid, signal.SIGKILL)
            cancelled.communicate(timeout=10)
