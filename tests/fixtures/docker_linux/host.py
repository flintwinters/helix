#!/usr/bin/env python3
"""Narrow Debian mechanisms invoked through Helix's authorized command effect.

One operation per invocation: `observe` reports the actual state of named resources and
`apply` makes exactly one resource match a value Helix already chose (`null` removes it).
This executable never plans, orders, retries, or decides what should exist. Fault controls
belong to the test fixture; they are not provider input.
"""
import grp
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile
import uuid

DATA = Path('/var/lib/helix-linux-host')
RUN = Path('/run/helix-linux')
# Managed paths are limited to these fixture roots; anything else is refused before any effect.
ROOTS = tuple(Path(root) for root in ('/etc/helix-demo', '/srv/helix', '/home', '/opt/helix-demo', '/var/lib/helix-demo'))
LIMIT = 1048576


def event(**details):
    """Completed mutations are fixture evidence, never a planning input."""
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / 'actions.jsonl').open('a') as stream:
        stream.write(json.dumps({'phase': 'completed', **details}, sort_keys=True) + '\n')


def fault(resource):
    path = RUN / 'fault.json'
    return json.loads(path.read_text()).get(resource) if path.exists() else None


def command(arguments, timeout=60):
    run = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})
    if run.returncode:
        raise RuntimeError(json.dumps({'command': arguments[0], 'code': run.returncode,
                                       'stderr': run.stderr[-8192:], 'stdout': run.stdout[-8192:]}))
    return run.stdout


def target():
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / 'target-id'
    if not path.exists():
        path.write_text(uuid.uuid4().hex)
    return path.read_text()


def managed(name):
    path = Path(name)
    if not path.is_absolute() or '..' in path.parts or not any(root == path or root in path.parents for root in ROOTS):
        raise ValueError('path outside owned fixture roots: ' + name)
    for ancestor in path.parents:
        if ancestor.is_symlink():
            raise ValueError('symlink in managed path: ' + name)
    return path


def owner_name(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def group_name(gid):
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def uid_of(name):
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        raise ValueError('unknown owner: ' + name) from None


def gid_of(name):
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        raise ValueError('unknown group: ' + name) from None


def observe_group(name):
    try:
        return {'gid': grp.getgrnam(name).gr_gid}
    except KeyError:
        return None


def observe_user(name):
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return None
    return {'uid': entry.pw_uid, 'group': group_name(entry.pw_gid), 'home': entry.pw_dir, 'shell': entry.pw_shell}


def observe_package(name):
    query = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status} ${Version}', name],
                           capture_output=True, text=True, timeout=10)
    if query.returncode not in (0, 1):
        raise RuntimeError('package observation failed: ' + query.stderr)
    if query.returncode == 1 and 'no packages found matching' not in query.stderr:
        raise RuntimeError('unknown package state: ' + query.stderr)
    status, _, version = query.stdout.partition(' ')
    if query.returncode == 0 and status not in ('installed', 'not-installed', 'config-files'):
        raise RuntimeError('incomplete or unknown package state: ' + query.stdout)
    return {'version': version} if query.returncode == 0 and status == 'installed' else None


def metadata(info):
    return {'owner': owner_name(info.st_uid), 'group': group_name(info.st_gid), 'mode': stat.S_IMODE(info.st_mode)}


def observe_directory(name):
    try:
        info = managed(name).lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('managed path is not a directory: ' + name)
    return metadata(info)


def observe_file(name):
    path = managed(name)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('managed path is not a regular file: ' + name)
    if info.st_size > LIMIT:
        raise ValueError('managed file exceeds fixture limit: ' + name)
    return {'content': path.read_text(), **metadata(info)}


def observe_symlink(name):
    path = managed(name)
    if not path.is_symlink():
        if path.exists():
            raise ValueError('managed path is not a symlink: ' + name)
        return None
    return {'target': os.readlink(path)}


OBSERVERS = {'group': observe_group, 'user': observe_user, 'package': observe_package,
             'directory': observe_directory, 'file': observe_file, 'symlink': observe_symlink}


def observe(payload):
    subjects = payload['subjects']
    if set(subjects) - set(OBSERVERS):
        raise ValueError('unsupported resource kind')
    return {'target': target(),
            'resources': {f'{kind}:{key}': OBSERVERS[kind](key) for kind, keys in subjects.items() for key in keys}}


def apply_group(name, value):
    if value is None:
        # `userdel` removes a user's same-named private group; a vanished group is already absent.
        if observe_group(name) is not None:
            command(['groupdel', name])
    elif observe_group(name) is None:
        command(['groupadd', '-g', str(value['gid']), name])
    else:
        command(['groupmod', '-g', str(value['gid']), name])


def apply_user(name, value):
    if value is None:
        command(['userdel', name])
        return
    # The home directory is its own declared resource; account creation never makes one.
    flags = ['-u', str(value['uid']), '-g', value['group'], '-d', value['home'], '-s', value['shell']]
    command(['usermod' if observe_user(name) else 'useradd', *([] if observe_user(name) else ['-M']), *flags, name])


def apply_package(name, value):
    arguments = ['apt-get', '-y', '--no-install-recommends', '-o', 'DPkg::Lock::Timeout=3']
    if fault(f'package:{name}') == 'repository':
        arguments += ['-o', 'Acquire::http::Proxy=http://127.0.0.1:1', '-o', 'Acquire::Retries=0']
    if value is None:
        command([*arguments, 'remove', name], timeout=120)
    else:
        command([*arguments, 'install', '--allow-downgrades', f"{name}={value['version']}"], timeout=300)


def apply_directory(name, value):
    path = managed(name)
    if value is None:
        observe_directory(name)
        path.rmdir()
        return
    if observe_directory(name) is None:
        os.mkdir(path)
    os.chown(path, uid_of(value['owner']), gid_of(value['group']))
    os.chmod(path, value['mode'])


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def apply_file(name, value):
    path = managed(name)
    observe_file(name)
    if value is None:
        path.unlink(missing_ok=True)
        return
    descriptor, staging = tempfile.mkstemp(prefix='.helix-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(value['content'])
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(staging, uid_of(value['owner']), gid_of(value['group']))
        os.chmod(staging, value['mode'])
        os.replace(staging, path)
        fsync_directory(path.parent)
    finally:
        Path(staging).unlink(missing_ok=True)


def apply_symlink(name, value):
    path = managed(name)
    observe_symlink(name)
    if value is None:
        path.unlink(missing_ok=True)
        return
    staging = path.parent / f'.helix-{uuid.uuid4().hex}'
    try:
        os.symlink(value['target'], staging)
        os.replace(staging, path)
    finally:
        if staging.is_symlink():
            staging.unlink()


APPLIERS = {'group': apply_group, 'user': apply_user, 'package': apply_package,
            'directory': apply_directory, 'file': apply_file, 'symlink': apply_symlink}


def apply(payload):
    kind, key, value = payload['kind'], payload['key'], payload['value']
    if kind not in APPLIERS or not isinstance(key, str) or not (value is None or isinstance(value, dict)):
        raise ValueError('invalid apply request')
    resource = f'{kind}:{key}'
    mode = fault(resource)
    if mode == 'fail':
        raise RuntimeError('injected ' + resource + ' failure')
    if mode == 'false-success':
        return {'completed': True, 'fixture_false_success': True}
    APPLIERS[kind](key, value)
    event(operation='apply', resource=resource, action='remove' if value is None else 'set')
    return {'completed': True, 'resource': resource}


def main():
    try:
        if len(sys.argv) != 3:
            raise ValueError('usage: helix-linux-host OP JSON')
        payload = json.loads(sys.argv[2])
        if not isinstance(payload, dict):
            raise ValueError('payload must be an object')
        operations = {'observe': observe, 'apply': apply}
        if sys.argv[1] not in operations:
            raise ValueError('unsupported host operation: ' + sys.argv[1])
        print(json.dumps(operations[sys.argv[1]](payload), sort_keys=True))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': {'kind': type(error).__name__, 'message': str(error)}}, sort_keys=True))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
