"""Versioned coding workloads; independent Python oracles stay out of model prompts.

Theme modules register each task with `task`: its prompt-visible goal and example,
a named edge case and a fixed-seed generator, decorating the Python oracle. The
Helix reference lives in references/NAME.yaml. `TASKS` holds only prompt-visible
data and references; oracles and generators stay private to the harness.
"""
import hashlib
import random
from pathlib import Path


VERSION = 3
ROOT = Path(__file__).resolve().parent
SIZES = (0, 1, 8, 64, 256)
TASKS, _ORACLES, _CASES = {}, {}, {}


def task(name, goal, example, edge, generate):
    """Register a task; `generate(rng, size)` yields at most `size` input entries."""
    def register(oracle):
        if name in TASKS:
            raise ValueError(f'duplicate task {name}')
        TASKS[name] = {'goal': goal, 'example': example,
                       'reference': (ROOT / 'references' / f'{name}.yaml').read_text()}
        _ORACLES[name], _CASES[name] = oracle, (edge, generate)
        return oracle
    return register


def oracle(task, values):
    if task not in _ORACLES:
        raise ValueError(f'unknown task {task}')
    return _ORACLES[task](values)


def inputs(task):
    """Named edge case plus fixed-seed scaling inputs; timing uses the largest."""
    edge, generate = _CASES[task]
    yield 'edge', edge
    for size in SIZES:
        yield f'n={size}', generate(random.Random(714209 + size), size)


def source_digest():
    """Digest of every task source; replay requires the definitions that scored a run."""
    sha = hashlib.sha256()
    for path in sorted(p for p in ROOT.rglob('*') if p.suffix in ('.py', '.yaml')):
        sha.update(f'{path.relative_to(ROOT).as_posix()}\0{path.read_text()}\0'.encode())
    return sha.hexdigest()


from tooling.agent_tasks import basics, text, records, formats, infra, business  # noqa: E402,F401  registration order
