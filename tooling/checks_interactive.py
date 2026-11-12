"""Interactive CLI lifecycle and installation contracts; invoked only by manage.py."""
import os
import queue
import shutil
import subprocess
import threading

import yaml


def check(ctx):
    verify_repl(ctx)
    verify_watch(ctx)
    verify_completions(ctx)
    verify_install(ctx)
    return []


def verify_repl(ctx):
    source = '[add, 20, 22]\n[div, 1, 0]\n:begin\nanswer: [mul, 6, 7]\n:end\n:quit\n'
    result = ctx.helix('repl', stdin=source)
    states = list(yaml.safe_load_all(result.stdout))
    assert result.returncode == 1 and not result.stderr, result
    assert [state['status'] for state in states] == ['finished', 'error', 'finished'], states
    assert states[0]['root'] == 42 and states[2]['root'] == {'answer': 42}, states
    result = ctx.helix('repl', stdin=':help\n:begin\n[\n:cancel\n42\n')
    assert result.returncode == 0 and 'lexical scope' in result.stderr, result
    assert yaml.safe_load(result.stdout)['root'] == 42, result
    assert ctx.helix('repl', stdin=':begin\n42\n').returncode == 2
    assert ctx.helix('repl', stdin='').returncode == 0
    case = ctx.root / 'tests/cases/daily_interactive.yaml'
    result = ctx.helix('repl', stdin=':begin\n' + case.read_text() + ':end\n')
    assert result.returncode == 0, result
    assert yaml.safe_load(result.stdout) == yaml.safe_load(case.with_suffix('.expected.yaml').read_text()), result


def verify_watch(ctx):
    directory = ctx.root / 'runtime/interactive'
    directory.mkdir(parents=True, exist_ok=True)
    main, middle, leaf = [directory / name for name in ('main.yaml', 'middle.yaml', 'leaf.yaml')]
    main.write_text('[import, middle.yaml]\n')
    middle.write_text('[import, leaf.yaml]\n')
    leaf.write_text('1\n')
    process = subprocess.Popen([str(ctx.binary), 'watch', str(main), '--interval-ms', '20', '--iterations', '7'],
                               cwd=ctx.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    documents = queue.Queue()
    errors = queue.Queue()

    def read_documents():
        lines = []
        for line in process.stdout:
            lines.append(line)
            if line.rstrip() == '...':
                documents.put(yaml.safe_load(''.join(lines)))
                lines = []

    reader = threading.Thread(target=read_documents, daemon=True)
    error_reader = threading.Thread(target=lambda: [errors.put(line) for line in process.stderr], daemon=True)
    reader.start()
    error_reader.start()
    try:
        assert documents.get(timeout=ctx.timeout)['root'] == 1
        # Same-byte rewrites do not rerun; mtime alone is not a source change.
        leaf.write_text('1\n')
        try:
            unexpected = documents.get(timeout=0.15)
        except queue.Empty:
            pass
        else:
            raise AssertionError(f'unchanged contents reran: {unexpected}')
        leaf.write_text('2\n')
        assert documents.get(timeout=ctx.timeout)['root'] == 2
        leaf.unlink()
        assert documents.get(timeout=ctx.timeout)['status'] == 'error'
        leaf.write_text('3\n')
        assert documents.get(timeout=ctx.timeout)['root'] == 3
        # A changed import path refreshes the dependency set.
        alternate = directory / 'alternate.yaml'
        alternate.write_text('4\n')
        middle.write_text('[import, alternate.yaml]\n')
        assert documents.get(timeout=ctx.timeout)['root'] == 4
        main.write_bytes(b'\xff')
        assert 'cannot read' in errors.get(timeout=ctx.timeout)
        try:
            repeated = errors.get(timeout=0.15)
        except queue.Empty:
            pass
        else:
            raise AssertionError(f'unchanged invalid UTF-8 reran: {repeated}')
        main.write_text('5\n')
        assert documents.get(timeout=ctx.timeout)['root'] == 5
        assert process.wait(timeout=ctx.timeout) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=ctx.timeout)
        reader.join(timeout=ctx.timeout)
        error_reader.join(timeout=ctx.timeout)
    for args in [('watch', '-'), ('watch', str(main), '--iterations', '0'),
                 ('watch', str(main), '--interval-ms', '0'), ('watch', str(main), '--iterations', 'bad')]:
        assert ctx.helix(*args).returncode == 2, args
    missing = directory / 'missing.yaml'
    missing.unlink(missing_ok=True)
    assert ctx.helix('watch', str(missing), '--iterations', '1').returncode == 2
    assert ctx.helix('watch', str(main), '--iterations', '1').returncode == 0


def verify_completions(ctx):
    for shell in ('bash', 'zsh', 'fish'):
        result = ctx.helix('completion', shell)
        assert result.returncode == 0 and not result.stderr and '@COMMANDS@' not in result.stdout, result
        assert all(command in result.stdout for command in ('repl', 'watch', 'inspect', 'lsp', 'completion', 'perform')), result
        if shutil.which(shell):
            syntax = subprocess.run([shell, '-n'], input=result.stdout, text=True, capture_output=True,
                                    cwd=ctx.root, timeout=ctx.timeout)
            assert syntax.returncode == 0, syntax.stderr
    assert ctx.helix('completion', 'unknown').returncode == 2
    result = ctx.helix('completion', 'bash')
    script = result.stdout + '\nCOMP_WORDS=(helix wa); COMP_CWORD=1; _helix_complete; printf "%s\\n" "${COMPREPLY[@]}"\n'
    completed = subprocess.run(['bash'], input=script, capture_output=True, text=True, cwd=ctx.root, timeout=ctx.timeout)
    assert completed.returncode == 0 and completed.stdout == 'watch\n', completed


def verify_install(ctx):
    destination = ctx.root / 'runtime/interactive/install'
    result = subprocess.run(['cargo', 'install', '--path', '.', '--locked', '--offline', '--debug',
                             '--root', str(destination), '--force'], cwd=ctx.root,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
    binary = destination / 'bin' / ('helix.exe' if os.name == 'nt' else 'helix')
    result = subprocess.run([str(binary), 'run', str(ctx.root / 'tests/cases/daily_interactive.yaml')],
                            cwd=destination, capture_output=True, text=True, timeout=ctx.timeout)
    assert result.returncode == 0, result
    assert result.stdout == (ctx.root / 'tests/cases/daily_interactive.expected.yaml').read_text(), result
