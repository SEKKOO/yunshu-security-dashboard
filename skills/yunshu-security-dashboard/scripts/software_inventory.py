"""Collect current installations or rebuild a software-only model from a snapshot."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import getpass
import json
import os
from pathlib import Path

from api import Client, private_dir, rows_total, save

PATH = '/epp/software/analysis/page_installed_software'
BASE = 'https://cs-hz0.sase.eagleyun.com'
FAMILIES = ('Dassault Systemes Software Version', 'Dassault Systemes SIMULIA', 'Solidworks')
DISPLAY_NAMES = dict(zip(FAMILIES, ('CATIA', 'ABAQUS', 'Solidworks')))
TZ = timezone(timedelta(hours=8))


def matches(name, family):
    name, family = name.casefold(), family.casefold()
    return family in name if family == 'solidworks' else name.startswith(family)


def collect(client, families):
    queries = list(dict.fromkeys('Dassault' if f.startswith('Dassault ') else f for f in families))
    batches = []
    started = datetime.now(TZ).isoformat(timespec='seconds')
    for term in queries:
        rows, page, expected = [], 1, None
        while True:
            client.sleep(1.1)
            body = client.get(PATH, {'sync_mode': 'full', 'software_name': term,
                                    'page_num': page, 'page_size': 2000})
            batch, total = rows_total(body)
            info = body['data']['page_info']
            if info.get('current_page') != page:
                raise ValueError('Unexpected installation page number')
            if expected is not None and total != expected:
                raise ValueError('Installation total drifted; restart the snapshot')
            expected = total
            if not batch and len(rows) < total:
                raise ValueError('Empty page before installation total')
            rows.extend(batch)
            if len(rows) > total:
                raise ValueError('Installation pagination exceeded total')
            if len(rows) == total:
                break
            page += 1
            if page > 10000:
                raise ValueError('Installation page limit exceeded')
        batches.append({'query': term, 'total': expected, 'rows': rows})
    return {'schema': 'yunshu-software-v1', 'started_at': started,
            'queried_at': datetime.now(TZ).isoformat(timespec='seconds'),
            'families': list(families), 'batches': batches}


def build_model(snapshot):
    if snapshot.get('schema') != 'yunshu-software-v1':
        raise ValueError('Unknown software snapshot schema')
    # Enforce a real captured timestamp even in offline mode.
    if datetime.fromisoformat(snapshot['queried_at']).tzinfo is None:
        raise ValueError('Snapshot time must include a timezone')
    families = snapshot['families']
    if not families or any(f not in FAMILIES for f in families):
        raise ValueError('Unsupported software family; confirm matching rules before extending')
    allrows = []
    for batch in snapshot['batches']:
        if len(batch['rows']) != batch['total']:
            raise ValueError('Software snapshot count mismatch')
        allrows.extend(batch['rows'])
    sections = []
    for family in families:
        candidates = [x for x in allrows if matches(x['software_name'], family)]
        rows, seen = [], set()
        for x in candidates:
            emp, dev = x.get('biz_related_emp') or {}, x.get('biz_related_terminal') or {}
            if not dev.get('id'):
                raise ValueError('Installation has no device ID; confirm schema before publishing')
            key = (emp.get('id', ''), dev['id'], x['software_name'],
                   x.get('software_version') or '', x.get('install_path') or '')
            if key in seen:
                continue
            seen.add(key)
            departments = emp.get('department') or []
            if not isinstance(departments, list) or any(not isinstance(d, dict) for d in departments):
                raise ValueError('Unknown installation department schema')
            rows.append({'employee_id': emp.get('id', ''), 'name': emp.get('name') or '未关联员工',
                         'department': ' / '.join(d['name'] for d in departments if d.get('name')) or '未提供',
                         'device_id': dev['id'], 'device': dev.get('name') or dev['id'],
                         'software': x['software_name'], 'version': x.get('software_version') or '',
                         'path': x.get('install_path') or '接口未提供安装路径'})
        rows.sort(key=lambda x: (x['software'].casefold(), x['version'], x['employee_id'], x['device_id'], x['path']))
        sections.append({'title': DISPLAY_NAMES[family], 'family': family, 'rows': rows, 'source_count': len(candidates),
                         'duplicates': len(candidates)-len(rows), 'installation_count': len(rows),
                         'software_names': dict(Counter(x['software'] for x in rows)),
                         'terminal_count': len({x['device_id'] for x in rows}),
                         'employee_count': len({x['employee_id'] for x in rows if x['employee_id']}),
                         'missing_paths': sum(x['path'] == '接口未提供安装路径' for x in rows)})
    return {'queried_at': snapshot['queried_at'], 'scope': '安装即纳入', 'sections': sections}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir', required=True)
    p.add_argument('--families', nargs='+', choices=FAMILIES)
    p.add_argument('--from-snapshot')
    p.add_argument('--confirmed-inputs', action='store_true')
    p.add_argument('--base-url', default=BASE)
    p.add_argument('--api-prefix', default='')
    a = p.parse_args()
    work = private_dir(Path(a.work_dir).resolve())
    target = work/'software-snapshot.json'
    if a.from_snapshot:
        snap = json.loads(Path(a.from_snapshot).read_text())
        if a.families and list(a.families) != snap['families']:
            p.error('Offline mode preserves snapshot families')
    else:
        if not a.confirmed_inputs or not a.families:
            p.error('Confirm date, credentials and software scope, then use --confirmed-inputs --families')
        if target.exists():
            p.error('Use a new run directory or --from-snapshot; existing snapshot will not be refreshed')
        access = os.environ.get('YUNSHU_ACCESS_KEY') or getpass.getpass('This execution AK: ')
        secret = os.environ.get('YUNSHU_SECRET_KEY') or getpass.getpass('This execution SK: ')
        snap = collect(Client(a.base_url, access, secret, a.api_prefix), a.families)
        save(target, snap)
    model = build_model(snap)
    save(work/'software-model.json', model)
    print(json.dumps({'model': str(work/'software-model.json'), 'queried_at': model['queried_at'],
                      'counts': {s['title']: s['installation_count'] for s in model['sections']}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
