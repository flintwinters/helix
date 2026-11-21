"""Business workloads: carts, ledgers, joins, validation and report rendering."""
import re

from tooling.agent_tasks import task
from tooling.agent_tasks.records import NAMES

SKUS = ['nut', 'bolt', 'gear', '雪', 'x-1']
ACCOUNTS = ['cash', 'bank', 'card', '雪']


@task('cart-total',
      goal='Input is a record with fields items (a list of {sku: string, price_cents: integer >= 0, qty: integer >= 0}) and discounts (a map from sku to an integer percent from 0 to 100). A line costs price_cents * qty minus its discount, where the discount is floor(price_cents * qty * percent / 100) for the line\'s sku (0 without one). Return the total cost of all lines in cents.',
      example={'input': {'items': [{'sku': 'nut', 'price_cents': 199, 'qty': 3}, {'sku': 'bolt', 'price_cents': 50, 'qty': 1}],
                         'discounts': {'nut': 10, 'gear': 50}}, 'output': 588},
      edge={'items': [{'sku': '雪', 'price_cents': 1, 'qty': 1}, {'sku': '', 'price_cents': 0, 'qty': 9}], 'discounts': {'雪': 100, '': 3}},
      generate=lambda rng, n: {'items': [{'sku': rng.choice(SKUS), 'price_cents': rng.randint(0, 5000), 'qty': rng.randint(0, 12)}
                                         for _ in range(n)],
                               'discounts': {sku: rng.randint(0, 100) for sku in rng.sample(SKUS, rng.randint(0, 3))}})
def cart_total(values):
    total = 0
    for item in values['items']:
        cost = item['price_cents'] * item['qty']
        total += cost - cost * values['discounts'].get(item['sku'], 0) // 100
    return total


def ledger_ops(rng, n):
    ops = []
    for _ in range(n):
        kind, amount = rng.choice(['deposit', 'withdraw', 'transfer']), rng.randint(1, 300)
        if kind == 'transfer':
            ops.append({'op': kind, 'from': rng.choice(ACCOUNTS), 'to': rng.choice(ACCOUNTS), 'amount': amount})
        else:
            ops.append({'op': kind, 'account': rng.choice(ACCOUNTS), 'amount': amount})
    return ops


@task('ledger-balances',
      goal='Input is a list of operations applied in order to accounts that start at balance 0: {op: "deposit", account, amount}, {op: "withdraw", account, amount} and {op: "transfer", from, to, amount}, with positive integer amounts. A withdraw or transfer is rejected, changing nothing, when the amount exceeds the current balance of the debited account (withdraw: account; transfer: from). A transfer may name the same account twice. Return {balances: a map from every account named by an applied operation to its final balance, rejected: the zero-based indices of rejected operations in order}.',
      example={'input': [{'op': 'deposit', 'account': 'a', 'amount': 10}, {'op': 'transfer', 'from': 'a', 'to': 'b', 'amount': 4},
                         {'op': 'withdraw', 'account': 'b', 'amount': 5}, {'op': 'withdraw', 'account': 'c', 'amount': 1}],
               'output': {'balances': {'a': 6, 'b': 4}, 'rejected': [2, 3]}},
      edge=[{'op': 'transfer', 'from': '雪', 'to': '雪', 'amount': 1}, {'op': 'deposit', 'account': '雪', 'amount': 1},
            {'op': 'transfer', 'from': '雪', 'to': '雪', 'amount': 1}, {'op': 'withdraw', 'account': '雪', 'amount': 1}],
      generate=ledger_ops)
def ledger_balances(ops):
    balances, rejected = {}, []
    for index, op in enumerate(ops):
        debited = {'deposit': None, 'withdraw': op.get('account'), 'transfer': op.get('from')}[op['op']]
        if debited is not None and balances.get(debited, 0) < op['amount']:
            rejected.append(index)
            continue
        if op['op'] == 'transfer':
            balances[op['from']] = balances.get(op['from'], 0) - op['amount']
            balances[op['to']] = balances.get(op['to'], 0) + op['amount']
        else:
            sign = 1 if op['op'] == 'deposit' else -1
            balances[op['account']] = balances.get(op['account'], 0) + sign * op['amount']
    return {'balances': balances, 'rejected': rejected}


@task('join-orders',
      goal='Input is a record with fields customers (a list of {id, name} with distinct string ids) and orders (a list of {id, customer, amounts: list of integers}). Return one {order, name, total} per order in input order: order is the order id, name is the name of the customer whose id equals the order\'s customer (null when there is none), and total is the sum of amounts.',
      example={'input': {'customers': [{'id': 'c1', 'name': 'Ann'}],
                         'orders': [{'id': 'o1', 'customer': 'c1', 'amounts': [5, 7]}, {'id': 'o2', 'customer': 'c9', 'amounts': []}]},
               'output': [{'order': 'o1', 'name': 'Ann', 'total': 12}, {'order': 'o2', 'name': None, 'total': 0}]},
      edge={'customers': [{'id': '', 'name': '雪'}], 'orders': [{'id': '', 'customer': '', 'amounts': [-1, 1]}]},
      generate=lambda rng, n: {'customers': [{'id': f'c{i}', 'name': rng.choice(NAMES)} for i in range(n)],
                               'orders': [{'id': f'o{i}', 'customer': f'c{rng.randint(0, n + 2)}',
                                           'amounts': [rng.randint(-10, 500) for _ in range(rng.randint(0, 4))]} for i in range(n)]})
def join_orders(values):
    names = {customer['id']: customer['name'] for customer in values['customers']}
    return [{'order': order['id'], 'name': names.get(order['customer']), 'total': sum(order['amounts'])}
            for order in values['orders']]


@task('monthly-totals',
      goal='Input is a list of {date: "YYYY-MM-DD" string, amount: integer}. Return a map from each month "YYYY-MM" present in the input to the sum of its amounts.',
      example={'input': [{'date': '2024-01-31', 'amount': 5}, {'date': '2024-02-01', 'amount': 2}, {'date': '2024-01-02', 'amount': -1}],
               'output': {'2024-01': 4, '2024-02': 2}},
      edge=[{'date': '0001-12-31', 'amount': 0}],
      generate=lambda rng, n: [{'date': f'{rng.choice([2023, 2024])}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}',
                                'amount': rng.randint(-100, 1000)} for _ in range(n)])
def monthly_totals(rows):
    result = {}
    for row in rows:
        result[row['date'][:7]] = result.get(row['date'][:7], 0) + row['amount']
    return result


def user_record(rng):
    return {key: rng.choice(choices) for key, choices in
            {'name': ['ann', '雪', '', None, 7], 'age': [0, 42, 150, -1, 151, '30', True, 2.5, None],
             'email': ['a@b', 'ann@example.com', 'no-at', 'a@@b', '@b', 'a@', 5, '雪@雪']}.items()
            if rng.random() < 0.9}


@task('validate-users',
      goal='Input is a list of user records. A record is valid when name is a nonempty string, age is an integer (not a boolean or float) from 0 to 150 inclusive, and email is a string with exactly one "@" that has at least one character before and after it. Absent fields are invalid. Return a list of {index, errors} for invalid records only, in input order, where index is zero-based and errors lists the invalid field names among "name", "age" and "email" in that order.',
      example={'input': [{'name': 'ann', 'age': 30, 'email': 'a@b.c'}, {'name': '', 'age': 200, 'email': 'a@b.c'}, {'age': 1, 'email': 'x'}],
               'output': [{'index': 1, 'errors': ['name', 'age']}, {'index': 2, 'errors': ['name', 'email']}]},
      edge=[{'name': '雪', 'age': True, 'email': '雪@雪'}, {'name': 'x', 'age': 150, 'email': 'a@b@c'}, {}],
      generate=lambda rng, n: [user_record(rng) for _ in range(n)])
def validate_users(rows):
    result = []
    for index, row in enumerate(rows):
        name, age, email = row.get('name'), row.get('age'), row.get('email')
        errors = [field for field, valid in [
            ('name', isinstance(name, str) and name != ''),
            ('age', isinstance(age, int) and not isinstance(age, bool) and 0 <= age <= 150),
            ('email', isinstance(email, str) and re.fullmatch(r'[^@]+@[^@]+', email) is not None)] if not valid]
        if errors:
            result.append({'index': index, 'errors': errors})
    return result


def money(cents):
    return f'{cents // 100}.{cents % 100:02d}'


@task('invoice-render',
      goal='Input is a list of line items {description: string, qty: integer >= 0, unit_cents: integer >= 0}. Return a string with one line per item, "DESCRIPTION xQTY = AMOUNT", followed by a final line "TOTAL = AMOUNT", every line terminated by "\\n". An item\'s amount is qty * unit_cents, the total is the sum of item amounts, and AMOUNT is written as whole units, ".", and exactly two digits of cents (for example 1205 cents is "12.05" and 7 cents is "0.07").',
      example={'input': [{'description': 'Widget', 'qty': 2, 'unit_cents': 1250}, {'description': 'Nut', 'qty': 7, 'unit_cents': 1}],
               'output': 'Widget x2 = 25.00\nNut x7 = 0.07\nTOTAL = 25.07\n'},
      edge=[{'description': '', 'qty': 0, 'unit_cents': 99}, {'description': '雪 x1 = 0.00', 'qty': 1, 'unit_cents': 100000}],
      generate=lambda rng, n: [{'description': rng.choice(SKUS + NAMES), 'qty': rng.randint(0, 20),
                                'unit_cents': rng.randint(0, 99999)} for _ in range(n)])
def invoice_render(items):
    lines = [f'{item["description"]} x{item["qty"]} = {money(item["qty"] * item["unit_cents"])}\n' for item in items]
    return ''.join(lines) + f'TOTAL = {money(sum(item["qty"] * item["unit_cents"] for item in items))}\n'


@task('table-render',
      goal='Input is a record with fields columns (a nonempty list of header strings) and rows (a list of lists of strings, one string per column). Each column\'s width is the largest length in characters among its header and cells. Pad every cell, headers included, on the right with spaces to its column width. Return a list of lines: the header cells joined by " | ", then a separator of width-many "-" per column joined by "-+-", then each row\'s cells joined by " | ".',
      example={'input': {'columns': ['id', 'name'], 'rows': [['1', 'Ann'], ['22', 'Bo']]},
               'output': ['id | name', '---+-----', '1  | Ann ', '22 | Bo  ']},
      edge={'columns': ['', '雪'], 'rows': [['', ''], ['雪雪', 'x']]},
      generate=lambda rng, n: {'columns': ['sku', 'name', 'qty'],
                               'rows': [[rng.choice(SKUS), rng.choice(NAMES), str(rng.randint(0, 10 ** rng.randint(0, 6)))]
                                        for _ in range(n)]})
def table_render(values):
    columns, rows = values['columns'], values['rows']
    widths = [max(len(cell) for cell in column) for column in zip(columns, *rows)]
    line = lambda cells: ' | '.join(cell.ljust(width) for cell, width in zip(cells, widths))
    return [line(columns), '-+-'.join('-' * width for width in widths)] + [line(row) for row in rows]
