"""Supervised Helix orchestration; the host validates authority and retained evidence."""
import yaml

from tooling.test_reports import failure


class PolicyError(RuntimeError):
    """A policy failure with a replayable diagnostic."""


def evaluate(context, operation, arguments, validate):
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
                if set(row) != {"suite", "action"} or (bless and row["suite"] == "robustness"):
                    return False
            elif row.get("action") == "skip":
                if (not bless or row["suite"] != "robustness"
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
        count = sum(len(row["failures"]) for row in observations)
        return (type(value["failure_count"]) is int and value["failure_count"] == count
                and type(value["exit_code"]) is int and value["exit_code"] == int(bool(count))
                and value["status"] == ("failed" if count else "passed"))

    return evaluate(context, "report", [observations], valid)
