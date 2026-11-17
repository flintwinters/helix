"""Algebraic laws of the implemented subtype fragment over a finite generated pool.

Each law is checked against the production `subtype`/`is` answers. `subtype` is sound but
incomplete, so only implications and identities the documented rules prove are asserted;
soundness is additionally checked against independent membership witnesses.
"""
import itertools
import json

from tests.suites.contracts import checked

TREE = {"type": "rec", "name": "t", "of": {"type": "union", "of": ["integer", {"type": "list", "of": {"type": "var", "name": "t"}}]}}
BASE = ["any", "never", TREE, "integer", "string",
        {"type": "list", "of": "integer"}, {"type": "tuple", "of": ["integer", "string"]},
        {"type": "tuple", "of": []}, {"type": "record", "fields": {"x": "integer"}},
        {"type": "union", "of": ["integer", "string"]}]
VALUES = [None, [[1], [[2]]], [["x"]], 1, 1.5, "x", [], [1], [1, "x"], ["x", 1], [1, 2], {}, {"x": 1}, {"x": "x"}]


def union(a, b):
    return {"type": "union", "of": [a, b]}


def meet(a, b):
    return {"type": "intersection", "of": [a, b]}


def batched(h, actor, items, size=60):
    """`actor` applied to each [left, right] item; batches stay under the program node limit."""
    results = []
    for start in range(0, len(items), size):
        call = {"$call": ["map", {"$call": ["fn", ["q"], {"$call": [actor, {"$call": ["get", {"$ref": "q"}, 0]}, {"$call": ["get", {"$ref": "q"}, 1]}]}]},
                          {"$literal": items[start:start + size]}]}
        results += checked(h, json.dumps({"r": call}))["r"]
    return results


def ask(h, pairs):
    """Production answers for each ordered type pair, keyed by the JSON of the pair."""
    unique = list({json.dumps(p, sort_keys=True): p for p in pairs}.values())
    return {json.dumps(p, sort_keys=True): a for p, a in zip(unique, batched(h, "subtype", unique))}


def verify(h):
    indices = range(len(BASE))
    derived = [union(BASE[i], BASE[j]) for i, j in itertools.product(indices, indices)]
    derived += [meet(BASE[i], BASE[j]) for i, j in itertools.product(indices, indices)]
    pool = BASE + derived
    members = batched(h, "is", [[v, t] for t in pool for v in VALUES])
    holds = {(json.dumps(t, sort_keys=True), i): members[n * len(VALUES) + i]
             for n, t in enumerate(pool) for i in range(len(VALUES))}
    key = lambda t: json.dumps(t, sort_keys=True)

    # Targeted identities need nested types outside the pool, so they are asked in one extra batch.
    identities = []
    for a, b in itertools.product(BASE, BASE):
        identities += [(a, union(a, meet(a, b))), (union(a, meet(a, b)), a), (a, meet(a, union(a, b))), (meet(a, union(a, b)), a)]
    for a in BASE:
        for left, right in [(union(a, "never"), a), (a, union(a, "never")), (meet(a, "any"), a), (a, meet(a, "any")),
                            (union(a, "any"), "any"), ("any", union(a, "any")), (meet(a, "never"), "never"), ("never", meet(a, "never")),
                            (union(a, a), a), (a, union(a, a)), (meet(a, a), a), (a, meet(a, a))]:
            identities.append((left, right))
    pairs = [(x, y) for x in pool for y in pool]
    answer = ask(h, pairs + identities)
    below = lambda x, y: answer[key([x, y])]

    for x in pool:
        assert below(x, x), ("reflexivity", x)
    for x, y in pairs:
        if below(x, y):
            for i in range(len(VALUES)):
                assert not holds[key(x), i] or holds[key(y), i], ("unsound", x, y, VALUES[i])
    proven = [(x, y) for x, y in pairs if below(x, y)]
    successors = {}
    for x, y in proven:
        successors.setdefault(key(x), []).append(y)
    for x, y in proven:
        for z in successors.get(key(y), []):
            assert below(x, z), ("transitivity", x, y, z)
    for a, b in itertools.product(BASE, BASE):
        u, m = union(a, b), meet(a, b)
        assert below(a, u) and below(b, u) and below(m, a) and below(m, b), ("bounds", a, b)
        assert below(u, union(b, a)) and below(union(b, a), u), ("union commutes", a, b)
        assert below(m, meet(b, a)) and below(meet(b, a), m), ("intersection commutes", a, b)
        for c in pool:
            if below(a, c) and below(b, c):
                assert below(u, c), ("least upper bound", a, b, c)
            if below(c, a) and below(c, b):
                assert below(c, m), ("greatest lower bound", a, b, c)
    equal = batched(h, "equivalent", [[x, y] for x, y in pairs[:200]])
    for (x, y), verdict in zip(pairs[:200], equal):
        assert verdict == (below(x, y) and below(y, x)), ("equivalent", x, y)
    for left, right in identities:
        assert answer[key([left, right])], ("identity", left, right)
    print(f"type laws: {len(pool)} types, {len(pairs)} subtype pairs, {len(identities)} identities, {len(VALUES)} membership witnesses")
