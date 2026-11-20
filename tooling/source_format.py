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


def positions(source):
    """Locate structural paths, including mapping keys, without constructing values."""
    result = {}

    def visit(node, path):
        result[path] = (node.start_mark.line, node.start_mark.column)
        if node.tag in ('!call', '!ref', '!literal'):
            path = path + ('$' + node.tag[1:],)
            result[path] = (node.start_mark.line, node.start_mark.column)
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                visit(value, path + (key.value,))
                result[path + (key.value,)] = (key.start_mark.line, key.start_mark.column)
        elif isinstance(node, yaml.SequenceNode):
            for index, value in enumerate(node.value):
                visit(value, path + (index,))

    visit(yaml.compose(source), ())
    return result


def retain_comments(source, formatted):
    """Keep comments near their following structural path, even when it becomes inline.

    Scalar token spans exclude hashes in quoted strings and block scalar content.
    Inline comments become standalone comments before the containing output line.
    """
    tokens = list(yaml.scan(source))
    protected = [(token.start_mark.index, token.end_mark.index) for token in tokens
                 if isinstance(token, yaml.tokens.ScalarToken)]
    before, after = positions(source), positions(formatted)
    anchors = sorted(before, key=lambda path: (*before[path], len(path)))
    comments = {}
    offset = 0
    for line_index, line in enumerate(source.splitlines(keepends=True)):
        for column, char in enumerate(line):
            if char == '#' and not any(start <= offset + column < end for start, end in protected):
                following = next((path for path in anchors if before[path] >= (line_index, column)), None)
                target = after[following][0] if following is not None else len(formatted.splitlines())
                comments.setdefault(target, []).append(line[column:].rstrip())
                break
        offset += len(line)
    output = []
    lines = formatted.splitlines()
    for index in range(len(lines) + 1):
        indent = len(lines[index]) - len(lines[index].lstrip()) if index < len(lines) else 0
        output.extend(' ' * indent + comment for comment in comments.get(index, []))
        if index < len(lines):
            output.append(lines[index])
    return '\n'.join(output) + '\n'


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
            formatted = retain_comments(source, result.stdout)
            # The runtime verifies its emission; verify comment reinsertion as well.
            verified = ctx.helix('fmt', '-', stdin=formatted)
            if verified.returncode or verified.stdout != result.stdout:
                raise ValueError('comment preservation changed the parsed program')
            changed = source != formatted
            if write and changed:
                path.write_text(formatted)
            results.append({'path': str(path), 'status': 'changed' if changed else 'clean'})
        except (OSError, ValueError, yaml.YAMLError) as error:
            results.append({'path': str(path), 'status': 'error', 'reason': str(error)})
    code = int(any(item['status'] == 'error' or (not write and item['status'] == 'changed') for item in results))
    if output_format == 'json':
        emit({'command': 'format', 'files': results, 'written': write, 'exit_code': code})
    else:
        for item in results:
            print(f"{item['status']}: {item['path']}" + (f": {item['reason']}" if 'reason' in item else ''))
    return code
