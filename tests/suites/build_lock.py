"""Build/snapshot exclusion contracts, reached through manage.py test --suite harness."""
from contextlib import contextmanager
from dataclasses import replace
import os
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
        original_link = os.link

        def assert_locked(expected):
            with (root / 'runtime' / 'build.lock').open('a') as other:
                try:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    assert expected, 'build lock remained held while executing tests'
                else:
                    fcntl.flock(other, fcntl.LOCK_UN)
                    assert not expected, 'another open handle acquired the build lock during build/snapshot'

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

        def link(source, destination):
            events.append('link')
            assert_locked(True)
            return original_link(source, destination)

        def replace_binary(contents):
            replacement = binary.with_suffix('.next')
            replacement.write_bytes(contents)
            replacement.replace(binary)

        with patch.object(manage, 'ROOT', root), patch.object(manage, 'build_lock', observed_lock), \
             patch.object(manage, '_build_unlocked', side_effect=compile), patch.object(os, 'link', side_effect=link):
            with manage.frozen_test_context(context) as (frozen, code):
                assert code == 0 and frozen.binary != binary
                snapshot = frozen.binary
                assert snapshot.read_bytes() == binary.read_bytes()
                assert frozen.root == context.root and frozen.timeout == context.timeout
                assert events == ['enter', 'build', 'link', 'exit'], events
                assert_locked(False)
                replace_binary(b'next executable generation')
                assert snapshot.read_bytes() == b'complete executable generation'
                events.clear()
                with manage.frozen_test_context(context) as (current, code):
                    assert code == 0 and current.binary != snapshot
                    assert current.binary.read_bytes() == b'next executable generation'
                    assert snapshot.exists(), 'a snapshot in use was pruned by a later build'
            assert current.binary.exists(), 'an idle current snapshot was not retained for reuse'
            events.clear()
            with manage.frozen_test_context(context) as (reused, code):
                assert code == 0 and reused.binary == current.binary
                assert not snapshot.exists(), 'an idle snapshot of a replaced build survived'
                assert events == ['enter', 'build', 'exit'], events
            # Cargo's recorded inputs, all older than the stamped build start, skip the rebuild;
            # any newer input, or a binary replaced by another builder, does not.
            source = root / 'src.rs'
            for path in (root / 'Cargo.toml', root / 'Cargo.lock', source):
                path.write_text('')
                os.utime(path, ns=(0, 0))
            binary.with_suffix('.d').write_text(f'{binary}: {source}\n')

            def edit_after_build(path):
                after = (root / 'runtime' / 'build.stamp').stat().st_mtime_ns + 1
                os.utime(path, ns=(after, after))

            for touched, rebuilt in ((None, False), (source, True), (None, False), (binary, True), (None, False)):
                if touched is not None:
                    edit_after_build(touched)
                events.clear()
                with manage.frozen_test_context(context) as (frozen, code):
                    assert code == 0 and frozen.binary.read_bytes() == binary.read_bytes()
                assert ('build' in events) == rebuilt, (touched, events)
            events.clear()
            assert manage.build() == 0
            assert events == ['enter', 'build', 'exit'], events
            assert_locked(False)

        edit_after_build(source)
        with patch.object(manage, 'ROOT', root), patch.object(manage, '_build_unlocked', return_value=7), \
             patch.object(shutil, 'copy2') as copy_mock:
            with manage.frozen_test_context(context) as (frozen, code):
                assert frozen is None and code == 7
                copy_mock.assert_not_called()
                assert_locked(False)

        replace_binary(b'uncopyable executable generation')
        with patch.object(manage, 'ROOT', root), patch.object(manage, '_build_unlocked', return_value=0), \
             patch.object(os, 'link', side_effect=OSError('cannot link')), \
             patch.object(shutil, 'copy2', side_effect=OSError('copy interrupted')):
            try:
                with manage.frozen_test_context(context):
                    raise AssertionError('a failed snapshot was yielded')
            except OSError as error:
                assert str(error) == 'copy interrupted'
            else:
                raise AssertionError('copy failure was swallowed')
            assert_locked(False)
            assert not any((root / 'runtime' / 'execution-binary' / manage._identity(binary)).glob('helix*')), \
                'a partial snapshot was retained'

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

        # Resolve the real policy before installing a fake executable/process
        # host. This contract observes Cargo lock ownership; policy supervision
        # and damaged responses are exercised independently by test_orchestration.
        from tooling import test_policy
        planned = test_policy.plan(h, ["library"], ["library"], False)
        reported = test_policy.report(h, [{"suite": "library", "failures": [], "stdout": "", "stderr": ""}])
        with patch.object(manage, 'ROOT', root), patch.object(subprocess, 'run', side_effect=run), \
             patch.object(subprocess, 'Popen', Process), \
             patch.object(test_policy, 'plan', return_value=planned), \
             patch.object(test_policy, 'report', return_value=reported):
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
            replace_binary(b'concurrent rebuild')
            assert snapshot.read_bytes() == b'wrapper executable generation'
            invoked.append(snapshot)
            return 9

        with patch.object(manage, 'ROOT', root), patch.object(manage, 'BIN', binary), \
             patch.object(sys, 'argv', ['manage.py', 'run', '-']), \
             patch.object(manage, '_build_unlocked', side_effect=compile), \
             patch.object(subprocess, 'call', side_effect=execute) as call_mock:
            assert manage.main() == 9
            assert len(invoked) == 1 and invoked[0].exists(), 'the idle snapshot was not retained for reuse'
            call_mock.reset_mock()
            with patch.object(manage, '_build_unlocked', return_value=7):
                assert manage.main() == 7
                call_mock.assert_not_called()
            assert_locked(False)
