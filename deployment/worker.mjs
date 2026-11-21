// Build injects core tools, assets and reviewed seed. Access is enforced by the Site's configured restricted sharing policy at Sites dispatch.
const STATE_KEY='launchguard/state-v1.json';
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin'};
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{...headers,'Content-Type':'application/json; charset=utf-8'}});
const fresh=()=>({data:structuredClone(customerSeed),actions:[],events:[],portalSync:null,revision:0});
async function readState(bucket){const object=await bucket.get(STATE_KEY);return {state:object?await object.json():fresh(),etag:object?.etag};}
async function writeState(bucket,state,etag){
  state.revision=(state.revision||0)+1;
  const options={httpMetadata:{contentType:'application/json'},onlyIf:etag?{etagMatches:etag}:{etagDoesNotMatch:'*'}};
  const result=await bucket.put(STATE_KEY,JSON.stringify(state),options);
  if(!result){const e=Error('Another session changed the data. Reload and retry.');e.status=409;throw e;}
}
function event(state,type,detail){state.events.unshift({at:new Date().toISOString(),type,detail});state.events=state.events.slice(0,100);}
async function parseBody(request){
  if(Number(request.headers.get('Content-Length')||0)>2000000)throw Error('Request exceeds 2 MB');
  const reader=request.body?.getReader();if(!reader)return {};
  let bytes=0;const chunks=[];while(true){const {done,value}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>2000000){await reader.cancel();throw Error('Request exceeds 2 MB');}chunks.push(value);}
  const all=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){all.set(chunk,offset);offset+=chunk.length;}return JSON.parse(new TextDecoder().decode(all)||'{}');
}
export default {
  async fetch(request,env){
    const url=new URL(request.url);
    try{
      if(request.method==='GET'&&url.pathname==='/health')return json({ok:true,storage:!!env.BUCKET});
      if(request.method==='GET'&&Object.hasOwn(assets,url.pathname)){
        const type=url.pathname==='/'||url.pathname.endsWith('.html')?'text/html; charset=utf-8':url.pathname.endsWith('.css')?'text/css':url.pathname.endsWith('.json')?'application/json':'text/javascript';
        return new Response(assets[url.pathname],{headers:{...headers,'Content-Type':type,'Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}});
      }
      if(!env.BUCKET)return json({error:'Persistent storage is not configured.'},503);
      if(request.method==='POST'){
        if(request.headers.get('Content-Type')?.split(';')[0]!=='application/json')return json({error:'JSON required'},415);
        const origin=request.headers.get('Origin');if(origin&&origin!==url.origin)return json({error:'Same-origin request required'},403);
      }
      const {state,etag}=await readState(env.BUCKET);
      if(request.method==='GET'&&url.pathname==='/api/state')return json({...state,report:assess(state.data),model:null,hosted:true});
      if(request.method!=='POST')return json({error:'Not found'},404);
      const body=await parseBody(request);
      let result={ok:true};
      switch(url.pathname){
        case '/api/import': state.data=validate(body);assess(state.data);state.actions=[];event(state,'import','Imported reviewed snapshot.');break;
        case '/api/customer':state.data=structuredClone(customerSeed);state.actions=[];event(state,state.data.sample?'sample-load':'customer-load',state.data.sample?'Restored deployed synthetic sample.':'Restored reviewed archive kickoff replay.');break;
        case '/api/reset':state.data=structuredClone(sampleSeed);state.actions=[];event(state,'sample-reset','Loaded synthetic scenario.');break;
        case '/api/run':{
          const report=assess(state.data),actions=planActions(report,state.actions);state.actions.push(...actions);event(state,'agent-run',`${report.status}; wrote ${actions.length} new action drafts.`);result={report,created:actions.length};break;
        }
        case '/api/action':{
          const action=state.actions.find(a=>a.id===body.id);if(!action||!['reviewed','dismissed'].includes(body.state))throw Error('Invalid action transition');action.state=body.state;event(state,'review',`${action.id}: ${body.state}`);break;
        }
        case '/api/recovery':{
          if(!state.data.sample)throw Error('Recovery simulation requires the synthetic sample.');const lot=state.data.lots.find(l=>l.id==='SB-RECOVERY');Object.assign(lot,{status:'confirmed',released:true,eta:'2026-11-22T14:00:00+01:00',updatedAt:state.data.now,source:'SYNTHETIC / confirmed-recovery.txt'});event(state,'sample-drop','Simulated earlier released shipment.');break;
        }
        case '/api/extract':return json({error:'Local Ollama is not connected to the hosted app. Use the local version for AI document extraction.'},400);
        default:return json({error:'Not found'},404);
      }
      await writeState(env.BUCKET,state,etag);return json(result);
    }catch(error){return json({error:error.message||'Request failed'},error.status||400);}
  }
};
