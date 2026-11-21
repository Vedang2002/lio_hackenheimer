import {readFile,writeFile} from 'node:fs/promises';
import {assess,planActions} from './engine.mjs';
const directory=new URL('./customer-data/',import.meta.url);
const data=JSON.parse(await readFile(new URL('snapshot.json',directory),'utf8'));
const report=assess(data);
const actions=planActions(report);
const scenarios=[];
function ceiling(name,changes){
  const d=structuredClone(data);
  for(const [id,quantity] of Object.entries(changes))d.components.find(c=>c.id===id).usable+=quantity;
  const r=assess(d);
  scenarios.push({name,label:'Hypothetical supply usable at kickoff; no real ETA or approval asserted',products:r.products,totalGap:r.products.reduce((n,p)=>n+p.gap,0)});
}
ceiling('Stock-only conservative baseline',{});
ceiling('28,000 flown cards + port cases + 20,000 rev-C sleeves all usable at kickoff',{'CMP-GAM-001':28000,'CMP-GAM-003':350000,'CMP-GAM-004':20000});
ceiling('Above plus offered 8,000 books',{'CMP-GAM-001':28000,'CMP-GAM-003':350000,'CMP-GAM-004':20000,'CMP-GAM-008':8000});
ceiling('Above plus full statue concession (not approved)',{'CMP-GAM-001':28000,'CMP-GAM-003':350000,'CMP-GAM-004':20000,'CMP-GAM-008':8000,'CMP-GAM-010':5600});
const analysis={...report,scenarios,filesRead:data.archiveFiles,notice:'All projections are conditional archive replay scenarios; actual 21 Nov progress and dock/QA times are unknown.'};
await writeFile(new URL('assessment.json',directory),JSON.stringify(analysis,null,2));
await writeFile(new URL('action-drafts.json',directory),JSON.stringify(actions,null,2));
await writeFile(new URL('action-drafts.txt',directory),actions.map(a=>`OWNER: ${a.owner}\nSTATE: ${a.state}\nSUBJECT: ${a.subject}\n\n${a.body}`).join('\n\n====================\n\n'));
console.log(JSON.stringify({status:report.status,products:report.products,actions:actions.length,scenarios:scenarios.map(s=>({name:s.name,gap:s.totalGap}))},null,2));
