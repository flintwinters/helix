"""Non-executing checks, canonical source preservation and framed editor protocol."""
import json
import subprocess

import yaml


def check(ctx):
    try:
        verify_checks(ctx)
        verify_format(ctx)
        verify_lsp(ctx)
        return []
    except (AssertionError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
        return [f"static tooling: {error}"]


def verify_checks(ctx):
    for source in ('[div, 1, 0]', '[import, "does-not-exist.yaml"]',
                   '[until, [fn, [x], false], [fn, [x], x], 0]',
                   '[fn, [add], 42]', '["add", 1]'):
        result = ctx.helix('check', '-', stdin=source)
        report = yaml.safe_load(result.stdout)
        assert result.returncode == 0 and report['mode'] == 'static' and report['diagnostics'] == [], result
    result = ctx.helix('check', '-', stdin='unused: [fn, [], [add, 1]]\nlazy: [if, true, 42, [sub, 1]]\n')
    report = yaml.safe_load(result.stdout)
    assert result.returncode == 1 and [d['path'] for d in report['diagnostics']] == ['/unused/2', '/lazy/3'], result
    for source in ('[', 'x: 1\nx: 2\n'):
        result = ctx.helix('check', '-', stdin=source)
        assert result.returncode == 1 and yaml.safe_load(result.stdout)['diagnostics'][0]['kind'] == 'parse', result
    result = ctx.helix('check', '--evaluate', '-', stdin='[div, 1, 0]')
    assert result.returncode == 1 and yaml.safe_load(result.stdout)['status'] == 'error', result
    schema = ctx.helix('schema')
    assert schema.returncode == 0 and json.loads(schema.stdout) == json.loads((ctx.root / 'editor/helix.schema.json').read_text()), schema


def verify_format(ctx):
    # Every parsable golden source must format to an idempotent source document.
    # The implementation independently compares the complete source graphs before emitting.
    for case in sorted((ctx.root / 'tests/cases').glob('*.yaml')):
        if case.name.endswith('.expected.yaml'):
            continue
        formatted = ctx.helix('fmt', '-', stdin=case.read_text())
        if formatted.returncode:
            # Invalid parser fixtures are expected to fail formatting as well.
            checked = ctx.helix('check', '-', stdin=case.read_text())
            diagnostics = yaml.safe_load(checked.stdout)['diagnostics']
            assert diagnostics and diagnostics[0]['kind'] in ('parse', 'limit'), (case.name, formatted)
            continue
        again = ctx.helix('fmt', '-', stdin=formatted.stdout)
        assert (again.returncode, again.stdout) == (0, formatted.stdout), (case.name, again)
    source = (ctx.root / 'tests/cases/tooling_source.yaml').read_text()
    formatted = ctx.helix('fmt', '-', stdin=source)
    assert formatted.returncode == 0 and '"add"' in formatted.stdout, formatted
    original = yaml.safe_load(ctx.helix('run', '-', stdin=source).stdout)
    after = yaml.safe_load(ctx.helix('run', '-', stdin=formatted.stdout).stdout)
    assert original['status'] == after['status'] == 'finished' and original['root'] == after['root'], (original, after)


def frame(message):
    body = json.dumps({'jsonrpc': '2.0', **message}, ensure_ascii=False).encode()
    return f'Content-Length: {len(body)}\r\n\r\n'.encode() + body


def messages(data):
    result = []
    while data:
        header, data = data.split(b'\r\n\r\n', 1)
        length = int(header.split(b':', 1)[1])
        body, data = data[:length], data[length:]
        assert len(body) == length
        result.append(json.loads(body))
    return result


def verify_lsp(ctx):
    uri = 'file:///unread/helix.yaml'
    request = [
        {'id': 0, 'method': 'textDocument/formatting'},
        {'id': 1, 'method': 'initialize', 'params': {}},
        {'method': 'initialized', 'params': {}},
        {'method': 'textDocument/didOpen', 'params': {'textDocument': {'uri': uri, 'version': 1, 'text': '"😀": [add, 1]'}}},
        {'method': 'textDocument/didChange', 'params': {'textDocument': {'uri': uri, 'version': 2}, 'contentChanges': [{'text': '[add, 20, 22]'}]}},
        {'id': 2, 'method': 'textDocument/formatting', 'params': {'textDocument': {'uri': uri}, 'options': {}}},
        {'id': 3, 'method': 'unknown'},
        {'method': 'textDocument/didClose', 'params': {'textDocument': {'uri': uri}}},
        {'id': 4, 'method': 'shutdown'},
        {'method': 'exit'},
    ]
    result = subprocess.run([str(ctx.binary), 'lsp'], input=b''.join(map(frame, request)), capture_output=True, cwd=ctx.root, timeout=ctx.timeout)
    assert result.returncode == 0 and not result.stderr, result
    replies = messages(result.stdout)
    assert replies[0]['error']['code'] == -32002, replies
    assert replies[1]['result']['capabilities']['textDocumentSync']['change'] == 1, replies
    diagnostic = replies[2]['params']['diagnostics'][0]
    assert diagnostic['code'] == 'arity' and diagnostic['range']['start']['character'] == 6, diagnostic
    assert replies[3]['params']['diagnostics'] == [], replies
    formatted = replies[4]['result'][0]['newText']
    assert ctx.helix('exec', formatted).stdout == '42\n', formatted
    assert replies[5]['error']['code'] == -32601, replies
    assert replies[6]['params']['diagnostics'] == [] and replies[7]['result'] is None, replies
    for bad in (b'Content-Length: 99999999\r\n\r\n', b'Content-Length: 3\r\n\r\nxx', b'Content-Length: 1\r\n\r\nx'):
        result = subprocess.run([str(ctx.binary), 'lsp'], input=bad, capture_output=True, cwd=ctx.root, timeout=ctx.timeout)
        assert result.returncode != 0 and not result.stdout, result
