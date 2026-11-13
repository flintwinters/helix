"""Supervised Helix orchestration; the host validates authority and retained evidence."""
import json
from pathlib import Path

from tooling.test_reports import failure


class PolicyError(RuntimeError):
    """A policy failure with a replayable diagnostic."""


def _valid_summary(value, observations):
    count = sum(len(row["failures"]) for row in observations)
    return (type(value["failure_count"]) is int and value["failure_count"] == count
            and type(value["exit_code"]) is int and value["exit_code"] == int(bool(count))
            and value["status"] == ("failed" if count else "passed"))


def load_rerun_report(path, catalog):
    """Validate persisted observations before any build; reports never carry authority."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field {key!r}")
            result[key] = value
        return result

    def invalid(message):
        raise ValueError(f"invalid test report {path}: {message}")

    try:
        value = json.loads(Path(path).read_text(), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        invalid(str(error))
    fields = {"version", "command", "status", "exit_code", "failure_count", "results"}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value["version"]) is not int or value["version"] != 1
            or value["command"] != "test" or not isinstance(value["results"], list)):
        invalid("expected a version 1 test-result JSON report from --format=json test")
    known = set(catalog) | {"standard_library", "orchestration"}
    seen = set()
    rows = value["results"]
    for row in rows:
        required = {"suite", "status", "failures"}
        optional = {"stdout", "stderr", "cases", "jobs", "blessed", "reason"}
        if (not isinstance(row, dict) or not required <= set(row) or set(row) - required - optional
                or not isinstance(row["suite"], str) or row["suite"] not in known
                or row["suite"] in seen):
            invalid("results must contain unique, known suites with supported fields")
        seen.add(row["suite"])
        if (not isinstance(row["failures"], list)
                or any(not isinstance(item, str) for item in row["failures"])
                or not isinstance(row["status"], str)
                or row["status"] not in {"passed", "failed", "skipped"}
                or bool(row["failures"]) != (row["status"] == "failed")
                or ("reason" in row) != (row["status"] == "skipped")):
            invalid(f"inconsistent failures/status for {row['suite']}")
        if (any(key in row and not isinstance(row[key], str) for key in ("stdout", "stderr", "reason"))
                or any(key in row and (type(row[key]) is not int or row[key] < minimum)
                       for key, minimum in (("cases", 0), ("jobs", 1)))
                or ("blessed" in row and type(row["blessed"]) is not bool)):
            invalid(f"invalid observation metadata for {row['suite']}")
    if not _valid_summary(value, rows):
        invalid("summary does not match recorded failures")
    failed = {row["suite"] for row in rows if row["status"] == "failed"}
    if "docker_nginx" in failed:
        invalid("Docker authority is not inherited; explicitly run ./manage.py test --suite docker_nginx")
    if "orchestration" in failed:
        invalid("repair the orchestration failure before selecting reruns; inspect its diagnostic and run "
                "./manage.py test --suite harness")
    if "standard_library" in failed and not any(
            row["suite"] in {"golden", "robustness"} and row["status"] != "skipped" for row in rows):
        invalid("failed standard_library has no runnable fixture companion; explicitly run "
                "./manage.py test --suite golden")
    return value


def rerun(context, catalog, previous):
    """Select whole failed suites; independently reject omitted or additional work."""
    rows = previous["results"]
    failed = {row["suite"] for row in rows if row["status"] == "failed"}
    if "standard_library" in failed:
        failed.update(row["suite"] for row in rows
                      if row["suite"] in {"golden", "robustness"} and row["status"] != "skipped")
    expected = [name for name in catalog if name in failed]
    selection = {"results": [{"suite": row["suite"], "status": row["status"]} for row in rows]}
    return evaluate(context, "rerun", [list(catalog), selection],
                    lambda value: isinstance(value, list) and value == expected)


def evaluate(context, operation, arguments, validate):
    import yaml

    source = yaml.safe_dump({"$call": ["let", {
        "policy": {"$call": ["import", str(context.root / "tooling" / "test_policy.yaml")]},
    }, {"$call": [f"policy.{operation}", *({"$literal": arg} for arg in arguments)]}]},
        allow_unicode=False)
    result = context.helix("run", "-", stdin=source)
    problem = context.malformed_state(result.stdout, result.returncode)
    if problem is None and (result.returncode != 0 or result.stderr):
        problem = "policy did not finish cleanly"
    if problem is None:
        value = yaml.safe_load(result.stdout)["root"]
        if not validate(value):
            problem = "policy response violated the host observation/authority contract"
    if problem is not None:
        diagnostic = failure(context.root, context.root / "tooling" / "test_policy.yaml",
                             operation, problem, "run", result, source=source)
        raise PolicyError(diagnostic)
    return value


def plan(context, catalog, requested, bless):
    allowed = set(requested or catalog)

    def valid(value):
        if not isinstance(value, list):
            return False
        seen = []
        for row in value:
            if (not isinstance(row, dict) or not isinstance(row.get("suite"), str)
                    or row["suite"] not in allowed | {"standard_library"}):
                return False
            if row.get("action") == "run":
                if (set(row) != {"suite", "action"} or (bless and row["suite"] == "robustness")
                        or (not requested and row["suite"] == "docker_nginx")):
                    return False
            elif row.get("action") == "skip":
                if (not ((bless and row["suite"] == "robustness")
                         or (not requested and row["suite"] == "docker_nginx"))
                        or set(row) != {"suite", "action", "reason"} or not isinstance(row["reason"], str)):
                    return False
            else:
                return False
            seen.append(row["suite"])
        if any(name in seen and ("standard_library" not in seen
                   or seen.index("standard_library") > seen.index(name))
               for name in ("golden", "robustness")):
            return False
        return (len(seen) == len(set(seen)) and allowed <= set(seen)
                and (("standard_library" in seen) == bool(allowed & {"golden", "robustness"})))

    return evaluate(context, "plan", [list(catalog), requested or [], bless], valid)


def report(context, observations):
    def valid(value):
        if (not isinstance(value, dict)
                or set(value) != {"command", "status", "exit_code", "failure_count", "results"}
                or value["command"] != "test" or not isinstance(value["results"], list)
                or len(value["results"]) != len(observations)):
            return False
        for row, observation in zip(value["results"], observations):
            if not isinstance(row, dict) or {key: item for key, item in row.items() if key != "status"} != observation:
                return False
            if not isinstance(row.get("status"), str) or row["status"] not in {"passed", "failed", "skipped"}:
                return False
            if (bool(observation["failures"]) != (row["status"] == "failed")
                    or ("reason" in observation) != (row["status"] == "skipped")):
                return False
        # Independently observed failures cannot be erased by a damaged interpreter.
        return _valid_summary(value, observations)

    return evaluate(context, "report", [observations], valid)
