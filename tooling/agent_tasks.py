"""Versioned coding workloads; independent Python oracles stay out of model prompts."""
import random


VERSION = 2
TASKS = {
    'sum-squares': {
        'goal': 'Input is a list of integers. Return the sum of the squares of its strictly positive elements. Empty input returns 0.',
        'example': {'input': [-2, 0, 3, 4], 'output': 25},
        'reference': 'solve: !call [fn, [xs], !call [fold, !call [fn, [acc, x], !call [if, !call [gt, !ref x, 0], !call [add, !ref acc, !call [mul, !ref x, !ref x]], !ref acc]], 0, !ref xs]]\n',
    },
    'word-counts': {
        'goal': 'Input is a list of strings. Return a map from each distinct string to its occurrence count. Matching is case sensitive; preserve empty strings and Unicode. Empty input returns an empty map.',
        'example': {'input': ['a', 'b', 'a'], 'output': {'a': 2, 'b': 1}},
        'reference': 'l: !call [import, std:list]\nsolve: !call [fn, [xs], !call [l.frequencies, !ref xs]]\n',
    },
    'group-totals': {
        'goal': 'Input is a list of records with string field category, integer field amount, and boolean field active. Return a map of summed amounts by category, using only records where active is true. Include an active category even when its sum is zero. Inactive records must not create categories. Empty input returns an empty map.',
        'example': {'input': [{'category': 'a', 'amount': 2, 'active': True}, {'category': 'a', 'amount': 8, 'active': False}], 'output': {'a': 2}},
        'reference': 'solve: !call [fn, [xs], !call [fold, !call [fn, [acc, row], !call [if, !call [get, !ref row, active], !call [let, {key: !call [get, !ref row, category]}, !call [set, !ref acc, !ref key, !call [add, !call [get, !ref acc, !ref key, 0], !call [get, !ref row, amount]]]], !ref acc]], {}, !ref xs]]\n',
    },
    'stable-unique': {
        'goal': 'Input is a list of strings. Return a list containing each distinct string exactly once, in order of its first occurrence. Matching is case sensitive. Preserve empty strings and Unicode. Empty input returns an empty list.',
        'example': {'input': ['b', 'a', 'b', '', 'a'], 'output': ['b', 'a', '']},
        'reference': 'l: !call [import, std:list]\nsolve: !call [fn, [xs], !call [l.unique, !ref xs]]\n',
    },
    'dependency-plan': {
        'goal': 'Input is a map from task names to lists of prerequisite task names. Return a record with order (a list), pending (a map), missing (a list), and status (a string). Start with no completed tasks. In each batch, select every pending task whose prerequisites were completed BEFORE that batch, sort the batch by Unicode task-name order, append the batch to order, and remove those tasks from pending. Stop when no task is ready. Preserve the original prerequisite lists for pending tasks. Enumerate every prerequisite absent from the original input as {task: TASK_NAME, dependency: PREREQUISITE}, sorted by task name and retaining prerequisite order and duplicates within each task. Set status to finished exactly when pending is empty; otherwise blocked. Cycles and their dependents remain pending. Empty input returns {order: [], pending: {}, missing: [], status: finished}.',
        'example': {'input': {'z': [], 'a': ['z'], 'b': []}, 'output': {'order': ['b', 'z', 'a'], 'pending': {}, 'missing': [], 'status': 'finished'}},
        'reference': 'd: !call [import, std:dependency]\nsolve: !call [fn, [tasks], !call [d.plan, !ref tasks]]\n',
    },
}


def oracle(task, values):
    if task == 'sum-squares':
        return sum(x * x for x in values if x > 0)
    if task == 'stable-unique':
        return list(dict.fromkeys(values))
    if task == 'dependency-plan':
        pending = dict(values)
        order, completed = [], set()
        while True:
            ready = sorted(task for task, dependencies in pending.items()
                           if all(dependency in completed for dependency in dependencies))
            if not ready:
                break
            order.extend(ready)
            completed.update(ready)
            for task in ready:
                del pending[task]
        missing = [{'task': task, 'dependency': dependency}
                   for task in sorted(values) for dependency in values[task] if dependency not in values]
        return {'order': order, 'pending': pending, 'missing': missing,
                'status': 'blocked' if pending else 'finished'}
    if task not in TASKS:
        raise ValueError(f'unknown task {task}')
    result = {}
    for value in values:
        if task == 'word-counts':
            result[value] = result.get(value, 0) + 1
        elif value['active']:
            key = value['category']
            result[key] = result.get(key, 0) + value['amount']
    return result


def inputs(task):
    """Named edge cases plus fixed-seed scaling inputs; timing uses the largest."""
    words = ['', 'a', 'A', '雪', '🙂', '$call', 'x/y']
    if task == 'sum-squares':
        yield 'edge', [-3, -1, 0, 1, 2, 2]
    elif task in ('word-counts', 'stable-unique'):
        yield 'edge', ['', '', '雪', 'A', 'a', '🙂', '$call']
    elif task == 'dependency-plan':
        yield 'edge', {'end': ['start', 'start'], 'start': [], 'b': ['c'], 'c': ['b'],
                       'missing': ['absent', 'absent'], '雪': []}
    else:
        yield 'edge', [
            {'category': 'ignored', 'amount': 99, 'active': False},
            {'category': '', 'amount': 5, 'active': True},
            {'category': '', 'amount': -5, 'active': True},
            {'category': '雪', 'amount': -7, 'active': True},
        ]
    for size in (0, 1, 8, 64, 256):
        rng = random.Random(714209 + size)
        if task == 'sum-squares':
            values = [rng.randint(-20, 20) for _ in range(size)]
        elif task in ('word-counts', 'stable-unique'):
            values = [rng.choice(words) for _ in range(size)]
        elif task == 'dependency-plan':
            names = [f'task{i:03d}' for i in range(size)]
            values = {name: rng.sample(names[:(i // 64) * 64], min(2, (i // 64) * 64))
                      for i, name in enumerate(names)}
            if size in (8, 64):
                values[names[-1]] = [names[-1]]
                values[names[-2]] = ['absent', 'absent']
        else:
            values = [{'category': rng.choice(words), 'amount': rng.randint(-20, 20),
                       'active': rng.choice([True, False])} for _ in range(size)]
        yield f'n={size}', values
