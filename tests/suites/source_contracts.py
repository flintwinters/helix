"""Independent YAML source positions for public diagnostic assertions."""
import yaml


def location(source, path):
    node = yaml.compose(source)
    for segment in path.strip('/').split('/') if path != '/' else []:
        if isinstance(node, yaml.MappingNode):
            node = next(value for key, value in node.value if key.value == segment)
        else:
            node = node.value[int(segment)]
    return node.start_mark.line + 1, node.start_mark.column + 1


def reference(path):
    """The exact `reference` list for a slash-written test path; digits are list indices."""
    return [int(part) if part.isdigit() else part for part in path.split('/')[1:]] if path != '/' else []
