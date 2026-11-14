"""Source-search acceptance, exclusively through manage.py test --suite search."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import yaml


def records(result, code=0):
    assert result.returncode == code, (result.returncode, result.stderr[-4000:])
    return [json.loads(line) for line in result.stdout.splitlines()]


def invoke(h, body, bindings=None):
    program = {"$call": ["let", {"s": {"$call": ["import", "std:search"]},
                                 "src": {"$call": ["import", "std:source"]}, **(bindings or {})}, body]}
    result = h.helix("exec", "--json", yaml.safe_dump(program))
    assert result.returncode == 0, (result.returncode, result.stderr[-4000:])
    return json.loads(result.stdout)


def verify(h):
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="search-") as directory:
        root = Path(directory)
        module = root / "math.yaml"
        module.write_text('''# searchable arithmetic 雪
sum: {$call: [fn, [[x, {$ref: integer}]], {$call: [add, {$ref: x}, 1]}, {$ref: integer}]}
quoted: {$literal: {$call: [fn, [], 7]}}
''')
        (root / "README.md").write_text("Searchable docs 雪\n")
        (root / "target").mkdir()
        (root / "target" / "hidden.yaml").write_text("[")
        (root / "loop").symlink_to(root, target_is_directory=True)
        start = time.monotonic()
        index = records(h.helix("search", "index", str(root), "--no-std"))
        assert index[-1]["complete"] and index[-1]["sources"] == 2, index[-1]
        exposed = records(h.helix("search", "index", str(root), "--no-std", "--no-default-excludes"), 1)
        assert not exposed[-1]["complete"], exposed[-1]
        dedup = records(h.helix("search", "index", str(module), str(module), "--no-std", "--library", "std:math", "--library", "std:math"))
        assert dedup[-1]["sources"] == 2, dedup[-1]

        functions = [r for r in index if r["kind"] == "function"]
        assert len(functions) == 1 and functions[0]["name"] == "sum", functions
        assert functions[0]["types"]["declared"]["params"] == ["integer"], functions
        assert any(r["kind"] == "documentation" for r in index), index
        source = next(r for r in index if r["kind"] == "source")
        assert any(n["kind"] == "literal" for n in source["nodes"]), source
        assert not any(n["kind"] == "function" and "quoted" in n["reference"]["path"] for n in source["nodes"])
        parsed = records(h.helix("search", "parse", "-", stdin='"1": ["雪"]'))[0]
        assert parsed["nodes"][-1]["reference"]["path"] == ["1", 0], parsed
        records(h.helix("search", "parse", "-", stdin="["), 1)
        (root / "bad.yaml").write_text("[")
        broken = records(h.helix("search", "index", str(root), "--no-std"), 1)
        assert any(r["kind"] == "diagnostic" for r in broken) and not broken[-1]["complete"]
        (root / "bad.yaml").unlink()
        # A real multistage pipe preserves diagnostic/summary control records.
        query = root / "query.yaml"
        query.write_text('''$call: [let, {s: {$call: [import, 'std:search']}}, {$call: [fn, [record], {$call: [s.keep, {$ref: record}, {$ref: s.functions}]}]}]
''')
        result = subprocess.run(["bash", "-o", "pipefail", "-c",
                                 '"$1" search index "$2" --no-std | "$1" exec --stream --file "$3" | "$1" exec --stream --file "$3"',
                                 "helix-search", str(h.binary), str(module), str(query)],
                                capture_output=True, text=True, cwd=h.root, timeout=60)
        pipeline = records(result)
        assert [r["kind"] for r in pipeline] == ["function", "summary"], pipeline
        # Installed-style invocation does not need repository sources or a current checkout.
        external = records(h.helix("search", "index", str(module), "--library", "std:math", "--no-std", cwd=root))
        assert any(r.get("source") == "std:math" for r in external), external[-1]
        print(json.dumps({"measurement": "small_project", "seconds": round(time.monotonic()-start, 3), "records": len(index)}))
        verify_queries(h, source)
        verify_audit(h, root)
        verify_streaming(h, root)
        verify_scale(h, root)
        verify_production_query(h, root)
        verify_standard_stream(h, root)


def verify_queries(h, source):
    r = lambda name: {"$ref": name}
    c = lambda name, *args: {"$call": [name, *args]}
    literal = lambda value: {"$literal": value}
    bindings = {"data": literal(source)}
    function = next(n for n in source["nodes"] if n["kind"] == "function")
    pattern = {"tag": "call", "head": {"tag": "exact", "value": "add"},
               "args": [{"tag": "capture", "name": "input"}, {"tag": "exact", "value": 1}]}
    result = invoke(h, c("s.match", r("data"), literal(function["body"]), literal(pattern)), bindings)
    assert result["matched"] and result["captures"]["input"]["value"] == {"$ref": "x"}, result
    assert result["captures"]["input"]["location"]["line"] == 2, result
    result = invoke(h, c("s.match", r("data"), literal({"source": source["source"], "path": ["quoted"]}), literal({"tag": "mapping", "fields": {}})), bindings)
    assert not result["matched"], result
    for regex_pattern, text, count in [("雪", "a雪b", 1), ("(?P<n>[0-9]+)", "x12y", 1), ("z", "abc", 0)]:
        result = invoke(h, c("s.regex", regex_pattern, text))
        assert len(result) == count, result
    result = h.helix("exec", '{$call: [regex_find, "[", text]}')
    assert result.returncode == 1 and "regex" in result.stderr, result
    relation = invoke(h, c("s.result_type", literal({"types": function["types"]}), "integer"))
    assert relation["status"] == "matches", relation
    candidates = invoke(h, c("s.select_bodies", r("data"), literal(pattern)), bindings)
    assert len(candidates["selection"]) == 1, candidates.get("selection")
    expressions = invoke(h, c("s.result_matches", r("data"), literal(function), literal(pattern)), bindings)
    assert expressions[0]["match"]["matched"], expressions
    repeated = {"tag": "sequence", "items": [{"tag": "capture", "name": "same"}, {"tag": "capture", "name": "same"}]}
    for values, expected in [([1, 1.0], True), ([1, 2], False)]:
        fixture = {"code": values, "nodes": [], "source": "<test>"}
        result = invoke(h, c("s.match", literal(fixture), literal({"source": "<test>", "path": []}), literal(repeated)))
        assert result["matched"] == expected, result
    summary = {"version": 1, "kind": "summary", "complete": False, "sources": 3}
    assert invoke(h, c("s.project", literal(summary), [])) == summary
    projected = invoke(h, c("s.project", literal(function), []))
    assert projected["types"] == function["types"] and projected["effects"] == function["effects"], projected
    reach = invoke(h, c("s.reachable", literal([{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]), "a", 10))
    assert reach["complete"] and reach["visited"] == ["a", "b"], reach


def verify_audit(h, root):
    dependency = root / "dependency.yaml"
    dependency.write_text('run: {$call: [fn, [], {$call: [effect, time, {}]}]}\n')
    main = root / "audit.yaml"
    main.write_text('''dep: {$call: [import, dependency.yaml]}
run: {$call: [fn, [], {$call: [dep.run]}]}
dynamic: {$call: [fn, [f], {$call: [call, {$ref: f}]}]}
recursive: {$call: [fn, [], {$call: [recursive]}]}
''')
    indexed = records(h.helix("search", "index", str(main), "--no-std"))
    functions = {r["name"]: r for r in indexed if r["kind"] == "function"}
    assert functions["run"]["effects"]["state"] == "requests", functions
    assert functions["run"]["effects"]["at"]["source"] == str(dependency), functions
    assert functions["dynamic"]["effects"]["state"] == "unresolved", functions
    assert functions["recursive"]["effects"]["state"] == "none_found", functions
    assert any(r["kind"] == "call" and r.get("target", {}).get("source") == str(dependency) for r in indexed if isinstance(r.get("target"), dict)), indexed



def verify_streaming(h, root):
    query = root / "identity.yaml"
    query.write_text('{$call: [fn, [x], [{$ref: x}]]}')
    process = subprocess.Popen([str(h.binary), "exec", "--stream", "--file", str(query)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=root)
    try:
        process.stdin.write('{"first":true}\n')
        process.stdin.flush()
        import selectors
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(timeout=10), "stream did not emit before EOF"
        assert json.loads(process.stdout.readline()) == {"first": True}
        selector.close()
        process.stdin.close()
        assert process.wait(timeout=10) == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def verify_scale(h, root):
    generated = root / "large"
    generated.mkdir()
    for index in range(24):
        (generated / f"module-{index}.yaml").write_text(yaml.safe_dump({
            f"f{n}": {"$call": ["fn", [["x", {"$ref": "integer"}]],
                                {"$call": ["add", {"$ref": "x"}, n]}, {"$ref": "integer"}]}
            for n in range(8)}))
    for name, args in [("generated", [str(generated), "--no-std"]),
                       ("production", ["--library", "std:list", "--library", "std:map", str(generated / "module-0.yaml"), "--no-std"])]:
        start = time.monotonic()
        result = h.helix("search", "index", *args)
        rows = records(result)
        assert rows[-1]["complete"], rows[-1]
        print(json.dumps({"measurement": name, "seconds": round(time.monotonic()-start, 3),
                          "sources": rows[-1]["sources"], "output_bytes": len(result.stdout.encode()),
                          "records": len(rows)}))


def verify_production_query(h, root):
    c = lambda name, *args: {"$call": [name, *args]}
    r = lambda name: {"$ref": name}
    pattern = {"tag": "call", "head": {"tag": "exact", "value": "if"},
               "args": [{"tag": "wildcard"}] * 3}
    query = root / "production-query.yaml"
    query.write_text(yaml.safe_dump(c("let", {"s": c("import", "std:search")},
        c("fn", ["record"], c("if", c("eq", r("record.kind"), "source"),
          [c("s.select_functions", c("s.select_bodies", r("record"), pattern),
             c("fn", ["candidate"], c("and", c("s.signature_matches", r("candidate"), "integer|float"),
                c("gt", c("len", c("s.calls", r("record"), "neg")), 0))))], [])))))
    start = time.monotonic()
    result = subprocess.run(["bash", "-o", "pipefail", "-c",
        '"$1" search index "$2" --no-std | "$1" exec --stream --file "$3"',
        "helix-production-query", str(h.binary), str(h.root / "std" / "math.yaml"), str(query)],
        capture_output=True, text=True, cwd=root, timeout=60)
    selected = records(result)
    assert selected and any(n["reference"]["path"] == ["abs"] for n in selected[0]["selection"]), selected
    print(json.dumps({"measurement": "production_query", "seconds": round(time.monotonic()-start, 3),
                      "selected": len(selected[0]["selection"])}))


def verify_standard_stream(h, root):
    empty = root / "empty"
    empty.mkdir()
    query = root / "all-functions.yaml"
    query.write_text((h.root / "examples" / "search" / "functions.yaml").read_text())
    start = time.monotonic()
    result = subprocess.run(["bash", "-o", "pipefail", "-c",
        '"$1" search index "$2" | "$1" exec --stream --file "$3"',
        "helix-standard-stream", str(h.binary), str(empty), str(query)],
        capture_output=True, text=True, cwd=root, timeout=120)
    rows = records(result)
    assert rows[-1]["complete"] and any(row.get("source") == "std:search" for row in rows), rows[-1:]
    print(json.dumps({"measurement": "embedded_library_stream", "seconds": round(time.monotonic()-start, 3), "records": len(rows)}))
