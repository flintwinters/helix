"""Safe YAML acceleration and pure-Python fallback contracts for state validation."""
from time import perf_counter
from unittest.mock import patch

import yaml


def verify(h):
    samples = [
        ("status: finished\nroot: {text: '雪', items: [null, true, 1, 1.5]}", 0, True),
        ("status: error\nerror: {kind: type, message: bad, line: 1, column: 2}", 1, True),
        ("status: error\nerrors: [{kind: type, message: bad, line: 1, column: 2}]", 1, True),
        ("status: finished\nroot: null", 1, False),
        ("status: ready", 0, False),
        ("status: error\nerror: {}", 1, False),
        ("[", 0, False),
        ("status: finished\nroot: !!python/object:unknown {}", 0, False),
        ("status: finished\nroot: 1\n---\nstatus: finished\nroot: 2", 0, False),
    ]
    for text, code, valid in samples:
        assert (h.malformed_state(text, code) is None) == valid, text
    # Exercise an installation without LibYAML even when the extension is present.
    with patch.object(yaml, "CSafeLoader", yaml.SafeLoader, create=True):
        del yaml.CSafeLoader
        for text, code, valid in samples:
            assert (h.malformed_state(text, code) is None) == valid, text

    # Both safe loaders must assign the same meaning to the actual state corpus.
    accelerated = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    accelerated_seconds = python_seconds = 0.0
    for directory in h.commands:
        for expected in sorted(directory.glob("*.expected.yaml")):
            source = expected.read_text()
            started = perf_counter()
            actual = yaml.load(source, Loader=accelerated)
            accelerated_seconds += perf_counter() - started
            started = perf_counter()
            reference = yaml.safe_load(source)
            python_seconds += perf_counter() - started
            assert actual == reference, expected
    print(f"YAML corpus: {accelerated.__name__} {accelerated_seconds:.3f}s; "
          f"SafeLoader {python_seconds:.3f}s")
