"""Workbook lessons: every solution passes, every starter fails its task, every prose example behaves as labelled.

Grading itself is `editor/web/workbook.py`, run here through the same binary the editor server uses.
"""
import importlib.util
import json


def load(ctx):
    path = ctx.root / "editor" / "web" / "workbook.py"
    spec = importlib.util.spec_from_file_location("workbook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(ctx):
    workbook = load(ctx)

    def run(args, stdin):
        done = ctx.helix(*args, stdin=stdin)
        return done.returncode, done.stdout, done.stderr

    failures = []
    chapters = workbook.chapters()
    assert chapters, "no workbook chapters found"
    for chapter in chapters:
        for step in chapter["steps"]:
            where = f"{chapter['file']}:{step['id']}"
            if "solution" in step:
                verdict = workbook.grade(step, step["solution"], run)
                if not verdict["passed"]:
                    failures.append(f"{where}: solution does not pass: {json.dumps(verdict)}")
            if "expect" in step:
                assert "solution" in step, f"{where}: graded step needs a solution"
                if workbook.grade(step, step["start"], run)["passed"]:
                    failures.append(f"{where}: the starter already passes, so the task teaches nothing")
            for kind, source in workbook.examples(step):
                code, out, err = run(["exec", "--json", "-"], source)
                if (kind == "helix") != (code == 0):
                    failures.append(f"{where}: `{kind}` example {'fails' if code else 'runs'}: {source.strip()[:60]!r} {err.strip()[:120]}")
    return failures
