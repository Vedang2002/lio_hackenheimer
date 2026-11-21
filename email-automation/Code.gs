/** LaunchGuard Gmail worker. Executes in the authorized user's Google Apps Script project. */
function emailConfig_() {
  var c=LAUNCHGUARD_EMAIL_CONFIG;
  if(!c||!['task_queue','acknowledge','approved_rules','draft'].includes(c.mode))throw Error('Invalid email mode.');
  if(!/^[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+$/.test(c.expectedAccount))throw Error('Invalid expected account.');
  if(![1,5,10,15,30].includes(c.intervalMinutes))throw Error('Unsupported trigger interval.');
  ['messagesPerRun','maxPagesPerRun','maxWritesPerDay'].forEach(function(k){if(!Number.isInteger(c[k])||c[k]<1||c[k]>100)throw Error('Invalid '+k);});
  if(typeof c.inboxQuery!=='string'||!c.inboxQuery.trim()||!Array.isArray(c.rules)||typeof c.acknowledgment!=='string'||!c.acknowledgment.trim())throw Error('Missing email configuration.');
  var ids={};c.rules.forEach(function(r){if(!r.id||ids[r.id]||!Array.isArray(r.matchAny)||!r.matchAny.length||r.matchAny.some(function(p){return typeof p!=='string'||p.trim().length<4;})||typeof r.reply!=='string'||!r.reply.trim())throw Error('Invalid or duplicate reply rule.');ids[r.id]=true;});
  if(JSON.stringify(c).length>7000)throw Error('Keep email configuration below 7,000 characters for persistent settings.');
  if(c.siteUrl&&c.siteUrl!=='https://launchguard-hackenheimer.lucidcoil32.chatgpt.site')throw Error('Use the registered LaunchGuard Site URL.');
  return c;
}

function gmailRequest_(method,path,body) {
  var options={method:method,headers:{Authorization:'Bearer '+ScriptApp.getOAuthToken()},muteHttpExceptions:true,followRedirects:false};
  if(body!==undefined){options.contentType='application/json';options.payload=JSON.stringify(body);}
  var response=UrlFetchApp.fetch('https://gmail.googleapis.com/gmail/v1/users/me/'+path,options);
  var status=response.getResponseCode();
  if(status<200||status>=300){var error=Error('Gmail API returned HTTP '+status);error.httpStatus=status;throw error;}
  return JSON.parse(response.getContentText()||'{}');
}

function verifyGmailConnection() {
  var config=emailConfig_(),profile=gmailRequest_('get','profile');
  if(!profile.emailAddress||profile.emailAddress.toLowerCase()!==config.expectedAccount.toLowerCase())throw Error('Authorize the configured support account; current Google account does not match.');
  var result={account:profile.emailAddress,mode:config.mode,enabled:PropertiesService.getScriptProperties().getProperty('LG_ENABLED')==='true'};
  console.log(JSON.stringify(result));return result;
}

function startEmailAutomation() {
  var lock=LockService.getScriptLock();if(!lock.tryLock(1000))throw Error('Worker is busy; retry shortly.');
  try{
    var config=emailConfig_();verifyGmailConnection();var props=PropertiesService.getScriptProperties();
    var previous=props.getProperty('LG_SETTINGS');var fingerprint=JSON.stringify(config);
    if(previous&&previous!==fingerprint)throw Error('Configuration changed. Run stopEmailAutomation, then acceptEmailConfiguration, then start again.');
    var triggers=ScriptApp.getProjectTriggers().filter(function(t){return t.getHandlerFunction()==='processCustomerInbox';});
    if(!triggers.length)ScriptApp.newTrigger('processCustomerInbox').timeBased().everyMinutes(config.intervalMinutes).create();
    var now=String(Date.now());if(!props.getProperty('LG_ACTIVATED_AT'))props.setProperty('LG_ACTIVATED_AT',now);
    if(!props.getProperty('LG_CHECKPOINT'))props.setProperty('LG_CHECKPOINT',now);
    props.setProperty('LG_SETTINGS',fingerprint);props.setProperty('LG_ENABLED','true');
    console.log('Email worker enabled. New messages only; account authorization and trigger installation completed.');
  }finally{lock.releaseLock();}
}

function stopEmailAutomation() {
  var lock=LockService.getScriptLock();if(!lock.tryLock(1000))throw Error('Worker is busy; retry shortly.');
  try{
    PropertiesService.getScriptProperties().setProperty('LG_ENABLED','false');
    ScriptApp.getProjectTriggers().forEach(function(t){if(t.getHandlerFunction()==='processCustomerInbox')ScriptApp.deleteTrigger(t);});
    console.log('Email worker disabled. Inbox messages and saved draft/send records were retained.');
  }finally{lock.releaseLock();}
}

function acceptEmailConfiguration() {
  var props=PropertiesService.getScriptProperties();if(props.getProperty('LG_ENABLED')==='true')throw Error('Stop automation before accepting configuration changes.');
  verifyGmailConnection();props.setProperty('LG_SETTINGS',JSON.stringify(emailConfig_()));
  // Restart scanning from the last completed checkpoint under the newly approved filter.
  props.deleteProperty('LG_SCAN');console.log('Configuration accepted. Run startEmailAutomation to resume.');
}

function header_(message,name) {
  var entries=(message.payload&&message.payload.headers)||[];
  var matches=entries.filter(function(h){return h.name.toLowerCase()===name.toLowerCase();});
  if(matches.length>1&&['from','reply-to','message-id','subject'].includes(name.toLowerCase()))throw Error('Ambiguous duplicate email header.');
  return matches.map(function(h){return h.value;}).join(' ');
}

function singleAddress_(value) {
  if(/[\r\n\x00-\x1f]/.test(value))return null;
  var matches=value.match(/[A-Za-z0-9.!#$%&'*+\/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}/g)||[];
  return matches.length===1?matches[0].toLowerCase():null;
}

function bodyText_(payload) {
  if(!payload)return '';
  var parts=[];
  function visit(p){if(p.mimeType==='text/plain'&&p.body&&p.body.data)parts.push(Utilities.newBlob(Utilities.base64DecodeWebSafe(p.body.data)).getDataAsString('UTF-8'));(p.parts||[]).forEach(visit);}
  visit(payload);
  // Do not use quoted history to select a public-response rule.
  return parts.join('\n').split(/\n(?:On .{0,250}wrote:|>)/)[0].slice(0,30000);
}

function replyDecision_(message,config) {
  var from=singleAddress_(header_(message,'From'));
  var target=singleAddress_(header_(message,'Reply-To')||header_(message,'From'));
  var labels=message.labelIds||[];
  if(!from||!target)return {skip:'ambiguous-address'};
  if(from===config.expectedAccount.toLowerCase()||target===config.expectedAccount.toLowerCase()||labels.includes('SENT')||!labels.includes('INBOX'))return {skip:'self-or-not-inbox'};
  var auto=header_(message,'Auto-Submitted').trim().toLowerCase();
  if(auto&&auto!=='no'||header_(message,'List-Id')||/\b(bulk|list|junk)\b/i.test(header_(message,'Precedence'))||/^(no[._-]?reply|mailer-daemon|postmaster)@/i.test(from)||header_(message,'Return-Path').trim()==='<>')return {skip:'automated-or-bulk'};
  var payloadType=(message.payload&&message.payload.mimeType)||'';
  if(/report|delivery-status/i.test(payloadType))return {skip:'delivery-report'};
  var originalId=header_(message,'Message-ID').trim();
  if(!/^<[^\s<>\r\n]{1,500}>$/.test(originalId))return {skip:'missing-or-invalid-message-id'};
  var subject=header_(message,'Subject');if(/[\r\n\x00-\x1f]/.test(subject)||subject.length>700)return {skip:'invalid-subject'};
  var text=(subject+'\n'+bodyText_(message.payload)).toLowerCase();
  var matches=config.rules.filter(function(rule){return rule.matchAny.some(function(term){return text.includes(term.toLowerCase());});});
  var reply=config.acknowledgment,ruleId='acknowledgment';
  var priority=/urgent|critical|blocked|line.stop|safety/i.test(text)?'high':/overdue|deadline|delay/i.test(text)?'medium':'normal';
  if(config.mode==='task_queue'){
    reply=matches.length===1?matches[0].reply:'Thank you for contacting Hackenheimer. We received your message'+(priority==='high'?' and flagged it as high priority':priority==='medium'?' and flagged it for timely review':'')+'. This is an automatic receipt; our team has not yet reviewed or resolved your request.\n\nHackenheimer Support';
    ruleId=matches.length===1?matches[0].id:'priority-receipt';
  }
  if(config.mode==='approved_rules'){
    if(matches.length!==1)return {review:matches.length?'multiple-rules':'no-approved-answer'};
    reply=matches[0].reply;ruleId=matches[0].id;
  }else if(config.mode==='draft'&&matches.length===1){reply=matches[0].reply;ruleId=matches[0].id;}
  if(reply.length>20000)throw Error('Reply template is too long.');
  return {to:target,subject:subject,originalId:originalId,reply:reply,ruleId:ruleId,priority:priority,draft:config.mode==='draft'};
}

function bridgeConfigured_(props){return !!(props.getProperty('LG_SITE_SERVICE_TOKEN')&&props.getProperty('LG_EMAIL_BRIDGE_SECRET'));}
function bridgeRequest_(path,body,config,props) {
  var response=UrlFetchApp.fetch(config.siteUrl+'/api/email/'+path,{method:'post',contentType:'application/json',headers:{'OAI-Sites-Authorization':'Bearer '+props.getProperty('LG_SITE_SERVICE_TOKEN'),'X-LaunchGuard-Email-Key':props.getProperty('LG_EMAIL_BRIDGE_SECRET')},payload:JSON.stringify(body),muteHttpExceptions:true,followRedirects:false});
  if(response.getResponseCode()<200||response.getResponseCode()>=300)throw Error('LaunchGuard email bridge is unavailable. No unconfirmed dashboard answer will be sent.');
  var result=JSON.parse(response.getContentText());return result;
}
function publishEmailResult_(props,id,record,config) {
  if(!record.taskId||!bridgeConfigured_(props))return;
  try{bridgeRequest_('result',{taskId:record.taskId,status:record.status==='sent'?'sent':record.status==='drafted'?'drafted':'uncertain',gmailId:record.gmailId||''},config,props);record.bridgePending=false;record_(props,id,record);}
  catch(error){record.bridgePending=true;record_(props,id,record);}
}

function outgoingId_(gmailId){if(!/^[A-Za-z0-9_-]{1,128}$/.test(gmailId))throw Error('Invalid Gmail message ID.');return '<launchguard.reply.'+gmailId+'@launchguard.invalid>';}

function mimeReply_(message,decision,account) {
  var subject=/^re\s*:/i.test(decision.subject)?decision.subject:'Re: '+decision.subject;
  if(/[^\x20-\x7e]/.test(subject)){var chars=Array.from(subject),words=[];for(var i=0;i<chars.length;i+=10)words.push('=?UTF-8?B?'+Utilities.base64Encode(chars.slice(i,i+10).join(''),Utilities.Charset.UTF_8)+'?=');subject=words.join('\r\n ');}
  var prior=header_(message,'References').match(/<[^\s<>\r\n]{1,500}>/g)||[];
  // One bounded reference is sufficient for Gmail threading alongside In-Reply-To and threadId.
  var references=decision.originalId;
  var encodedBody=Utilities.base64Encode(decision.reply,Utilities.Charset.UTF_8).match(/.{1,76}/g).join('\r\n');
  var mime=['From: '+account,'To: '+decision.to,'Subject: '+subject,'Message-ID: '+outgoingId_(message.id),'In-Reply-To: '+decision.originalId,'References: '+references,'Auto-Submitted: auto-replied','X-Auto-Response-Suppress: All','MIME-Version: 1.0','Content-Type: text/plain; charset=UTF-8','Content-Transfer-Encoding: base64','',''+encodedBody].join('\r\n');
  return Utilities.base64EncodeWebSafe(mime,Utilities.Charset.UTF_8).replace(/=+$/,'');
}

function record_(props,id,value){props.setProperty('LG_MSG_'+id,JSON.stringify(Object.assign({at:Date.now()},value)));}

function reconcileOne_(props,id,record) {
  var query=(record.kind==='draft'?'in:drafts':'in:sent')+' rfc822msgid:'+outgoingId_(id);
  var matches=gmailRequest_('get','messages?maxResults=2&q='+encodeURIComponent(query));
  if(matches.messages&&matches.messages.length){record.status=record.kind==='draft'?'drafted':'sent';record.gmailId=matches.messages[0].id;}
  else record.status='uncertain'; // Never resend after an ambiguous network outcome.
  record.at=Date.now();record_(props,id,record);return record;
}

function processOne_(message,config,props,stats,budget) {
  var key='LG_MSG_'+message.id,existing=props.getProperty(key);
  if(existing){var saved=JSON.parse(existing);if(saved.status==='sending'||saved.status==='creating-draft')saved=reconcileOne_(props,message.id,saved);if(saved.bridgePending||saved.taskId)publishEmailResult_(props,message.id,saved,config);return;}
  if(!Number.isFinite(Number(message.internalDate))||!message.threadId){record_(props,message.id,{status:'review',reason:'missing-message-metadata'});stats.review++;return;}
  if(Number(message.internalDate)<Number(props.getProperty('LG_ACTIVATED_AT'))){record_(props,message.id,{status:'skipped',reason:'before-activation'});stats.skipped++;return;}
  var decision;try{decision=replyDecision_(message,config);}catch(error){record_(props,message.id,{status:'review',reason:'Malformed or ambiguous message headers; review manually.'});stats.review++;return;}
  if(decision.skip||decision.review){record_(props,message.id,{status:decision.skip?'skipped':'review',reason:decision.skip||decision.review});stats[decision.skip?'skipped':'review']++;return;}
  if(config.mode==='task_queue'&&bridgeConfigured_(props)){
    var plan=bridgeRequest_('plan',{id:message.id,threadId:message.threadId,from:decision.to,subject:decision.subject,text:bodyText_(message.payload)},config,props);
    if(typeof plan.reply!=='string'||!plan.reply.trim()||plan.reply.length>20000||typeof plan.taskId!=='string')throw Error('Invalid dashboard reply plan.');
    if(plan.alreadySent){record_(props,message.id,{status:'sent',taskId:plan.taskId,reason:'Dashboard already confirmed this message.'});return;}
    decision.reply=plan.reply;decision.priority=plan.priority;decision.taskId=plan.taskId;decision.ruleId=plan.policy;
  }
  if(budget.used>=config.maxWritesPerDay)throw Error('Daily email write limit reached; processing will resume on the next UTC day.');
  var raw=mimeReply_(message,decision,config.expectedAccount);
  var pending={status:decision.draft?'creating-draft':'sending',kind:decision.draft?'draft':'send',ruleId:decision.ruleId,priority:decision.priority,taskId:decision.taskId||null};
  // Persist reservation and budget BEFORE the Gmail call; a crash never authorizes an automatic duplicate send.
  budget.used++;props.setProperty('LG_BUDGET',JSON.stringify(budget));record_(props,message.id,pending);
  try{
    var result=decision.draft?gmailRequest_('post','drafts',{message:{threadId:message.threadId,raw:raw}}):gmailRequest_('post','messages/send',{threadId:message.threadId,raw:raw});
    if(!(result.id||result.message&&result.message.id))throw Error('Gmail did not confirm a message ID.');
    var completed=Object.assign({},pending,{status:decision.draft?'drafted':'sent',gmailId:result.id||result.message.id});record_(props,message.id,completed);publishEmailResult_(props,message.id,completed,config);stats[decision.draft?'drafted':'sent']++;
  }catch(error){var uncertain=Object.assign({},pending,{status:'uncertain',reason:'Gmail result not confirmed; inspect Sent/Drafts before any manual retry.'});record_(props,message.id,uncertain);publishEmailResult_(props,message.id,uncertain,config);throw Error('Email write outcome requires review; this message will not be sent again automatically.');}
}

function processCustomerInbox() {
  var lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {busy:true};
  var props=PropertiesService.getScriptProperties(),started=Date.now();
  var stats={sent:0,drafted:0,review:0,skipped:0,read:0};
  try{
    if(props.getProperty('LG_ENABLED')!=='true')return {enabled:false};
    var config=emailConfig_();if(props.getProperty('LG_SETTINGS')!==JSON.stringify(config))throw Error('Configuration changed; stop, accept configuration, and restart before sending.');
    verifyGmailConnection();
    if(bridgeConfigured_(props))bridgeRequest_('heartbeat',{enabled:true},config,props);
    // Retry dashboard status writes independently of the scan checkpoint; never repeat Gmail sends.
    Object.keys(props.getProperties()).filter(function(k){return k.indexOf('LG_MSG_')===0;}).slice(-200).forEach(function(k){var r=JSON.parse(props.getProperty(k));if(r.bridgePending)publishEmailResult_(props,k.slice(7),r,config);});
    var day=new Date(started).toISOString().slice(0,10),budget=JSON.parse(props.getProperty('LG_BUDGET')||'{}');if(budget.day!==day)budget={day:day,used:0};
    var scan=JSON.parse(props.getProperty('LG_SCAN')||'null');
    if(!scan){var checkpoint=Number(props.getProperty('LG_CHECKPOINT')||props.getProperty('LG_ACTIVATED_AT'));if(!Number.isFinite(checkpoint)||checkpoint<=0)throw Error('Start automation before processing messages.');scan={started:started,query:config.inboxQuery+' after:'+Math.floor((checkpoint-300000)/1000)+' before:'+(Math.floor(started/1000)+1),pageToken:null};props.setProperty('LG_SCAN',JSON.stringify(scan));}
    var seenThisRun={};
    for(var page=0;page<config.maxPagesPerRun;page++){
      var endpoint='messages?maxResults=100&q='+encodeURIComponent(scan.query)+(scan.pageToken?'&pageToken='+encodeURIComponent(scan.pageToken):'');
      var listing=gmailRequest_('get',endpoint);
      var messages=listing.messages||[],batch=[],partial=false;
      for(var i=0;i<messages.length;i++){
        if(stats.read>=config.messagesPerRun||Date.now()-started>240000){partial=true;break;}
        var id=messages[i].id;if(seenThisRun[id])continue;seenThisRun[id]=true;
        var saved=props.getProperty('LG_MSG_'+id);
        if(saved){var record=JSON.parse(saved);if(['sending','creating-draft'].includes(record.status)){record=reconcileOne_(props,id,record);publishEmailResult_(props,id,record,config);}continue;}
        var message=gmailRequest_('get','messages/'+encodeURIComponent(id)+'?format=full');stats.read++;
        batch.push(message);
      }
      // Prioritize the bounded fetched batch. Never skip the remainder of an unfinished page.
      function rank_(m){try{var d=replyDecision_(m,config);return d.priority==='high'?0:d.priority==='medium'?1:2;}catch(e){return 3;}}
      batch.sort(function(a,b){return rank_(a)-rank_(b)||Number(a.internalDate)-Number(b.internalDate);});
      batch.forEach(function(m){processOne_(m,config,props,stats,budget);});
      if(partial){props.setProperty('LG_LAST_RUN',JSON.stringify({at:Date.now(),stats:stats,remaining:true}));return stats;}
      if(!listing.nextPageToken){props.setProperty('LG_CHECKPOINT',String(scan.started));props.deleteProperty('LG_SCAN');props.setProperty('LG_LAST_RUN',JSON.stringify({at:Date.now(),stats:stats}));return stats;}
      scan.pageToken=listing.nextPageToken;props.setProperty('LG_SCAN',JSON.stringify(scan));
    }
    props.setProperty('LG_LAST_RUN',JSON.stringify({at:Date.now(),stats:stats,remaining:true}));return stats;
  }catch(error){props.setProperty('LG_LAST_ERROR',JSON.stringify({at:Date.now(),message:error.message}));throw error;}
  finally{lock.releaseLock();}
}

function showEmailAutomationStatus() {
  var props=PropertiesService.getScriptProperties(),all=props.getProperties(),counts={};
  Object.keys(all).filter(function(k){return k.indexOf('LG_MSG_')===0;}).forEach(function(k){var status=JSON.parse(all[k]).status;counts[status]=(counts[status]||0)+1;});
  var result={enabled:props.getProperty('LG_ENABLED')==='true',activatedAt:props.getProperty('LG_ACTIVATED_AT'),lastRun:JSON.parse(props.getProperty('LG_LAST_RUN')||'null'),lastError:JSON.parse(props.getProperty('LG_LAST_ERROR')||'null'),counts:counts};console.log(JSON.stringify(result));return result;
}

function reconcileUncertainEmailWrites() {
  var lock=LockService.getScriptLock();if(!lock.tryLock(1000))throw Error('Worker is busy.');
  try{verifyGmailConnection();var props=PropertiesService.getScriptProperties(),all=props.getProperties(),count=0;Object.keys(all).forEach(function(k){if(k.indexOf('LG_MSG_')!==0||count>=20)return;var record=JSON.parse(all[k]);if(['sending','creating-draft','uncertain'].includes(record.status)){record=reconcileOne_(props,k.slice(7),record);publishEmailResult_(props,k.slice(7),record,emailConfig_());count++;}});return {checked:count};}finally{lock.releaseLock();}
}

function listEmailRecordsForReview() {
  var props=PropertiesService.getScriptProperties(),all=props.getProperties(),records=[];
  Object.keys(all).filter(function(k){return k.indexOf('LG_MSG_')===0;}).forEach(function(k){var r=JSON.parse(all[k]);if(['review','uncertain','sending','creating-draft'].includes(r.status))records.push({incomingGmailId:k.slice(7),status:r.status,reason:r.reason||'',at:r.at});});
  console.log(JSON.stringify(records.slice(0,100)));return records.slice(0,100);
}

function pruneOldEmailProcessingRecords() {
  var lock=LockService.getScriptLock();if(!lock.tryLock(1000))throw Error('Worker is busy.');
  try{
    var props=PropertiesService.getScriptProperties();if(props.getProperty('LG_SCAN'))throw Error('Finish the current scan before pruning records.');
    var checkpoint=Number(props.getProperty('LG_CHECKPOINT')||0),cutoff=Math.min(Date.now()-30*86400000,checkpoint-86400000),all=props.getProperties(),removed=0;
    Object.keys(all).forEach(function(k){if(k.indexOf('LG_MSG_')!==0)return;var r=JSON.parse(all[k]);if(['sent','drafted','skipped'].includes(r.status)&&r.at<cutoff){props.deleteProperty(k);removed++;}});
    return {removed:removed};
  }finally{lock.releaseLock();}
}
