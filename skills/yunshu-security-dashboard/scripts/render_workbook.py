"""Portable XLSX writers. No Node.js, private packages or native Excel required."""
from copy import copy
from pathlib import Path
import json
import math
import os
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as E


def load(path):
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError('Missing openpyxl. Run: python -m pip install -r <skill>/requirements.txt') from None
    return openpyxl.load_workbook(path)


def clone_row(sheet, source, target, columns=15):
    for c in range(1, columns+1):
        a,b=sheet.cell(source,c),sheet.cell(target,c)
        b.value=a.value; b._style=copy(a._style)
        b.alignment=copy(a.alignment)
    sheet.row_dimensions[target].height=sheet.row_dimensions[source].height


def cache_ratio(path, address, value):
    # The only generated formula is the ratio; compute its export cache explicitly.
    # openpyxl preserves the formula but does not evaluate Excel formulas.
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with ZipFile(path) as z:entries={n:z.read(n) for n in z.namelist()}
    root=E.fromstring(entries['xl/worksheets/sheet1.xml'])
    cell=root.find(f'.//{{{ns}}}c[@r="{address}"]')
    if cell is None or cell.find(f'{{{ns}}}f') is None:raise ValueError('Missing ratio formula')
    cell.attrib.pop('t',None)
    if isinstance(value,str):cell.set('t','str')
    v=cell.find(f'{{{ns}}}v')
    if v is None:v=E.SubElement(cell,f'{{{ns}}}v')
    v.text=str(value)
    entries['xl/worksheets/sheet1.xml']=E.tostring(root,encoding='utf-8')
    with ZipFile(path,'w',ZIP_DEFLATED) as z:
        for n,b in entries.items():z.writestr(n,b)


def probe(template, output):
    w=load(template); w.save(output); checked=load(output)
    if w.sheetnames!=checked.sheetnames:raise ValueError('XLSX round-trip failed')
    for s in w:
        for row in s:
            for c in row:
                if c.value!=checked[s.title][c.coordinate].value:raise ValueError('XLSX value round-trip failed')


def render_dashboard(template, model, layout, work):
    from openpyxl.styles import Alignment
    w=load(template); s=w[layout['sheet']]
    for merged in layout['bodyMerges']:s.unmerge_cells(merged)
    heights={int(k):v for k,v in layout['heights'].items()}; last=layout['last']
    def shift(start,delta):
        nonlocal last
        if not delta:return
        for r in range(last,start-1,-1):
            clone_row(s,r,r+delta)
            s.row_dimensions[r+delta].height=heights.get(r,16.5)
        last+=delta
    day_start=layout['dayTitle']+2
    day_old=layout['monthTitle']-day_start
    month_old=layout['footer']-(layout['monthTitle']+2)-1
    dc=max(day_old,len(model['day']),len(model['auditDay']),10)
    mc=max(month_old,len(model['month']),len(model['auditMonth']),10)
    de,me=dc-day_old,mc-month_old
    shift(layout['footer']-1,me);shift(layout['monthTitle'],de)
    mt=layout['monthTitle']+de;ms=mt+2;footer=layout['footer']+de+me
    merges=[]; expected={}
    def put(r,c,v):s.cell(r,c,v);expected[(r,c)]=v
    def lines(value,n):return sum(max(1,math.ceil(len(x)/n)) for x in str(value or '').split('\n'))
    def write(start,cap,risk,audit):
        for r in range(start+1,start+cap):clone_row(s,start,r)
        for row in s.iter_rows(min_row=start,max_row=start+cap-1,min_col=2,max_col=14):
            for c in row:c.value=None;c.alignment=Alignment(wrap_text=True,vertical='center')
        for i,item in enumerate(risk):
            for j,v in enumerate(item['cells']):put(start+i,j+2,v)
        for i,item in enumerate(audit):
            for j,v in enumerate(item):put(start+i,j+11,v)
        for i in range(cap):
            row=start+i; r=risk[i]['cells'] if i<len(risk) else ['']*6;a=audit[i] if i<len(audit) else ['']*4
            s.cell(row,5).number_format=s.cell(row,7).number_format='#,##0" 次"'
            s.cell(row,13).number_format='0.00" GiB"'
            s.row_dimensions[row].height=max(24,14*max(lines(r[0],7),lines(r[1],6),lines(r[2],8),lines(r[4],10),lines(a[1],6),lines(a[3],8))+8)
        i=0
        while i<len(risk):
            j=i+1
            while j<len(risk) and risk[j]['employee_id']==risk[i]['employee_id']:j+=1
            if j-i>1:
                addr=f'B{start+i}:B{start+j-1}';s.merge_cells(addr);merges.append(addr)
                for k in range(i+1,j):expected.pop((start+k,2),None)
            i=j
    write(day_start,dc,model['day'],model['auditDay']);write(ms,mc,model['month'],model['auditMonth'])
    for key,value in [('员工总数',model['employees']),('风险员工',model['riskEmployees']),('设备总数',model['devices'])]:s[layout['summary'][key]]=value
    denom,numer=layout['summary']['员工总数'],layout['summary']['风险员工']
    ratio=layout['summary']['风险员工占比'];s[ratio]=f'=IF({denom}>0,{numer}/{denom},"不可计算")';s[ratio].number_format='0.00%'
    s['I1']=model['updated'];s['B4']=model['scope']
    for title in [layout['dayTitle'],mt]:s[f'K{title}']='文件外发放行（已知大小合计）'
    staged=Path(work)/'rendered.xlsx';w.save(staged)
    cache_ratio(staged,ratio,model['riskEmployees']/model['employees'] if model['employees']>0 else '不可计算')
    checked=load(staged)[s.title]
    for (r,c),value in expected.items():
        actual=checked.cell(r,c).value
        if isinstance(value,(int,float)):
            if not isinstance(actual,(int,float)) or abs(actual-value)>1e-8:raise ValueError('XLSX numeric mismatch')
        elif actual!=value:raise ValueError('XLSX value mismatch')
    result={'dayStart':day_start,'monthStart':ms,'monthTitle':mt,'footer':footer,'dayCap':dc,'monthCap':mc,'expectedMerges':merges}
    (Path(work)/'render-layout.json').write_text(json.dumps(result))
    return staged


def render_software(template, model, output):
    from openpyxl.styles import Alignment, Font, PatternFill
    from software_inventory import DISPLAY_NAMES
    w=load(template)
    if len(w.worksheets)!=1:raise ValueError('Confirm software template sheet selection')
    s=w.active; locations=[]
    for sec in model['sections']:
        family=sec.get('family') or next((k for k,v in DISPLAY_NAMES.items() if v==sec['title']),sec['title'])
        names={f'{family} 盗版软件安装清单',f'{sec["title"]} 盗版软件安装清单'}
        found=[c.row for row in s for c in row if c.column==2 and c.value in names]
        if len(found)!=1:raise ValueError('Missing/ambiguous software heading; confirm template before API access')
        locations.append(found[0])
    if locations!=sorted(locations):raise ValueError('Software section order mismatch')
    first=locations[0]
    allowed=set(locations+[r+1 for r in locations])
    if any(c.value is not None and (c.row not in allowed or c.column not in range(2,6)) for row in s.iter_rows(min_row=first) for c in row):
        raise ValueError('Software area contains data; confirm clearing/replacing these records')
    for r in locations:
        if [s.cell(r+1,c).value for c in range(2,6)]!=['姓名','部门','设备','安装路径']:raise ValueError('Unknown software fields')
    title_styles=[copy(s.cell(first,c)._style) for c in range(2,6)]
    header_styles=[copy(s.cell(first+1,c)._style) for c in range(2,6)]
    for m in list(s.merged_cells.ranges):
        if m.min_row>=first:s.unmerge_cells(str(m))
    for row in s.iter_rows(min_row=first):
        for c in row:c.value=None
    r=first;expected=[]
    def row(values,height=36):
        nonlocal r
        for j,v in enumerate(values):
            c=s.cell(r,j+2,v);c.font=Font(name='Arial',size=12);c.alignment=Alignment(vertical='center',wrap_text=True)
            expected.append((r,j+2,v))
        s.row_dimensions[r].height=height;r+=1
    for sec in model['sections']:
        title=r;row([f'{sec["title"]} 盗版软件安装清单'],44);s.merge_cells(start_row=title,start_column=2,end_row=title,end_column=5)
        for c,style in zip(range(2,6),title_styles):s.cell(title,c)._style=copy(style)
        s.cell(title,2).alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
        note=r;row([f'统计口径：安装即纳入；采集时间：{model["queried_at"]}'],44);s.merge_cells(start_row=note,start_column=2,end_row=note,end_column=5)
        groups={}
        for x in sec['rows']:groups.setdefault((x['software'],x['version']),[]).append(x)
        for (name,version),items in groups.items():
            group=r;row([name+(f'\n版本：{version}' if version else '')],60);s.merge_cells(start_row=group,start_column=2,end_row=group,end_column=5)
            s.cell(group,2).font=Font(name='Arial',size=11,bold=True);s.cell(group,2).fill=PatternFill('solid',fgColor='E7E6E6')
            hdr=r;row(['姓名','部门','设备','安装路径'],24)
            for c,style in zip(range(2,6),header_styles):s.cell(hdr,c)._style=copy(style)
            start=r
            for x in items:
                vals=[x['name'],x['department'],x['device'],x['path']]
                width=[12,8,14,15]
                n=max(math.ceil(sum(2 if ord(ch)>255 else 1 for ch in v)/width[i]) for i,v in enumerate(vals))
                row(vals,max(36,n*19+12))
            i=0
            while i<len(items):
                j=i+1
                while j<len(items) and items[j]['employee_id']==items[i]['employee_id']:j+=1
                if j-i>1 and items[i]['employee_id']:
                    s.merge_cells(start_row=start+i,start_column=2,end_row=start+j-1,end_column=2)
                    expected[:]=[e for e in expected if not (e[1]==2 and start+i<e[0]<start+j)]
                i=j
        r+=1
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(suffix='.xlsx',dir=output.parent);os.close(fd)
    try:
        w.save(temp)
        import openpyxl
        cached=openpyxl.load_workbook(template,data_only=True)[s.title]
        for rownum in range(1,first):
            for c in s[rownum]:
                value=cached[c.coordinate].value
                if c.data_type=='f' and isinstance(value,(str,int,float)):
                    cache_ratio(temp,c.coordinate,value)
        checked=load(temp)[s.title]
        for rownum,col,value in expected:
            if checked.cell(rownum,col).value!=value:raise ValueError('Software XLSX mismatch')
        original=load(template)[s.title]
        for rownum in range(1,first):
            for col in range(1,original.max_column+1):
                a,b=original.cell(rownum,col),checked.cell(rownum,col)
                if a.value!=b.value or a._style!=b._style:raise ValueError('Earlier dashboard changed')
            if original.row_dimensions[rownum].height!=checked.row_dimensions[rownum].height:raise ValueError('Earlier row height changed')
        for col,dim in original.column_dimensions.items():
            if dim.width!=checked.column_dimensions[col].width:raise ValueError('Column width changed')
        if {str(m) for m in original.merged_cells.ranges if m.max_row<first}!={str(m) for m in checked.merged_cells.ranges if m.max_row<first}:
            raise ValueError('Earlier merges changed')
        os.replace(temp,output)
    finally:
        if Path(temp).exists():Path(temp).unlink()
    return output
