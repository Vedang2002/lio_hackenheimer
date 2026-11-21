export const SOONY_ORIGIN='https://start-hack-portal-cxh3crbdaef2f8gt.westeurope-01.azurewebsites.net';
export async function syncSoony(state,store,token,fetcher=fetch,now=new Date().toISOString()) {
  if(!token){state.portalSync={...state.portalSync,configured:false,failed:true,requiresReview:true,message:'Set the server-side Soony token to enable automatic checks.'};return;}
  const prior=state.portalSync||{},baseline=state.data.sourceChecksums||{};
  try {
    const headers={Authorization:'Bearer '+token};
    const response=await fetcher(SOONY_ORIGIN+'/api/v1/files',{headers,redirect:'manual',signal:AbortSignal.timeout(30000)});
    if(!response.ok)throw Error('Soony index returned HTTP '+response.status);
    const index=await response.json();
    if(!Array.isArray(index.files)||index.files.length!==index.count||index.count>5000)throw Error('Invalid Soony index.');
    const seen=new Set();
    for(const file of index.files){
      if(typeof file.path!=='string'||!file.path||file.path.length>500||file.path.startsWith('/')||file.path.split('/').some(p=>!p||p==='.'||p==='..')||/[\\:\x00-\x1f]/.test(file.path)||seen.has(file.path)||!/^[a-f0-9]{64}$/i.test(file.sha256)||!Number.isSafeInteger(file.bytes)||file.bytes<0||file.bytes>20000000)throw Error('Invalid Soony file metadata.');
      seen.add(file.path);
    }
    const changed=index.files.filter(f=>baseline[f.path]!==f.sha256).map(f=>f.path);
    const removed=Object.keys(baseline).filter(p=>!seen.has(p));
    const downloaded={...(prior.downloadedChecksums||{})};let bytes=0,count=0;
    // Files matching the embedded reviewed seed are already available in the private source.
    // Download new/changed files in bounded batches; immutable hash keys preserve prior evidence.
    for(const file of index.files){
      if(baseline[file.path]===file.sha256||downloaded[file.path]===file.sha256)continue;
      if(count>=10||bytes+file.bytes>20000000)break;
      const url=SOONY_ORIGIN+'/api/v1/files/'+file.path.split('/').map(encodeURIComponent).join('/');
      const r=await fetcher(url,{headers,redirect:'manual',signal:AbortSignal.timeout(60000)});
      if(!r.ok)throw Error('Soony file returned HTTP '+r.status);
      const reader=r.body.getReader(),chunks=[];let size=0;
      while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>file.bytes||size>20000000){await reader.cancel();throw Error('Soony file exceeds declared size.');}chunks.push(value);}
      if(size!==file.bytes)throw Error('Soony file size mismatch.');
      const data=new Uint8Array(size);let offset=0;for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.length;}
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',data))).map(b=>b.toString(16).padStart(2,'0')).join('');
      if(digest!==file.sha256.toLowerCase())throw Error('Soony file checksum mismatch.');
      await store.put('soony/files/'+file.sha256+'/'+file.path,data);
      downloaded[file.path]=file.sha256;count++;bytes+=size;
    }
    await store.put('soony/latest-index.json',JSON.stringify(index));
    for(const path of [...changed,...removed]){
      const id='source-'+path;
      const existing=state.actions.find(a=>a.id===id);
      const version=index.files.find(f=>f.path===path)?.sha256||'removed';
      if(existing?.sourceVersion===version)continue;
      const task={id,kind:'source-change',title:'Review updated Soony evidence: '+path,source:path,sourceVersion:version,owner:'Soony evidence review',priority:'high',state:'queued',workflow:'evidence-review',active:true,createdAt:now,body:'This source differs from the reviewed forecast. Verify quantities, releases and timing, then import a reviewed normalized snapshot. Raw file changes never release stock automatically.'};
      if(existing)Object.assign(existing,task);else state.actions.push(task);
    }
    const pending=index.files.filter(f=>baseline[f.path]!==f.sha256&&downloaded[f.path]!==f.sha256).length;
    state.portalSync={configured:true,checkedAt:now,count:index.count,changed,removed,requiresReview:changed.length+removed.length>0,downloadedChecksums:downloaded,downloadedThisRun:count,pendingDownloads:pending,failed:false};
  }catch(error){state.portalSync={...prior,configured:true,checkedAt:now,failed:true,requiresReview:true,message:error.message};}
}
