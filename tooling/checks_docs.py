"""Local navigation contracts for maintained Markdown manuals (no network access)."""
import html
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


def prose_lines(text):
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            yield number, line


def anchors(text):
    found = set()
    for _, line in prose_lines(text):
        found.update(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)["\']', line))
        heading = re.match(r'^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$', line)
        if not heading:
            continue
        title = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', heading[1])
        title = re.sub(r'<[^>]+>', '', title)
        slug = re.sub(r'[^\w\- ]', '', html.unescape(title).lower()).replace(' ', '-')
        unique, suffix = slug, 0
        while unique in found:
            suffix += 1
            unique = f'{slug}-{suffix}'
        found.add(unique)
    return found


def failures(sources):
    """Check inline local links used by the manuals, retaining source line blame."""
    errors = []
    headings = {}
    for source in sources:
        for number, line in prose_lines(source.read_text()):
            for match in re.finditer(r'\[[^]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+["\'][^)]*)?\)', line):
                target = match[1].removeprefix('<').removesuffix('>')
                url = urlsplit(target)
                if url.scheme or url.netloc:
                    continue
                destination = source.parent / unquote(url.path) if url.path else source
                if not destination.exists():
                    errors.append(f'{source}:{number}: missing link target {target}')
                elif url.fragment and destination.suffix.lower() == '.md':
                    if destination not in headings:
                        headings[destination] = anchors(destination.read_text())
                    if unquote(url.fragment) not in headings[destination]:
                        errors.append(f'{source}:{number}: missing heading anchor {target}')
    return errors


def verify(ctx):
    sources = sorted(ctx.root.glob('*.md')) + [ctx.root / 'tests' / 'README.md']
    errors = failures(sources)
    assert not errors, '\n'.join(errors)

    # Exercise the validator's failure paths through the same static suite. Keep
    # fixtures inside runtime; their missing target cannot collide with real docs.
    destination = ctx.root / 'runtime' / 'static'
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='doc-links-', dir=destination) as folder:
        root = Path(folder)
        target = root / 'target.md'
        target.write_text('# `Calls`, values & types\n# Repeat\n# Repeat\n<a id="legacy"></a>\n')
        source = root / 'source.md'
        good = ('[heading](target.md#calls-values--types)\n'
                '[duplicate](target.md#repeat-1) [legacy](target.md#legacy)\n'
                '[external](https://example.invalid/no-file#no-anchor)\n'
                '```yaml\n[example](missing.md#absent)\n```\n'
                '~~~\n[another example](missing.md)\n~~~\n')
        source.write_text(good)
        assert failures([source]) == [], failures([source])
        source.write_text(good + '[broken](target.md#absent)\n[missing](missing.md)\n')
        errors = failures([source])
        assert len(errors) == 2 and 'missing heading anchor' in errors[0] and 'missing link target' in errors[1], errors
