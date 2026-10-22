"""Offline behavioral tests using synthetic records only."""
from metrics import activated_count, risk_ranking, outbound_ranking


def event(i, employee, policy, risk, when=15, level='middle'):
    return dict(event_id=i, employee_id=employee, policy_name=policy, risk=risk,
                alert_time=when, level=level,
                hit_summary={'count': 9999, 'channels': [{'name': 'Git'}, {'name': 'Git'}]},
                logs_total=9999)


events = [event('1', 'a', '基线', 6), event('2', 'a', '基线', 6),
          event('3', 'a', '另一策略', 8, level='high'),
          event('4', 'b', '低危策略', 1, level='low'),
          event('5', 'b', '低危策略', 1, level='low'),
          event('6', 'b', '低危策略', 1, level='low'),
          event('7', 'a', '基线', 6, when=5), event('8', 'a', '基线', 6, when=30)]
rank = risk_ranking(events, 10, 20, 0)
assert [r['employee_id'] for r in rank] == ['a', 'b']
assert rank[0]['score_total'] == 20  # Not 20 * 9999; historical and future excluded.
assert [p['highest_score'] for p in rank[0]['policies']] == [8, 6]
assert rank[0]['policies'][1]['reports'] == 2
assert rank[0]['policies'][1]['history_reports'] == 3
assert rank[0]['policies'][1]['display'] == '基线（6分）'
assert rank[1]['score_total'] == 3  # Low-level-only employee remains eligible.
assert activated_count([{'employee_id': 'a', 'is_active': True, 'status': 'pause'},
                        {'employee_id': 'b', 'is_active': False, 'status': 'normal'}]) == 1


def log(i, **extra):
    return dict(log_id=i, employee_id='a', time=15, trigger='securityFileSend',
                action='observe', file_size=1024, file_parent_category='文本文档', **extra)


logs = [log('1'), log('2'), {**log('3'), 'file_size': 8192, 'file_parent_category': '压缩文件'},
        {**log('4'), 'file_size': None}, {**log('5'), 'trigger': 'codeUpload'},
        {**log('6'), 'action': 'block'}, {**log('7'), 'time': 30},
        {**log('8'), 'policy_action': 'block', 'file_size': 512, 'file_parent_category': ''}]
out = outbound_ranking(logs, 10, 20)
assert out['ranking'][0]['bytes'] == 10752
assert out['ranking'][0]['types'][0] == ('压缩文件', 8192)
assert out['diagnostics']['missing_file_size'] == 1
assert out['diagnostics']['missing_file_type'] == 1
for function, rows, args in [(risk_ranking, events + [events[0]], (10, 20, 0)),
                              (outbound_ranking, logs + [logs[0]], (10, 20))]:
    try:
        function(rows, *args)
        raise AssertionError('Duplicate records must be rejected')
    except ValueError:
        pass
print('PASS: all levels; report counts; score sums; ranking; histories; activation; actual release filter; type-byte totals; duplicate rejection.')
