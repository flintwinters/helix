"""Serialization workloads over CSV, JSON and YAML text."""
import csv
import io
import json

import yaml

from tooling.agent_tasks import task
from tooling.agent_tasks.records import merge

CELLS = ['a', 'x,y', 'say "hi"', '雪', 'two words', '', 'line\nbreak', '0']


def quote(field):
    return f'"{field.replace(chr(34), chr(34) * 2)}"' if not field or any(c in field for c in ',"\r\n') else field


def csv_text(rows, end='\n'):
    return ''.join(','.join(quote(field) for field in row) + end for row in rows)


def csv_rows(source):
    return list(csv.reader(io.StringIO(source, newline='')))


@task('csv-to-records',
      goal='Input is CSV text: fields separated by ",", every row terminated by "\\n", fields optionally enclosed in double quotes (inside which "," and "\\n" are literal and "" is one quote). The first row is the header of distinct column names; every other row has one field per column. Return a list with one map per data row from column name to field string, in row order.',
      example={'input': 'id,name\n1,"Smith, J"\n2,""\n', 'output': [{'id': '1', 'name': 'Smith, J'}, {'id': '2', 'name': ''}]},
      edge='"a,b","c""d",雪\n"",,"x\ny"\n',
      generate=lambda rng, n: csv_text([['id', 'note', 'tag']] + [[str(i), rng.choice(CELLS), rng.choice(CELLS)]
                                                                 for i in range(n)]))
def csv_to_records(source):
    header, *rows = csv_rows(source)
    return [dict(zip(header, row)) for row in rows]


@task('records-to-csv',
      goal='Input is a record with fields columns (a list of column names) and rows (a list of maps from column names to strings). Return CSV text: a header row of the column names, then one row per input row giving its values in column order, with "" for an absent column. Separate fields with "," and terminate every row with "\\r\\n". Enclose a field in double quotes exactly when it is empty or contains ",", a double quote, "\\r" or "\\n"; double each double quote inside an enclosed field.',
      example={'input': {'columns': ['id', 'note'], 'rows': [{'id': '1', 'note': 'a,b'}, {'id': '2'}]},
               'output': 'id,note\r\n1,"a,b"\r\n2,""\r\n'},
      edge={'columns': ['say "x"', '雪'], 'rows': [{}, {'雪': 'line\nbreak', 'extra': 'ignored'}]},
      generate=lambda rng, n: {'columns': ['id', 'note', 'tag'],
                               'rows': [{key: rng.choice(CELLS) for key in ('id', 'note', 'tag', 'other')
                                         if rng.random() < 0.8} for _ in range(n)]})
def records_to_csv(values):
    return csv_text([values['columns']] + [[row.get(column, '') for column in values['columns']]
                                           for row in values['rows']], '\r\n')


@task('csv-column-totals',
      goal='Input is a record with fields csv (CSV text with a header row, rows terminated by "\\n", fields optionally double-quoted) and columns (a list of header names). Every listed column holds decimal integers, possibly negative or quoted. Return a map from each listed column to the integer sum of its values over the data rows.',
      example={'input': {'csv': 'item,qty,cents\nnut,2,"-50"\n"bolt, big",3,125\n', 'columns': ['qty', 'cents']},
               'output': {'qty': 5, 'cents': 75}},
      edge={'csv': 'a,b\n', 'columns': ['b', 'a']},
      generate=lambda rng, n: {'csv': csv_text([['sku', 'qty', 'note', 'cents']] + [[rng.choice(CELLS), str(rng.randint(0, 9)), rng.choice(CELLS),
                                                                                    str(rng.randint(-999, 9999))] for _ in range(n)]),
                               'columns': rng.choice([['qty'], ['cents', 'qty'], []])})
def csv_column_totals(values):
    header, *rows = csv_rows(values['csv'])
    return {column: sum(int(row[header.index(column)]) for row in rows) for column in values['columns']}


def json_value(rng, depth):
    if depth == 0 or rng.random() < 0.25:
        return rng.choice([0, -3, 'x', '雪', None, True, False, 'a"b'])
    if rng.random() < 0.5:
        return [json_value(rng, depth - 1) for _ in range(rng.randint(0, 4))]
    return {rng.choice(['a', 'b', 'c', '']): json_value(rng, depth - 1) for _ in range(rng.randint(0, 4))}


def json_path(rng, value):
    path = []
    while rng.random() < 0.8:
        if isinstance(value, dict) and value and rng.random() < 0.9:
            key = rng.choice(sorted(value))
        elif isinstance(value, list) and value and rng.random() < 0.9:
            key = rng.randrange(len(value) + 1)
        else:
            key = rng.choice(['a', 0, 'zz', 2])
        path.append(key)
        value = value.get(key) if isinstance(value, dict) else value[key] if isinstance(value, list) and isinstance(key, int) and key < len(value) else None
    return path


def json_document(rng, n):
    document = {f'k{i}': json_value(rng, 4) for i in range(n)}
    return {'document': json.dumps(document, ensure_ascii=False), 'path': json_path(rng, document)}


@task('json-extract',
      goal='Input is a record with fields document (JSON text) and path (a list of string keys and non-negative integer indices). Parse the document and follow the path: a string step selects that key of a JSON object, an integer step selects that index of a JSON array. Return the selected value, or null when any step does not apply (absent key or index, or the wrong kind of container or step). An empty path returns the whole document.',
      example={'input': {'document': '{"a": [{"b": 7}]}', 'path': ['a', 0, 'b']}, 'output': 7},
      edge={'document': '{"0": [1], "x": {"y": null}}', 'path': [0]},
      generate=json_document)
def json_extract(values):
    value = json.loads(values['document'])
    for step in values['path']:
        if isinstance(step, str) and isinstance(value, dict) and step in value:
            value = value[step]
        elif isinstance(step, int) and isinstance(value, list) and step < len(value):
            value = value[step]
        else:
            return None
    return value


def event_lines(rng, n):
    lines = []
    for _ in range(n):
        if rng.random() < 0.15:
            lines.append(rng.choice(['', '{', 'null', '[1]', '{"user": 1, "ms": 2}', '{"user": "ann", "ms": "5"}',
                                     '{"user": "ann"}', '{"user": "bo", "ms": true}', '{"user": "bo", "ms": 1.5}']))
        else:
            lines.append(json.dumps({'user': rng.choice(['ann', 'bo', '雪']), 'ms': rng.randint(0, 500),
                                     'extra': rng.choice([None, 'x'])}, ensure_ascii=False))
    return '\n'.join(lines)


@task('json-lines-aggregate',
      goal='Input is JSON Lines text: lines separated by "\\n". Ignore empty lines. A valid line is a JSON object with a string field user and an integer field ms (booleans and floats are not integers); other fields are allowed. Every other nonempty line is invalid. Return {users: map from user to {events: count of valid lines, total_ms: sum of ms}, invalid: number of invalid lines}.',
      example={'input': '{"user": "ann", "ms": 5}\n\n{"user": "ann", "ms": 7}\nnot json\n{"user": "bo"}',
               'output': {'users': {'ann': {'events': 2, 'total_ms': 12}}, 'invalid': 2}},
      edge='{"user": "", "ms": 0}\n{"user": "x", "ms": -1, "user2": 3}\n[]\n"s"',
      generate=event_lines)
def json_lines_aggregate(source):
    users, invalid = {}, 0
    for line in source.split('\n'):
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            value = None
        if (isinstance(value, dict) and isinstance(value.get('user'), str)
                and isinstance(value.get('ms'), int) and not isinstance(value['ms'], bool)):
            stats = users.setdefault(value['user'], {'events': 0, 'total_ms': 0})
            stats['events'] += 1
            stats['total_ms'] += value['ms']
        else:
            invalid += 1
    return {'users': users, 'invalid': invalid}


@task('yaml-config-merge',
      goal='Input is a list of YAML documents (strings), each a mapping. Parse them and deep-merge them in order: for a key present in both, when both values are maps merge them recursively, otherwise the later value wins (lists are replaced). Return the merged map; an empty list returns an empty map.',
      example={'input': ['db: {host: a, port: 1}\nmode: dev\n', 'db: {host: b}\nmode: [prod]\n'],
               'output': {'db': {'host': 'b', 'port': 1}, 'mode': ['prod']}},
      edge=['a: {b: {c: 1}}\n', 'a: {b: null}\n', 'a: {b: {d: 2}}\n'],
      generate=lambda rng, n: [json.dumps({rng.choice(['a', 'b', 'c', '雪']): json_value(rng, 3) for _ in range(rng.randint(0, 3))},
                                          ensure_ascii=False) for _ in range(n)])
def yaml_config_merge(documents):
    result = {}
    for document in documents:
        result = merge(result, yaml.safe_load(document))
    return result
