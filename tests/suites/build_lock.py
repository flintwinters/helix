"""Build/snapshot exclusion contracts, reached through manage.py test --suite harness."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import shutil
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
            assert not list((root / 'runtime').glob('test-binary-*'))
