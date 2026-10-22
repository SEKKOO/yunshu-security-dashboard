"""Validate the supported workbook schema before any live data requests."""
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as E
import re

NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}

def inspect_template(path):
    with ZipFile(path) as z:
        book=E.fromstring(z.read('xl/workbook.xml')); sheets=book.findall('m:sheets/m:sheet',NS)
        if len(sheets)!=1:raise ValueError('Template must have one worksheet; confirm structural changes before reading data')
        relation=sheets[0].get('{'+NS['r']+'}id')
        rels=E.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target=next(r.get('Target') for r in rels if r.get('Id')==relation)
        sheetpath=target.lstrip('/') if target.startswith('/') else 'xl/'+target
        root=E.fromstring(z.read(sheetpath)); strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(s.itertext()) for s in E.fromstring(z.read('xl/sharedStrings.xml'))]
        values={}; formulas=[]
        for c in root.findall('.//m:sheetData/m:row/m:c',NS):
            address=c.get('r'); v=c.find('m:v',NS); text=v.text if v is not None else None
            if c.get('t')=='s':text=strings[int(text)] if text is not None else ''
            if c.get('t')=='inlineStr':text=''.join(t.text or '' for t in c.findall('.//m:t',NS))
            if text is not None:values[address]=text
            if c.find('m:f',NS) is not None:formulas.append(address)
        def locate(value):
            hits=[k for k,v in values.items() if v==value]
            if len(hits)!=1:raise ValueError('Ambiguous/missing template label: '+value)
            return hits[0]
        def row(label):
            addr=locate(label)
            if not addr.startswith('B'):raise ValueError('Section titles must remain in column B')
            return int(re.search(r'\d+',addr)[0])
        day=row('当日高危TOP10'); month=row('本月累计TOP10')
        footer=row('月人均风险事件数量（折线图+柱状图）-维度：公司、部门')
        if not day+3<month or not month+3<footer:raise ValueError('Invalid template section order')
        headers=['姓名','部门','高危事件','数据量','主要通道（TOP5）']
        for title,history in [(day,'近7日相同通道行为数据'),(month,'近3月相同通道行为数据')]:
            for col,text in zip('BCDEFG',headers+[history]):
                if values.get(f'{col}{title+1}')!=text:raise ValueError('Unsupported risk table field layout')
            for col,text in zip('KLMN',['姓名','部门','数据大小','数据类型（TOP3）']):
                if values.get(f'{col}{title+1}')!=text:raise ValueError('Unsupported outbound field layout')
        summary={}
        for label in ['员工总数','风险员工','风险员工占比','设备总数']:
            address=locate(label); col,num=re.fullmatch(r'([A-Z]+)(\d+)',address).groups()
            if len(col)!=1 or int(num)>=day:raise ValueError('Unsupported summary layout')
            summary[label]=f'{chr(ord(col)+1)}{num}'
        if any(f not in summary.values() for f in formulas):
            raise ValueError('Template contains formulas outside supported summary; confirm before inserting rows')
        merges=[x.get('ref') for x in root.findall('m:mergeCells/m:mergeCell',NS)]
        body_merges=[]
        for merged in merges:
            a,b=merged.split(':'); ca,ra=re.fullmatch(r'([A-Z]+)(\d+)',a).groups(); cb,rb=re.fullmatch(r'([A-Z]+)(\d+)',b).groups()
            ra,rb=int(ra),int(rb)
            if rb>=day+2:
                if ca!=cb or ca!='B' or not ((day+2<=ra<=rb<month) or (month+2<=ra<=rb<footer)):
                    raise ValueError('Unsupported merge crossing a data/section boundary')
                body_merges.append(merged)
        heights={str(r.get('r')):float(r.get('ht')) for r in root.findall('m:sheetData/m:row',NS) if r.get('ht')}
        maxrow=max([int(re.search(r'\d+',a)[0]) for a in values]+[footer])
        return {'sheet':sheets[0].get('name'),'dayTitle':day,'monthTitle':month,'footer':footer,
                'summary':summary,'bodyMerges':body_merges,'heights':heights,'last':maxrow}
