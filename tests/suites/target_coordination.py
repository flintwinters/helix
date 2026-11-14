"""Different journals share a target lock; crash uncertainty survives lock release."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import json


def verify(h):
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='target-coordination-') as directory:
        base = Path(directory)
        lock, marker = base / 'target.lock', base / 'started'
        source = base / 'program.yaml'
        script = f'import os,time; open({str(marker)!r},"w").write(str(os.getpid())); time.sleep(30)'
        source.write_text(json.dumps({'$call': ['effect', 'command', {'program': sys.executable, 'args': ['-c', script]}]}))
        flags = ['--target-lock', str(lock), '--allow-command', sys.executable, '--command-timeout-ms', '60000']
        process = subprocess.Popen([str(h.binary), 'perform', *flags, '--checkpoint', str(base / 'one.json'), str(source)],
                                   cwd=h.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        child = None
        try:
            deadline = time.monotonic() + 20
            while not marker.exists() or not marker.read_text():
                assert process.poll() is None, process.communicate()
                assert time.monotonic() < deadline
                time.sleep(0.02)
            child = int(marker.read_text())
            second = h.helix('perform', *flags, '--checkpoint', str(base / 'two.json'), str(source))
            assert second.returncode != 0 and 'another controller' in second.stderr, second
            assert not (base / 'two.json').exists()
            refused = h.helix('target-release', str(lock))
            assert refused.returncode != 0, refused
            process.kill()
            process.communicate(timeout=10)
            # Even after flock is released, a different journal cannot apply.
            refused = h.helix('perform', *flags, str(source))
            assert refused.returncode != 0 and 'unreleased operation' in refused.stderr, refused
            os.killpg(child, signal.SIGKILL)
            child = None
            # The operator has independently stopped the surviving mutator and
            # records the failed request before releasing its target domain.
            observed = base / 'observed.json'
            observed.write_text(json.dumps({'request': {'id': 1, 'operation': 'command',
                'payload': {'program': sys.executable, 'args': ['-c', script]}},
                'outcome': {'error': {'kind': 'interrupted', 'message': 'mutator stopped independently'}}}))
            reconciled = h.helix('reconcile', str(base / 'one.json'), str(observed))
            assert reconciled.returncode == 0, reconciled
            released = h.helix('target-release', str(lock))
            assert released.returncode == 0, released
            source.write_text('42\n')
            clean = h.helix('perform', '--target-lock', str(lock), str(source))
            assert clean.returncode == 0, clean
            repeated = h.helix('perform', '--target-lock', str(lock), str(source))
            assert repeated.returncode == 0, repeated
            malformed = base / 'malformed.lock'
            malformed.write_text('broken')
            assert h.helix('perform', '--target-lock', str(malformed), str(source)).returncode != 0
        finally:
            if process.poll() is None:
                process.kill()
            if child is not None:
                try:
                    os.killpg(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.communicate(timeout=10)
