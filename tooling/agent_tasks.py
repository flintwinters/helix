"""Versioned coding workloads; independent Python oracles stay out of model prompts."""
import random


VERSION = 1
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
}


def oracle(task, values):
    if task == 'sum-squares':
        return sum(x * x for x in values if x > 0)
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
    elif task == 'word-counts':
        yield 'edge', ['', '', '雪', 'A', 'a', '🙂', '$call']
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
        elif task == 'word-counts':
            values = [rng.choice(words) for _ in range(size)]
        else:
            values = [{'category': rng.choice(words), 'amount': rng.randint(-20, 20),
                       'active': rng.choice([True, False])} for _ in range(size)]
        yield f'n={size}', values
