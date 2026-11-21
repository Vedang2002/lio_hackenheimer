import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runHostedGmail,parseGmailMessage} from './gmail.mjs';
const sample=JSON.parse(await readFile(new URL('./sample.json',import.meta.url)));
const START=Date.parse('2026-11-21T17:00:00Z');
const incoming=(id='m1',subject='Help',extra={})=>({id,threadId:'t1',internalDate:String(START+1000),labelIds:['INBOX'],payload:{mimeType:'text/plain',headers:Object.entries({From:'Customer <customer@example.com>',Subject:subject,'Message-ID':'<original@example.com>',...extra}).map(([name,value])=>({name,value})),body:{data:Buffer.from('Please help with our request').toString('base64url')}}});
function fixture(options={}){
  let state={data:structuredClone(sample),actions:[],events:[],gmail:{activatedAt:START,records:{}}},version=0,clock=START+2000;const sent=[],calls=[];
  const storage={read:async()=>({state:structuredClone(state),etag:String(version)}),write:async(s,e)=>{if(e!==String(version))throw Error('Conflict');state=structuredClone(s);version++;}};
  const env={GMAIL_ENABLED:'true',GMAIL_CLIENT_ID:'mock-client',GMAIL_CLIENT_SECRET:'mock-client-secret',GMAIL_REFRESH_TOKEN:'mock-refresh'};
  const fetcher=async(url,request)=>{
    calls.push(url);assert.equal(request.redirect,'manual');
    if(url==='https://oauth2.googleapis.com/token')return Response.json({access_token:'mock-access'});
    const p=new URL(url),route=p.pathname.split('/users/me/')[1];
    if(route==='profile')return Response.json({emailAddress:options.account||'support.hackenheimer@gmail.com'});
    if(route==='messages'){
      if((p.searchParams.get('q')||'').startsWith('in:sent'))return Response.json({messages:sent.length?[{id:'out1'}]:[]});
      return Response.json({messages:(options.messages||[]).map(m=>({id:m.id})),...(options.nextPageToken?{nextPageToken:options.nextPageToken}:{})});
    }
    if(route==='messages/send'){
      if(options.failBefore)throw Error('Offline');sent.push(JSON.parse(request.body));
      if(options.loseResponse)throw Error('Lost response');return Response.json({id:'out'+sent.length});
    }
    if(route?.startsWith('messages/'))return Response.json(options.messages.find(m=>m.id===route.split('/')[1]));
    throw Error('Unexpected Google API route');
  };
  return {env,storage,fetcher,sent,calls,state:()=>state,run:()=>runHostedGmail(env,storage,fetcher,()=>clock),advance:ms=>clock+=ms};
}
test('hosted Gmail is disabled without opt-in and rejects the wrong account before reading mail',async()=>{const f=fixture({account:'other@example.com'});assert.deepEqual(await runHostedGmail({},f.storage,f.fetcher),{enabled:false});assert.equal(f.calls.length,0);await assert.rejects(f.run(),/does not match/);assert.ok(!f.calls.some(c=>c.includes('/messages')));});
test('direct OAuth worker ranks mail, sends through support account and persists duplicate protection',async()=>{const f=fixture({messages:[incoming('regular'),incoming('urgent','Urgent blocked line')]});const result=await f.run();assert.equal(result.sent,2);const mime=Buffer.from(f.sent[0].raw,'base64url').toString('utf8');assert.match(mime,/From: support.hackenheimer@gmail.com/);assert.match(mime,/Message-ID: <launchguard.reply.urgent@/);assert.equal(f.sent[0].threadId,'t1');assert.equal(f.state().actions.find(a=>a.id==='mail-urgent').state,'sent');await f.run();assert.equal(f.sent.length,2);assert.ok(!JSON.stringify(f.state()).includes('mock-refresh'));assert.equal(f.state().email.transport,'hosted-oauth');});
test('uncertain Google send is never repeated and a later Sent lookup reconciles it',async()=>{const options={messages:[incoming()],loseResponse:true},f=fixture(options);await assert.rejects(f.run(),/requires review/);assert.equal(f.sent.length,1);assert.equal(f.state().gmail.records.m1.status,'uncertain');options.loseResponse=false;await f.run();assert.equal(f.sent.length,1);assert.equal(f.state().gmail.records.m1.status,'sent');assert.equal(f.state().actions.find(a=>a.id==='mail-m1').state,'sent');});
test('a failure before send is held instead of blindly retried',async()=>{const options={messages:[incoming()],failBefore:true},f=fixture(options);await assert.rejects(f.run(),/requires review/);options.failBefore=false;await f.run();assert.equal(f.sent.length,0);assert.equal(f.state().gmail.records.m1.status,'uncertain');});
test('existing lease blocks concurrent workers and daily budget is reserved before sending',async()=>{const f=fixture({messages:[incoming()]});let current=await f.storage.read();current.state.gmail.leaseUntil=START+60000;await f.storage.write(current.state,current.etag);assert.equal((await f.run()).busy,true);assert.equal(f.calls.length,0);current=await f.storage.read();current.state.gmail.leaseUntil=0;current.state.gmail.budget={day:'2026-11-21',used:50};await f.storage.write(current.state,current.etag);await assert.rejects(f.run(),/Daily email limit/);assert.equal(f.sent.length,0);assert.equal(f.state().gmail.leaseOwner,null);});
test('loop filters, historical exclusion and ambiguous headers prevent replies',()=>{assert.equal(parseGmailMessage(incoming('old'),START+2000).skip,'before-activation');for(const header of [{'Auto-Submitted':'auto-replied'},{From:'support.hackenheimer@gmail.com'},{From:'no-reply@example.com'},{'Reply-To':'a@example.com, b@example.com'},{Subject:'Hi\r\nBcc: private@example.com'}])assert.ok(parseGmailMessage(incoming('m1','Help',header),START).skip);const m=incoming();m.payload.headers.push({name:'From',value:'other@example.com'});assert.throws(()=>parseGmailMessage(m,START),/Ambiguous/);});
test('unfinished pages resume across twenty-message batches without dropping messages',async()=>{const f=fixture({messages:Array.from({length:23},(_,i)=>incoming('m'+i))});assert.equal((await f.run()).sent,20);assert.ok(f.state().gmail.scan);assert.equal((await f.run()).sent,3);assert.equal(f.sent.length,23);assert.equal(f.state().gmail.scan,null);});
