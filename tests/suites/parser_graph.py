"""Parent-link and span consequences through the public serialized-state boundary."""
import yaml

from tests.suites.contracts import checked


def verify(h):
    scopes = [
        ('x: 1\ninner: {x: 2, y: [add, x, 3]}', {'x': 1, 'inner': {'x': 2, 'y': 5}}),
        ('inner: {y: [add, x, 3]}\nx: 2', {'inner': {'y': 5}, 'x': 2}),
        ('x: 9\nxs: [{x: 2, y: [add, x, 3]}, {y: [add, x, 1]}]',
         {'x': 9, 'xs': [{'x': 2, 'y': 5}, {'y': 10}]}),
        ('x: 9\nr: [let, {x: 2}, [let, {y: [add, x, 1]}, [add, x, y]]]', {'x': 9, 'r': 5}),
    ]
    for source, expected in scopes:
        assert checked(h, source) == expected
    for source, path in [
        ('outer:\n  xs:\n    - {r: [add, missing, 1]}\n', '/outer/xs/0/r/1'),
        ('"雪": {r: [add, missing, 1]}', '/雪/r/1'),
        ('r: [if, true, [not, missing], false]', '/r/2/1'),
    ]:
        result = h.helix('run', '-', stdin=source)
        assert h.malformed_state(result.stdout, result.returncode) is None
        error = yaml.safe_load(result.stdout)['error']
        line = next(i for i, text in enumerate(source.splitlines(), 1) if 'missing' in text)
        column = source.splitlines()[line - 1].index('missing') + 1
        assert (error['kind'], error['path'], error['line'], error['column']) == ('unbound', path, line, column), error
    scalars = [
        ('null', None), ('~', None), ('TRUE', True), ('false', False),
        ('"true"', 'true'), ("'1'", '1'), ('1.25', 1.25), ('-2', -2),
        ('"line\\nbreak"', 'line\nbreak'), ('|+\n  a\n\n', 'a\n\n'),
        ('>-\n  a\n  b\n', 'a b'), ('"\\u96ea"', '雪'),
    ]
    for source, expected in scalars:
        root = checked(h, source)
        assert type(root) is type(expected) and root == expected, (source, root)
    for source, kind, line, column in [
        ('a: 1\na: 2', 'parse', 2, 1),
        ('a: &a 1\nb: *a', 'unsupported', 2, 4),
        ('? [a, b]\n: 1', 'unsupported', 1, 3),
    ]:
        result = h.helix('run', '-', stdin=source)
        error = yaml.safe_load(result.stdout)['error']
        assert (error['kind'], error['line'], error['column']) == (kind, line, column), error
