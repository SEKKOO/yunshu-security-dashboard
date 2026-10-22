"""Pure dashboard metrics; no network access, credentials, or customer data."""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation


def number(value):
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Missing or invalid numeric metric') from None
    if not n.is_finite() or n < 0:
        raise ValueError('Metric must be finite and nonnegative')
    return n


def unique(rows, key):
    seen = set()
    for row in rows:
        identity = row.get(key)
        if not identity:
            raise ValueError(f'Missing {key}')
        if identity in seen:
            raise ValueError(f'Duplicate {key}; validate pagination before reporting')
        seen.add(identity)
        yield row


def activated_count(employees):
    return sum(row.get('is_active') is True for row in unique(employees, 'employee_id'))


def risk_ranking(events, start, end, history_start, limit=10):
    people = {}; historical = Counter()
    for row in unique(events, 'event_id'):
        if not history_start <= int(row['alert_time']) <= end:
            continue
        if not row.get('employee_id'):
            raise ValueError('Unresolved employee in risk data')
        if not isinstance(row.get('policy_name'),str) or not row['policy_name'].strip():
            raise ValueError('Missing or invalid risk policy name')
        score=number(row.get('risk')); employee=row['employee_id']; policy=row['policy_name']
        historical[(employee,policy)]+=1
        if int(row['alert_time']) >= start:
            person=people.setdefault(employee,{'score':Decimal(0),'reports':0,'policies':{}})
            person['score']+=score; person['reports']+=1
            group=person['policies'].setdefault(policy,{'score':Decimal(0),'reports':0,'scores':Counter(),'channels':Counter()})
            group['score']+=score;group['reports']+=1;group['scores'][score]+=1
            names={c.get('name') or c.get('code') or c.get('type') for c in (row.get('hit_summary') or {}).get('channels') or []}
            group['channels'].update(n for n in names if n)
    selected = sorted(people, key=lambda e: (-people[e]['score'], -people[e]['reports'], e))[:limit]
    result = []
    for employee in selected:
        groups = []
        for policy, group in people[employee]['policies'].items():
            per_score=group['scores'];channels=group['channels']
            score_counts = sorted(per_score.items(), reverse=True)
            label = (f'{score_counts[0][0]:g}分' if len(score_counts) == 1 else
                     '；'.join(f'{score:g}分×{n}次' for score, n in score_counts))
            groups.append({'policy_name': policy, 'display': f'{policy}（{label}）',
                           'reports': group['reports'], 'highest_score': float(max(per_score)),
                           'score_counts': [(float(s), n) for s, n in score_counts],
                           'score_total': float(group['score']),
                           'channels': [n for n, count in sorted(channels.items(), key=lambda x: (-x[1], x[0]))[:5]],
                           'history_reports': historical[(employee, policy)]})
        groups.sort(key=lambda g: (-g['highest_score'], -g['reports'], g['policy_name']))
        result.append({'employee_id': employee, 'score_total': float(people[employee]['score']),
                       'reports': people[employee]['reports'], 'policies': groups})
    return result


def outbound_ranking(logs, start, end, terminal_map=None, limit=10):
    people = defaultdict(lambda: {'bytes': 0, 'type_bytes': Counter()})
    diagnostics = Counter()
    terminal_map = terminal_map or {}
    for row in unique(logs, 'log_id'):
        if not start <= int(row['time']) <= end:
            continue
        if row.get('trigger') != 'securityFileSend' or row.get('action') != 'observe':
            continue
        employee = row.get('employee_id')
        if not employee:
            matches = {terminal_map.get(str(row.get(k) or '').strip().lower())
                       for k in ['terminal_id', 'terminal_name', 'dev_sn', 'terminal_mac_addr']}
            matches.discard(None)
            matches.discard('')
            if len(matches) > 1:
                raise ValueError('Ambiguous terminal identity')
            employee = next(iter(matches), None)
        if not employee:
            raise ValueError('Unresolved employee in outbound data')
        if row.get('file_size') is None:
            diagnostics['missing_file_size'] += 1
            continue
        value = number(row['file_size'])
        if value != value.to_integral_value():
            raise ValueError('File size is not an integer byte count')
        size = int(value)
        category = row.get('file_parent_category') or '日志未提供类型'
        if not isinstance(category, str):
            raise ValueError('Unexpected file category schema')
        if category == '日志未提供类型':
            diagnostics['missing_file_type'] += 1
        people[employee]['bytes'] += size
        people[employee]['type_bytes'][category] += size
    selected = sorted(people, key=lambda e: (-people[e]['bytes'], e))[:limit]
    ranking = []
    for employee in selected:
        item = people[employee]
        if sum(item['type_bytes'].values()) != item['bytes']:
            raise ValueError('File type totals do not reconcile')
        ranking.append({'employee_id': employee, 'bytes': item['bytes'],
                        'types': sorted(item['type_bytes'].items(), key=lambda x: (-x[1], x[0]))[:3]})
    return {'ranking': ranking, 'diagnostics': dict(diagnostics)}


def size_text(size):
    for power, unit in [(3, 'GiB'), (2, 'MiB'), (1, 'KiB')]:
        if size >= 1024 ** power:
            return f'{size / 1024 ** power:.2f} {unit}'
    return f'{size} B'
