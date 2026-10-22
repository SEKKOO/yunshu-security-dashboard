"""Single API helper; generate.py is the complete report entry point."""
import argparse, json, os
from pathlib import Path
from api import Client, Pager, ALLOWED, save

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--path',required=True,choices=sorted(ALLOWED)); p.add_argument('--base-url',default='https://cs-hz0.sase.eagleyun.com')
    p.add_argument('--api-prefix',default=''); p.add_argument('--params-json',default='{}'); p.add_argument('--paginate',action='store_true')
    p.add_argument('--page-size',type=int,default=200); p.add_argument('--work-dir',required=True); p.add_argument('--output',required=True)
    a=p.parse_args(); params=json.loads(a.params_json)
    if not isinstance(params,dict):p.error('params-json must be an object')
    c=Client(a.base_url,os.environ.get('YUNSHU_ACCESS_KEY'),os.environ.get('YUNSHU_SECRET_KEY'),a.api_prefix)
    if not a.paginate:save(a.output,c.get(a.path,params)); return
    pager=Pager(c,a.work_dir,a.path,params,a.page_size); target=Path(a.output); target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix(target.suffix+'.partial')
    with open(tmp,'w',encoding='utf-8',opener=lambda p,f:os.open(p,f,0o600)) as out:
        out.write('[')
        for n,row in enumerate(pager):
            if n:out.write(',')
            json.dump(row,out,ensure_ascii=False,separators=(',',':'))
        out.write(']')
    tmp.chmod(0o600); tmp.replace(target); print(json.dumps({'rows':pager.state['rows'],'complete':pager.state['complete']}))

if __name__=='__main__':main()
