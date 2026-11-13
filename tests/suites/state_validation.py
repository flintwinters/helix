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
    error = {"kind": "type", "message": "bad", "line": 1, "column": 2,
             "payload": {"arbitrary": [None, True, 1.5]}, "path": ["field", 0]}
    # The single-error and collected-error forms share the diagnostic contract;
    # extra diagnostic data and arbitrary partial roots are not schema errors.
    for key in ("error", "errors"):
        def error_state(diagnostic):
            return {"status": "error", key: diagnostic if key == "error" else [diagnostic],
                    "root": {"retained": [None, {"value": "雪"}]}}

        samples.append((yaml.safe_dump(error_state(error)), 1, True))
        for field, invalid in (
            ("kind", (None, 1, [], {})), ("message", (None, True, [], {})),
            ("line", (None, True, False, 1.0, 0, -1, "1")),
            ("column", (None, True, False, 2.0, 0, -1, "2")),
        ):
            for value in invalid:
                samples.append((yaml.safe_dump(error_state({**error, field: value})), 1, False))
            missing = {name: value for name, value in error.items() if name != field}
            samples.append((yaml.safe_dump(error_state(missing)), 1, False))
        for invalid in (None, "bad", 42, []):
            samples.append((yaml.safe_dump(error_state(invalid)), 1, False))
    for invalid in ([], {}, "bad", [error, {**error, "line": True}]):
        samples.append((yaml.safe_dump({"status": "error", "errors": invalid}), 1, False))
    # Supplying both channels cannot hide malformed diagnostics in either one.
    samples.append((yaml.safe_dump({"status": "error", "error": error,
                                   "errors": [error]}), 1, True))
    samples.append((yaml.safe_dump({"status": "error", "error": {**error, "message": None},
                                   "errors": [error]}), 1, False))
    for invalid in ([], None, [{**error, "column": False}]):
        samples.append((yaml.safe_dump({"status": "error", "error": error,
                                       "errors": invalid}), 1, False))

    pending = {"id": 0, "operation": "time", "payload": None}
    for payload in (None, 42, "data", [True], {"arbitrary": [1, None]}):
        samples.append((yaml.safe_dump({"status": "signaled", "root": None,
                                       "pending_effect": {**pending, "payload": payload}}), 1, True))
    for field, invalid in (("id", (None, True, False, 0.0, -1, "0")),
                           ("operation", (None, True, 42, [], {}))):
        for value in invalid:
            samples.append((yaml.safe_dump({"status": "signaled",
                                           "pending_effect": {**pending, field: value}}), 1, False))
    for field in pending:
        samples.append((yaml.safe_dump({"status": "signaled", "pending_effect": {
            name: value for name, value in pending.items() if name != field}}), 1, False))
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
