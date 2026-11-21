"""The original five protocol-v2 workloads, with unchanged prompts and cases."""
from tooling.agent_tasks import task

WORDS = ['', 'a', 'A', '雪', '🙂', '$call', 'x/y']


@task('sum-squares',
      goal='Input is a list of integers. Return the sum of the squares of its strictly positive elements. Empty input returns 0.',
      example={'input': [-2, 0, 3, 4], 'output': 25},
      edge=[-3, -1, 0, 1, 2, 2],
      generate=lambda rng, n: [rng.randint(-20, 20) for _ in range(n)])
def sum_squares(xs):
    return sum(x * x for x in xs if x > 0)


def count(xs):
    result = {}
    for x in xs:
        result[x] = result.get(x, 0) + 1
    return result


task('word-counts',
     goal='Input is a list of strings. Return a map from each distinct string to its occurrence count. Matching is case sensitive; preserve empty strings and Unicode. Empty input returns an empty map.',
     example={'input': ['a', 'b', 'a'], 'output': {'a': 2, 'b': 1}},
     edge=['', '', '雪', 'A', 'a', '🙂', '$call'],
     generate=lambda rng, n: [rng.choice(WORDS) for _ in range(n)])(count)


@task('group-totals',
      goal='Input is a list of records with string field category, integer field amount, and boolean field active. Return a map of summed amounts by category, using only records where active is true. Include an active category even when its sum is zero. Inactive records must not create categories. Empty input returns an empty map.',
      example={'input': [{'category': 'a', 'amount': 2, 'active': True}, {'category': 'a', 'amount': 8, 'active': False}], 'output': {'a': 2}},
      edge=[{'category': 'ignored', 'amount': 99, 'active': False},
            {'category': '', 'amount': 5, 'active': True},
            {'category': '', 'amount': -5, 'active': True},
            {'category': '雪', 'amount': -7, 'active': True}],
      generate=lambda rng, n: [{'category': rng.choice(WORDS), 'amount': rng.randint(-20, 20),
                                'active': rng.choice([True, False])} for _ in range(n)])
def group_totals(rows):
    result = {}
    for row in rows:
        if row['active']:
            result[row['category']] = result.get(row['category'], 0) + row['amount']
    return result


@task('stable-unique',
      goal='Input is a list of strings. Return a list containing each distinct string exactly once, in order of its first occurrence. Matching is case sensitive. Preserve empty strings and Unicode. Empty input returns an empty list.',
      example={'input': ['b', 'a', 'b', '', 'a'], 'output': ['b', 'a', '']},
      edge=['', '', '雪', 'A', 'a', '🙂', '$call'],
      generate=lambda rng, n: [rng.choice(WORDS) for _ in range(n)])
def stable_unique(xs):
    return list(dict.fromkeys(xs))


def dependency_graph(rng, size):
    names = [f'task{i:03d}' for i in range(size)]
    values = {name: rng.sample(names[:(i // 64) * 64], min(2, (i // 64) * 64))
              for i, name in enumerate(names)}
    if size in (8, 64):
        values[names[-1]] = [names[-1]]
        values[names[-2]] = ['absent', 'absent']
    return values


@task('dependency-plan',
      goal='Input is a map from task names to lists of prerequisite task names. Return a record with order (a list), pending (a map), missing (a list), and status (a string). Start with no completed tasks. In each batch, select every pending task whose prerequisites were completed BEFORE that batch, sort the batch by Unicode task-name order, append the batch to order, and remove those tasks from pending. Stop when no task is ready. Preserve the original prerequisite lists for pending tasks. Enumerate every prerequisite absent from the original input as {task: TASK_NAME, dependency: PREREQUISITE}, sorted by task name and retaining prerequisite order and duplicates within each task. Set status to finished exactly when pending is empty; otherwise blocked. Cycles and their dependents remain pending. Empty input returns {order: [], pending: {}, missing: [], status: finished}.',
      example={'input': {'z': [], 'a': ['z'], 'b': []}, 'output': {'order': ['b', 'z', 'a'], 'pending': {}, 'missing': [], 'status': 'finished'}},
      edge={'end': ['start', 'start'], 'start': [], 'b': ['c'], 'c': ['b'],
            'missing': ['absent', 'absent'], '雪': []},
      generate=dependency_graph)
def dependency_plan(values):
    pending = dict(values)
    order, completed = [], set()
    while True:
        ready = sorted(name for name, dependencies in pending.items()
                       if all(dependency in completed for dependency in dependencies))
        if not ready:
            break
        order.extend(ready)
        completed.update(ready)
        for name in ready:
            del pending[name]
    missing = [{'task': name, 'dependency': dependency}
               for name in sorted(values) for dependency in values[name] if dependency not in values]
    return {'order': order, 'pending': pending, 'missing': missing,
            'status': 'blocked' if pending else 'finished'}
