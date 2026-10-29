"""Offline transport/pagination tests; optional complete Excel test using mock API data."""
import argparse, io, json, subprocess, sys, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse,parse_qs
from api import Client, Pager, NoRedirect, rows_total
from generate import boundaries, collect

RISK='/dlp/risk/event/list'
def body(rows,total):return {'code':200,'data':{'rows':rows,'page_info':{'total_num':total}}}

class Fake:
    binding={'base':'https://test.invalid','prefix':'','auth_tag':'synthetic'}
    def __init__(self,fn):self.fn=fn;self.calls=[];self.sleep=lambda x:None
    def get(self,path,params):self.calls.append(params['page_num']);return self.fn(params['page_num'])

class Tests(unittest.TestCase):
    def test_preflight_precedes_client_and_collection(self):
        import generate
        with tempfile.TemporaryDirectory() as temp:
            argv=['generate.py','--date','2020-02-29','--confirmed-inputs','--output',str(Path(temp)/'report.xlsx'),'--work-dir',temp]
            with patch.object(sys,'argv',argv),patch.object(generate,'runtime',side_effect=RuntimeError('missing Excel runtime')),patch.object(generate,'Client') as client,patch.object(generate,'collect') as collector:
                with self.assertRaisesRegex(RuntimeError,'missing Excel'):generate.main()
                client.assert_not_called();collector.assert_not_called()
    def test_redirects_and_origin(self):
        for base in ['http://test.invalid','https://u:p@test.invalid','https://test.invalid/?q=1']:
            with self.assertRaises(ValueError):Client(base,'demo','demo')
        with self.assertRaises(RuntimeError):NoRedirect().redirect_request(None,None,302,'',{},'https://else.invalid')
        requests=[]
        def redirect(req,timeout):
            requests.append(req.full_url);raise HTTPError(req.full_url,302,'',{},None)
        c=Client('https://test.invalid','demo','demo',transport=redirect,sleep=lambda n:None)
        with self.assertRaises(RuntimeError):c.get(RISK)
        self.assertEqual(len(requests),1)  # No destination ever receives a second request.

    def test_retry_and_permissions(self):
        calls=[]; waits=[]
        def transport(req,timeout):
            calls.append(1)
            if len(calls)<3:raise HTTPError(req.full_url,429,'',{'Retry-After':'1'},None)
            return io.BytesIO(json.dumps(body([],0)).encode())
        c=Client('https://test.invalid','demo','demo',transport=transport,sleep=waits.append)
        self.assertEqual(c.get(RISK)['code'],200);self.assertEqual(len(calls),3);self.assertEqual(len(waits),2)
        calls.clear()
        def denied(req,timeout):calls.append(1);raise HTTPError(req.full_url,403,'',{},None)
        c.opener=denied
        with self.assertRaises(RuntimeError):c.get(RISK)
        self.assertEqual(len(calls),1)

    def test_totals(self):
        self.assertEqual(rows_total(body([],0)),([],0))
        self.assertEqual(rows_total({'data':{'rows':[],'page_info':{'total':'0'}}}),([],0))
        for payload in [{'data':{'rows':[]}}, {'data':{'rows':[],'page_info':{'total_num':2,'total':3}}}, {'data':{'rows':[],'total_num':1.5}}]:
            with self.assertRaises(RuntimeError):rows_total(payload)

    def test_resume_and_empty_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            phase=[False]
            def response(page):
                if page==1:return body([{'event_id':'1'},{'event_id':'2'}],3)
                return body([{'event_id':'3'}] if phase[0] else [],3)
            c=Fake(response);p=Pager(c,temp,RISK,page_size=2)
            with self.assertRaises(RuntimeError):list(p)
            self.assertEqual(c.calls,[1,2,2,2,2]);self.assertTrue((p.root/'page_000001.json').exists())
            phase[0]=True;c.calls.clear();p=Pager(c,temp,RISK,page_size=2)
            self.assertEqual([r['event_id'] for r in p],['1','2','3']);self.assertEqual(c.calls,[2]);self.assertTrue(p.state['complete'])
            c.calls.clear();self.assertEqual(len(list(Pager(c,temp,RISK,page_size=2))),3);self.assertEqual(c.calls,[])
            other=Fake(response);other.binding={**c.binding,'auth_tag':'another-tenant'}
            self.assertNotEqual(Pager(other,temp,RISK,page_size=2).root,p.root)

    def test_duplicates_drift_unknown_total(self):
        for fn in [lambda p:body([{'event_id':'1'}],2),
                   lambda p:body([{'event_id':str(p)}],2 if p==1 else 3),
                   lambda p:{'code':200,'data':{'rows':[{'event_id':'1'}]}}]:
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(RuntimeError):list(Pager(Fake(fn),temp,RISK,page_size=1))

    def test_leap_boundaries(self):
        b=boundaries('2020-02-29','2020-02-29T23:59:59+08:00')
        self.assertEqual(b['day']-b['month'],29*86400);self.assertEqual(b['day']-b['history'],89*86400)

def excel_test(node,modules,directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    b=boundaries('2020-02-29','2020-02-29T23:59:59+08:00');when=b['end']-100
    employees=[{'employee_id':f'e{i:02}','employee_name':'同名员工' if i<2 else f'示例员工{i}',
                'department':['示例部门'],'is_active':i!=11} for i in range(12)]
    risks=[{'event_id':f'v{i}-{j}','employee_id':e['employee_id'],'policy_name':f'示例策略{j}',
            'risk':score,'level':'low' if score<6 else 'middle','alert_time':when,
            'hit_summary':{'count':999,'channels':[{'name':'示例通道'}]}}
           for i,e in enumerate(employees) for j,score in enumerate([10,8,6,4,2,1])]
    logs=[{'log_id':f'l{i}','employee_id':e['employee_id'],'trigger':'securityFileSend','action':'observe',
           'time':when,'file_size':(i+1)*1024**2,'file_parent_category':'文本文档'} for i,e in enumerate(employees)]
    paths={'/idaas/identity_source/list':[{'source_id':'source-test','auth_name':'测试身份源'}],
           '/idaas/employee/employee_list':employees,
           '/idaas/employee/list_with_terminal':[{**e,'terminal_list':[]} for e in employees],RISK:risks,
           '/dlp/log_process/list_dlp_file_send_log':logs}
    calls=[]
    def transport(req,timeout):
        parsed=urlparse(req.full_url);params=parse_qs(parsed.query);calls.append(parsed.path)
        if parsed.path=='/idaas/identity_source/custom/employee/get_count':out={'code':200,'data':{'total_num':'12'}}
        elif parsed.path=='/epp/it/terminal/list_all_terminal':out=body([],20)
        else:
            allrows=paths[parsed.path];size=int(params['page_size'][0]);page=int(params['page_num'][0]);out=body(allrows[(page-1)*size:page*size],len(allrows))
        return io.BytesIO(json.dumps(out,ensure_ascii=False).encode())
    c=Client('https://test.invalid','fake-key','fake-secret',transport=transport,sleep=lambda n:None)
    run=directory/'run';run.mkdir(exist_ok=True);collect(c,run,b)
    out=directory/'pipeline.xlsx'
    cmd=[sys.executable,str(Path(__file__).with_name('generate.py')),'--from-snapshot',str(run/'snapshot.json'),
         '--work-dir',str(directory/'render'),'--output',str(out)]
    subprocess.run(cmd,check=True)
    layout=json.loads((directory/'render/render-layout.json').read_text())
    assert layout['dayCap']==60 and layout['monthCap']==60  # Both sections must grow and move.
    model=json.loads((directory/'render/model.json').read_text())
    assert model['employees']==11 and model['riskEmployees']==12
    assert model['day'][0]['cells'][3]==1 and len(model['day'])==60
    # Same names with distinct employee IDs must have separate six-row merges.
    assert 'B7:B12' in layout['expectedMerges'] and 'B13:B18' in layout['expectedMerges']
    assert out.exists() and len(calls)>5
    print('PASS complete mock-API -> page cache -> snapshot -> metrics -> Excel; expansion, same-name IDs, merge and ratio verified')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--node',help=argparse.SUPPRESS);parser.add_argument('--node-modules',help=argparse.SUPPRESS);parser.add_argument('--work-dir');parser.add_argument('--excel',action='store_true')
    args,remaining=parser.parse_known_args()
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():sys.exit(1)
    if args.excel or (args.node and args.node_modules):
        excel_test(args.node,args.node_modules,args.work_dir or tempfile.mkdtemp(prefix='yunshu-skill-test-'))
