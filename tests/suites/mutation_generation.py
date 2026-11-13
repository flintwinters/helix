"""Independent mutation-policy parity and generator boundary contracts."""
from dataclasses import replace
import subprocess
import time

import yaml


def reference_plan(source):
    """Independent scalar spans from Python's line boundaries, including order/duplicates."""
    plan = []
    start = 0
    for line in source.splitlines(keepends=True):
        end = start + len(line)
        plan.extend(([[0, start]], [[0, start], [end, len(source)]]))
        start = end
    plan.extend([[0, index]] for index in range(0, len(source), 5))
    return plan


def reference(source):
    """Materialize the same mutation contract for host-generator parity checks."""
    return ["".join(source[start:end] for start, end in spans) for spans in reference_plan(source)]


def verify(h):
    sources = ["", "a", "abcde", "abcdef", "\n", "\n\n", "a: 1\nb: 2\n",
               "🙂é中e\u0301xyz", 'value: "[raise, hostile]"\n', "a\r\nb\rc\n"]
    sources.extend("a" + separator + "b" + separator for separator in
                   ("\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"))
    sources.append("".join(f"field_{index}: {index}\n" for index in range(100)))
    sources.extend(("a" * 255 + "\r\n" + "b" * 257,
                    "🙂" * 255 + "\u2028" + "é" * 257,
                    "a" * 256 + "\r\n" + "b" * 256))
    # Lookahead may reach EOF, and chunk positions count scalars rather than
    # UTF-8 bytes even when CRLF straddles the boundary.
    sources.extend(("🙂" * 255 + "\r", "🙂" * 255 + "\r\n" + "é" * 256 + "\r"))
    from tooling.test_runner import discover_cases
    largest = max(discover_cases(h.commands, h.root), key=lambda case: case.stat().st_size)
    sources.append(largest.read_text())
    for index, source in enumerate(sources):
        started = time.perf_counter()
        try:
            actual = list(h.mutations(source))
        except RuntimeError as error:
            raise AssertionError(f"mutation generation failed for {len(source)} characters: {source[:80]!r}\n{error}") from error
        expected = reference(source)
        assert actual == expected, (repr(source[:100]), actual[:10], expected[:10])
        if index == len(sources) - 1:
            print(f"mutation_generation: fixture={largest.relative_to(h.root)} "
                  f"characters={len(source)} variants={len(actual)} seconds={time.perf_counter() - started:.3f}")

    # A broken generator must fail the harness, never silently skip robustness.
    def planned_mutations(source, plan):
        def planned(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, yaml.safe_dump({"status": "finished", "root": plan}), "")
        return list(replace(h, process_runner=planned).mutations(source))

    assert planned_mutations("", []) == []
    coverage_source = "α\r\nbeta\n中tail"
    complete = reference_plan(coverage_source)
    # Coverage concerns source intervals, not a canonical serialization or order.
    alternative = []
    for spans in reversed(complete):
        segmented = [[0, 0]]
        for start, end in spans:
            midpoint = (start + end) // 2
            segmented.extend(([start, midpoint], [midpoint, midpoint], [midpoint, end]))
        alternative.append(segmented)
    assert planned_mutations(coverage_source, alternative) == list(reversed(reference(coverage_source)))
    assert planned_mutations("abcde", [[], [], []]) == ["", "", ""]
    # Total count and unique work matter; duplicate frequencies remain policy's
    # parity contract. The empty prefix is still supplied by offset zero.
    redistributed = [complete[1], *complete[1:]]
    assert planned_mutations(coverage_source, redistributed) == [
        "".join(coverage_source[start:end] for start, end in spans) for spans in redistributed]

    invalid_coverage = [
        ("empty", []),
        ("truncated", complete[:-1]),
        ("identity only", [[[0, len(coverage_source)]]] * len(complete)),
    ]
    # The first line deletion has a unique positional meaning in this source.
    missing = [list(spans) for spans in complete]
    missing[1] = complete[0]
    invalid_coverage.append(("unique deletion omitted", missing))
    for label, spans in (("out of category", [[1, 2]]),
                          ("overlapping spans", [[0, 3], [2, 4]]),
                          ("reordered spans", [[3, 5], [0, 2]])):
        invalid_coverage.append((label, [spans, *complete[1:]]))
    for label, plan in invalid_coverage:
        try:
            planned_mutations(coverage_source, plan)
        except (RuntimeError, ValueError):
            pass
        else:
            raise AssertionError(f"generator accepted incomplete mutation coverage: {label}")

    # Equal emitted strings do not substitute for distinct deletion positions.
    repeated_source = "aa\naa\n"
    positional = reference_plan(repeated_source)
    positional[1] = positional[3]
    try:
        planned_mutations(repeated_source, positional)
    except (RuntimeError, ValueError):
        pass
    else:
        raise AssertionError("generator accepted a missing deletion hidden by repeated source text")
    responses = [(0, "", ""), (0, "[", ""), (0, "status: finished\nroot: 42\n", ""),
                 (0, "status: finished\nroot: [1]\n", ""), (-11, "", ""), (124, "", ""),
                 (1, "status: error\nerror: {kind: type, message: bad, line: 1, column: 1}\n", ""),
                 (0, "status: finished\nroot: []\n", "unexpected stderr")]
    for plan in ([1], [[1]], [[[0]]], [[[0, 1, 2]]], [[[False, 1]]],
                 [[[-1, 1]]], [[[2, 1]]], [[[0, 99]]], [[[0, "1"]]]):
        responses.append((0, yaml.safe_dump({"status": "finished", "root": plan}), ""))
    for code, output, stderr in responses:
        def fake(args, **kwargs):
            return subprocess.CompletedProcess(args, code, output, stderr)
        broken = replace(h, process_runner=fake)
        try:
            list(broken.mutations("a: 1\n"))
        except (RuntimeError, ValueError):
            pass
        else:
            raise AssertionError(f"generator failure accepted: {code}, {output!r}")

    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    try:
        list(replace(h, process_runner=timeout).mutations("a: 1\n"))
    except (RuntimeError, ValueError):
        pass
    else:
        raise AssertionError("generator timeout accepted")
