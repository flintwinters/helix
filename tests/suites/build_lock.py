"""Build/snapshot exclusion contracts, reached through manage.py test --suite harness."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch


def verify(h):
    import fcntl
    import manage

    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='build-lock-') as directory:
        root = Path(directory)
        binary = root / 'compiler-output'
        binary.write_bytes(b'complete executable generation')
        context = replace(h, root=root, binary=binary)
        events = []
        original_lock = manage.build_lock
        original_copy = shutil.copy2

        def assert_locked(expected):
            with (root / 'runtime' / 'build.lock').open('a') as other:
                try:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    assert expected, 'build lock remained held while executing tests'
                else:
                    fcntl.flock(other, fcntl.LOCK_UN)
                    assert not expected, 'another open handle acquired the build lock during build/copy'

        @contextmanager
        def observed_lock():
            with original_lock():
                events.append('enter')
                try:
                    yield
                finally:
                    events.append('exit')

        def compile(**kwargs):
            events.append('build')
            assert_locked(True)
            return 0

        def copy(source, destination):
            events.append('copy')
            assert_locked(True)
            return original_copy(source, destination)

        with patch.object(manage, 'ROOT', root), patch.object(manage, 'build_lock', observed_lock), \
             patch.object(manage, '_build_unlocked', side_effect=compile), patch.object(shutil, 'copy2', side_effect=copy):
            with manage.frozen_test_context(context) as (frozen, code):
                assert code == 0 and frozen.binary != binary
                snapshot = frozen.binary
                assert snapshot.read_bytes() == binary.read_bytes()
                assert frozen.root == context.root and frozen.timeout == context.timeout
                assert events == ['enter', 'build', 'copy', 'exit'], events
                assert_locked(False)
                binary.write_bytes(b'next executable generation')
                assert snapshot.read_bytes() == b'complete executable generation'
            assert not snapshot.exists(), 'snapshot directory survived its test context'
            events.clear()
            assert manage.build() == 0
            assert events == ['enter', 'build', 'exit'], events
            assert_locked(False)

        with patch.object(manage, 'ROOT', root), patch.object(manage, '_build_unlocked', return_value=7), \
             patch.object(shutil, 'copy2') as copy_mock:
            with manage.frozen_test_context(context) as (frozen, code):
                assert frozen is None and code == 7
                copy_mock.assert_not_called()
                assert_locked(False)

        with patch.object(manage, 'ROOT', root), patch.object(manage, '_build_unlocked', return_value=0), \
             patch.object(shutil, 'copy2', side_effect=OSError('copy interrupted')):
            try:
                with manage.frozen_test_context(context):
                    raise AssertionError('a failed snapshot was yielded')
            except OSError as error:
                assert str(error) == 'copy interrupted'
            else:
                raise AssertionError('copy failure was swallowed')
            assert_locked(False)
            assert [entry.name for entry in (root / 'runtime').iterdir()] == ['build.lock']

        # Cargo test/install can rebuild the same target as ordinary manage builds.
        from tests.suites.contracts import execution
        from tooling.checks_interactive import verify_install
        fixture = root / 'tests' / 'cases' / 'daily_interactive.expected.yaml'
        fixture.parent.mkdir(parents=True)
        fixture.write_text('status: finished\nroot: 42\n')
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            assert_locked(args[0] == 'cargo')
            return subprocess.CompletedProcess(args, 0, '' if args[0] == 'cargo' else fixture.read_text(), '')

        class Process:
            returncode = 0

            def __init__(self, args, **kwargs):
                calls.append(args)
                assert args[:2] == ['cargo', 'test']
                assert_locked(True)

            def __enter__(self):
                return self

            def communicate(self, **kwargs):
                assert_locked(True)
                return '', None

            def __exit__(self, *args):
                assert_locked(True)

        with patch.object(manage, 'ROOT', root), patch.object(subprocess, 'run', side_effect=run), \
             patch.object(subprocess, 'Popen', Process):
            assert context.regression(False, ['library']) == 0
            execution(context)
            verify_install(context)
            assert [args[1] for args in calls[:3]] == ['test', 'test', 'install'], calls
            assert len(calls) == 4, calls
            assert_locked(False)

        # Ordinary wrappers must execute a snapshot after releasing build exclusion.
        binary.write_bytes(b'wrapper executable generation')
        invoked = []

        def execute(args, **kwargs):
            assert args[1:] == ['run', '-'] and kwargs['cwd'] == root
            snapshot = Path(args[0])
            assert snapshot != binary and snapshot.read_bytes() == b'wrapper executable generation'
            assert_locked(False)
            binary.write_bytes(b'concurrent rebuild')
            assert snapshot.read_bytes() == b'wrapper executable generation'
            invoked.append(snapshot)
            return 9

        with patch.object(manage, 'ROOT', root), patch.object(manage, 'BIN', binary), \
             patch.object(sys, 'argv', ['manage.py', 'run', '-']), \
             patch.object(manage, '_build_unlocked', side_effect=compile), \
             patch.object(subprocess, 'call', side_effect=execute) as call_mock:
            assert manage.main() == 9
            assert len(invoked) == 1 and not invoked[0].exists()
            call_mock.reset_mock()
            with patch.object(manage, '_build_unlocked', return_value=7):
                assert manage.main() == 7
                call_mock.assert_not_called()
            assert_locked(False)
