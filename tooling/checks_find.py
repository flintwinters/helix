"""`helix find` contracts: ranked text search, structural type filters and honest unseen counts; run via manage.py test."""
import yaml


def find(ctx, *args):
    result = ctx.helix("find", "--yaml", *args)
    assert result.returncode == 0, result
    return yaml.safe_load(result.stdout)


def names(report):
    return [(row["module"], row["name"]) for row in report["results"]]


def check(ctx):
    # Text: exact names outrank substrings, every word must match, scope narrows.
    report = find(ctx, "split")
    assert names(report)[0] == ("std:text", "split"), names(report)
    assert (None, "text_repeat") in names(find(ctx, "padding"))
    assert all(row["kind"] in ("builtin", "std") and row["next"].startswith("helix ") for row in report["results"])
    assert find(ctx, "add", "--in", "builtins")["results"][0]["name"] == "add"
    assert find(ctx, "split", "zzzzqqq")["total"] == 0

    # Structure: parameter types match distinct parameters, result types match as written.
    strings = find(ctx, "--takes", "string", "--takes", "string", "--returns", "list_of(string)")
    split = next(row for row in strings["results"] if row["name"] == "split")
    assert split["params"] == [{"name": "source", "type": "string"}, {"name": "separator", "type": "string"}]
    assert split["returns"] == "list_of(string)"
    assert find(ctx, "--takes", "string", "--takes", "string", "--takes", "string", "--in", "text", "split")["total"] == 0
    wildcard = find(ctx, "--returns", "list_of(_)", "--in", "text")
    assert ("std:text", "split") in names(wildcard)
    assert find(ctx, "--arity", "2", "--in", "text")["results"]
    assert all(row["arity"]["min"] <= 2 for row in find(ctx, "--arity", "2", "--in", "builtins")["results"])

    # Builtins carry written types too; `number` abbreviates union(integer, float) and unions match by alternative.
    each = find(ctx, "--takes", "list", "--takes", "function", "--in", "builtins")
    assert {"map", "filter", "fold", "each"} <= {row["name"] for row in each["results"]}, names(each)
    arithmetic = find(ctx, "--takes", "integer", "--returns", "float", "--in", "builtins")
    assert {"add", "div"} <= {row["name"] for row in arithmetic["results"]} and "len" not in {row["name"] for row in arithmetic["results"]}
    assert find(ctx, "--takes", "any", "--in", "builtins")["results"], "`any` is matched as written"
    assert all(row["params"] is not None and row["returns"] for row in find(ctx, "--in", "builtins", "--limit", "500")["results"])

    # Types cannot see unannotated entries, and the report says how many.
    assert strings["unseen_by_types"] > 0
    shown = ctx.helix("find", "--takes", "string")
    assert shown.returncode == 0 and "lack type annotations" in shown.stdout and "next:" in shown.stdout

    # Failures name the rejected input and the correction.
    for bad, hint in ((["--takes", "list_of("], "list_of(string)"), (["--in", "nope"], "builtins"), (["--bogus"], "usage"), (["--arity", "x"], "integer"), (["--takes"], "requires a value")):
        result = ctx.helix("find", *bad)
        assert result.returncode == 2 and hint in result.stderr, (bad, result)
    return []
