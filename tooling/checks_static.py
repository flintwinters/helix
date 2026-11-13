"""Non-executing checks, canonical source preservation and framed editor protocol."""
import json
import subprocess

import yaml


def check(ctx):
    try:
        verify_checks(ctx)
        verify_bodies(ctx)
        verify_policy_result_fields(ctx)
        verify_record_boundaries(ctx)
        verify_annotation_bounds(ctx)
        verify_numeric_enum_overlap(ctx)
        verify_computed_heads(ctx)
        verify_repository_modules(ctx)
        verify_format(ctx)
        verify_lsp(ctx)
        return []
    except (AssertionError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
        return [f"static tooling: {error}"]


def verify_checks(ctx):
    for source in ('{"$call": ["div", 1, 0]}', '{"$call": ["import", "does-not-exist.yaml"]}',
                   '{"$call": ["until", {"$call": ["fn", ["x"], false]}, {"$call": ["fn", ["x"], {"$ref": "x"}]}, 0]}',
                   '{"$call": ["fn", ["add"], 42]}', '["add", 1]'):
        result = ctx.helix('check', '-', stdin=source)
        report = yaml.safe_load(result.stdout)
        assert result.returncode == 0 and report['mode'] == 'static' and report['diagnostics'] == [], result
    result = ctx.helix('check', '-', stdin='{"unused": {"$call": ["fn", [], {"$call": ["add", 1]}]}, "lazy": {"$call": ["if", true, 42, {"$call": ["sub", 1]}]}}')
    report = yaml.safe_load(result.stdout)
    assert result.returncode == 1 and [d['path'] for d in report['diagnostics']] == ['/lazy/$call/3', '/unused/$call/2'], result
    for source in ('[', 'x: 1\nx: 2\n'):
        result = ctx.helix('check', '-', stdin=source)
        assert result.returncode == 1 and yaml.safe_load(result.stdout)['diagnostics'][0]['kind'] == 'parse', result
    result = ctx.helix('check', '--evaluate', '-', stdin='{"$call": ["div", 1, 0]}')
    assert result.returncode == 1 and yaml.safe_load(result.stdout)['status'] == 'error', result
    schema = ctx.helix('schema')
    assert schema.returncode == 0 and json.loads(schema.stdout) == json.loads((ctx.root / 'editor/helix.schema.json').read_text()), schema


def static_report(ctx, source):
    result = ctx.helix('check', '-', stdin=source)
    return result.returncode, yaml.safe_load(result.stdout)


def verify_repository_modules(ctx):
    """Exercise the checker on the policy that develops Helix, including unused bodies.

    Unproved obligations remain visible in the saved reports; they are not failures
    or claims that the module has been proved safe.
    """
    destination = ctx.root / 'runtime' / 'static'
    destination.mkdir(exist_ok=True)
    sources = sorted((ctx.root / 'std').glob('*.yaml')) + sorted((ctx.root / 'tooling').glob('*.yaml'))
    assert sources, 'no repository Helix modules discovered'
    failures = []
    unchecked = 0
    for source in sources:
        relative = source.relative_to(ctx.root)
        result = ctx.helix('check', str(relative))
        artifact = destination / f'{source.parent.name}-{source.stem}.yaml'
        artifact.write_text(result.stdout)
        report = yaml.safe_load(result.stdout)
        assert isinstance(report, dict) and report.get('version') == 1 and report.get('mode') == 'static', (relative, result)
        assert isinstance(report.get('diagnostics'), list) and isinstance(report.get('unchecked'), list), (relative, report)
        unchecked += len(report['unchecked'])
        if result.returncode != 0 or report.get('status') != 'ok' or report['diagnostics']:
            failures.append(f'{relative}: {report["diagnostics"]}; report: {artifact.relative_to(ctx.root)}')
    assert not failures, '\n'.join(failures)
    print(f'static: {len(sources)} repository modules checked; {unchecked} unproved obligations; reports: runtime/static/')


def verify_bodies(ctx):
    """Annotated bodies and call sites are checked without running: definite mismatches fail,
    possible ones and unanalyzable code are listed as unchecked."""
    def fn(params, body, result=None):
        return {'$call': ['fn', params, body] + ([result] if result else [])}
    ok = {
        'literal result': fn([], 1, 'integer'),
        'parameter result': fn([['x', 'integer']], {'$ref': 'x'}, 'integer'),
        'arithmetic widening': fn([['x', 'integer']], {'$call': ['add', {'$ref': 'x'}, 1.5]}, 'float'),
        'branches': fn([['x', 'boolean']], {'$call': ['if', {'$ref': 'x'}, 1, 'no']}, {'$call': ['union', 'integer', 'string']}),
        'call': {'f': fn([['x', 'integer']], {'$ref': 'x'}, 'integer'), 'v': {'$call': ['f', 2]}},
    }
    for name, source in ok.items():
        code, report = static_report(ctx, json.dumps(source))
        assert code == 0 and report['diagnostics'] == [] and report['unchecked'] == [], (name, report)
    bad = {
        'result mismatch': (fn([], 'a', 'integer'), 'type'),
        'operand mismatch': (fn([['x', 'string']], {'$call': ['add', {'$ref': 'x'}, 1]}), 'type'),
        'argument mismatch': ({'f': fn([['x', 'integer']], {'$ref': 'x'}), 'v': {'$call': ['f', 'no']}}, 'type'),
        'call arity': ({'f': fn([['x', 'integer']], {'$ref': 'x'}), 'v': {'$call': ['f', 1, 2]}}, 'type'),
        'effect in region': ({'$call': ['type_time', {'$call': ['effect', 'read_file', 'x']}]}, 'type'),
    }
    for name, (source, kind) in bad.items():
        code, report = static_report(ctx, json.dumps(source))
        assert code == 1 and report['diagnostics'] and report['diagnostics'][0]['kind'] == kind, (name, report)
    effectful = fn([], {'$call': ['effect', 'clock', None]})
    # A provider factory constructs effectful callbacks in a pure region. Construction
    # and invocation must remain distinct, including named and inline function values.
    factories = [
        {'$call': ['type_time', effectful]},
        {'factory': fn([], {'apply': effectful}), 'value': {'$call': ['type_time', {'$call': ['factory']}]}},
        {'callback': effectful, 'value': {'$call': ['type_time', {'$ref': 'callback'}]}},
    ]
    for source in factories:
        code, report = static_report(ctx, json.dumps(source))
        assert code == 0 and report['diagnostics'] == [] and report['unchecked'] == [], report
    invocations = [
        {'$call': ['type_time', {'$call': ['call', effectful]}]},
        {'callback': effectful, 'value': {'$call': ['type_time', {'$call': ['callback']}]}},
        {'callback': fn(['x'], {'$call': ['effect', 'clock', None]}),
         'value': {'$call': ['type_time', {'$call': ['map', {'$ref': 'callback'}, [1]]}]}},
    ]
    for source in invocations:
        code, report = static_report(ctx, json.dumps(source))
        assert code == 1 and any(d['kind'] == 'type' for d in report['diagnostics']), report
    computed = {'callback': {'$call': ['if', True, effectful, fn([], 42)]},
                'value': {'$call': ['type_time', {'$call': ['callback']}]}}
    code, report = static_report(ctx, json.dumps(computed))
    assert report['diagnostics'] or report['unchecked'], report
    # A union argument may or may not satisfy the parameter: not an error, but not silent.
    code, report = static_report(ctx, json.dumps({'f': fn([['x', 'integer']], {'$ref': 'x'}),
        'g': fn([['y', {'$call': ['union', 'integer', 'string']}], ], {'$call': ['f', {'$ref': 'y'}]})}))
    assert code == 0 and report['diagnostics'] == [] and [d['kind'] for d in report['unchecked']] == ['unchecked'], report


def verify_policy_result_fields(ctx):
    """Plain check catches malformed report fields in the actual development policy."""
    import copy

    original = ctx.root / 'tooling' / 'test_policy.yaml'
    process = ctx.helix('check', str(original.relative_to(ctx.root)))
    report = yaml.safe_load(process.stdout)
    assert process.returncode == 0 and report['mode'] == 'static' and report['diagnostics'] == [], report
    policy = yaml.safe_load(original.read_text())
    destination = ctx.root / 'runtime' / 'static' / 'policy-mutations'
    destination.mkdir(parents=True, exist_ok=True)
    for operation, field, value in [('result', 'status', 17), ('report', 'exit_code', 'zero')]:
        mutated = copy.deepcopy(policy)
        body = mutated[operation]['$call'][2]['$call']
        if operation == 'result':
            assert body[0] == 'set' and body[2] == field, body
            body[3] = value
        else:
            assert body[0] == 'let' and field in body[2], body
            body[2][field] = value
        source = destination / f'{operation}-{field}.yaml'
        source.write_text(yaml.safe_dump(mutated, sort_keys=False))
        process = ctx.helix('check', str(source.relative_to(ctx.root)))
        source.with_suffix('.report.yaml').write_text(process.stdout)
        report = yaml.safe_load(process.stdout)
        assert report['mode'] == 'static', (operation, report)
        relevant = [item for item in report['diagnostics']
                    if item['kind'] == 'type' and item.get('path', '').startswith(f'/{operation}/$call/2')
                    and (field in item['message'] or field in item.get('path', ''))]
        assert process.returncode == 1 and relevant, (operation, field, report)
        assert all(item['line'] > 0 and item['column'] > 0 for item in relevant), relevant


def verify_record_boundaries(ctx):
    """Closed records expose definite field errors without treating uncertainty as failure."""
    def call(actor, *args):
        return {'$call': [actor, *args]}

    def fn(params, body, result):
        return call('fn', params, body, result)

    def record(**fields):
        return call('record', *(item for pair in fields.items() for item in pair))

    row = {'$ref': 'row'}
    key = {'$ref': 'key'}
    incompatible = [
        ('required_field', fn([['row', record(x='integer')]], row, record(x='string'))),
        ('required_missing', fn([['row', record()]], row, record(x='integer'))),
        ('required_extra', fn([['row', record(x='integer')]], row, record())),
    ]
    for name, source in incompatible:
        code, report = static_report(ctx, json.dumps(source))
        assert code == 1 and any(item['kind'] == 'type' and item['path'] == '/$call/2'
                                 for item in report['diagnostics']), (name, report)

    source = fn([['row', 'map'], ['key', 'string']],
                {'bad': 'wrong', 'unknown': call('get', row, key)},
                record(bad='integer', unknown='any'))
    code, report = static_report(ctx, json.dumps(source))
    relevant = [item for item in report['diagnostics']
                if item['kind'] == 'type' and item['path'] == '/$call/2/bad']
    assert code == 1 and relevant, ('unknown sibling must not hide bad field', report)
    assert all(item['line'] > 0 and item['column'] > 0 for item in relevant), relevant

    # Each witness inhabits the overlap or supplies a value the checker cannot know.
    # The literal case also guards against interpreting data as a malformed add call.
    literal_data = {'bad': {'$call': ['add', 1]}}
    possible = [
        ('optional_overlap', fn([['row', record(x=call('optional', 'integer'))]],
                                row, record(x=call('optional', 'string'))), [{}], {}, True),
        ('optional_extra', fn([['row', record(x=call('optional', 'integer'))]],
                              row, record()), [{}], {}, True),
        ('unknown_get_key', fn([['key', 'string']], call('get', {'x': 1}, key), 'integer'),
         ['x'], 1, True),
        ('unknown_set_key', fn([['key', 'string'], ['value', 'any']],
                              call('set', {'x': 1}, key, {'$ref': 'value'}), record(x='integer')),
         ['x', 1], {'x': 1}, True),
        ('literal_boundary', fn([], {'$literal': literal_data}, record(bad='map')),
         [], literal_data, True),
        ('with_field', fn([], {'x': 1, 'y': 'ok'},
                          call('with_field', record(x='integer'), 'y', 'string')),
         [], {'x': 1, 'y': 'ok'}, False),
        ('secret_shape', fn([], {'type': 'secret', 'ref': 'credentials/test'}, 'secret'),
         [], {'type': 'secret', 'ref': 'credentials/test'}, True),
    ]
    for name, function, arguments, expected, uncertain in possible:
        code, report = static_report(ctx, json.dumps(function))
        assert code == 0 and report['diagnostics'] == [], (name, report)
        assert bool(report['unchecked']) == uncertain, (name, report)
        source = call('let', {'produce': function}, call('produce', *arguments))
        process = ctx.helix('run', '-', stdin=json.dumps(source))
        assert process.returncode == 0 and yaml.safe_load(process.stdout)['root'] == expected, (name, process)


def verify_annotation_bounds(ctx):
    """Aliases preserve ordinary proofs; cycles and oversized expansions stay unproved."""
    def call(actor, *args):
        return {'$call': [actor, *args]}

    def ref(name):
        return {'$ref': name}

    def annotated(aliases, annotation, *, identity=False):
        params = [['value', annotation]] if identity else []
        body = ref('value') if identity else 1
        return {**aliases, 'produce': call('fn', params, body, annotation)}

    # Bare strings are data; only explicit references follow lexical aliases.
    for name, annotation, body in [('literal_primitive', 'integer', 1),
                                   ('reference_primitive', ref('integer'), 'ok')]:
        source = {'integer': 'string', 'produce': call('fn', [], body, annotation),
                  'answer': call('produce')}
        code, report = static_report(ctx, json.dumps(source))
        assert code == 0 and report['diagnostics'] == [] and report['unchecked'] == [], (name, report)
        result = ctx.helix('run', '-', stdin=json.dumps(source))
        assert result.returncode == 0 and yaml.safe_load(result.stdout)['root']['answer'] == body, (name, result)
    source = annotated({'Alias': 'integer'}, 'Alias')
    code, report = static_report(ctx, json.dumps(source))
    assert code == 0 and report['diagnostics'] == [] and any(
        item['path'] == '/produce/$call/3' for item in report['unchecked']), report

    aliases = {'alias0': 'integer'}
    aliases.update({f'alias{i}': ref(f'alias{i - 1}') for i in range(1, 80)})
    proved = [('long_alias_chain', annotated(aliases, ref('alias79')))]

    def shared_records(levels, leaf):
        aliases = {'shape0': leaf}
        for level in range(1, levels + 1):
            fields = [part for key in ('a', 'b', 'c', 'd')
                      for part in (key, ref(f'shape{level - 1}'))]
            aliases[f'shape{level}'] = call('record', *fields)
        return aliases, ref(f'shape{levels}')

    ordinary, annotation = shared_records(2, 'integer')
    proved.append(('shared_record_proof', annotated(ordinary, annotation, identity=True)))
    for name, source in proved:
        code, report = static_report(ctx, json.dumps(source))
        assert code == 0 and report['diagnostics'] == [] and report['unchecked'] == [], (name, report)

    large_shape, annotation = shared_records(6, 'integer')
    # 4**4 copies of a 4096-byte literal exceed the expanded string budget,
    # while the descriptor node count and original source remain small.
    large_strings, string_annotation = shared_records(4, call('literal', 'x' * 4096))
    unproved = [
        ('alias_cycle', annotated({'a': ref('b'), 'b': ref('a')}, ref('a'))),
        ('constructor_cycle', annotated({'a': call('list_of', ref('a'))}, ref('a'))),
        ('expanded_shape_budget', annotated(large_shape, annotation)),
        ('expanded_string_budget', annotated(large_strings, string_annotation)),
    ]
    for name, source in unproved:
        code, report = static_report(ctx, json.dumps(source))
        assert code == 0 and report['diagnostics'] == [], (name, report)
        assert any(item['path'] == '/produce/$call/3' and 'outside the supported' in item['message']
                   for item in report['unchecked']), (name, report)


def verify_computed_heads(ctx):
    """Computed heads contain source bodies/regions, with the same literal and effect boundaries."""
    bad_function = {'$call': ['fn', [], 'wrong', 'integer']}
    examples = [
        ({'$call': [bad_function]}, '/$call/0/$call/2'),
        ({'$call': [{'$call': ['if', True, {'$call': ['fn', [], 42]}, bad_function]}]},
         '/$call/0/$call/3/$call/2'),
        ({'$call': [{'$call': ['type_time', {'$call': ['effect', 'time', None]}]}]},
         '/$call/0/$call/1'),
    ]
    for source, path in examples:
        code, report = static_report(ctx, json.dumps(source))
        assert code == 1 and any(d['kind'] == 'type' and d['path'] == path for d in report['diagnostics']), report
    for source in (
        {'$literal': examples[0][0]},
        {'$call': [{'$literal': bad_function}]},
        {'$call': [{'$call': ['fn', [], {'$call': ['div', 1, 0]}]}]},
        {'$call': [{'$call': ['import', 'does-not-exist.yaml']}]},
    ):
        code, report = static_report(ctx, json.dumps(source))
        assert code == 0 and report['diagnostics'] == [] and report['unchecked'] == [], report


def verify_numeric_enum_overlap(ctx):
    """Equal numeric representations overlap, without proving every value fits."""
    def call(actor, *args):
        return {'$call': [actor, *args]}

    def fn(params, body, result):
        return call('fn', params, body, result)

    literal = lambda value: call('literal', value)
    examples = [
        ('float_return_integer_literal', 1.0, literal(1)),
        ('integer_return_float_literal', 1, literal(1.0)),
        ('float_return_mixed_enum', 1.0, call('enum', 1, 'ok')),
        ('fractional_return', 1.5, literal(1.5)),
    ]
    sources = [(name, call('let', {'produce': fn([], value, annotation)}, call('produce')), value)
               for name, value, annotation in examples]
    sources.append(('enum_argument_to_float', call('let', {
        'consume': fn([['x', 'float']], {'$ref': 'x'}, 'float'),
        'produce': fn([['x', literal(1)]], call('consume', {'$ref': 'x'}), 'float'),
    }, call('produce', 1.0)), 1.0))
    for name, source, expected in sources:
        text = json.dumps(source)
        code, report = static_report(ctx, text)
        assert code == 0 and report['diagnostics'] == [], (name, report)
        assert any('may not be' in item['message'] for item in report['unchecked']), (name, report)
        # An actual accepted execution witnesses overlap independently of static inference.
        process = ctx.helix('run', '-', stdin=text)
        assert process.returncode == 0 and yaml.safe_load(process.stdout)['root'] == expected, (name, process)

    # Keep definite mismatches loud: equality does not coerce strings or rounded integers.
    for value, annotation in [('1', literal(1)), (1, literal(1.5)),
                              (9007199254740992.0, literal(9007199254740993))]:
        source = fn([], value, annotation)
        code, report = static_report(ctx, json.dumps(source))
        assert code == 1 and any(item['kind'] == 'type' for item in report['diagnostics']), report


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
    assert formatted.returncode == 0, formatted
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
        {'method': 'textDocument/didOpen', 'params': {'textDocument': {'uri': uri, 'version': 1, 'text': '{"😀": {"$call": ["add", 1]}}'}}},
        {'method': 'textDocument/didChange', 'params': {'textDocument': {'uri': uri, 'version': 2}, 'contentChanges': [{'text': '{"$call": ["add", 20, 22]}'}]}},
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
    assert diagnostic['code'] == 'arity' and diagnostic['range']['start']['character'] == 7, diagnostic
    assert replies[3]['params']['diagnostics'] == [], replies
    formatted = replies[4]['result'][0]['newText']
    assert ctx.helix('exec', formatted).stdout == '42\n', formatted
    assert replies[5]['error']['code'] == -32601, replies
    assert replies[6]['params']['diagnostics'] == [] and replies[7]['result'] is None, replies
    for bad in (b'Content-Length: 99999999\r\n\r\n', b'Content-Length: 3\r\n\r\nxx', b'Content-Length: 1\r\n\r\nx'):
        result = subprocess.run([str(ctx.binary), 'lsp'], input=bad, capture_output=True, cwd=ctx.root, timeout=ctx.timeout)
        assert result.returncode != 0 and not result.stdout, result
