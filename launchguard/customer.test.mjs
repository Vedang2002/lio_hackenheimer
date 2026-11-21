import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {assess,planActions} from './engine.mjs';
let data;
try{data=JSON.parse(await readFile(new URL('./customer-data/snapshot.json',import.meta.url),'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
test('actual archive normalizes BOM, embedded magnets, rates and reviewed stock',{skip:!data},()=>{
  const ce=data.products.find(p=>p.id==='FG-GH6-CE'),std=data.products.find(p=>p.id==='FG-GH6-STD');
  assert.equal(ce.rate,900);assert.equal(std.rate,6000);assert.equal(Object.keys(ce.bom).length,7);
  assert.equal(ce.bom['CMP-MAG-005'],undefined);assert.equal(ce.bom['CMP-GAM-003'],undefined);
  const usable=id=>data.components.find(c=>c.id===id).usable;
  assert.equal(usable('CMP-GAM-008'),19300);assert.equal(usable('CMP-GAM-010'),21800);assert.equal(usable('CMP-GAM-004'),122000);
});
test('stock-only customer forecast respects overlapping gaps',{skip:!data},()=>{
  const report=assess(data),ce=report.products.find(p=>p.id==='FG-GH6-CE'),std=report.products.find(p=>p.id==='FG-GH6-STD');
  assert.equal(report.status,'AT RISK');assert.equal(ce.gap,5900);assert.equal(std.gap,32000);assert.equal(ce.projected,66100);assert.equal(std.projected,608000);
});
test('optimistic material ceiling still has 6800 card shortage',{skip:!data},()=>{
  const d=structuredClone(data);
  for(const [id,n] of Object.entries({'CMP-GAM-001':28000,'CMP-GAM-003':350000,'CMP-GAM-004':20000,'CMP-GAM-008':8000,'CMP-GAM-010':5600}))d.components.find(c=>c.id===id).usable+=n;
  const report=assess(d);assert.equal(report.products.reduce((n,p)=>n+p.gap,0),6800);
  assert.ok(report.issues.some(i=>i.kind==='quality-hold')); // scenario stock changes do not silently resolve release evidence
});
test('customer evidence generates deduplicated owner-assigned actions',{skip:!data},()=>{
  const report=assess(data),actions=planActions(report);
  assert.ok(actions.some(a=>a.owner==='Daniel Okafor'&&a.kind==='quality-hold'));
  assert.ok(actions.some(a=>a.owner==='Clara Weiss'&&a.kind==='artwork-conflict'));
  assert.equal(planActions(report,actions).length,0);
});
