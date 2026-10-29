"""Signed GET client and resumable, disk-backed validated pagination; standard library."""
import base64, hashlib, hmac, json, os, sqlite3, time
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener

IDS = {'/dlp/log_process/list_dlp_file_send_log':'log_id','/dlp/risk/event/list':'event_id',
 '/idaas/identity_source/list':'source_id','/idaas/employee/employee_list':'employee_id',
 '/idaas/employee/list_with_terminal':'employee_id'}
ALLOWED = set(IDS) | {'/idaas/identity_source/custom/employee/get_count',
 '/idaas/employee/get_detail','/epp/it/terminal/list_all_terminal',
 '/epp/terminal_software/list','/epp/software/analysis/get_software_detail',
 '/epp/software/analysis/page_installed_software'}
PROJECT = {
 '/dlp/log_process/list_dlp_file_send_log':['log_id','time','employee_id','trigger','action','file_size','file_parent_category','terminal_id','terminal_name','dev_sn','terminal_mac_addr'],
 '/dlp/risk/event/list':['event_id','alert_time','employee_id','policy_name','risk','level','hit_summary'],
 '/idaas/identity_source/list':['source_id','auth_name'],
 '/idaas/employee/employee_list':['employee_id','employee_name','display_name','department','is_active'],
 '/idaas/employee/list_with_terminal':['employee_id','employee_name','department','is_active','terminal_list']}

def private_dir(path):
    path=Path(path); path.mkdir(parents=True,exist_ok=True,mode=0o700); path.chmod(0o700)
    return path

def save(path,value):
    path=Path(path); private_dir(path.parent); temp=path.with_suffix(path.suffix+'.partial')
    with open(temp,'w',encoding='utf-8',opener=lambda p,f:os.open(p,f,0o600)) as out:
        json.dump(value,out,ensure_ascii=False,separators=(',',':'))
    temp.chmod(0o600); temp.replace(path)

def query(params):
    pairs=[]
    for key in sorted(params):
        value=params[key]
        if value is None or value=='':continue
        values=value if isinstance(value,(list,tuple)) else [value]
        pairs.extend(f'{quote(str(key),safe="")}={quote(v,safe="")}' for v in sorted(map(str,values)))
    return '&'.join(pairs)

def validate_base(base):
    p=urlparse(base)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
        raise ValueError('Base URL must be an HTTPS origin; use api-prefix for a path')
    return base.rstrip('/')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise RuntimeError('Authenticated redirects are disabled; confirm the API origin')

class Client:
    def __init__(self,base,access,secret,prefix='',transport=None,sleep=time.sleep):
        self.base=validate_base(base); self.access,self.secret=access,secret
        if not access or not secret:raise ValueError('This execution requires AK and SK')
        self.prefix='/'+prefix.strip('/') if prefix.strip('/') else ''
        self.opener=transport or build_opener(NoRedirect()).open; self.sleep=sleep
        self.binding={'base':self.base,'prefix':self.prefix,'auth_tag':hashlib.sha256(access.encode()).hexdigest()}
    def get(self,path,params=None):
        if path not in ALLOWED:raise ValueError('Unsupported read-only API path')
        signed=self.prefix+path; qs=query(params or {}); url=self.base+signed+('?' + qs if qs else '')
        for attempt in range(6):
            date=formatdate(usegmt=True); source=f'GET\n{signed}\n{qs}\n{self.access}\n{date}\n'
            signature=base64.b64encode(hmac.new(self.secret.encode(),source.encode(),hashlib.sha256).digest()).decode()
            req=Request(url,headers={'Accept':'application/json','Date':date,'X-HMAC-ALGORITHM':'hmac-sha256',
                'X-HMAC-ACCESS-KEY':self.access,'X-HMAC-SIGNATURE':signature},method='GET')
            try:
                with self.opener(req,timeout=90) as response:body=json.load(response)
                if not isinstance(body,dict) or str(body.get('code'))!='200':raise RuntimeError('Failed/unknown business response; no report published')
                return body
            except HTTPError as exc:
                if 300<=exc.code<400:raise RuntimeError('Authenticated redirects are disabled') from None
                if exc.code not in (429,500,502,503,504) or attempt==5:raise RuntimeError(f'API HTTP {exc.code}; response omitted') from None
                delay=min(60,8*(attempt+1)); retry=exc.headers.get('Retry-After') if exc.headers else None
                if retry:
                    try:wait=float(retry) if retry.isdigit() else parsedate_to_datetime(retry).timestamp()-time.time()
                    except (TypeError,ValueError,OverflowError):wait=0
                    if wait>60:raise RuntimeError('Retry-After exceeds 60 seconds; resume this run later')
                    delay=max(delay,wait)
                self.sleep(delay)
            except (URLError,TimeoutError):
                if attempt==5:raise RuntimeError('Network retries exhausted; resume this run later') from None
                self.sleep(min(30,3*(attempt+1)))

def rows_total(body,require_total=True):
    data=body.get('data')
    if not isinstance(data,dict):raise RuntimeError('Expected data object with rows and explicit total')
    rows=next((data[k] for k in ('rows','list','items') if isinstance(data.get(k),list)),None)
    if rows is None or any(not isinstance(r,dict) for r in rows):raise RuntimeError('Unknown list schema')
    info=data.get('page_info') or {}
    if not isinstance(info,dict):raise RuntimeError('Unknown pagination schema')
    values=[d[k] for d in (info,data) for k in ('total_num','total') if d.get(k) is not None]
    try:
        nums=[int(v) for v in values]
        if any(str(v).strip()!=str(n) for v,n in zip(values,nums)):raise ValueError()
    except (ValueError,TypeError,OverflowError):raise RuntimeError('Invalid pagination total') from None
    if len(set(nums))>1 or any(n<0 for n in nums):raise RuntimeError('Conflicting/negative pagination total')
    total=nums[0] if nums else None
    if total is None and require_total:raise RuntimeError('No recognized total; short-page fallback is disabled')
    return rows,total

class Pager:
    """Page checkpoint before advancing; ID index rebuilt from pages on every resume."""
    def __init__(self,client,root,path,params=None,page_size=200,project=True):
        if path not in IDS or page_size<1:raise ValueError('Pagination requires a known endpoint and automatic ID mapping')
        self.client,self.path,self.params=client,path,params or {}; self.size,self.project=page_size,project
        self.description={**client.binding,'path':path,'params':self.params,'page_size':page_size,'projection_version':2 if project else 0}
        digest=hashlib.sha256(json.dumps(self.description,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.root=private_dir(Path(root)/digest); self.manifest=self.root/'manifest.json'
        if self.manifest.exists() and json.loads(self.manifest.read_text()).get('request')!=self.description:raise RuntimeError('Cache binding mismatch')
        self.state={'request':self.description,'complete':False,'rows':0,'pages':0,'total':None,'checked_at':time.time()}
    def __iter__(self):
        dbpath=self.root/'ids.sqlite'; db=sqlite3.connect(dbpath); dbpath.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS ids (id TEXT PRIMARY KEY)'); db.execute('DELETE FROM ids')
        expected,seen,page=None,0,1
        try:
            while True:
                cache=self.root/f'page_{page:06d}.json'
                if cache.exists():
                    envelope=json.loads(cache.read_text()); rows,total=envelope['rows'],envelope['total']
                else:
                    for attempt in range(4):
                        self.client.sleep(0.5)
                        raw,total=rows_total(self.client.get(self.path,{**self.params,'page_num':page,'page_size':self.size}))
                        if expected is not None and total!=expected:raise RuntimeError('Total changed; create a fresh snapshot run')
                        if raw or total==seen:break
                        if attempt==3:raise RuntimeError('Empty page before total after 4 attempts; resume later')
                        self.client.sleep(2*(attempt+1))
                    rows=[self._project(r) for r in raw] if self.project else raw
                    ids=[r.get(IDS[self.path]) for r in rows]
                    if any(not i for i in ids) or len(set(ids))!=len(ids):raise RuntimeError('Missing/duplicate page IDs')
                    save(cache,{'rows':rows,'total':total})
                if expected is not None and total!=expected:raise RuntimeError('Cached total mismatch')
                expected=total
                field={'/dlp/risk/event/list':'alert_time','/dlp/log_process/list_dlp_file_send_log':'time'}.get(self.path)
                if field and 'start_time' in self.params and 'end_time' in self.params:
                    for row in rows:
                        try:stamp=int(row[field])
                        except (KeyError,TypeError,ValueError,OverflowError):raise RuntimeError('Missing/invalid row timestamp') from None
                        if not int(self.params['start_time'])<=stamp<=int(self.params['end_time']):
                            raise RuntimeError('API row outside the confirmed time snapshot')
                try:
                    db.executemany('INSERT INTO ids VALUES (?)',[(str(r[IDS[self.path]]),) for r in rows]); db.commit()
                except (sqlite3.IntegrityError,KeyError):raise RuntimeError('Duplicate/missing IDs across pages; restart snapshot') from None
                seen+=len(rows)
                if seen>expected or (not rows and seen<expected):raise RuntimeError('Pagination count mismatch')
                self.state.update(rows=seen,pages=page,total=expected,complete=seen==expected,checked_at=time.time()); save(self.manifest,self.state)
                yield from rows
                if seen==expected:break
                page+=1
                if page>1000000:raise RuntimeError('Pagination page limit exceeded')
        finally:db.close()
    def _project(self,row):
        clean={k:row[k] for k in PROJECT[self.path] if k in row}
        if self.path=='/dlp/risk/event/list':clean['hit_summary']={'channels':(row.get('hit_summary') or {}).get('channels') or []}
        if self.path=='/idaas/employee/list_with_terminal':
            clean['terminal_list']=[{k:t[k] for k in ('te_id','terminal_name','serial_number','mac_addr') if k in t} for t in row.get('terminal_list') or []]
        return clean
