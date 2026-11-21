import {assess} from './engine.mjs';
import {automateQueue,emailPlan,emailResult,priorityFor} from './automation.mjs';
const SUPPORT_ACCOUNT='support.hackenheimer@gmail.com';
const toBase64=text=>{const bytes=new TextEncoder().encode(text);let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);return btoa(binary);};
const fromBase64=text=>new TextDecoder().decode(Uint8Array.from(atob(text.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0)));
function header(message,name){const values=(message.payload?.headers||[]).filter(h=>h.name.toLowerCase()===name.toLowerCase());if(values.length>1&&['from','reply-to','subject','message-id'].includes(name.toLowerCase()))throw Error('Ambiguous email headers.');return values.map(v=>v.value).join(' ');}
function address(text){if(/[\r\n\x00-\x1f]/.test(text))return null;const matches=text.match(/[A-Za-z0-9.!#$%&'*+\/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}/g)||[];return matches.length===1?matches[0].toLowerCase():null;}
export function parseGmailMessage(message,activatedAt){
  if(!/^[-\w]{1,128}$/.test(message.id)||!/^[-\w]{1,128}$/.test(message.threadId)||!Number.isFinite(Number(message.internalDate)))return {skip:'invalid-metadata'};
  if(Number(message.internalDate)<activatedAt)return {skip:'before-activation'};
  const from=address(header(message,'From')),target=address(header(message,'Reply-To')||header(message,'From'));
  if(!from||!target||from===SUPPORT_ACCOUNT||target===SUPPORT_ACCOUNT||!message.labelIds?.includes('INBOX')||message.labelIds.includes('SENT'))return {skip:'self-or-invalid-address'};
  const auto=header(message,'Auto-Submitted').trim().toLowerCase();
  if(auto&&auto!=='no'||header(message,'List-Id')||/\b(bulk|list|junk)\b/i.test(header(message,'Precedence'))||/^(no[._-]?reply|mailer-daemon|postmaster)@/i.test(from)||header(message,'Return-Path').trim()==='<>'||/report|delivery-status/i.test(message.payload?.mimeType||''))return {skip:'automated-or-bulk'};
  const originalId=header(message,'Message-ID').trim(),subject=header(message,'Subject');
  if(!/^<[^\s<>\r\n]{1,500}>$/.test(originalId)||/[\r\n\x00-\x1f]/.test(subject)||subject.length>700)return {skip:'invalid-thread-headers'};
  const parts=[];function visit(p){if(p.mimeType==='text/plain'&&p.body?.data)parts.push(fromBase64(p.body.data));for(const part of p.parts||[])visit(part);}visit(message.payload||{});
  const text=parts.join('\n').split(/\n(?:On .{0,250}wrote:|>)/)[0].slice(0,30000);
  return {id:message.id,threadId:message.threadId,from:target,subject,text,originalId,priority:priorityFor(subject+' '+text)};
}
const outgoingId=id=>'<launchguard.reply.'+id+'@launchguard.invalid>';
export function gmailMime(input,reply){
  let subject=/^re\s*:/i.test(input.subject)?input.subject:'Re: '+input.subject;
  if(/[^\x20-\x7e]/.test(subject)){const chars=Array.from(subject),words=[];for(let i=0;i<chars.length;i+=10)words.push('=?UTF-8?B?'+toBase64(chars.slice(i,i+10).join(''))+'?=');subject=words.join('\r\n ');}
  const mime=['From: '+SUPPORT_ACCOUNT,'To: '+input.from,'Subject: '+subject,'Message-ID: '+outgoingId(input.id),'In-Reply-To: '+input.originalId,'References: '+input.originalId,'Auto-Submitted: auto-replied','X-Auto-Response-Suppress: All','MIME-Version: 1.0','Content-Type: text/plain; charset=UTF-8','Content-Transfer-Encoding: base64','',toBase64(reply).match(/.{1,76}/g).join('\r\n')].join('\r\n');
  return toBase64(mime).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
}
async function boundedJson(response){
  if(!response.ok)throw Error('Google API returned HTTP '+response.status+'.');
  const reader=response.body.getReader(),chunks=[];let size=0;
  while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>2000000){await reader.cancel();throw Error('Google response exceeds the processing limit.');}chunks.push(value);}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}return JSON.parse(new TextDecoder().decode(bytes));
}
export async function runHostedGmail(env,storage,fetcher=fetch,clock=()=>Date.now()){
  if(env.GMAIL_ENABLED!=='true')return {enabled:false};
  if(!env.GMAIL_CLIENT_ID||!env.GMAIL_CLIENT_SECRET||!env.GMAIL_REFRESH_TOKEN)throw Error('Gmail OAuth credentials are not configured.');
  const started=clock(),owner=crypto.randomUUID();let leased=false;
  const update=async(fn,requireLease=true)=>{const {state,etag}=await storage.read();if(requireLease&&state.gmail?.leaseOwner!==owner)throw Error('Email worker lease changed. No duplicate send is authorized.');fn(state);await storage.write(state,etag);return state;};
  try{
    let initial=await storage.read();if(initial.state.gmail?.leaseUntil>started)return {busy:true};
    initial.state.gmail={...initial.state.gmail,leaseOwner:owner,leaseUntil:started+180000,records:initial.state.gmail?.records||{},activatedAt:initial.state.gmail?.activatedAt||started};
    await storage.write(initial.state,initial.etag);leased=true;
    const token=await boundedJson(await fetcher('https://oauth2.googleapis.com/token',{method:'POST',redirect:'manual',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({client_id:env.GMAIL_CLIENT_ID,client_secret:env.GMAIL_CLIENT_SECRET,refresh_token:env.GMAIL_REFRESH_TOKEN,grant_type:'refresh_token'}),signal:AbortSignal.timeout(30000)}));
    if(!token.access_token)throw Error('Google refresh did not return an access token.');
    const gmail=async(path,body)=>boundedJson(await fetcher('https://gmail.googleapis.com/gmail/v1/users/me/'+path,{method:body?'POST':'GET',redirect:'manual',headers:{Authorization:'Bearer '+token.access_token,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(20000)}));
    const profile=await gmail('profile');if(profile.emailAddress?.toLowerCase()!==SUPPORT_ACCOUNT)throw Error('Gmail OAuth account does not match the configured support account.');
    let current=await update(s=>{s.email={account:SUPPORT_ACCOUNT,lastSeenAt:new Date(clock()).toISOString(),enabled:true,transport:'hosted-oauth'};s.gmail.lastError=null;const day=new Date(clock()).toISOString().slice(0,10);if(s.gmail.budget?.day!==day)s.gmail.budget={day,used:0};if(!s.gmail.scan)s.gmail.scan={started,query:'in:inbox -category:promotions -category:social after:'+Math.floor(((s.gmail.checkpoint||s.gmail.activatedAt)-300000)/1000)+' before:'+(Math.floor(started/1000)+1),pageToken:null};});
    // Recover confirmed sends without sending again after an interrupted/uncertain request.
    for(const [id,record]of Object.entries(current.gmail.records).filter(([,r])=>['sending','uncertain'].includes(r.status)).slice(0,5)){
      const sent=await gmail('messages?maxResults=2&q='+encodeURIComponent('in:sent rfc822msgid:'+outgoingId(id)));
      if(sent.messages?.length)current=await update(s=>{s.gmail.records[id]={...record,status:'sent',gmailId:sent.messages[0].id,at:clock()};emailResult(s,{taskId:record.taskId,status:'sent',gmailId:sent.messages[0].id});});
    }
    const scan=current.gmail.scan,listing=await gmail('messages?maxResults=100&q='+encodeURIComponent(scan.query)+(scan.pageToken?'&pageToken='+encodeURIComponent(scan.pageToken):''));
    const pending=(listing.messages||[]).filter(m=>!current.gmail.records[m.id]);const batch=[];
    for(const item of pending.slice(0,20)){
      const message=await gmail('messages/'+encodeURIComponent(item.id)+'?format=full');let input;
      try{input=parseGmailMessage(message,current.gmail.activatedAt);}catch{input={skip:'malformed-headers'};}
      if(input.skip){current=await update(s=>{s.gmail.records[item.id]={status:'skipped',reason:input.skip,at:clock()};});continue;}batch.push(input);
    }
    const rank={high:0,medium:1,normal:2};batch.sort((a,b)=>rank[a.priority]-rank[b.priority]);let sentCount=0;
    for(const input of batch){
      if(clock()-started>200000)break;
      let plan;
      current=await update(s=>{if(s.gmail.records[input.id])return;automateQueue(s,assess(s.data));plan=emailPlan(s,input,new Date(clock()).toISOString());if(plan.alreadySent){s.gmail.records[input.id]={status:'sent',taskId:plan.taskId,at:clock()};return;}if(s.gmail.budget.used>=50)throw Error('Daily email limit reached; remaining messages will resume next UTC day.');s.gmail.budget.used++;s.gmail.records[input.id]={status:'sending',taskId:plan.taskId,at:clock()};const task=s.actions.find(a=>a.id===plan.taskId);task.state='sending';s.gmail.leaseUntil=clock()+180000;});
      if(!plan||plan.alreadySent)continue;
      try{
        const sent=await gmail('messages/send',{threadId:input.threadId,raw:gmailMime(input,plan.reply)});if(!sent.id)throw Error('Gmail did not confirm a message ID.');
        current=await update(s=>{s.gmail.records[input.id]={status:'sent',taskId:plan.taskId,gmailId:sent.id,at:clock()};emailResult(s,{taskId:plan.taskId,status:'sent',gmailId:sent.id});});sentCount++;
      }catch(error){await update(s=>{s.gmail.records[input.id]={status:'uncertain',taskId:plan.taskId,at:clock()};emailResult(s,{taskId:plan.taskId,status:'uncertain'});});throw Error('Email send outcome requires review. It will not be sent again automatically.');}
    }
    await update(s=>{const unfinished=pending.some(m=>!s.gmail.records[m.id]);if(!unfinished){if(listing.nextPageToken)s.gmail.scan.pageToken=listing.nextPageToken;else{s.gmail.checkpoint=scan.started;s.gmail.scan=null;}}s.gmail.lastRun=new Date(clock()).toISOString();const cutoff=Math.min(clock()-30*86400000,(s.gmail.checkpoint||0)-86400000);for(const [id,r]of Object.entries(s.gmail.records))if(['sent','skipped'].includes(r.status)&&r.at<cutoff)delete s.gmail.records[id];});
    return {enabled:true,read:Math.min(pending.length,20),sent:sentCount};
  }catch(error){if(leased)try{await update(s=>{s.gmail.lastError=error.message;});}catch{}throw error;}
  finally{if(leased)try{await update(s=>{s.gmail.leaseUntil=0;s.gmail.leaseOwner=null;});}catch{}}
}
