"""Source YAML reading and batch formatting through the authoritative runtime formatter."""
import copy
from pathlib import Path

import yaml

from tooling.output import emit


class SourceLoader(yaml.SafeLoader):
    """Normalize source tags into the `{$call: ...}` representation the runtime reads."""


SourceLoader.add_constructor("!call", lambda loader, node: {"$call": loader.construct_sequence(node, deep=True)})
SourceLoader.add_constructor("!ref", lambda loader, node: {"$ref": loader.construct_scalar(node)})


def _literal(loader, node):
    """Construct the payload under the tag its untagged spelling would resolve to."""
    payload = copy.copy(node)
    payload.tag = loader.resolve(type(node), node.value, (getattr(node, "style", None) is None, True))
    return {"$literal": loader.construct_object(payload, deep=True)}


SourceLoader.add_constructor("!literal", _literal)


def format_files(ctx, paths, *, write=False, output_format='text'):
    files = set()
    for path in map(Path, paths):
        if path.is_dir():
            files.update(file for file in path.rglob('*') if file.suffix in ('.yaml', '.yml') and file.is_file())
        else:
            files.add(path)
    results = []
    for path in sorted(files):
        try:
            source = path.read_text()
            result = ctx.helix('fmt', '-', stdin=source)
            if result.returncode:
                raise ValueError(result.stdout + result.stderr)
            formatted = result.stdout
            changed = source != formatted
            if write and changed:
                path.write_text(formatted)
            results.append({'path': str(path), 'status': 'changed' if changed else 'clean'})
        except (OSError, ValueError, yaml.YAMLError) as error:
            results.append({'path': str(path), 'status': 'error', 'reason': str(error)})
    code = int(any(item['status'] == 'error' or (not write and item['status'] == 'changed') for item in results))
    if output_format == 'yaml':
        emit({'command': 'format', 'files': results, 'written': write, 'exit_code': code})
    else:
        for item in results:
            print(f"{item['status']}: {item['path']}" + (f": {item['reason']}" if 'reason' in item else ''))
    return code
