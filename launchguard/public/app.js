let state,refreshing=false;
const el=id=>document.getElementById(id),num=x=>x.toLocaleString('en-GB');
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
async function api(path,data){const r=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const b=await r.json();if(!r.ok)throw Error(b.error||'Request failed');return b;}
function download(name,data){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function renderActions(){
  const filter=el('queue-filter').value,finished=a=>a.active===false||['reviewed','dismissed','sent','drafted'].includes(a.state);
  const tasks=state.actions.filter(a=>filter==='history'?finished(a):!finished(a)&&(filter==='all'||a.priority===filter||a.kind===filter));
  el('actions').replaceChildren(...tasks.map(a=>{
    const n=node('article',undefined,'action');n.append(node('span',`${(a.priority||'normal').toUpperCase()} · ${a.state.toUpperCase()}`,'badge priority-'+(a.priority||'normal')),node('p',a.title),node('p',a.owner,'source'));
    if(a.kind==='customer-email')n.append(node('p',`${a.from} · ${a.replyPolicy==='approved-answer'?'Approved answer':'Automatic receipt'}`,'source'));
    const d=node('details');d.append(node('summary',a.kind==='customer-email'?'View reply':'View evidence request'),node('pre',a.body));n.append(d);
    if(!finished(a)&&a.kind!=='customer-email'){
      for(const [s,title]of [['reviewed','Mark reviewed'],['dismissed','Dismiss']]){const b=node('button',title);b.onclick=()=>act(b,async()=>{await api('action',{id:a.id,state:s});await refresh();});n.append(b);}
      const approved=node('details');approved.append(node('summary','Approve a customer-facing answer'));
      approved.append(node('p','This exact answer may be emailed automatically only to the specified address when their message includes the reference. Include only information approved for that customer.','muted'));
      const form=node('form');
      function field(title,type,value){const label=node('label',title),input=node(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;input.required=true;input.value=value||'';label.append(input);form.append(label);return input;}
      const recipient=field('Exact customer email','email',a.customerReply?.recipient),reference=field('Reference or phrase in their message','text',a.customerReply?.matchAny?.[0]),reply=field('Approved public reply','textarea',a.customerReply?.body);
      reply.maxLength=5000;reference.minLength=4;reference.maxLength=150;
      const save=node('button','Approve automatic reply');save.type='submit';form.append(save);
      form.onsubmit=e=>{e.preventDefault();act(save,async()=>{await api('customer-reply',{id:a.id,recipient:recipient.value.trim(),matchAny:[reference.value.trim()],body:reply.value.trim()});await refresh();});};approved.append(form);n.append(approved);
    }
    return n;
  }));
  if(!tasks.length)el('actions').append(node('p','No tasks in this view. Your queue updates automatically.','muted'));
}
async function refresh(){
  if(refreshing)return;refreshing=true;
  try{
    state=await api('state');const r=state.report;
    el('sample').textContent=r.sample?'Synthetic demonstration · Real launch status is unknown.':`${r.dataset} · Conditional forecast based on reviewed evidence, not a live warehouse count.`;
    if(state.portalSync?.requiresReview&&!r.sample)el('sample').textContent+=' Source changes or an unverified source connection need attention; earlier reviewed forecast retained.';
    el('status').textContent=r.status;el('summary').textContent=`${num(r.products.reduce((s,p)=>s+p.gap,0))} copies short under this plan · ${r.hours.toFixed(1)} packing hours from snapshot · ${new Date(r.snapshot).toLocaleString('en-GB',{timeZone:'Europe/Berlin'})} CET`;
    el('sync-status').textContent=!state.syncConfigured?'Automatic connection needs a server-side Soony token.':state.portalSync?.failed?'Latest check failed. Previous forecast retained.':state.portalSync?.checkedAt?`Last checked ${new Date(state.portalSync.checkedAt).toLocaleString()}. ${state.portalSync.requiresReview?'Updated files need review.':'Reviewed source hashes match.'} ${state.portalSync.pendingDownloads?state.portalSync.pendingDownloads+' downloads pending.':''}`:'Automatic checks configured; waiting for the first run.';
    const heartbeat=state.email?.lastSeenAt,age=heartbeat?Date.now()-Date.parse(heartbeat):Infinity;
    el('email-status').textContent=age<(state.email?.transport==='hosted-oauth'?4500000:300000)?`Worker checked in ${new Date(heartbeat).toLocaleTimeString()}. Sender: support.hackenheimer@gmail.com.`:heartbeat?'No recent worker check-in. Check the Google timer and credentials.':'Not connected to this dashboard. Google authorization and worker setup required.';
    const open=state.actions.filter(a=>a.active!==false&&!['reviewed','dismissed','sent','drafted'].includes(a.state));el('queue-status').textContent=`${open.length} open · ${open.filter(a=>a.priority==='high').length} high priority. Automatically ranked.`;
    el('chat-mode').textContent=state.model||state.chatMode==='ai'?'AI + evidence':'Evidence assistant · AI model not connected';
    el('products').replaceChildren(...r.products.map(p=>{const card=node('article',undefined,'product');card.append(node('h2',p.name));const q=node('div',num(p.projected),'quantity');q.append(node('small',` / ${num(p.target)} projected`));card.append(q);const bar=node('div',undefined,'meter');for(const [cls,val]of [['packed',p.packed],['forecast',p.projected-p.packed]]){const seg=node('div',undefined,cls);seg.style.width=(100*val/p.target)+'%';bar.append(seg);}card.append(bar);const facts=node('div',undefined,'facts');facts.append(node('span',`${num(p.packed)} already packed`),node('span',`${num(p.gap)} short`),node('span',`${p.requiredRate?.toFixed(0)??'—'}/h needed`));card.append(facts);return card;}));
    el('issue-count').textContent=r.issues.length+' findings';el('issues').replaceChildren(...r.issues.map(i=>{const n=node('article',undefined,'issue');n.append(node('b',i.kind.replaceAll('-',' ')),node('p',i.title),node('p',i.owner+' · '+i.source,'source'));return n;}));
    renderActions();el('assumptions').replaceChildren(...r.assumptions.map(x=>node('li',x)));el('events').replaceChildren(...state.events.map(e=>node('p',`${e.at} · ${e.type}: ${e.detail}`,'event')));
    el('model').textContent=state.model?`Local AI: ${state.model}. Exact quotes are checked for document extraction.`:'Local Ollama can provide AI explanations and document extraction. The hosted assistant uses deterministic evidence until a model is connected.';el('recovery').disabled=!r.sample;el('extract').disabled=!state.model;
  }finally{refreshing=false;}
}
async function act(button,fn){button.disabled=true;el('message').textContent='Working…';try{await fn();el('message').textContent='Saved. Your workspace is up to date.';}catch(e){el('message').textContent=e.message;}finally{button.disabled=false;}}
for(const [id,path]of [['run','run'],['recovery','recovery'],['reset','reset'],['customer','customer']])el(id).onclick=()=>act(el(id),async()=>{await api(path,{});await refresh();});
el('queue-filter').onchange=()=>state&&renderActions();
el('export').onclick=()=>download('launchguard-actions.json',state.actions);el('download').onclick=()=>download('launchguard-snapshot.json',state.data);
el('upload').onchange=async e=>{const file=e.target.files[0];if(file)await act(el('run'),async()=>{await api('import',JSON.parse(await file.text()));await refresh();});e.target.value='';};
el('extract').onclick=()=>act(el('extract'),async()=>{const result=await api('extract',{source:el('source').value,text:el('document').value});el('extraction').textContent=JSON.stringify(result,null,2);await refresh();});
el('chat-form').onsubmit=async e=>{
  e.preventDefault();const question=el('chat-question').value.trim();if(!question)return;const button=el('chat-send');button.disabled=true;
  el('conversation').append(node('p',question,'chat-question'));el('chat-question').value='';
  const answer=node('div',undefined,'chat-answer');answer.append(node('p','Finding the relevant evidence…'));el('conversation').append(answer);
  try{const result=await api('chat',{question});answer.replaceChildren(node('p',result.answer));if(result.sources.length){answer.append(node('b','Sources'));for(const source of result.sources)answer.append(node('p',source,'source'));}answer.append(node('p',result.notice,'source'));}catch(error){answer.replaceChildren(node('p',error.message));}finally{button.disabled=false;answer.scrollIntoView({behavior:'smooth',block:'nearest'});}
};
for(const button of document.querySelectorAll('[data-question]'))button.onclick=()=>{el('chat-question').value=button.dataset.question;el('chat-form').requestSubmit();};
refresh().catch(e=>el('message').textContent=e.message);
setInterval(()=>{if(document.hidden||document.querySelector('#actions input:focus, #actions textarea:focus'))return;refresh().catch(()=>{el('message').textContent='Workspace refresh failed. Retrying automatically.';});},30000);
