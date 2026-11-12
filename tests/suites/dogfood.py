"""Host discovery and process checks; std:test owns assertions and report policy."""
import json
import tempfile
from pathlib import Path

import yaml


def execute(h, source, artifact):
    # Import from an explicit path so nested test-module imports remain file-relative.
    program = (f'{{"t": {{"$call": ["import", "std:test"]}}, "cases": {{"$call": ["import", {json.dumps(str(source))}]}}, "report": {{"$call": ["t.require_pass", {{"$ref": "cases.report"}}]}}}}')
    artifact.with_suffix('.input.yaml').write_text(program)
    result = h.helix('run', '-', stdin=program)
    artifact.with_suffix('.state.yaml').write_text(result.stdout)
    why = h.malformed_state(result.stdout, result.returncode)
    assert why is None, f'{source}: {why}; stderr={result.stderr}'
    return result, yaml.safe_load(result.stdout)


def verify(h):
    destination = h.root / 'runtime' / 'dogfood'
    destination.mkdir(exist_ok=True)
    sources = sorted((h.root / 'tests' / 'dogfood').rglob('*.yaml'))
    assert sources, 'no dogfooded test modules discovered'
    failures = []
    for index, source in enumerate(sources):
        artifact = destination / f'{index}-{source.stem}'
        result, state = execute(h, source, artifact)
        if result.returncode:
            error = state.get('error', {})
            report = error.get('data', {})
            rejected = [row for row in report.get('results', []) if row.get('outcome') == 'failed']
            examples = list(dict.fromkeys(
                f'{row.get("name")}: {row.get("error", {}).get("message", "rejected")}'
                for row in rejected))[:5]
            summary = '; '.join(examples) or f'{error.get("kind")}: {error.get("message")}'
            failures.append(f'{source.relative_to(h.root)}: {len(rejected)} failed; {summary}; '
                            f'state: {artifact.with_suffix(".state.yaml")}; '
                            f'replay: ./manage.py run {artifact.with_suffix(".input.yaml")}')
        else:
            report = state['root']['report']
            print(f'dogfood: {source.relative_to(h.root)}: {report["passed"]}/{report["total"]} passed')
    # Exercise the real bridge against failure, malformed modules, and missing reports.
    with tempfile.TemporaryDirectory(dir=destination, prefix='runner-') as directory:
        source = Path(directory) / 'probe.yaml'
        for program, kind in [
            ('{"t": {"$call": ["import", "std:test"]}, "report": {"$call": ["t.suite", [{"name": "failure", "run": {"$call": ["fn", [], {"$call": ["t.equal", 1, 2]}]}}, {"name": "later", "run": {"$call": ["fn", [], 42]}}]]}}', 'test_failure'),
            ('value: 42', 'unbound'),
            ('[', 'parse'),
        ]:
            source.write_text(program)
            result, state = execute(h, source, Path(directory) / 'result')
            assert result.returncode == 1 and state['error']['kind'] == kind, state
            if kind == 'test_failure':
                report = state['error']['data']
                assert report['failed'] == report['passed'] == 1, report
                assert report['results'][1]['value'] == 42, report
    assert not failures, '\n'.join(failures)
