#!/usr/bin/env python3
"""Narrow container mechanisms invoked through Helix's authorized command effect.

This executable never computes a plan, chooses desired values, retries an effect
or restores a controller. Payloads describe one explicit operation selected by
Helix. Fault controls belong to the test fixture, not to program input.
"""
import http.client
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

from common import CONFIG, DATA, FILES, RUN, SERVER, event, supervisor, wait_until


class FileInstallFailure(RuntimeError):
    """Keep visible partial replacements separate from resource completion."""

    def __init__(self, cause, progress):
        super().__init__(str(cause))
        self.cause = cause
        self.progress = progress


def command(arguments, timeout=45):
    RUN.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=RUN) as stdout, tempfile.TemporaryFile(dir=RUN) as stderr:
        child = subprocess.Popen(arguments, stdout=stdout, stderr=stderr,
                                 env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})
        deadline = time.monotonic() + timeout
        try:
            while child.poll() is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError('mechanism command exceeded its deadline')
                if max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > 1048576:
                    raise RuntimeError('mechanism command exceeded its output limit')
                time.sleep(0.02)
            stdout.seek(0)
            stderr.seek(0)
            output, error = stdout.read(1048577), stderr.read(1048577)
            if max(len(output), len(error)) > 1048576:
                raise RuntimeError('mechanism command exceeded its output limit')
            if child.returncode:
                raise RuntimeError(json.dumps({'command': arguments[0], 'code': child.returncode,
                                               'stderr': error[-8192:].decode(errors='replace'),
                                               'stdout': output[-8192:].decode(errors='replace')}))
            return output.decode()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)
            # Descendants stay in the effect command's process group. The
            # existing host cleans it when this helper exits, including errors.


def fault_mode(operation):
    path = RUN / 'fault.json'
    return json.loads(path.read_text()).get(operation) if path.exists() else None


def pause(operation, phase):
    (RUN / 'fault-reached.json').write_text(json.dumps({'operation': operation, 'phase': phase}))
    deadline = time.monotonic() + 120
    while not (RUN / 'fault-release').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('fixture pause exceeded 120 seconds')
        time.sleep(0.05)


def probe(port):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=0.5)
    try:
        connection.request('GET', '/', headers={'Connection': 'close'})
        response = connection.getresponse()
        body = response.read(1048577)
        if len(body) > 1048576:
            raise ValueError('HTTP response exceeds fixture limit')
        return {'status': response.status, 'body': body.decode(),
                'activation': response.getheader('X-Helix-Activation')}
    except (ConnectionError, TimeoutError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def file_state(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f'managed path is not a regular file: {path}')
    if info.st_size > 1048576:
        raise ValueError(f'managed file exceeds fixture limit: {path}')
    return {'content': path.read_text(), 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode)}


def observe(payload):
    query = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status} ${Version}', 'nginx'],
                           capture_output=True, text=True, timeout=5)
    if query.returncode not in (0, 1):
        raise RuntimeError('package observation failed: ' + query.stderr)
    if query.returncode == 1 and 'no packages found matching' not in query.stderr:
        raise RuntimeError('unknown package state: ' + query.stderr)
    status, _, version = query.stdout.partition(' ')
    if query.returncode == 0 and status not in ('installed', 'not-installed', 'config-files'):
        raise RuntimeError('incomplete or unknown package state: ' + query.stdout)
    version = version if status == 'installed' else None
    response = probe(payload['port'])
    service = supervisor('status')
    return {'target': (DATA / 'target-id').read_text(), 'package_version': version,
            'files': {str(path): file_state(path) for path in FILES},
            'service': {'running': service['running'],
                        'activation': response['activation'] if response is not None else None},
            'response': {key: response[key] for key in ('status', 'body')} if response is not None else None}


def checked_files(payload):
    files = payload['files']
    if not isinstance(files, dict) or not files:
        raise ValueError('files must be a nonempty map')
    result = {}
    for name, spec in files.items():
        path = Path(name)
        if path not in FILES:
            raise ValueError('path outside owned fixture resources')
        if set(spec) != {'content', 'uid', 'gid', 'mode'} or not isinstance(spec['content'], str):
            raise ValueError('invalid file specification')
        if any(type(spec[key]) is not int for key in ('uid', 'gid', 'mode')):
            raise ValueError('file metadata must be integers')
        if not 0 <= spec['mode'] <= 0o777 or spec['uid'] < 0 or spec['gid'] < 0:
            raise ValueError('unsupported file metadata')
        for ancestor in (path.parent, *path.parent.parents):
            if ancestor.is_symlink():
                raise ValueError('symlink in managed path')
        file_state(path)  # Refuse non-regular destinations before touching any files.
        result[path] = spec
    return result


def install_files(payload, fault, progress):
    files = checked_files(payload)
    if fault == 'permission-denied':
        raise PermissionError('injected managed-file permission denial')
    RUN.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='candidate-', dir=RUN) as candidate:
        candidate = Path(candidate)
        if payload['validate']:
            # Validate the complete proposed configuration before replacing live files.
            configuration = files.get(CONFIG, file_state(CONFIG))
            server = files.get(SERVER, file_state(SERVER))
            if configuration is None or server is None:
                raise ValueError('configuration validation requires both nginx fragments')
            candidate_server = candidate / 'server.conf'
            candidate_server.write_text(server['content'])
            candidate_config = candidate / 'nginx.conf'
            candidate_config.write_text(configuration['content'].replace(str(SERVER), str(candidate_server)))
            if fault == 'invalid-candidate':
                with candidate_config.open('a') as stream:
                    stream.write('\nhelix_fixture_invalid_directive on;\n')
            command(['/usr/sbin/nginx', '-t', '-c', str(candidate_config)], timeout=5)
        for index, (path, spec) in enumerate(sorted(files.items())):
            if fault == 'replace-failure' and index == 1:
                raise OSError('injected second atomic replacement failure')
            if file_state(path) == spec:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, staging = tempfile.mkstemp(prefix='.helix-', dir=path.parent)
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(spec['content'])
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chown(staging, spec['uid'], spec['gid'])
                os.chmod(staging, spec['mode'])
                progress['uncertain'] = True
                progress['uncertain_path'] = str(path)
                os.replace(staging, path)
                progress['completed_paths'].append(str(path))
                parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
                progress['uncertain'] = False
                progress.pop('uncertain_path')
                event('files-install', path=str(path))
            finally:
                Path(staging).unlink(missing_ok=True)
    return {'installed': list(map(str, files))}


def package(operation, payload, fault):
    arguments = ['apt-get', '-y', '--no-install-recommends', '-o', 'DPkg::Lock::Timeout=3']
    if fault == 'repository':
        # A package fetch failure is induced without changing global repository state.
        arguments += ['-o', 'Acquire::http::Proxy=http://127.0.0.1:1', '-o', 'Acquire::Retries=0']
    if operation == 'package-install':
        version = payload['version']
        if not isinstance(version, str) or not version or any(c.isspace() for c in version):
            raise ValueError('package version must be explicit')
        arguments += ['install', '--allow-downgrades', 'nginx=' + version]
    else:
        arguments += ['remove', 'nginx']
    command(arguments)
    event(operation)
    return {'completed': True}


def ready(payload):
    def matches():
        response = probe(payload['port'])
        return response is not None and response['status'] == 200 and response['body'] == payload['body'] and response['activation'] == payload['activation']
    wait_until(matches)


def execute(operation, payload):
    fault = fault_mode(operation)
    if fault == 'fail':
        raise RuntimeError('injected ' + operation + ' failure')
    if fault == 'delay':
        pause(operation, 'before')
    if fault in ('false-success', 'suppress-reload'):
        return {'completed': True, 'fixture_false_success': True}
    if operation == 'observe':
        return observe(payload)
    if operation == 'probe':
        return probe(payload['port'])
    if operation in ('package-install', 'package-remove'):
        result = package(operation, payload, fault)
    elif operation == 'files-install':
        progress = {'completed_paths': [], 'uncertain': False}
        try:
            result = install_files(payload, fault, progress)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            raise FileInstallFailure(error, progress) from error
    elif operation == 'files-remove':
        for name in payload['paths']:
            path = Path(name)
            if path not in FILES:
                raise ValueError('path outside owned fixture resources')
            file_state(path)
            if path.exists():
                path.unlink()
                event(operation, path=str(path))
        result = {'removed': payload['paths']}
    elif operation in ('service-start', 'service-reload', 'service-stop'):
        result = supervisor(operation.removeprefix('service-'))
        if operation != 'service-stop':
            ready(payload)
    else:
        raise ValueError('unsupported host operation: ' + operation)
    if fault == 'after-mutation':
        pause(operation, 'after')
    return result


def main():
    try:
        if len(sys.argv) != 3:
            raise ValueError('usage: helix-nginx-host OP JSON')
        payload = json.loads(sys.argv[2])
        if not isinstance(payload, dict):
            raise ValueError('payload must be an object')
        result = execute(sys.argv[1], payload)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        cause = error.cause if isinstance(error, FileInstallFailure) else error
        detail = {'kind': type(cause).__name__, 'message': str(cause)}
        if isinstance(error, FileInstallFailure):
            detail.update(error.progress)
        print(json.dumps({'error': detail}, sort_keys=True))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
