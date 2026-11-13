"""Explicit, offline conversion of legacy Helix source; never used by execution."""
from pathlib import Path
import re
import math

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

FUNCTION_RESULTS = {"fn", "closure", "get", "if", "cond", "let", "call", "fold", "until", "expect", "type_time", "try", "finally"}
# Historical forms stay recognizable after their runtime actors are retired.
RETIRED_FORMS = {"closure": "let"}

MARKER = "# Helix explicit syntax; migrated from legacy source.\n"
ROOT = Path(__file__).resolve().parents[1]


class LegacyLoader(yaml.SafeLoader):
    """Use YAML booleans matching Helix rather than YAML 1.1 yes/no aliases."""


LegacyLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in rules if tag != "tag:yaml.org,2002:bool"]
    for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
LegacyLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"))


class SourceDumper(yaml.SafeDumper):
    """Quote strings independent of PyYAML's older implicit scalar schema."""


def _string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')


def _mapping(dumper, value):
    flow = True if len(value) == 1 and next(iter(value)) in ("$call", "$ref", "$literal") and len(str(value)) <= 110 else None
    return dumper.represent_mapping("tag:yaml.org,2002:map", value, flow_style=flow)


SourceDumper.add_representer(str, _string)
SourceDumper.add_representer(dict, _mapping)


def migrate_source(source: str, *, names=()) -> str:
    """Translate a legacy document; `names` supplies bindings outside a snippet.

    This deliberately retains legacy lexical spelling only during migration.
    Malformed YAML raises YAMLError; the caller can preserve negative fixtures.
    The marker makes corpus migration repeatable without interpreting new lists.
    Legacy dynamic head fallback cannot always be inferred statically; audit all
    migrated outcomes before blessing, and write intended ordinary data explicitly.
    """
    if source.startswith(MARKER):
        return source
    loader = LegacyLoader(source)
    try:
        root = loader.get_single_node()
        if root is None:
            return source
        builtins = set(re.findall(r'actor\(\s*"([^"]+)"', (ROOT / "src/builtins.rs").read_text()))
        builtins.update(RETIRED_FORMS)
        active = set()
        seen = set()

        def validate(node):
            if id(node) in seen:
                raise ValueError("YAML aliases remain unsupported and are not rewritten")
            seen.add(id(node))
            if isinstance(node, MappingNode):
                keys = [key.value for key, _ in node.value if isinstance(key, ScalarNode)]
                if len(keys) != len(node.value) or len(keys) != len(set(keys)):
                    raise ValueError("duplicate or non-scalar mapping keys are not rewritten")
                for key, value in node.value:
                    validate(key)
                    validate(value)
            elif isinstance(node, SequenceNode):
                for value in node.value:
                    validate(value)

        validate(root)

        def scalar(node):
            text = node.value
            if node.style is not None:
                return text
            if text in ("", "~", "null", "Null", "NULL"):
                return None
            if text in ("true", "True", "TRUE", "false", "False", "FALSE"):
                return text.lower() == "true"
            if re.fullmatch(r"[+-]?[0-9]+", text):
                integer = int(text)
                if -(2 ** 63) <= integer < 2 ** 63:
                    return integer
            if re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
                number = float(text)
                if math.isfinite(number):
                    return number
            return text

        def plain(node):
            return isinstance(node, ScalarNode) and node.style is None and isinstance(scalar(node), str)

        def data(node):
            if isinstance(node, ScalarNode):
                return scalar(node)
            if isinstance(node, SequenceNode):
                return [data(item) for item in node.value]
            return {data(key): data(value) for key, value in node.value}

        def walk(node, scope, argument=False):
            if id(node) in active:
                raise ValueError("cyclic YAML aliases cannot be migrated")
            if isinstance(node, ScalarNode):
                return {"$ref": node.value} if argument and plain(node) else data(node)
            active.add(id(node))
            try:
                if isinstance(node, MappingNode):
                    keys = [data(key) for key, _ in node.value]
                    if len(keys) == 1 and keys[0] in ("$call", "$ref", "$literal"):
                        return data(node)
                    local = scope | {key for key in keys if isinstance(key, str)}
                    return {data(key): walk(value, local) for key, value in node.value}
                items = node.value
                if not items:
                    return []
                head = items[0]
                name = head.value if plain(head) else None
                computed = False
                if isinstance(head, SequenceNode) and head.value:
                    first = head.value[0]
                    if isinstance(first, SequenceNode):
                        computed = True
                    elif plain(first):
                        computed = first.value in FUNCTION_RESULTS or (first.value not in builtins and first.value.split(".")[0] in scope)
                call = (name is not None and (name in builtins or name.split(".")[0] in scope)) or computed
                if not call:
                    return [walk(item, scope) for item in items]
                actor = name if name is not None else walk(head, scope, True)
                retired_form = name in RETIRED_FORMS and name not in scope
                if retired_form:
                    actor = RETIRED_FORMS[name]
                args = items[1:]
                if name == "import":
                    return {"$call": [actor, *(data(arg) for arg in args)]}
                if name == "fn" and args and isinstance(args[0], SequenceNode):
                    parameters, local = [], set(scope)
                    for param in args[0].value:
                        if isinstance(param, ScalarNode):
                            parameters.append(data(param))
                            local.add(param.value)
                        elif isinstance(param, SequenceNode) and param.value:
                            parameters.append([data(param.value[0]), *(walk(x, scope, True) for x in param.value[1:])])
                            local.add(param.value[0].value)
                        else:
                            parameters.append(data(param))
                    return {"$call": [actor, parameters, *(walk(arg, local, True) for arg in args[1:])]}
                if (name == "let" or retired_form) and args and isinstance(args[0], MappingNode):
                    local = scope | {key.value for key, _ in args[0].value}
                    return {"$call": [actor, walk(args[0], scope), *(walk(arg, local, True) for arg in args[1:])]}
                return {"$call": [actor, *(walk(arg, scope, True) for arg in args)]}
            finally:
                active.remove(id(node))

        result = walk(root, set(names))
        header = []
        for line in source.splitlines(keepends=True):
            if line.strip() and not line.lstrip().startswith("#"):
                break
            header.append(line)
        return MARKER + "".join(header) + yaml.dump(result, Dumper=SourceDumper, allow_unicode=False, sort_keys=False, width=110, default_flow_style=None)
    finally:
        loader.dispose()


def migrate_files(paths, *, write=False):
    """Return a per-file report, excluding golden outputs and malformed fixtures."""
    files = sorted({file for path in paths for file in (path.rglob("*.yaml") if path.is_dir() else [path])
                    if not file.name.endswith(".expected.yaml")})
    results = []
    for path in files:
        try:
            before = path.read_text()
            after = migrate_source(before)
            changed = before != after
            if write and changed:
                path.write_text(after)
            results.append({"path": str(path), "status": "changed" if changed else "unchanged"})
        except OSError as error:
            results.append({"path": str(path), "status": "error", "reason": str(error)})
        except (yaml.YAMLError, ValueError) as error:
            results.append({"path": str(path), "status": "skipped", "reason": str(error)})
    return results


def audit_golden(context, baseline=None, output_format="text"):
    """Compare migrated execution to existing expectations before rewriting goldens."""
    import json
    import shlex
    import subprocess
    from tooling.test_runner import discover_cases

    if baseline is not None:
        revision = subprocess.run(["git", "rev-parse", "--verify", "--end-of-options", f"{baseline}^{{commit}}"],
                                  cwd=context.root, capture_output=True, text=True)
        if revision.returncode:
            raise ValueError(f"invalid baseline revision: {baseline}")
    results = []
    new_cases = []

    def differences(old, new, path="/"):
        if isinstance(old, dict) and isinstance(new, dict):
            if set(old) == {"ref"} and set(new) == {"ref"} and all(re.fullmatch(r"object[0-9]+", str(value["ref"])) for value in (old, new)):
                return []  # Inspection object allocation names are not language values.
            changes = []
            for key in sorted(old.keys() | new.keys(), key=str):
                child = path.rstrip("/") + "/" + str(key)
                if key not in old or key not in new:
                    changes.append({"path": child, "before": old.get(key), "after": new.get(key),
                                    "missing_before": key not in old, "missing_after": key not in new})
                else:
                    changes.extend(differences(old[key], new[key], child))
            return changes
        return [] if old == new else [{"path": path, "before": old, "after": new}]

    def semantic(value):
        if isinstance(value, list):
            return [semantic(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {"ref"} and re.fullmatch(r"object[0-9]+", str(value["ref"])):
                return {"ref": "<inspection object>"}
            omitted = {"line", "column", "path", "reference", "calls"} if {"kind", "message"} <= value.keys() else set()
            return {key: (item.replace("/$call", "") if key == "message" and isinstance(item, str) else semantic(item))
                    for key, item in value.items() if key not in omitted}
        return value

    for case in discover_cases(context.commands, context.root):
        expected = case.with_suffix(".expected.yaml")
        if not expected.exists():
            continue
        if baseline is not None:
            previous = subprocess.run(["git", "show", f"{baseline}:{expected.relative_to(context.root).as_posix()}"],
                                      cwd=context.root, capture_output=True, text=True)
            if previous.returncode:
                new_cases.append(str(case.relative_to(context.root)))
                continue
            old = yaml.safe_load(previous.stdout)
        else:
            old = yaml.safe_load(expected.read_text())
        run = context.helix(*shlex.split(context.commands[case.parent]), str(case.relative_to(context.root)))
        malformed = context.malformed_state(run.stdout, run.returncode)
        new = yaml.safe_load(run.stdout) if not malformed else {}
        changes = differences(old.get("root"), new.get("root")) if old.get("status") == new.get("status") == "finished" else []
        results.append({"case": str(case.relative_to(context.root)), "before": old.get("status"),
                        "after": new.get("status"), "malformed": malformed, "root_changes": changes,
                        "semantic_root_changes": differences(semantic(old.get("root")), semantic(new.get("root"))) if changes else [],
                        "error": new.get("error") if old.get("status") != new.get("status") else None,
                        "diagnostic_changes": differences(semantic(old.get("error", old.get("errors"))),
                                                          semantic(new.get("error", new.get("errors"))))})
    failed = [row for row in results if row["malformed"] or row["before"] != row["after"]]
    changed = [row for row in results if row["semantic_root_changes"]]
    report = context.root / "runtime" / "migration-audit" / "report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    evidence = {"baseline": baseline, "cases": results, "new_cases": new_cases}
    report.write_text(json.dumps(evidence, ensure_ascii=True, indent=2) + "\n")
    if output_format == "json":
        from tooling.output import emit
        emit({"command": "migration-audit", "status": "failed" if failed else "ok",
              "status_failures": len(failed), "changed_roots": len(changed),
              "evidence": str(report.relative_to(context.root)), **evidence})
        return 1 if failed else 0
    for case in new_cases:
        print(f"NEW {case}: no expectation in baseline {baseline}")
    print(f"migration-audit: {len(results)} cases; {len(failed)} status failures; {len(changed)} changed roots; evidence: {report.relative_to(context.root)}")
    for row in failed:
        print(f"FAIL {row['case']}: {row['before']} -> {row['after']}: {row['malformed'] or row['error']}")
    for row in changed:
        print(f"REVIEW {row['case']}: " + ", ".join(change["path"] for change in row["semantic_root_changes"]))
    for row in results:
        if row["diagnostic_changes"]:
            print(f"DIAGNOSTIC {row['case']}: " + ", ".join(change["path"] for change in row["diagnostic_changes"]))
    return 1 if failed else 0
