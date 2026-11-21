// One-time desktop OAuth flow. Open the printed Google URL yourself; no browser automation.
import http from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {randomBytes,createHash,timingSafeEqual} from 'node:crypto';
import {dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const clientPath=process.argv[2];
if(!clientPath)throw Error('Usage: node email-automation/connect-gmail.mjs <path-to-desktop-client.json>');
const config=JSON.parse(await readFile(clientPath,'utf8')).installed;
if(!config?.client_id||!config.client_secret)throw Error('Provide a Google Desktop OAuth client JSON file.');
const expected='support.hackenheimer@gmail.com';
const scopes=['https://www.googleapis.com/auth/gmail.readonly','https://www.googleapis.com/auth/gmail.send'];
const verifier=randomBytes(48).toString('base64url'),state=randomBytes(32).toString('base64url');
const challenge=createHash('sha256').update(verifier).digest('base64url');
const output=new URL('../launchguard/runtime/gmail-oauth.json',import.meta.url);
let finished=false;
const server=http.createServer(async(req,res)=>{
  res.setHeader('Content-Type','text/plain; charset=utf-8');res.setHeader('Cache-Control','no-store');res.setHeader('Referrer-Policy','no-referrer');
  const url=new URL(req.url,'http://127.0.0.1:8788');
  if(url.pathname!=='/callback'){res.writeHead(404);res.end('Not found');return;}
  const supplied=url.searchParams.get('state')||'';
  if(supplied.length!==state.length||!timingSafeEqual(Buffer.from(supplied),Buffer.from(state))){res.writeHead(403);res.end('Invalid authorization state.');return;}
  if(finished){res.writeHead(409);res.end('This connection attempt has finished.');return;}
  if(url.searchParams.has('error')){res.writeHead(400);res.end('Google authorization was not completed. Return to the setup instructions.');console.log('Google consent was declined or blocked. Check consent-screen test users and Gmail API enablement.');return;}
  const code=url.searchParams.get('code');if(!code){res.writeHead(400);res.end('Missing authorization code.');return;}
  finished=true;
  try {
    const response=await fetch('https://oauth2.googleapis.com/token',{method:'POST',redirect:'manual',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({code,client_id:config.client_id,client_secret:config.client_secret,redirect_uri:'http://127.0.0.1:8788/callback',grant_type:'authorization_code',code_verifier:verifier}),signal:AbortSignal.timeout(30000)});
    if(!response.ok)throw Error('Google token exchange failed (HTTP '+response.status+').');
    const token=await response.json();if(!token.refresh_token||!token.access_token)throw Error('Google did not return offline access; revoke the old grant and reconnect with consent.');
    const granted=new Set((token.scope||'').split(' '));if(scopes.some(scope=>!granted.has(scope)))throw Error('Both requested Gmail permissions must be granted.');
    const profileResponse=await fetch('https://gmail.googleapis.com/gmail/v1/users/me/profile',{headers:{Authorization:'Bearer '+token.access_token},redirect:'manual',signal:AbortSignal.timeout(30000)});
    if(!profileResponse.ok)throw Error('Enable Gmail API in the OAuth client project, then retry.');
    const profile=await profileResponse.json();if(profile.emailAddress?.toLowerCase()!==expected)throw Error('The authorized account is not the configured support account. Reconnect using '+expected+'.');
    await mkdir(dirname(fileURLToPath(output)),{recursive:true});
    await writeFile(output,JSON.stringify({client_id:config.client_id,client_secret:config.client_secret,refresh_token:token.refresh_token,account:expected,scope:token.scope,connectedAt:new Date().toISOString()}),{mode:0o600,flag:'wx'});
    res.end('Google authorization completed for '+expected+'. You can close this tab. Return to LaunchGuard setup to securely configure the hosted worker. No email has been sent.');
    console.log('GMAIL_OAUTH_READY: support account verified. Temporary credentials are in ignored launchguard/runtime/gmail-oauth.json. No email sent.');
    server.close();clearTimeout(expiry);
  }catch(error){res.writeHead(400);res.end(error.code==='EEXIST'?'A previous local authorization file already exists. Return to setup to finish or rotate it.':error.message);console.log(error.code==='EEXIST'?'Authorization file already exists; no credentials overwritten.':error.message);server.close();clearTimeout(expiry);}
});
server.listen(8788,'127.0.0.1',()=>{
  const url=new URL('https://accounts.google.com/o/oauth2/v2/auth');
  for(const [key,value]of Object.entries({client_id:config.client_id,redirect_uri:'http://127.0.0.1:8788/callback',response_type:'code',scope:scopes.join(' '),access_type:'offline',prompt:'consent',login_hint:expected,state,code_challenge:challenge,code_challenge_method:'S256'}))url.searchParams.set(key,value);
  console.log('Open this Google authorization link yourself and use '+expected+':\n'+url.href);
  console.log('Waiting up to 15 minutes for the localhost callback. No screen or browser access is used.');
});
const expiry=setTimeout(()=>{console.log('Authorization window expired. Rerun the setup command to try again.');server.close();},15*60000);
