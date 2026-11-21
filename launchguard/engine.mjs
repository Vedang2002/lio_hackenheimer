import { createHash } from 'node:crypto';

export function validate(d) {
  const integer = x => Number.isSafeInteger(x) && x >= 0;
  const date = x => typeof x === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(x) && Number.isFinite(Date.parse(x));
  if (!d || !date(d.now) || !date(d.deadline) || !Array.isArray(d.shifts) || !Array.isArray(d.products) || !Array.isArray(d.components) || !Array.isArray(d.lots)) throw Error('Expected now, deadline, shifts, products, components, lots; timestamps must include timezone.');
  if (Date.parse(d.now) > Date.parse(d.deadline)) throw Error('Snapshot is after dispatch deadline.');
  const unique = (xs, label) => { if (new Set(xs.map(x=>x.id)).size !== xs.length || xs.some(x=>!x.id)) throw Error('Missing or duplicate '+label+' ID'); };
  unique(d.products,'product'); unique(d.components,'component'); unique(d.lots,'lot');
  const parts = new Set(d.components.map(x=>x.id));
  if(d.staleHours!==undefined && (!Number.isFinite(d.staleHours)||d.staleHours<=0))throw Error('staleHours must be positive.');
  for(const p of d.products) {
    if (!integer(p.target) || !integer(p.packed) || p.packed>p.target || !(p.rate>0) || !Number.isFinite(p.rate) || !p.bom || !Object.keys(p.bom).length) throw Error('Invalid product '+p.id);
    for(const [id,q] of Object.entries(p.bom)) if(!parts.has(id)||!integer(q)||q===0) throw Error('Invalid BOM '+p.id);
  }
  for(const c of d.components) if(!integer(c.erp) || !integer(c.usable) || !date(c.observedAt) || !c.source) throw Error('Invalid component '+c.id);
  for(const l of d.lots) if(!parts.has(l.component)||!integer(l.qty)||!date(l.eta)||!l.source||!date(l.updatedAt)||!['confirmed','unconfirmed','received'].includes(l.status)||typeof l.released!=='boolean') throw Error('Invalid lot '+l.id);
  const windows=d.shifts.map(s=> {if(!date(s.start)||!date(s.end)||Date.parse(s.start)>=Date.parse(s.end))throw Error('Invalid shift'); return [Date.parse(s.start),Date.parse(s.end)];}).sort((a,b)=>a[0]-b[0]);
  if(windows.some((w,i)=>i&&w[0]<windows[i-1][1]))throw Error('Overlapping shifts');
  if(d.products.length!==new Set(d.products.map(p=>p.priority)).size || d.products.some(p=>!integer(p.priority)))throw Error('Unique numeric product priorities required for shared stock allocation.');
  if(d.findings!==undefined && (!Array.isArray(d.findings)||d.findings.some(f=>!f.id||!f.kind||!f.title||!f.source||!f.owner)))throw Error('Findings need id, kind, title, source and owner.');
  if(d.assumptions!==undefined&&(!Array.isArray(d.assumptions)||d.assumptions.some(a=>typeof a!=='string')))throw Error('Assumptions must be text.');
  return d;
}

export const hoursBetween = (d,a,b) => d.shifts.reduce((h,s)=>h+Math.max(0,Math.min(+new Date(b),+new Date(s.end))-Math.max(+new Date(a),+new Date(s.start)))/3600000,0);
const hash = x => createHash('sha256').update(JSON.stringify(x)).digest('hex').slice(0,20);

// Deterministic tools own inventory and arithmetic. Model output never mutates stock.
export function assess(input) {
  const d=validate(input), now=+new Date(d.now), end=+new Date(d.deadline), issues=[];
  const staleHours=d.staleHours ?? 12;
  const stale=t => now-+new Date(t)>staleHours*3600000 || +new Date(t)>now;
  const add=(kind,key,title,source,owner='Felix Brunner')=>issues.push({id:hash([kind,key,title,source]),kind,title,source,owner});
  for(const finding of d.findings||[])add(finding.kind,finding.id,finding.title,finding.source,finding.owner);
  const inventory=Object.fromEntries(d.components.map(c=>[c.id,c.usable]));
  for(const c of d.components){
    if(c.erp!==c.usable)add('stock-conflict',c.id,`${c.id}: ERP ${c.erp}; planning-usable ${c.usable}. Reconcile and physically verify before release.`,c.source);
    if(stale(c.observedAt))add('stale-stock',c.id,`${c.id}: stock evidence stale or future-dated; physically verify.`,c.source);
  }
  for(const l of d.lots){
    if(l.status==='received') continue; // receipt already included in usable stock; never count twice
    if(!l.released)add('release-hold',l.id,`${l.id}: release not evidenced; do not pack.`,l.source);
    if(stale(l.updatedAt))add('stale-tracking',l.id,`${l.id}: obtain current location, ETA and proof of delivery readiness.`,l.source);
    if(l.status!=='confirmed')add('unconfirmed',l.id,`${l.id}: confirm quantity, revision, Hamburg dock ETA and release documents.`,l.source);
    if(+new Date(l.eta)<now)add('overdue',l.id,`${l.id}: ETA has passed without receipt; verify dock status before counting supply.`,l.source);
    if(+new Date(l.eta)>=end)add('late',l.id,`${l.id}: arrives at or after dispatch; request an earlier evidenced option.`,l.source);
  }
  const products=[...d.products].sort((a,b)=>a.priority-b.priority);
  const remaining=Object.fromEntries(products.map(p=>[p.id,p.target-p.packed]));
  const built=Object.fromEntries(products.map(p=>[p.id,0]));
  // Boundary simulation avoids using goods before arrival and respects overnight closure.
  const boundaries=[now,end,...d.shifts.flatMap(s=>[+new Date(s.start),+new Date(s.end)]),...d.lots.map(l=>+new Date(l.eta))].filter(t=>t>=now&&t<=end).sort((a,b)=>a-b);
  const times=[...new Set(boundaries)]; const admitted=new Set();
  const carry=Object.fromEntries(products.map(p=>[p.id,0]));
  for(let i=0;i<times.length-1;i++){
    const t=times[i],next=times[i+1];
    for(const l of d.lots)if(!admitted.has(l.id)&&l.status==='confirmed'&&l.released&&!stale(l.updatedAt)&&+new Date(l.eta)>=now&&+new Date(l.eta)<=t){inventory[l.component]+=l.qty;admitted.add(l.id);}
    const work=hoursBetween(d,new Date(t),new Date(next));
    for(const p of products){
      // Product-specific lines/cell; shared components allocated by declared priority.
      const material=Math.min(...Object.entries(p.bom).map(([id,q])=>Math.floor(inventory[id]/q)));
      const capacity=work*p.rate+carry[p.id];
      const qty=Math.min(remaining[p.id],Math.floor(capacity+1e-8),material);
      // Keep fractional production only while the line actually had materials to run.
      carry[p.id]=qty<Math.floor(capacity+1e-8)?0:capacity-qty;
      remaining[p.id]-=qty;built[p.id]+=qty;
      for(const [id,q] of Object.entries(p.bom))inventory[id]-=qty*q;
    }
  }
  const results=products.map(p=>{
    const gap=remaining[p.id], needed=p.target-p.packed, hours=hoursBetween(d,d.now,d.deadline);
    if(gap)add('shortfall',p.id,`${p.name}: ${gap} copies cannot finish under the evidenced plan. Confirm recovery before committing spend.`,p.source||'Imported product plan','Dr. Maren Vogt');
    return {id:p.id,name:p.name,target:p.target,packed:p.packed,needed,projected:p.packed+built[p.id],gap,requiredRate:hours?needed/hours:null,rate:p.rate};
  });
  const uncertain=(d.findings||[]).length>0||issues.some(x=>['stale-stock','stale-tracking','unconfirmed','stock-conflict'].includes(x.kind));
  return {snapshot:d.now,deadline:d.deadline,dataset:d.dataset||'Imported snapshot',sample:!!d.sample,hours:hoursBetween(d,d.now,d.deadline),status:results.some(p=>p.gap)?'AT RISK':uncertain?'NEEDS VERIFICATION':'FEASIBLE UNDER INPUT ASSUMPTIONS',products:results,issues,inventory,assumptions:[...(d.assumptions||[]),'Usable stock is unallocated component stock for planning, excluding packed copies and received lots already included. Physical verification may remain outstanding.','Product rates are total independent line/cell rates; priority governs shared material allocation.','Only confirmed, released shipments with fresh tracking are admitted at dock ETA; include receiving/QA time in ETA.','Forecast is conditional on supplied rates, stock and evidence; no substitutions or legal release inferred.']};
}

export function planActions(report, previous=[]) {
  const seen=new Set(previous.map(a=>a.id));
  return report.issues.filter(i=>!seen.has(i.id)).map(i=>({...i,state:'draft',createdAt:report.snapshot,subject:`LaunchGuard / ${i.kind} / evidence required`,body:`Hello,\n\n${i.title}\n\nPlease provide quantity, item revision, release status, Hamburg dock-ready time with timezone, and supporting evidence. Dispatch deadline: ${report.deadline}.\nReference: ${i.source}\n\nPlease reply with the earliest feasible recovery option and incremental cost.\nLaunchGuard procurement team`}));
}
