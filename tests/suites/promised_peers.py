"""Fresh-process coordinated participant restoration; part of snapshots suite."""
import json
import tempfile
from pathlib import Path

import yaml

def verify(h):
    client = (h.root / 'tests/programs/peer_client.yaml').read_text()
    server = (h.root / 'tests/programs/peer_server.yaml').read_text()
    malformed = h.helix('system-snapshot', '-', stdin=json.dumps({'participants': {'broken': '['}}))
    assert malformed.returncode == 1 and not malformed.stderr, malformed
    damaged_source = json.loads(malformed.stdout)
    machine = damaged_source['machines']['broken']
    assert machine['objects'][machine['machine']]['value']['status'] == 'error', machine
    failed = h.helix('system-snapshot', '-', '--steps', '100',
        stdin=json.dumps({'participants': {'broken': '{$call: [div, 1, 0]}'}}))
    assert failed.returncode == 1 and not failed.stderr, failed
    checkpoint = json.loads(failed.stdout)
    retained = h.helix('system-restore', '-', '--steps', '0', stdin=failed.stdout)
    assert retained.returncode == 1 and not retained.stderr, retained
    assert json.loads(retained.stdout) == checkpoint, retained
    terminal = h.helix('system-restore', '-', stdin=failed.stdout)
    assert terminal.returncode == 1 and not terminal.stderr, terminal
    assert yaml.safe_load(terminal.stdout)['status'] == 'error', terminal
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='promised-peers-') as directory:
        base = Path(directory)
        source = base / 'system.yaml'
        source.write_text(yaml.safe_dump({'participants': {'client': client, 'server': server}}))
        saved = h.helix('system-snapshot', str(source))
        assert saved.returncode == 0 and not saved.stderr, saved
        source.unlink()
        original = saved.stdout
        accepted = None
        # Each transition is performed by a fresh process with only the joint state.
        for _ in range(150):
            document = json.loads(saved.stdout)
            if len(document['deliveries']) == 1:
                accepted = saved.stdout
                break
            saved = h.helix('system-restore', '-', '--steps', '1', stdin=saved.stdout)
            assert saved.returncode == 0 and not saved.stderr, saved
        assert accepted is not None, 'never checkpointed accepted request before reply'
        outcomes = []
        for checkpoint in (original, accepted, accepted):
            # A detached working directory has no original source or import resolver.
            result = h.helix('system-restore', '-', stdin=checkpoint, cwd=base)
            assert result.returncode == 0 and not result.stderr, result
            state = yaml.safe_load(result.stdout)
            assert state['status'] == 'finished', state
            assert state['machines']['client']['root'] == 8, state
            assert state['delivery_count'] == 2, state
            assert len(state['machines']['server']['effects']) == 2, state
            outcomes.append(state)
        assert outcomes[0] == outcomes[1] == outcomes[2], outcomes
        damaged = json.loads(accepted)
        damaged['deliveries'] = []
        rejected = h.helix('system-restore', '-', stdin=json.dumps(damaged))
        assert rejected.returncode == 1 and not rejected.stderr, rejected
        assert yaml.safe_load(rejected.stdout)['status'] == 'error', rejected
