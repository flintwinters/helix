"""List, map and record workloads: aggregation, reshaping, sorting and joining."""
from tooling.agent_tasks import task

NAMES = ['ann', 'bo', 'cy', 'Dee', 'ed', '雪', '']
DEPTS = ['eng', 'ops', 'sales', '雪']


def ints(rng, n, low=-50, high=50):
    return [rng.randint(low, high) for _ in range(n)]


@task('max-or-null',
      goal='Input is a list of integers. Return the largest element, or null for an empty list.',
      example={'input': [3, -1, 7, 7], 'output': 7},
      edge=[-5, -9],
      generate=ints)
def max_or_null(xs):
    return max(xs, default=None)


@task('running-totals',
      goal='Input is a list of integers. Return the list of running totals: element i is the sum of input elements 0 through i.',
      example={'input': [1, 2, -4], 'output': [1, 3, -1]},
      edge=[0, 0, -1],
      generate=ints)
def running_totals(xs):
    total, result = 0, []
    for x in xs:
        total += x
        result.append(total)
    return result


@task('chunk-list',
      goal='Input is a record with list field items and integer field size (size >= 1). Split items into consecutive chunks of size elements, preserving order; the last chunk may be shorter. Empty items returns an empty list.',
      example={'input': {'items': [1, 2, 3, 4, 5], 'size': 2}, 'output': [[1, 2], [3, 4], [5]]},
      edge={'items': ['a', None, [1], {'k': 'v'}], 'size': 4},
      generate=lambda rng, n: {'items': ints(rng, n), 'size': rng.randint(1, 9)})
def chunk_list(values):
    items, size = values['items'], values['size']
    return [items[i:i + size] for i in range(0, len(items), size)]


def people(rng, n):
    return [{'id': f'u{rng.randint(0, max(n // 2, 1))}', 'name': rng.choice(NAMES)} for _ in range(n)]


@task('index-by-id',
      goal='Input is a list of records, each with a string field id. Return a map from id to the record (unchanged). When ids repeat, the later record wins.',
      example={'input': [{'id': 'a', 'v': 1}, {'id': 'b', 'v': 2}, {'id': 'a', 'v': 3}],
               'output': {'a': {'id': 'a', 'v': 3}, 'b': {'id': 'b', 'v': 2}}},
      edge=[{'id': '', 'name': 'x'}, {'id': '雪', 'name': None}],
      generate=people)
def index_by_id(rows):
    return {row['id']: row for row in rows}


@task('invert-map',
      goal='Input is a map from string keys to string values. Return a map from each value to the list of keys having it, sorted by Unicode order.',
      example={'input': {'b': 'x', 'a': 'x', 'c': 'y'}, 'output': {'x': ['a', 'b'], 'y': ['c']}},
      edge={'': '', '雪': '', 'Z': 'z'},
      generate=lambda rng, n: {f'k{i}{rng.choice(NAMES)}': rng.choice(DEPTS) for i in range(n)})
def invert_map(mapping):
    result = {}
    for key in sorted(mapping):
        result.setdefault(mapping[key], []).append(key)
    return result


def nested(rng, depth):
    if depth == 0 or rng.random() < 0.3:
        return rng.choice([1, 'x', None, True, [1, 2], {}])
    return {rng.choice(['a', 'b', 'c', '雪']): nested(rng, depth - 1) for _ in range(rng.randint(0, 3))}


def flatten(mapping, prefix=''):
    result = {}
    for key, value in mapping.items():
        if isinstance(value, dict):
            result.update(flatten(value, f'{prefix}{key}.'))
        else:
            result[prefix + key] = value
    return result


@task('flatten-keys',
      goal='Input is a map whose values may be nested maps. Return a flat map from dotted key paths to the non-map leaf values, for example {a: {b: 1}} becomes {"a.b": 1}. Lists, null and other scalars are leaves; nested empty maps contribute no keys. Keys contain no dots.',
      example={'input': {'a': {'b': 1, 'c': {'d': [2]}}, 'e': None, 'f': {}}, 'output': {'a.b': 1, 'a.c.d': [2], 'e': None}},
      edge={'': {'': 0}, '雪': {'x': {}}},
      generate=lambda rng, n: {f'k{i}': nested(rng, 4) for i in range(n)})
def flatten_keys(mapping):
    return flatten(mapping)


def unflatten(mapping):
    result = {}
    for path, value in mapping.items():
        *parents, leaf = path.split('.')
        target = result
        for parent in parents:
            target = target.setdefault(parent, {})
        target[leaf] = value
    return result


def flat_paths(rng, n):
    leaves = flatten({f'k{i}': nested(rng, 4) for i in range(n)})
    return {path: value for path, value in leaves.items() if value != {}}


@task('unflatten-keys',
      goal='Input is a map from dotted key paths to values. Return the nested map that splits each path on "." into a chain of nested maps holding the value at the last segment; for example {"a.b": 1, "a.c": 2} becomes {a: {b: 1, c: 2}}. Segments may be empty strings. No path is a proper dotted prefix of another.',
      example={'input': {'a.b': 1, 'a.c': 2, 'd': [3]}, 'output': {'a': {'b': 1, 'c': 2}, 'd': [3]}},
      edge={'.': 1, 'x..y': None, '雪.z': {}},
      generate=flat_paths)
def unflatten_keys(mapping):
    return unflatten(mapping)


def merge(base, override):
    result = dict(base)
    for key, value in override.items():
        result[key] = (merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict)
                       else value)
    return result


@task('deep-merge',
      goal='Input is a record with map fields base and override. Return base recursively merged with override: for a key present in both, when both values are maps merge them recursively, otherwise the override value wins (lists are replaced, not concatenated). Keys present in only one side are kept.',
      example={'input': {'base': {'a': {'x': 1, 'y': 2}, 'l': [1]}, 'override': {'a': {'y': 3}, 'l': [2], 'n': None}},
               'output': {'a': {'x': 1, 'y': 3}, 'l': [2], 'n': None}},
      edge={'base': {'a': {'b': 1}, 'c': 1}, 'override': {'a': 5, 'c': {'d': 2}}},
      generate=lambda rng, n: {'base': {f'k{rng.randint(0, n)}': nested(rng, 3) for _ in range(n)},
                               'override': {f'k{rng.randint(0, n)}': nested(rng, 3) for _ in range(n)}})
def deep_merge(values):
    return merge(values['base'], values['override'])


@task('dedupe-by-key',
      goal='Input is a list of records, each with a string field email. Return the records in input order, keeping only the first record for each distinct email (exact, case-sensitive match).',
      example={'input': [{'email': 'a@x', 'n': 1}, {'email': 'A@x', 'n': 2}, {'email': 'a@x', 'n': 3}],
               'output': [{'email': 'a@x', 'n': 1}, {'email': 'A@x', 'n': 2}]},
      edge=[{'email': ''}, {'email': '', 'x': 1}, {'email': '雪'}],
      generate=lambda rng, n: [{'email': f'{rng.choice(NAMES)}@x', 'n': i} for i in range(n)])
def dedupe_by_key(rows):
    seen, result = set(), []
    for row in rows:
        if row['email'] not in seen:
            seen.add(row['email'])
            result.append(row)
    return result


def scores(rng, n):
    return [{'name': f'{rng.choice(NAMES)}{i}', 'score': rng.randint(0, 20)} for i in range(n)]


@task('top-n-by-score',
      goal='Input is a record with field records (a list of {name: string, score: integer}) and integer field n (n >= 0). Return the names of the n records with the highest scores, ordered by score descending, ties broken by name in Unicode order. Return all names when there are fewer than n records.',
      example={'input': {'records': [{'name': 'b', 'score': 5}, {'name': 'a', 'score': 5}, {'name': 'c', 'score': 9}], 'n': 2},
               'output': ['c', 'a']},
      edge={'records': [{'name': '雪', 'score': -1}, {'name': '', 'score': -1}], 'n': 3},
      generate=lambda rng, n: {'records': scores(rng, n), 'n': rng.randint(0, 10)})
def top_n_by_score(values):
    ordered = sorted(values['records'], key=lambda row: (-row['score'], row['name']))
    return [row['name'] for row in ordered[:values['n']]]


def employees(rng, n):
    return [{'name': f'{rng.choice(NAMES)}{i % 7}', 'dept': rng.choice(DEPTS), 'salary': rng.randint(1, 6) * 1000}
            for i in range(n)]


@task('sort-multi-key',
      goal='Input is a list of employee records {name: string, dept: string, salary: integer}. Sort them by dept ascending (Unicode order), then salary descending, then name ascending (Unicode order). Return the list of names in sorted order.',
      example={'input': [{'name': 'b', 'dept': 'x', 'salary': 1}, {'name': 'a', 'dept': 'x', 'salary': 1},
                         {'name': 'c', 'dept': 'x', 'salary': 2}, {'name': 'd', 'dept': 'a', 'salary': 0}],
               'output': ['d', 'c', 'a', 'b']},
      edge=[{'name': 'z', 'dept': '', 'salary': -1}, {'name': 'z', 'dept': '', 'salary': 5}, {'name': '', 'dept': '雪', 'salary': 0}],
      generate=employees)
def sort_multi_key(rows):
    return [row['name'] for row in sorted(rows, key=lambda row: (row['dept'], -row['salary'], row['name']))]


MONTHS = ['2026-01', '2026-02', '2026-03', '2025-12']


@task('pivot-sales',
      goal='Input is a list of sales records {region: string, month: string, amount: integer}. Return a nested map region -> month -> summed amount, containing only region/month pairs that occur.',
      example={'input': [{'region': 'eu', 'month': 'jan', 'amount': 5}, {'region': 'eu', 'month': 'jan', 'amount': 2},
                         {'region': 'us', 'month': 'feb', 'amount': 1}],
               'output': {'eu': {'jan': 7}, 'us': {'feb': 1}}},
      edge=[{'region': '', 'month': '', 'amount': 0}, {'region': '雪', 'month': 'x', 'amount': -3}],
      generate=lambda rng, n: [{'region': rng.choice(DEPTS), 'month': rng.choice(MONTHS), 'amount': rng.randint(-5, 100)}
                               for _ in range(n)])
def pivot_sales(rows):
    result = {}
    for row in rows:
        months = result.setdefault(row['region'], {})
        months[row['month']] = months.get(row['month'], 0) + row['amount']
    return result


@task('group-stats',
      goal='Input is a list of records {group: string, value: integer}. Return a map from each group to {count, min, max, total} computed over that group\'s values.',
      example={'input': [{'group': 'a', 'value': 3}, {'group': 'a', 'value': -1}, {'group': 'b', 'value': 4}],
               'output': {'a': {'count': 2, 'min': -1, 'max': 3, 'total': 2}, 'b': {'count': 1, 'min': 4, 'max': 4, 'total': 4}}},
      edge=[{'group': '', 'value': 0}, {'group': '雪', 'value': -7}, {'group': '雪', 'value': -7}],
      generate=lambda rng, n: [{'group': rng.choice(DEPTS), 'value': rng.randint(-100, 100)} for _ in range(n)])
def group_stats(rows):
    result = {}
    for row in rows:
        value = row['value']
        stats = result.setdefault(row['group'], {'count': 0, 'min': value, 'max': value, 'total': 0})
        stats.update(count=stats['count'] + 1, min=min(stats['min'], value), max=max(stats['max'], value),
                     total=stats['total'] + value)
    return result


def forest(rng, n):
    rows, depth = [], {}
    for i in range(n):
        parents = [row['id'] for row in rows[-12:] if depth[row['id']] < 5]
        parent = rng.choice(parents) if parents and rng.random() < 0.85 else None
        depth[f'n{i}'] = depth[parent] + 1 if parent else 0
        rows.append({'id': f'n{i}', 'parent': parent})
    rng.shuffle(rows)
    return rows


@task('tree-from-parents',
      goal='Input is a list of nodes {id: string, parent: string or null} with unique ids; every non-null parent names a node in the list and parent links form no cycles. Build the forest: return the list of roots (nodes with null parent), each as {id, children}, where children is the list of that node\'s child nodes in the same shape. Roots and children keep input order.',
      example={'input': [{'id': 'b', 'parent': 'a'}, {'id': 'a', 'parent': None}, {'id': 'c', 'parent': 'a'}],
               'output': [{'id': 'a', 'children': [{'id': 'b', 'children': []}, {'id': 'c', 'children': []}]}]},
      edge=[{'id': '雪', 'parent': None}, {'id': '', 'parent': '雪'}, {'id': 'x', 'parent': ''}],
      generate=forest)
def tree_from_parents(rows):
    def build(parent):
        return [{'id': row['id'], 'children': build(row['id'])} for row in rows if row['parent'] == parent]
    return build(None)


@task('set-compare',
      goal='Input is a record with string-list fields left and right. Return {both, only_left, only_right}: the distinct strings in both lists, only in left, and only in right, each sorted by Unicode order.',
      example={'input': {'left': ['b', 'a', 'b'], 'right': ['c', 'b']}, 'output': {'both': ['b'], 'only_left': ['a'], 'only_right': ['c']}},
      edge={'left': ['', '雪'], 'right': ['', 'A', 'a']},
      generate=lambda rng, n: {'left': [f'{rng.choice(NAMES)}{rng.randint(0, 9)}' for _ in range(n)],
                               'right': [f'{rng.choice(NAMES)}{rng.randint(0, 9)}' for _ in range(n)]})
def set_compare(values):
    left, right = set(values['left']), set(values['right'])
    return {'both': sorted(left & right), 'only_left': sorted(left - right), 'only_right': sorted(right - left)}
