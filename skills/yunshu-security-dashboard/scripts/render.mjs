// Runtime modules supplied explicitly by the workspace dependency loader; no global install mutation.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const args=process.argv.slice(2);
const arg=(name)=>{const i=args.indexOf(name);return i<0?null:args[i+1];};
const modules=arg('--modules');
if(!modules)throw Error('--modules is required');
const require=createRequire(path.join(path.dirname(path.resolve(modules)),'yunshu-runtime.cjs'));
const {FileBlob,SpreadsheetFile}=await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const config=JSON.parse(await fs.readFile(arg('--probe')||arg('--render'),'utf8'));
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(config.template));
const s=wb.worksheets.getItem(config.layout.sheet);
await fs.mkdir(config.work,{recursive:true});
if(arg('--probe')){
  const probe=path.join(config.work,'preflight.xlsx');
  await (await SpreadsheetFile.exportXlsx(wb)).save(probe);
  const roundTrip=await SpreadsheetFile.importXlsx(await FileBlob.load(probe));
  if(roundTrip.worksheets.getItem(config.layout.sheet).getRange('B2').values[0][0]!==s.getRange('B2').values[0][0])throw Error('XLSX round-trip probe failed');
  const png=await wb.render({sheetName:config.layout.sheet,range:'A1:O6',scale:1,format:'png'});
  const bytes=new Uint8Array(await png.arrayBuffer());
  if(bytes.length<100)throw Error('PNG renderer returned no usable image');
  await fs.writeFile(path.join(config.work,'preflight.png'),bytes);
  console.log('PASS Excel environment probe');
}else{
  const d=JSON.parse(await fs.readFile(config.model,'utf8')),l=config.layout;
  for(const merge of l.bodyMerges)s.unmergeCells(merge);
  s.charts.deleteAll();
  let last=l.last;
  const heights={...l.heights};
  function shift(from,delta){
    if(!delta)return;
    for(let r=last;r>=from;r--){
      s.getRange(`A${r+delta}:O${r+delta}`).copyFrom(s.getRange(`A${r}:O${r}`),'all');
      const height=heights[r]||16.5;
      s.getRange(`A${r+delta}:O${r+delta}`).format.rowHeight=height;heights[r+delta]=height;
    }
    last+=delta;
  }
  const dayStart=l.dayTitle+2,oldDay=l.monthTitle-dayStart;
  const oldMonth=l.footer-(l.monthTitle+2)-1;
  const dayCap=Math.max(oldDay,d.day.length,d.auditDay.length,10);
  const monthCap=Math.max(oldMonth,d.month.length,d.auditMonth.length,10);
  const de=dayCap-oldDay,me=monthCap-oldMonth;
  shift(l.footer-1,me);shift(l.monthTitle,de);
  const monthTitle=l.monthTitle+de,monthStart=monthTitle+2,footer=l.footer+de+me;
  const expectedMerges=[];
  function write(start,cap,risk,audit){
    for(let r=start+1;r<start+cap;r++)s.getRange(`A${r}:O${r}`).copyFrom(s.getRange(`A${start}:O${start}`),'all');
    s.getRange(`B${start}:N${start+cap-1}`).clear({applyTo:'contents'});
    if(risk.length)s.getRange(`B${start}:G${start+risk.length-1}`).values=risk.map(r=>r.cells);
    if(audit.length)s.getRange(`K${start}:N${start+audit.length-1}`).values=audit;
    s.getRange(`B${start}:N${start+cap-1}`).format.wrapText=true;
    s.getRange(`E${start}:E${start+cap-1}`).setNumberFormat('#,##0" 次"');
    s.getRange(`G${start}:G${start+cap-1}`).setNumberFormat('#,##0" 次"');
    s.getRange(`M${start}:M${start+cap-1}`).setNumberFormat('0.00" GiB"');
    const lines=(value,n)=>String(value??'').split('\n').reduce((sum,x)=>sum+Math.max(1,Math.ceil(x.length/n)),0);
    for(let i=0;i<cap;i++){
      const r=risk[i]?.cells||[],a=audit[i]||[];
      const n=Math.max(1,lines(r[0],7),lines(r[1],6),lines(r[2],8),lines(r[4],10),lines(a[1],6),lines(a[3],8));
      s.getRange(`A${start+i}:O${start+i}`).format.rowHeight=Math.max(24,n*14+8);
    }
    for(let i=0;i<risk.length;){
      let j=i+1;while(j<risk.length&&risk[j].employee_id===risk[i].employee_id)j++;
      if(j-i>1){
        s.getRange(`B${start+i+1}:B${start+j-1}`).clear({applyTo:'contents'});
        const range=`B${start+i}:B${start+j-1}`;s.mergeCells(range);
        s.getRange(range).format.verticalAlignment='center';expectedMerges.push(range);
      }
      i=j;
    }
  }
  write(dayStart,dayCap,d.day,d.auditDay);write(monthStart,monthCap,d.month,d.auditMonth);
  const put=(name,value)=>s.getRange(l.summary[name]).values=[[value]];
  put('员工总数',d.employees);put('风险员工',d.riskEmployees);put('设备总数',d.devices);
  const denominator=l.summary['员工总数'],numerator=l.summary['风险员工'];
  s.getRange(l.summary['风险员工占比']).formulas=[[`=IF(${denominator}>0,${numerator}/${denominator},"不可计算")`]];
  s.getRange(l.summary['风险员工占比']).setNumberFormat('0.00%');
  s.getRange('I1').values=[[d.updated]];s.getRange('B4').values=[[d.scope]];
  s.getRange(`K${l.dayTitle}`).values=[['文件外发放行（已知大小合计）']];
  s.getRange(`K${monthTitle}`).values=[['文件外发放行（已知大小合计）']];
  wb.recalculate();
  const temp=path.join(config.work,'rendered.xlsx');await (await SpreadsheetFile.exportXlsx(wb)).save(temp);
  // Reimport the serialized workbook and compare every populated data cell, including merge anchors.
  const checked=await SpreadsheetFile.importXlsx(await FileBlob.load(temp));
  const cs=checked.worksheets.getItem(l.sheet);
  const equal=(actual,expected)=>typeof expected==='number'?Math.abs(actual-expected)<1e-8:actual===expected;
  for(const [start,rows,audit] of [[dayStart,d.day,d.auditDay],[monthStart,d.month,d.auditMonth]]){
    for(let i=0;i<rows.length;i++){
      for(let j=0;j<6;j++){
        if(j===0&&i>0&&rows[i].employee_id===rows[i-1].employee_id)continue;
        if(!equal(cs.getCell(start+i-1,j+1).values[0][0],rows[i].cells[j]))throw Error('Serialized risk data mismatch');
      }
    }
    for(let i=0;i<audit.length;i++)for(let j=0;j<4;j++)if(!equal(cs.getCell(start+i-1,j+10).values[0][0],audit[i][j]))throw Error('Serialized outbound data mismatch');
  }
  for(const [label,value] of [['员工总数',d.employees],['风险员工',d.riskEmployees],['设备总数',d.devices]])if(!equal(cs.getRange(l.summary[label]).values[0][0],value))throw Error('Serialized summary mismatch');
  const ratio=cs.getRange(l.summary['风险员工占比']).values[0][0];
  if(!equal(ratio,d.employees>0?d.riskEmployees/d.employees:'不可计算'))throw Error('Ratio recalculation failed');
  const layoutOut={dayStart,monthStart,monthTitle,footer,dayCap,monthCap,expectedMerges};
  await fs.writeFile(path.join(config.work,'render-layout.json'),JSON.stringify(layoutOut));
  for(const [name,start,end] of [['day',1,monthTitle-1],['month',monthTitle,footer]]){
    // All affected rows are rendered in bounded chunks to avoid a single huge PNG.
    for(let first=start;first<=end;first+=20){
      const preview=await wb.render({sheetName:l.sheet,range:`A${first}:O${Math.min(first+19,end)}`,scale:1,format:'png'});
      await fs.writeFile(path.join(config.work,`${name}-${first}.png`),new Uint8Array(await preview.arrayBuffer()));
    }
  }
  // Do not publish here: Python validates OOXML merges/labels and atomically publishes afterward.
  console.log('PASS: rendered and reimported; staged at '+temp);
}
