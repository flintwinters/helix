"""String-processing workloads: tokenizing, parsing line formats and rendering text."""
import re

from tooling.agent_tasks import task

WORDS = ['a', 'B', 'cat', 'Dog', '雪', '🙂', '$call', 'x/y', 'x-y']
LETTERS = ['apple', 'Apple', 'pear', 'fig', 'kiwi', 'plum', 'lime', 'date', 'nut', 'yam']


def tokens(pattern, source):
    return re.findall(pattern, source)


@task('reverse-words',
      goal='Input is a string of words separated by single spaces. Return the words in reverse order, joined by single spaces. Empty input returns the empty string.',
      example={'input': 'one two three', 'output': 'three two one'},
      edge='雪 $call 🙂',
      generate=lambda rng, n: ' '.join(rng.choice(WORDS) for _ in range(n)))
def reverse_words(source):
    return ' '.join(reversed(source.split(' ')))


@task('collapse-whitespace',
      goal='Input is a string. A word is a maximal run of characters other than space, tab and newline. Return the words joined by single spaces. Input without words returns the empty string.',
      example={'input': '  a\tb \n\n c  ', 'output': 'a b c'},
      edge=' \t\n ',
      generate=lambda rng, n: ''.join(rng.choice(WORDS) + rng.choice([' ', '  ', '\t', '\n', ' \n\t'])
                                      for _ in range(n)))
def collapse_whitespace(source):
    return ' '.join(tokens(r'[^ \t\n]+', source))


EMAIL = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'


def email_text(rng, n):
    parts = []
    for _ in range(n):
        kind = rng.randrange(4)
        if kind == 0:
            parts.append(f'{rng.choice(["ann", "bo", "c.d", "e+f"])}@{rng.choice(["x", "y.z"])}.{rng.choice(["io", "com", "d"])}')
        elif kind == 1:
            parts.append(f'<{rng.choice(["ann", "bo"])}@x.io>,')
        else:
            parts.append(rng.choice(WORDS + ['@', 'a@b', 'mail:']))
    return ' '.join(parts)


@task('extract-emails',
      goal=f'Input is a string. Find the leftmost non-overlapping matches of the regular expression {EMAIL} scanning left to right. Return each distinct match once, in order of first occurrence. Input without matches returns an empty list.',
      example={'input': 'Mail ann@x.io, bo@y.com or ann@x.io; not a@b', 'output': ['ann@x.io', 'bo@y.com']},
      edge='x@y.z <a.b+c@d-e.org>; @no.pe e@f.gh e@f.gh',
      generate=email_text)
def extract_emails(source):
    return list(dict.fromkeys(tokens(EMAIL, source)))


DURATION = r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?'


def duration(rng):
    if rng.random() < 0.2:
        return rng.choice(['', 'h', '5x', '1h1h', '2m3h', ' 1s', '1.5h', '-1s'])
    return ''.join(f'{rng.randint(0, 99):0{rng.choice([1, 2])}d}{unit}'
                   for unit in 'hms' if rng.random() < 0.6) or '0s'


@task('parse-durations',
      goal=f'Input is a list of strings. A valid duration is a nonempty string that fully matches {DURATION}, for example 1h30m or 45s. Map each valid duration to its total number of seconds (h = 3600, m = 60, s = 1; numbers are decimal and may have leading zeros) and every other string to null. Return the list in input order.',
      example={'input': ['1h30m', '45s', '', '2h5x', '07m'], 'output': [5400, 45, None, None, 420]},
      edge=['0s', 'h', '1h1h', '100h', '1m2s', '3h4s'],
      generate=lambda rng, n: [duration(rng) for _ in range(n)])
def parse_durations(values):
    result = []
    for value in values:
        match = re.fullmatch(DURATION, value)
        result.append(None if not value or not match else
                      sum(int(part or 0) * scale for part, scale in zip(match.groups(), (3600, 60, 1))))
    return result


def ranked(counts, limit):
    return [[word, count] for word, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


@task('word-frequency-top',
      goal='Input is a record with string field text and integer field n (n >= 0). A word is a maximal run of ASCII letters A-Z or a-z; matching is case sensitive. Return the n most frequent words as [word, count] pairs, ordered by count descending, then by word in Unicode order. Return fewer pairs when there are fewer distinct words.',
      example={'input': {'text': 'b a, b! c a b', 'n': 2}, 'output': [['b', 3], ['a', 2]]},
      edge={'text': '雪 ... Apple apple, APPLE-apple', 'n': 5},
      generate=lambda rng, n: {'text': ' '.join(rng.choice(LETTERS) + rng.choice(['', ',', '.', '!', ' 7'])
                                                for _ in range(n)), 'n': rng.randint(0, 6)})
def word_frequency_top(values):
    counts = {}
    for word in tokens(r'[A-Za-z]+', values['text']):
        counts[word] = counts.get(word, 0) + 1
    return ranked(counts, values['n'])


def config_lines(rng, n, header=False):
    lines = []
    for _ in range(n):
        kind = rng.randrange(8)
        if kind == 0:
            lines.append('')
        elif kind == 1:
            lines.append(rng.choice(['# comment', '#x=1', '; note']))
        elif kind == 2 and header:
            lines.append(f'[{rng.choice(["db", "web", "a b", "雪"])}]')
        elif kind == 3:
            lines.append(rng.choice(['novalue', ' spaced = yes ', '=empty']))
        else:
            lines.append(f'{rng.choice(["host", "port", "user", "url"])}={rng.choice(["1", "a=b", "", "雪", "x y"])}')
    return '\n'.join(lines)


@task('kv-config-parse',
      goal='Input is a string of lines separated by "\\n". Ignore empty lines and lines starting with "#". Every other line containing "=" assigns the text after its first "=" to the key before it, both verbatim (no trimming; the key may be empty). Ignore lines without "=". Later assignments replace earlier ones. Return the resulting map.',
      example={'input': '# db\nhost=a=b\n\nport=1\nhost=c\njunk', 'output': {'host': 'c', 'port': '1'}},
      edge='=v\n k = v \n#x=1\n;y=2',
      generate=config_lines)
def kv_config_parse(source):
    result = {}
    for line in source.split('\n'):
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            result[key] = value
    return result


@task('ini-parse',
      goal='Input is an INI string of lines separated by "\\n". Ignore empty lines and lines starting with ";" or "#". A line that starts with "[" and ends with "]" opens the section named by the text between them; reopening a section continues it, and an opened section appears even without keys. Every other line containing "=" assigns the text after its first "=" to the key before it, both verbatim, in the current section; keys before any header belong to section "" (which appears only when such a key exists). Ignore other lines. Later assignments replace earlier ones. Return a map from section name to its map of keys.',
      example={'input': 'a=1\n[db]\nhost=x\n; note\n[web]\n[db]\nhost=y', 'output': {'': {'a': '1'}, 'db': {'host': 'y'}, 'web': {}}},
      edge='[a b]\n[雪]\nk=[v]\n[x\nbad',
      generate=lambda rng, n: config_lines(rng, n, header=True))
def ini_parse(source):
    result, section = {}, ''
    for line in source.split('\n'):
        if not line or line[0] in ';#':
            continue
        if line.startswith('[') and line.endswith(']') and len(line) >= 2:
            section = line[1:-1]
            result.setdefault(section, {})
        elif '=' in line:
            key, value = line.split('=', 1)
            result.setdefault(section, {})[key] = value
    return result


@task('query-string-parse',
      goal='Input is a URL query string without the leading "?". Split it on "&" and skip empty segments. Split each segment at its first "=" into key and value; a segment without "=" has value "". Replace every "+" in keys and values with a space; perform no other decoding. Return a map from key to the list of its values in order of appearance.',
      example={'input': 'a=1&b=x+y&a=2&&flag', 'output': {'a': ['1', '2'], 'b': ['x y'], 'flag': ['']}},
      edge='&&=&+=+&a==b&雪',
      generate=lambda rng, n: '&'.join(rng.choice(['', 'a', 'b+c', '雪']) + rng.choice(['', '=', '=1', '=x+y', '==', '=+'])
                                       for _ in range(n)))
def query_string_parse(source):
    result = {}
    for segment in source.split('&'):
        if segment:
            key, _, value = segment.partition('=')
            result.setdefault(key.replace('+', ' '), []).append(value.replace('+', ' '))
    return result


def normalize(path):
    stack = []
    for part in path.split('/'):
        if part == '..':
            stack = stack[:-1]
        elif part not in ('', '.'):
            stack.append(part)
    return '/' + '/'.join(stack)


@task('path-normalize',
      goal='Input is a list of absolute POSIX paths (each starts with "/"). Normalize each: split on "/", drop empty and "." segments, let ".." remove the previous remaining segment (at the root it does nothing), and rejoin as "/" followed by the segments separated by "/". The root normalizes to "/". Return the list in input order.',
      example={'input': ['/a//b/./c/', '/a/../../b', '/'], 'output': ['/a/b/c', '/b', '/']},
      edge=['/..', '//', '/./雪/../x/..', '/...'],
      generate=lambda rng, n: ['/' + '/'.join(rng.choice(['a', 'b', '..', '.', '', '雪', '...'])
                                              for _ in range(rng.randint(0, 6))) for _ in range(n)])
def path_normalize(paths):
    return [normalize(path) for path in paths]


@task('template-render',
      goal='Input is a record with string field template and field values, a map from names to strings. Replace every placeholder {{NAME}}, where NAME is one or more characters from A-Z, a-z, 0-9 and _, with values[NAME], or with the empty string when NAME is absent. Scan left to right without re-scanning substituted text; leave every other character unchanged. Return the rendered string.',
      example={'input': {'template': 'Hi {{name}}, {{missing}}{x}!', 'values': {'name': 'Ann'}}, 'output': 'Hi Ann, {x}!'},
      edge={'template': '{{{a}}} {{ a }} {{a-b}} {{a}}{{a}}', 'values': {'a': '{{a}}'}},
      generate=lambda rng, n: {'template': ''.join(rng.choice(['{{name}}', '{{id}}', '{{gone}}', '{', '}', ' ', 'text', '雪', '{{ x }}'])
                                                   for _ in range(n)),
                               'values': {'name': 'Ann', 'id': '7', 'x': 'never'}})
def template_render(values):
    return re.sub(r'\{\{([A-Za-z0-9_]+)\}\}', lambda match: values['values'].get(match.group(1), ''),
                  values['template'])


def markdown(rng, n):
    return '\n'.join(rng.choice(['# Title', '## Usage', '### Deep 雪', '####### seven', '#nospace', 'text',
                                 '```', '```yaml', '', '- item', '## ']) for _ in range(n))


@task('markdown-toc',
      goal='Input is a Markdown string of lines separated by "\\n". A line starting with "```" toggles a fenced code block (initially outside). Outside fenced blocks, a heading is a line of 1 to 6 "#" characters, one space, then at least one more character. Return a list of {level: NUMBER_OF_HASHES, title: TEXT_AFTER_THE_SPACE} for the headings in order.',
      example={'input': '# A\ntext\n```\n# not\n```\n## B c', 'output': [{'level': 1, 'title': 'A'}, {'level': 2, 'title': 'B c'}]},
      edge='####### no\n#no\n###### 雪 \n## \n```yaml\n# hidden',
      generate=markdown)
def markdown_toc(source):
    fenced, result = False, []
    for line in source.split('\n'):
        if line.startswith('```'):
            fenced = not fenced
        elif not fenced and (match := re.fullmatch(r'(#{1,6}) (.+)', line)):
            result.append({'level': len(match.group(1)), 'title': match.group(2)})
    return result


@task('word-wrap',
      goal='Input is a record with string field text and integer field width (width >= 1). Words are the maximal runs of non-space characters in text. Fill lines greedily: a line holds as many consecutive words, separated by single spaces, as fit within width characters; a word longer than width occupies a line alone. Return the list of lines. Text without words returns an empty list.',
      example={'input': {'text': 'the quick brown fox', 'width': 10}, 'output': ['the quick', 'brown fox']},
      edge={'text': '  supercalifragilistic a 雪雪 ', 'width': 4},
      generate=lambda rng, n: {'text': ' '.join(rng.choice(WORDS + ['', 'longerword', 'lengthiest-word-here'])
                                                for _ in range(n)), 'width': rng.randint(1, 24)})
def word_wrap(values):
    lines = []
    for word in (word for word in values['text'].split(' ') if word):
        if lines and len(lines[-1]) + 1 + len(word) <= values['width']:
            lines[-1] += ' ' + word
        else:
            lines.append(word)
    return lines


LEVELS = ('DEBUG', 'INFO', 'WARN', 'ERROR')


def log_lines(rng, n):
    return [f'2026-01-{rng.randint(1, 28):02d}T00:00:00' + rng.choice([f' {level} msg' for level in LEVELS] +
                                                                     [' INFO', ' info x', ' FATAL boom', '', ' ', '  ERROR'])
            for _ in range(n)]


@task('log-level-counts',
      goal='Input is a list of log lines. Fields are separated by single spaces; the level is the second field (it may be empty). Count lines whose level is exactly DEBUG, INFO, WARN or ERROR under that level, and every other line, including lines without a second field, under other. Return a map with all five keys DEBUG, INFO, WARN, ERROR and other, using 0 for absent counts.',
      example={'input': ['t INFO up', 't ERROR down', 't INFO', 't'], 'output': {'DEBUG': 0, 'INFO': 2, 'WARN': 0, 'ERROR': 1, 'other': 1}},
      edge=['t info', 't  ERROR', '', 'WARN', 't WARN'],
      generate=log_lines)
def log_level_counts(lines):
    result = dict.fromkeys(LEVELS, 0) | {'other': 0}
    for line in lines:
        fields = line.split(' ')
        level = fields[1] if len(fields) > 1 and fields[1] in LEVELS else 'other'
        result[level] += 1
    return result


def access_lines(rng, n):
    return [' '.join([f'10.0.0.{rng.randint(1, 9)}', rng.choice(['GET', 'POST']),
                      rng.choice(['/', '/api', '/api/items', '/雪', '/login']),
                      str(rng.choice([200, 201, 204, 301, 404, 500])), rng.choice(['-', '0', str(rng.randint(1, 5000))])])
            for _ in range(n)]


@task('access-log-summary',
      goal='Input is a list of access-log lines, each "IP METHOD PATH STATUS BYTES" with single spaces; STATUS is three digits and BYTES is a decimal integer or "-" meaning 0. Return {requests: line count, bytes: total bytes, status_classes: map from the first status digit followed by "xx" (such as 2xx) to its line count, top_paths: up to three [path, count] pairs ordered by count descending then path in Unicode order, unique_ips: number of distinct IPs}.',
      example={'input': ['1.1.1.1 GET / 200 10', '1.1.1.2 GET /a 404 -', '1.1.1.1 POST / 201 5'],
               'output': {'requests': 3, 'bytes': 15, 'status_classes': {'2xx': 2, '4xx': 1},
                          'top_paths': [['/', 2], ['/a', 1]], 'unique_ips': 2}},
      edge=['::1 GET /雪 500 -', '::1 GET /b 301 0', '::1 GET /a 301 7', '::1 GET /c 301 1'],
      generate=access_lines)
def access_log_summary(lines):
    classes, paths, ips, total = {}, {}, set(), 0
    for line in lines:
        ip, _, path, status, size = line.split(' ')
        total += 0 if size == '-' else int(size)
        classes[status[0] + 'xx'] = classes.get(status[0] + 'xx', 0) + 1
        paths[path] = paths.get(path, 0) + 1
        ips.add(ip)
    return {'requests': len(lines), 'bytes': total, 'status_classes': classes,
            'top_paths': ranked(paths, 3), 'unique_ips': len(ips)}
