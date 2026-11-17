"""Opaque host inputs never become execution data, even when a child echoes them."""
import base64
import json
from pathlib import Path
import sys
import tempfile

import yaml


def verify(h):
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='secret-effects-') as directory:
        base = Path(directory)
        credential = 'private-credential-7d189d\n'
        encoded = base64.b64encode(credential.encode()).decode()
        secret = base / 'credential'
        secret.write_text(credential)
        secret.chmod(0o600)
        target = base / 'received'
        script = ('import sys,base64; from pathlib import Path; value=sys.stdin.read(); '
                  f'Path({str(target)!r}).write_text(value); '
                  'print(value); print(base64.b64encode(value.encode()).decode(), file=sys.stderr)')
        handle = {'type': 'secret', 'ref': 'deploy-token'}
        payload = {'program': sys.executable, 'args': ['-c', script], 'stdin': handle}
        source = base / 'program.yaml'
        source.write_text(json.dumps({'$call': ['effect', 'command', payload]}))
        checkpoint, progress = base / 'journal.json', base / 'progress.jsonl'
        grants = ['--allow-command', sys.executable, '--allow-secret', f'deploy-token={secret}']
        run = h.helix('perform', *grants, '--command-progress', str(progress),
                      '--checkpoint', str(checkpoint), str(source))
        assert run.returncode == 0 and target.read_text() == credential, run
        assert yaml.safe_load(run.stderr)['root'] == {'code': 0, 'stdout': '', 'stderr': ''}
        inspected = h.helix('checkpoint', str(checkpoint))
        assert inspected.returncode == 0, inspected
        for output in (run.stdout, run.stderr, checkpoint.read_text(), progress.read_text(), inspected.stdout):
            assert credential.strip() not in output and encoded not in output, output
        assert str(secret) not in checkpoint.read_text(), 'a secret grant was persisted'

        # Capture suppression includes error paths and transformed output, not
        # just replacement of literal credential bytes in successful strings.
        failure = dict(payload, args=['-c', 'import sys,time; print(sys.stdin.read(), flush=True); time.sleep(10)'])
        source.write_text(json.dumps({'$call': ['effect', 'command', failure]}))
        timed_out = h.helix('perform', *grants, '--command-timeout-ms', '100', str(source))
        assert timed_out.returncode != 0 and credential.strip() not in timed_out.stderr, timed_out
        assert yaml.safe_load(timed_out.stderr)['error']['kind'] == 'secret'
        target.unlink()
        source.write_text(json.dumps({'$call': ['effect', 'command', payload]}))
        denied = h.helix('perform', '--allow-command', sys.executable, str(source))
        assert denied.returncode != 0 and not target.exists(), denied
        secret.chmod(0o644)
        public = h.helix('perform', *grants, str(source))
        assert public.returncode != 0 and not target.exists(), public
        secret.chmod(0o600)

        # A disk-restored request still contains only a handle and needs fresh
        # authority. Reconciliation uses the same output-protection boundary.
        frozen = h.helix('snapshot', str(source), '--steps', '100')
        assert frozen.returncode == 0, frozen
        document = {'format': 'helix-effects', 'version': 1, 'authority': 'caller-required',
                    'phase': 'ready', 'host': {'exit_code': 0, 'random_seed': None},
                    'snapshot': yaml.safe_load(frozen.stdout)}
        pending = base / 'pending.json'
        pending.write_text(json.dumps(document))
        resumed = h.helix('perform', *grants, '--resume-checkpoint', str(pending))
        assert resumed.returncode == 0 and target.read_text() == credential, resumed
        assert credential.strip() not in pending.read_text()
        uncertain = base / 'uncertain.json'
        document['phase'] = 'in_flight'
        uncertain.write_text(json.dumps(document))
        outcome = base / 'observed.json'
        outcome.write_text(json.dumps({'request': {'id': 1, 'operation': 'command', 'payload': payload},
                                      'outcome': {'ok': {'code': 0, 'stdout': credential, 'stderr': encoded}}}))
        reconciled = h.helix('reconcile', str(uncertain), str(outcome))
        assert reconciled.returncode == 0, reconciled
        assert credential.strip() not in uncertain.read_text() and encoded not in uncertain.read_text()
