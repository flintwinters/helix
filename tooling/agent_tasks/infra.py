"""Infrastructure workloads: configuration drift, package and service plans, inventories."""
import re

from tooling.agent_tasks import task
from tooling.agent_tasks.basics import dependency_plan

PACKAGES = ['nginx', 'curl', 'git', 'vim', 'jq', 'zsh', '雪']
VERSIONS = ['1.0', '1.1', '2.0']
HOSTS = ['web1', 'web2', 'db1', 'cache', '雪']


def settings(rng, n):
    return {f'k{rng.randint(0, n + 3)}': rng.choice([1, '1', None, True, [1], {'x': 1}]) for _ in range(n)}


@task('config-diff',
      goal='Input is a record with map fields before and after. Return {added, removed, changed}: added lists keys only in after, removed lists keys only in before, and changed lists {key, before, after} for keys in both whose values differ structurally (1 and "1" differ; null is a value distinct from absence). All three lists are ordered by key in Unicode order.',
      example={'input': {'before': {'a': 1, 'b': 2, 'c': None}, 'after': {'b': 3, 'c': None, 'd': 4}},
               'output': {'added': ['d'], 'removed': ['a'], 'changed': [{'key': 'b', 'before': 2, 'after': 3}]}},
      edge={'before': {'x': None, 'y': [1, 2]}, 'after': {'y': [2, 1], '雪': None}},
      generate=lambda rng, n: {'before': settings(rng, n), 'after': settings(rng, n)})
def config_diff(values):
    before, after = values['before'], values['after']
    return {'added': sorted(after.keys() - before.keys()), 'removed': sorted(before.keys() - after.keys()),
            'changed': [{'key': key, 'before': before[key], 'after': after[key]}
                        for key in sorted(before.keys() & after.keys()) if before[key] != after[key]
                        or type(before[key]) is not type(after[key])]}


def versions(rng, n):
    return {f'{rng.choice(PACKAGES)}{rng.randint(0, n)}': rng.choice(VERSIONS) for _ in range(n)}


@task('package-plan',
      goal='Input is a record with fields installed and desired, each a map from package name to version string. Return {install, upgrade, remove, unchanged}: install lists desired packages not installed; upgrade lists {name, from, to} for packages in both maps whose versions differ (from is the installed version); remove lists installed packages not desired; unchanged counts packages whose versions match. Order lists by package name in Unicode order.',
      example={'input': {'installed': {'git': '1.0', 'vim': '2.0', 'jq': '1.0'}, 'desired': {'git': '1.1', 'curl': '7', 'jq': '1.0'}},
               'output': {'install': ['curl'], 'upgrade': [{'name': 'git', 'from': '1.0', 'to': '1.1'}], 'remove': ['vim'], 'unchanged': 1}},
      edge={'installed': {'雪': '', 'a': '1'}, 'desired': {'雪': '0', 'A': '1'}},
      generate=lambda rng, n: {'installed': versions(rng, n), 'desired': versions(rng, n)})
def package_plan(values):
    installed, desired = values['installed'], values['desired']
    both = sorted(installed.keys() & desired.keys())
    return {'install': sorted(desired.keys() - installed.keys()),
            'upgrade': [{'name': name, 'from': installed[name], 'to': desired[name]}
                        for name in both if installed[name] != desired[name]],
            'remove': sorted(installed.keys() - desired.keys()),
            'unchanged': sum(installed[name] == desired[name] for name in both)}


def services(rng, n):
    names = [f'svc{i:03d}' for i in range(n)]
    return {name: {'enabled': rng.random() < 0.8,
                   'after': rng.sample(names[max(0, i - 8):i] + ['ghost', name], min(i // 3, 2)) if i else []}
            for i, name in enumerate(names)}


@task('service-start-order',
      goal='Input is a map from service name to {enabled: boolean, after: list of service names it must start after}. Consider only enabled services, and ignore "after" entries naming services that are absent or not enabled. Start services in batches: each batch is every not-yet-started service whose remaining "after" services have all started in earlier batches, sorted by name in Unicode order. Return {order: the concatenated batches, blocked: the enabled services never started (because of cycles), sorted by name}.',
      example={'input': {'web': {'enabled': True, 'after': ['db', 'cache']}, 'db': {'enabled': True, 'after': []},
                         'cache': {'enabled': False, 'after': []}, 'a': {'enabled': True, 'after': ['a']}},
               'output': {'order': ['db', 'web'], 'blocked': ['a']}},
      edge={'雪': {'enabled': True, 'after': ['x', 'y']}, 'x': {'enabled': True, 'after': ['y']},
            'y': {'enabled': True, 'after': ['x']}, 'z': {'enabled': False, 'after': ['z']}},
      generate=services)
def service_start_order(values):
    enabled = {name: service for name, service in values.items() if service['enabled']}
    plan = dependency_plan({name: [after for after in service['after'] if after in enabled]
                            for name, service in enabled.items()})
    return {'order': plan['order'], 'blocked': sorted(plan['pending'])}


@task('inventory-groups',
      goal='Input is a list of hosts {name: string, tags: list of strings}. Return a map from each tag to the distinct names of hosts carrying it, sorted by Unicode order.',
      example={'input': [{'name': 'web2', 'tags': ['web', 'eu']}, {'name': 'web1', 'tags': ['web']}, {'name': 'db', 'tags': []}],
               'output': {'web': ['web1', 'web2'], 'eu': ['web2']}},
      edge=[{'name': '雪', 'tags': ['', '']}, {'name': '雪', 'tags': ['']}],
      generate=lambda rng, n: [{'name': f'{rng.choice(HOSTS)}{rng.randint(0, 9)}',
                                'tags': [rng.choice(['web', 'db', 'eu', 'us', 'canary', '雪']) for _ in range(rng.randint(0, 3))]}
                               for _ in range(n)])
def inventory_groups(hosts):
    result = {}
    for host in hosts:
        for tag in host['tags']:
            result.setdefault(tag, set()).add(host['name'])
    return {tag: sorted(names) for tag, names in result.items()}


def host_pattern(rng):
    start = rng.randint(0, 12)
    if rng.random() < 0.2:
        return rng.choice(HOSTS) + '.example'
    width = rng.choice([1, 2, 3])
    return f'{rng.choice(HOSTS)}[{start:0{width}d}:{start + rng.randint(0, 4)}].{rng.choice(["x", "雪"])}'


@task('host-range-expand',
      goal='Input is a list of host patterns. A pattern contains at most one range "[A:B]", where A and B are decimal digit strings with A <= B numerically. Expand it to one host per integer from A to B inclusive, replacing the range with the integer padded with leading zeros to the number of digits in A. A pattern without a range expands to itself. Return the concatenated expansions in input order.',
      example={'input': ['web[08:10].x', 'db', 'n[1:2]'], 'output': ['web08.x', 'web09.x', 'web10.x', 'db', 'n1', 'n2']},
      edge=['[0:0]', '[000:2]雪', 'a[9:9]b'],
      generate=lambda rng, n: [host_pattern(rng) for _ in range(n)])
def host_range_expand(patterns):
    result = []
    for pattern in patterns:
        match = re.search(r'\[(\d+):(\d+)\]', pattern)
        if not match:
            result.append(pattern)
            continue
        first, last = match.groups()
        result.extend(f'{pattern[:match.start()]}{number:0{len(first)}d}{pattern[match.end():]}'
                      for number in range(int(first), int(last) + 1))
    return result


def firewall(rng, n):
    return [{'service': f'svc{i}', 'port': rng.choice([22, 80, 443, 5432, 8080]), 'protocol': rng.choice(['tcp', 'udp']),
             'sources': [rng.choice(['0.0.0.0/0', '10.0.0.0/8', '192.168.1.0/24']) for _ in range(rng.randint(0, 3))]}
            for i in range(n)]


@task('firewall-rules',
      goal='Input is a list of service records {service: string, port: integer, protocol: string, sources: list of CIDR strings}. Produce one rule string "allow PROTOCOL PORT from SOURCE" per service and source (PORT in decimal). Return the distinct rules sorted by Unicode string order.',
      example={'input': [{'service': 'ssh', 'port': 22, 'protocol': 'tcp', 'sources': ['10.0.0.0/8']},
                         {'service': 'web', 'port': 443, 'protocol': 'tcp', 'sources': ['0.0.0.0/0', '10.0.0.0/8']},
                         {'service': 'alt', 'port': 22, 'protocol': 'tcp', 'sources': ['10.0.0.0/8']}],
               'output': ['allow tcp 22 from 10.0.0.0/8', 'allow tcp 443 from 0.0.0.0/0', 'allow tcp 443 from 10.0.0.0/8']},
      edge=[{'service': 'none', 'port': 1, 'protocol': 'udp', 'sources': []},
            {'service': 'big', 'port': 65535, 'protocol': 'udp', 'sources': ['::/0', '::/0']}],
      generate=firewall)
def firewall_rules(rows):
    return sorted({f'allow {row["protocol"]} {row["port"]} from {source}' for row in rows for source in row['sources']})


@task('port-conflicts',
      goal='Input is a list of bindings {service: string, host: string, port: integer}. A conflict is a (host, port) pair bound by at least two distinct services. Return a list of {host, port, services} for each conflict, where services lists the distinct services sorted by Unicode order; order conflicts by host (Unicode order), then port ascending.',
      example={'input': [{'service': 'a', 'host': 'h', 'port': 80}, {'service': 'b', 'host': 'h', 'port': 80},
                         {'service': 'a', 'host': 'h', 'port': 80}, {'service': 'c', 'host': 'g', 'port': 80}],
               'output': [{'host': 'h', 'port': 80, 'services': ['a', 'b']}]},
      edge=[{'service': 'x', 'host': '雪', 'port': 9}, {'service': 'x', 'host': '雪', 'port': 9},
            {'service': 'y', 'host': '雪', 'port': 10}, {'service': 'z', 'host': '雪', 'port': 10}, {'service': 'z', 'host': '', 'port': 10}],
      generate=lambda rng, n: [{'service': f's{rng.randint(0, 20)}', 'host': rng.choice(HOSTS),
                                'port': rng.choice([22, 80, 443, 1000, 8080])} for _ in range(n)])
def port_conflicts(rows):
    bindings = {}
    for row in rows:
        bindings.setdefault((row['host'], row['port']), set()).add(row['service'])
    return [{'host': host, 'port': port, 'services': sorted(names)}
            for (host, port), names in sorted(bindings.items()) if len(names) > 1]


@task('disk-usage-rollup',
      goal='Input is a list of files {path: absolute POSIX path without "." or ".." segments, empty segments or trailing "/", bytes: integer}. Return a map from every directory that contains at least one listed file, directly or in subdirectories, to the total bytes of those files. Directories are written as their absolute path ("/" for the root, otherwise without a trailing "/"); files themselves are not keys.',
      example={'input': [{'path': '/a/b/f', 'bytes': 5}, {'path': '/a/g', 'bytes': 2}, {'path': '/h', 'bytes': 1}],
               'output': {'/': 8, '/a': 7, '/a/b': 5}},
      edge=[{'path': '/雪/x', 'bytes': 0}, {'path': '/雪', 'bytes': 3}],
      generate=lambda rng, n: [{'path': '/' + '/'.join(rng.choice(['etc', 'var', 'log', 'a', '雪']) for _ in range(rng.randint(1, 5))),
                                'bytes': rng.randint(0, 4096)} for _ in range(n)])
def disk_usage_rollup(files):
    result = {}
    for file in files:
        parts = file['path'].split('/')[1:-1]
        for depth in range(len(parts) + 1):
            directory = '/' + '/'.join(parts[:depth])
            result[directory] = result.get(directory, 0) + file['bytes']
    return result
