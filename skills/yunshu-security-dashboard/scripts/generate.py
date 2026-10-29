"""Portable preflight -> collect/resume -> metrics -> verified openpyxl export."""
import argparse, getpass, hashlib, json, os, shutil, subprocess, sys, tempfile, uuid
from zipfile import BadZipFile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from api import Client, Pager, rows_total, save, private_dir
from metrics import activated_count, risk_ranking, outbound_ranking, size_text
from workbook_layout import inspect_template

ROOT=Path(__file__).resolve().parent
TZ=timezone(timedelta(hours=8))
BASE='https://cs-hz0.sase.eagleyun.com'

def boundaries(date,cutoff=None):
    d=datetime.strptime(date,'%Y-%m-%d').replace(tzinfo=TZ); now=datetime.now(TZ)
    if d.date()>now.date():raise ValueError('Reporting date cannot be in the future')
    end=datetime.fromisoformat(cutoff) if cutoff else (now if d.date()==now.date() else d+timedelta(days=1,seconds=-1))
    if end.tzinfo is None or end.date()!=d.date() or end>d+timedelta(days=1) or end>now:
        raise ValueError('Cutoff must be timezone-aware, on the reporting date, and not in the future')
    end=end.astimezone(TZ).replace(microsecond=0)
    if end.date()!=d.date():raise ValueError('Cutoff must fall on the reporting date in Beijing time')
    return {'date':date,'day':int(d.timestamp()),'month':int((d-timedelta(days=29)).timestamp()),
            'week':int((d-timedelta(days=6)).timestamp()),'history':int((d-timedelta(days=89)).timestamp()),'end':int(end.timestamp())}

def runtime(args,work,template):
    if sys.version_info<(3,10):raise RuntimeError('Python 3.10+ is required')
    from render_workbook import probe
    layout=inspect_template(template)
    probe(template,work/'preflight.xlsx')
    return None,None,layout

def collect(client,work,b):
    cache=work/'pages'; provenance={}
    def read(path,params=None,size=200):
        pager=Pager(client,cache,path,params,size)
        rows=list(pager)
        provenance[path+json.dumps(params or {},sort_keys=True)]={k:v for k,v in pager.state.items() if k!='request'}
        return rows
    sources=read('/idaas/identity_source/list')
    employees=read('/idaas/employee/employee_list')
    bindings=[]; counts=[]
    for source in sources:
        sid=source['source_id']
        counts.append(client.get('/idaas/identity_source/custom/employee/get_count',{'iden_source_id':sid})['data'])
        bindings.extend(read('/idaas/employee/list_with_terminal',{'iden_source_id':sid}))
    device=client.get('/epp/it/terminal/list_all_terminal',{'page_num':1,'page_size':1})
    _,device_total=rows_total(device)
    riskpager=Pager(client,cache,'/dlp/risk/event/list',{'start_time':b['history'],'end_time':b['end']},1200)
    risk_people=set()
    for row in riskpager:risk_people.add(row.get('employee_id'))
    risk_pages=[str((riskpager.root/f'page_{i:06d}.json').relative_to(work)) for i in range(1,riskpager.state['pages']+1)]
    provenance['risk']={k:v for k,v in riskpager.state.items() if k!='request'}
    pager=Pager(client,cache,'/dlp/log_process/list_dlp_file_send_log',{'start_time':b['month'],'end_time':b['end']},1200)
    audit_people=set()
    for row in pager:
        if row.get('trigger')=='securityFileSend' and row.get('action')=='observe' and row.get('employee_id'):
            audit_people.add(row['employee_id'])
    provenance['audit']={k:v for k,v in pager.state.items() if k!='request'}
    audit_pages=[str((pager.root/f'page_{i:06d}.json').relative_to(work)) for i in range(1,pager.state['pages']+1)]
    snap={'schema':2,'bounds':b,'employees':employees,'bindings':bindings,'counts':counts,'devices':device_total,
          'risk_pages':risk_pages,'risk_total':riskpager.state['rows'],
          'audit_pages':audit_pages,'audit_total':pager.state['rows'],'provenance':provenance}
    # Resolve report-visible employees absent from the directory via official detail, never fabricate names.
    wanted=risk_people|audit_people; known={e['employee_id'] for e in employees}|{e['employee_id'] for e in bindings}
    snap['details']={}
    for eid in sorted(wanted-known-{None,''}):
        detail=client.get('/idaas/employee/get_detail',{'employee_id':eid})['data']
        snap['details'][eid]={k:detail[k] for k in ('employee_id','employee_name','display_name','department') if k in detail}
    save(work/'snapshot.json',snap)
    return snap

def audit_rows(snap,base):
    seen=0
    if 'audit_inline' in snap:
        for row in snap['audit_inline']:seen+=1; yield row
    else:
        for relative in snap['audit_pages']:
            path=(base/relative).resolve()
            if not path.is_relative_to(base.resolve()):raise ValueError('Snapshot audit path escapes run directory')
            envelope=json.loads(path.read_text())
            for row in envelope['rows']:seen+=1; yield row
    if seen!=snap['audit_total']:raise RuntimeError('Snapshot audit row count does not reconcile')

def risk_rows(snap,base):
    if 'risks' in snap:
        yield from snap['risks']; return
    seen=0
    for relative in snap['risk_pages']:
        path=(base/relative).resolve()
        if not path.is_relative_to(base.resolve()):raise ValueError('Snapshot risk path escapes run directory')
        for row in json.loads(path.read_text())['rows']:seen+=1;yield row
    if seen!=snap['risk_total']:raise RuntimeError('Snapshot risk row count does not reconcile')

def build_model(snap,base):
    b=snap['bounds']; employees=snap['employees']; profiles={e['employee_id']:dict(e) for e in employees}
    active=activated_count(employees)
    if any(not isinstance(e.get('is_active'),bool) for e in employees):raise ValueError('Unknown activation state; confirm API schema')
    try:official_count=sum(int(c['total_num']) for c in snap['counts'])
    except (TypeError,ValueError,KeyError):raise ValueError('Official employee counts are missing or invalid') from None
    if official_count!=len(profiles):raise ValueError('Employee directory does not reconcile with identity-source counts; confirm tenant scope')
    for eid,detail in snap.get('details',{}).items():profiles.setdefault(eid,{}).update(detail)
    terminal={}
    for e in snap['bindings']:
        profiles.setdefault(e['employee_id'],e)
        for t in e.get('terminal_list') or []:
            for key in ['te_id','terminal_name','serial_number','mac_addr']:
                for value in str(t.get(key) or '').split(','):
                    if not value.strip():continue
                    value=value.strip().lower()
                    if value in terminal and terminal[value]!=e['employee_id']:terminal[value]=None
                    else:terminal[value]=e['employee_id']
    def identity(eid):
        e=profiles.get(eid,{}); name=e.get('employee_name') or e.get('display_name')
        if not name:raise ValueError('Missing official name; request employee detail before publishing')
        dep=e.get('department') or []
        return name,' / '.join(map(str,dep)) if isinstance(dep,list) else str(dep)
    def risk(start,history):
        out=[]
        for e in risk_ranking(risk_rows(snap,base),start,b['end'],history):
            name,dep=identity(e['employee_id'])
            for p in e['policies']:
                out.append({'employee_id':e['employee_id'],'cells':[f"{name}\n（风险分：{e['score_total']:g}）",dep,
                     p['display'],p['reports'],'\n'.join(p['channels']),p['history_reports']]})
        return out
    def audit(start):
        result=outbound_ranking(audit_rows(snap,base),start,b['end'],terminal)
        out=[]
        for e in result['ranking']:
            name,dep=identity(e['employee_id'])
            out.append([name,dep,e['bytes']/1024**3,'\n'.join(f'{typ}（{size_text(size)}）' for typ,size in e['types'])])
        return out,result['diagnostics']
    day,daydiag=audit(b['day']); month,monthdiag=audit(b['month'])
    risky={r['employee_id'] for r in risk_rows(snap,base) if b['month']<=int(r['alert_time'])<=b['end'] and r.get('employee_id')}
    return {'employees':active,'riskEmployees':len(risky),'devices':snap['devices'],'day':risk(b['day'],b['week']),
         'month':risk(b['month'],b['history']),'auditDay':day,'auditMonth':month,
         'updated':datetime.fromtimestamp(b['end'],TZ).strftime('%Y-%m-%d %H:%M:%S')+'（北京时间）',
         'scope':f"当日：{b['date']}；本月累计：{datetime.fromtimestamp(b['month'],TZ):%Y-%m-%d}至{b['date']}（最近30天）",
         'diagnostics':{'day':daydiag,'month':monthdiag},'bounds':b}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date'); p.add_argument('--cutoff'); p.add_argument('--base-url',default=BASE); p.add_argument('--api-prefix',default='')
    p.add_argument('--template',default=str(ROOT.parent/'assets/dashboard-template.xlsx')); p.add_argument('--output')
    p.add_argument('--work-dir'); p.add_argument('--resume-run'); p.add_argument('--from-snapshot'); p.add_argument('--confirmed-inputs',action='store_true')
    p.add_argument('--node',help=argparse.SUPPRESS); p.add_argument('--node-modules',help=argparse.SUPPRESS); p.add_argument('--preflight',action='store_true')
    a=p.parse_args(); template=Path(a.template).resolve()
    work=private_dir(Path(a.resume_run or a.work_dir or tempfile.mkdtemp(prefix='yunshu-dashboard-')).resolve())
    node,modules,layout=runtime(a,work,template)
    if a.preflight:print('PASS: Python, openpyxl, template and XLSX round-trip; visual preview optional'); return
    if not a.output:p.error('--output is required')
    output=Path(a.output).resolve()
    if output==template:p.error('Output must not overwrite the template')
    if a.from_snapshot:
        source=Path(a.from_snapshot).resolve(); snap=json.loads(source.read_text()); base=source.parent
        if snap.get('schema')!=2:p.error('Unsupported offline snapshot schema')
    else:
        if not a.confirmed_inputs or not a.date:p.error('Confirm the date and this execution AK/SK, then supply --confirmed-inputs --date')
        access=os.environ.get('YUNSHU_ACCESS_KEY') or getpass.getpass('This execution AK: ')
        secret=os.environ.get('YUNSHU_SECRET_KEY') or getpass.getpass('This execution SK: ')
        client=Client(a.base_url,access,secret,a.api_prefix)
        runfile=work/'run.json'
        if a.resume_run:
            run=json.loads(runfile.read_text())
            if run['binding']!=client.binding or run['bounds']['date']!=a.date:raise ValueError('Resume tenant/date mismatch')
            b=run['bounds']
            if a.cutoff and boundaries(a.date,a.cutoff)!=b:raise ValueError('Resume cutoff mismatch')
        else:
            if runfile.exists():raise ValueError('Run directory already exists; explicitly use --resume-run or a fresh directory')
            b=boundaries(a.date,a.cutoff); run={'binding':client.binding,'bounds':b}
        run['status']='collecting'; run['checked_at']=datetime.now(TZ).isoformat(); save(runfile,run)
        snap=collect(client,work,b); base=work; run['status']='collected'; save(runfile,run)
    model=build_model(snap,base); modelpath=work/'model.json'; save(modelpath,model)
    config=work/'render.json'; save(config,{'template':str(template),'layout':layout,'work':str(work),'model':str(modelpath),'output':str(output)})
    from render_workbook import render_dashboard
    render_dashboard(template,model,layout,work)
    publish(work,output,template,layout)
    if not a.from_snapshot:
        run['status']='complete'; run['output_sha256']=hashlib.sha256(output.read_bytes()).hexdigest(); save(runfile,run)
    print(json.dumps({'output':str(output),'cutoff':model['updated'],'diagnostics':model['diagnostics'],'run_dir':str(work)},ensure_ascii=False))

def publish(work,output,template,original_layout):
    from zipfile import ZipFile
    from xml.etree import ElementTree as E
    staged=work/'rendered.xlsx'; layout=json.loads((work/'render-layout.json').read_text())
    checked=inspect_template(staged)
    if checked['dayTitle']!=original_layout['dayTitle'] or checked['monthTitle']!=layout['monthTitle'] or checked['footer']!=layout['footer']:
        raise ValueError('Serialized section boundaries do not match the rendered layout')
    if set(checked['bodyMerges'])!=set(layout['expectedMerges']):raise ValueError('Name merge verification failed')
    if checked['summary']!=original_layout['summary']:raise ValueError('Original summary fields moved')
    with ZipFile(staged) as z:
        if any(n.startswith('xl/charts/') and n.endswith('.xml') for n in z.namelist()):raise ValueError('Unexpected chart in output')
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.'+output.stem+'-',suffix='.xlsx',dir=output.parent)
    os.close(fd)
    try:shutil.copyfile(staged,temp); os.replace(temp,output)
    finally:
        if Path(temp).exists():Path(temp).unlink()

if __name__=='__main__':
    try:main()
    except (RuntimeError,ValueError,KeyError,OSError,BadZipFile,subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr); sys.exit(1)
