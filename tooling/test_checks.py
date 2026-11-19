"""Run reusable std:test assertions over observations captured by a host."""
from tooling.test_reports import failure
from tooling.test_runner import dump_yaml, load_yaml


def compare(context, observations):
    """Return failed named equalities; process/state failures are host errors."""
    names = [row['name'] for row in observations]
    if not names or len(names) != len(set(names)):
        raise ValueError('comparison names must be nonempty and unique')
    source = dump_yaml({'$call': ['let', {
        't': {'$call': ['import', 'std:test']},
    }, {'$call': ['t.table', {'$literal': [
        {'name': row['name'], 'input': {'actual': row['actual'], 'expected': row['expected']}}
        for row in observations
    ]}, {'$call': ['fn', ['row'], {'$call': ['t.equal',
        {'$call': ['get', {'$ref': 'row'}, 'actual']},
        {'$call': ['get', {'$ref': 'row'}, 'expected']},
    ]}]}]}]})
    result = context.helix('run', '-', stdin=source)
    problem = context.malformed_state(result.stdout, result.returncode)
    if problem is None and (result.returncode != 0 or result.stderr):
        problem = 'comparison process did not finish cleanly'
    if problem is None:
        report = load_yaml(result.stdout)['root']
        rows = report.get('results') if isinstance(report, dict) else None
        if (not isinstance(rows, list) or len(rows) != len(names)
                or any(not isinstance(row, dict) or row.get('name') != name
                       or row.get('outcome') not in {'passed', 'failed'}
                       for row, name in zip(rows, names))):
            problem = 'comparison process returned an invalid std:test report'
        elif any(type(report.get(key)) is not int or report[key] != count for key, count in (
                ('total', len(rows)), ('passed', sum(row['outcome'] == 'passed' for row in rows)),
                ('failed', sum(row['outcome'] == 'failed' for row in rows)))):
            problem = 'comparison report counts do not match its results'
    if problem is not None:
        raise RuntimeError(failure(context.root, context.root / 'std/test.yaml',
                                   'assertions', problem, 'run', result, source=source))
    return [row for row in rows if row['outcome'] == 'failed']
