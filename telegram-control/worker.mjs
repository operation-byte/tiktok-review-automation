import {viewerHtml, openHtml} from './pages.mjs';
const SHOPS=['welive_main','snackersbox','konomi_living'];
const WORKFLOWS=['daily-reviews.yml','dry-run.yml','relogin.yml'];
const BIGSELLER_WORKFLOWS=['bigseller-sync.yml','bigseller-login.yml'];
const LABELS={welive_main:'WELIVE',snackersbox:"Snacker's Box",konomi_living:'Konomi Living'};
const headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY'};
const reply=(text,status=200)=>new Response(text,{status,headers});
const json=value=>new Response(JSON.stringify(value),{headers:{...headers,'Content-Type':'application/json'}});
const hash=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))).map(x=>x.toString(16).padStart(2,'0')).join('');
const token=()=>crypto.randomUUID()+crypto.randomUUID();
export function authorised(message,owner){return message?.chat?.type==='private' && String(message.from?.id)===owner && String(message.chat?.id)===owner;}
export function parseCommand(text=''){
 const parts=text.trim().toLowerCase().split(/\s+/);const raw=parts[0].split('@')[0];const name=raw.startsWith('tt_')?'/'+raw:raw;
 if(!name.startsWith('/tt_'))return null;
 const action=name.slice(4);
 if(action==='login'&&parts.length===1)return {action,shop:''};
 const shop=parts[1]||'all';
 if(!['help','status','errors','run','dryrun','pause','resume','login','loginstatus'].includes(action)||parts.length>2||!['all',...SHOPS].includes(shop)||(action==='login'&&shop==='all'))return {error:true};
 return {action,shop};
}
export function parseCallback(data=''){if(data==='bs_login')return {action:'login',service:'bigseller'};const shop=typeof data==='string'&&data.startsWith('tt_login:')?data.slice(9):'';return SHOPS.includes(shop)?{action:'login',shop}:null;}
export function parseBigSellerCommand(text=''){
 const parts=text.trim().toLowerCase().split(/\s+/),name=parts[0].split('@')[0];
 if(!name.startsWith('/bs_'))return null;
 const action=name.slice(4);
 return parts.length===1&&['help','status','errors','login','run','dryrun','pause','resume'].includes(action)?{action,service:'bigseller'}:{error:true,service:'bigseller'};
}
const parseControlCommand=text=>parseBigSellerCommand(text)||parseCommand(text);
export function authorisedCallback(cq,owner){return String(cq?.from?.id)===owner && cq?.message?.chat?.type==='private' && String(cq.message.chat.id)===owner;}
// Namespaced automation commands and login buttons stay here; stock traffic is forwarded.
export const isTikTokUpdate=u=>{const callback=parseCallback(u?.callback_query?.data);return !!(parseCommand(u?.message?.text)||(callback&&callback.service!=='bigseller'));};
export const isControlUpdate=u=>isTikTokUpdate(u)||!!parseBigSellerCommand(u?.message?.text)||parseCallback(u?.callback_query?.data)?.service==='bigseller';
export const validSession=(s,now=Date.now())=>!!s && s.expires>now && ['starting','ready'].includes(s.status);
const HELP='TikTok reviews\n/tt_status — latest run and login status\n/tt_run [shop|all] — live review check\n/tt_dryrun [shop|all] — no posting\n/tt_pause — pause future live runs\n/tt_resume — enable daily runs\n/tt_login — tap a shop button to log in\n/tt_errors — recent failures and recovery\nShop names: welive_main, snackersbox, konomi_living.\nRelogin opens a private browser link on phone or computer. Never send passwords or OTPs in chat.';
const BS_HELP='BigSeller stock sync\n/bs_login — private BigSeller login browser\n/bs_run — export Single SKU inventory and update dashboard stock\n/bs_dryrun — download and validate export without updating stock\n/bs_status — latest login, stock import and job status\n/bs_pause — pause scheduled syncs\n/bs_resume — enable scheduled syncs\n/bs_errors — recent failed jobs\nUse Warehouse in the main dashboard for the same controls. Never send passwords or OTPs in chat.';
export class Control{
 constructor(ctx,env){this.ctx=ctx;this.env=env;this.agent=null;this.viewer=null;this.pending=[];this.busy=false;this.commandQueue=Promise.resolve();}
 async tg(method,body){const r=await fetch(`https://api.telegram.org/bot${this.env.BOT_TOKEN}/${method}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error('Telegram delivery failed');return r;}
 async say(text,extra={}){try{await this.env.WEBHOOK_AUDIT.put('last_reply_attempt',JSON.stringify({at:new Date().toISOString(),type:text.startsWith('TikTok reviews')?'help':text.startsWith('Which shop')?'shop-prompt':'other'}))}catch(e){};let status=0,ok=false;try{const r=await this.tg('sendMessage',{chat_id:this.env.OWNER_ID,text,link_preview_options:{is_disabled:true},...extra});status=r.status;ok=true;}finally{try{await this.env.WEBHOOK_AUDIT.put('last_reply_result',JSON.stringify({at:new Date().toISOString(),status,ok}))}catch(e){}}}
 async gh(path,method='GET',body){const r=await fetch(`https://api.github.com/repos/${this.env.REPO}${path}`,{method,headers:{Authorization:`Bearer ${this.env.GH_TOKEN}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'tiktok-telegram-control','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error(`GitHub ${r.status}`);return r.status===204?null:r.json();}
 async update(u){const task=this.commandQueue.then(()=>this.handleUpdate(u));this.commandQueue=task.catch(()=>{});return task;}
 async handleUpdate(u){try{await this.env.WEBHOOK_AUDIT.put('last_do_update',JSON.stringify({at:new Date().toISOString(),ownerOk:authorised(u.message,this.env.OWNER_ID)||authorisedCallback(u.callback_query,this.env.OWNER_ID),validId:Number.isSafeInteger(u.update_id)}))}catch{}
  const cb=!u.message&&u.callback_query?u.callback_query:null;
  if(!(cb?authorisedCallback(cb,this.env.OWNER_ID):authorised(u.message,this.env.OWNER_ID))||!Number.isSafeInteger(u.update_id))return;
  // Storage write before dispatch prevents retries from posting twice.
  let seen=await this.ctx.storage.get('seen')||[];if(seen.includes(u.update_id))return;
  seen.push(u.update_id);await this.ctx.storage.put('seen',seen.slice(-256));
try{await this.env.WEBHOOK_AUDIT.put('last_tiktok_command',JSON.stringify({at:new Date().toISOString(),command:cb?'button:'+(parseCallback(cb.data)?.shop||'invalid'):parseCommand(u.message?.text)?.action||'invalid',ownerOk:true}))}catch(e){};
  if(this.busy){await this.say('Another command is being handled. Please try again shortly.');return;}
  this.busy=true;
  try{
   if(cb){
    const cmd=parseCallback(cb.data);
    await this.tg('answerCallbackQuery',{callback_query_id:cb.id,text:cmd?'Starting login for '+(cmd.service==='bigseller'?'BigSeller':LABELS[cmd.shop]):'That button is no longer valid.'}).catch(()=>{});
    if(!cmd)return;
    await this.tg('editMessageReplyMarkup',{chat_id:cb.message.chat.id,message_id:cb.message.message_id,reply_markup:{inline_keyboard:[]}}).catch(()=>{});
    await this.command(cmd,u.update_id);
   }else await this.command(parseControlCommand(u.message.text),u.update_id);
  }catch(e){const bs=!!parseBigSellerCommand(u.message?.text)||parseCallback(cb?.data)?.service==='bigseller';await this.say(`${bs?'BigSeller':'TikTok'} command could not complete (${String(e.message).slice(0,80)}). Use ${bs?'/bs_status':'/tt_status'} before retrying; a request may already have started.`);}finally{this.busy=false;}
 }
 async runs(workflows=WORKFLOWS){return (await this.gh('/actions/runs?per_page=50')).workflow_runs.filter(r=>workflows.some(w=>r.path===`.github/workflows/${w}`));}
 async bigSellerEnabled(){try{return (await this.gh('/actions/variables/BIGSELLER_AUTOMATION_ENABLED')).value==='true';}catch(e){if(e.message==='GitHub 404')return false;throw e;}}
 async bigSellerAction(action){
  if(!['help','status','errors','login','run','dryrun','pause','resume'].includes(action))return {ok:false,message:'Unknown BigSeller action. Use /bs_help.'};
  if(action==='help')return {ok:true,message:BS_HELP};
  if(action==='pause'||action==='resume'){
   const variable={name:'BIGSELLER_AUTOMATION_ENABLED',value:action==='resume'?'true':'false'};
   try{await this.gh('/actions/variables/BIGSELLER_AUTOMATION_ENABLED','PATCH',variable);}catch(e){if(e.message!=='GitHub 404')throw e;await this.gh('/actions/variables','POST',variable);}
   return {ok:true,message:action==='pause'?'Scheduled BigSeller stock syncs paused. An active sync can finish.':'BigSeller scheduled stock syncs enabled. Use Run sync or /bs_run for an update now.'};
  }
  const runs=await this.runs(BIGSELLER_WORKFLOWS);
  if(action==='status'||action==='errors'){
   const summary=await this.ctx.storage.get('bigsellerSummary')||{};
   const selected=(action==='errors'?runs.filter(r=>r.conclusion&&r.conclusion!=='success'):runs).slice(0,3);
   const enabled=await this.bigSellerEnabled();
   return {ok:true,status:{...summary,enabled,runs:selected.map(r=>({status:r.conclusion||r.status,url:r.html_url,created_at:r.created_at,updated_at:r.updated_at}))},message:`BigSeller scheduled sync: ${enabled?'enabled':'paused'}\nLast report: ${summary.status||'No report yet'}${summary.when?' · '+summary.when:''}${summary.login_needed?'\nLogin required — tap Login to BigSeller in the alert or use /bs_login.':''}${summary.rows!==undefined?'\nImported SKU rows: '+summary.rows:''}${summary.lastImportAt?'\nLast stock update: '+summary.lastImportAt:''}\n`+selected.map(r=>`${r.name}: ${r.conclusion||r.status}\n${r.html_url}`).join('\n')};
  }
  const active=runs.find(r=>!r.conclusion);
  if(active)return {ok:false,message:'A BigSeller login/sync job is already active.\n'+active.html_url};
  if(action==='run'&&!await this.bigSellerEnabled())return {ok:false,message:'BigSeller sync is paused. Use /bs_resume or Resume in Warehouse first.'};
  const recent=await this.ctx.storage.get('bigsellerLastDispatch')||0;
  if(Date.now()-recent<60000)return {ok:false,message:'A BigSeller job was just requested. Wait one minute, then refresh status.'};
  const inputs={};let workflow='bigseller-sync.yml',loginUrl='';
  if(action==='login'){
   workflow='bigseller-login.yml';inputs.session=crypto.randomUUID();const key=token();
   const stub=this.env.CONTROL.get(this.env.CONTROL.idFromName('session:'+inputs.session));
   await stub.fetch('https://internal/create',{method:'POST',body:JSON.stringify({shop:'bigseller',service:'bigseller',keyHash:await hash(key),expires:Date.now()+35*60000,status:'starting'})});
   loginUrl=`${this.env.PUBLIC_URL}/open/${inputs.session}?service=bigseller#${key}`;
  }else inputs.dry_run=action==='dryrun'?'true':'false';
  await this.ctx.storage.put('bigsellerLastDispatch',Date.now());
  try{await this.gh(`/actions/workflows/${workflow}/dispatches`,'POST',{ref:'main',inputs});}catch(e){await this.ctx.storage.delete('bigsellerLastDispatch');throw e;}
  return {ok:true,...(loginUrl?{loginUrl}:{}),message:action==='login'?`Starting your private BigSeller login browser. Allow a few minutes for startup.\n${loginUrl}\nLog in, open Inventory List, then press Save login. The browser closes after 15 minutes. Do not share this link.`:action==='dryrun'?'BigSeller export check requested. Stock data will stay unchanged.':'BigSeller sync requested. Stock update is confirmed only after the main dashboard importer and Procurement refresh complete.'};
 }
 async command(cmd,updateId){
  if(cmd?.service==='bigseller'){const result=await this.bigSellerAction(cmd.error?'invalid':cmd.action);return this.say(result.message);}
  if(cmd?.error)return this.say('That TikTok command was not recognised. Send /tt_login to pick a shop with buttons, or /tt_help for all commands.');
  if(!cmd)return this.say(HELP);
  const {action,shop}=cmd;
  if(action==='help')return this.say(HELP);
  if(['pause','resume'].includes(action)){
   if(shop!=='all')return this.say('Pause/resume currently applies to all review shops. Use /tt_pause or /tt_resume.');
   await this.gh('/actions/variables/AUTOMATION_ENABLED','PATCH',{name:'AUTOMATION_ENABLED',value:action==='resume'?'true':'false'});
   return this.say(action==='pause'?'Future live review runs paused. An already-running shop is allowed to finish and save its history.':'Daily reviews enabled for 09:15 Singapore time. Use /tt_run for a check now.');
  }
  if(action==='login'&&!shop)return this.say('Which shop do you want to log in to?',{reply_markup:{inline_keyboard:SHOPS.map(s=>[{text:LABELS[s],callback_data:'tt_login:'+s}])}});
  if(['status','loginstatus','errors'].includes(action)){
   const runs=await this.runs();const enabled=await this.gh('/actions/variables/AUTOMATION_ENABLED');
   const state=await this.ctx.storage.get('summary');
   let text=`TikTok reviews: ${enabled.value==='true'?'enabled':'paused'}; daily 09:15 SGT.\n`;
   if(state){text+=`Last report: ${state.when}\n`+SHOPS.map(s=>`${s}: ${state.shops?.[s]?.status||'unknown'}${state.shops?.[s]?.login_needed?' — use /tt_login '+s:''}`).join('\n')+'\n';}
   else text+='Detailed shop status will arrive with the next run.\n';
   const selected=action==='errors'?runs.filter(r=>r.conclusion&&r.conclusion!=='success').slice(0,3):runs.slice(0,2);
   text+=selected.map(r=>`${r.name}: ${r.conclusion||r.status}\n${r.html_url}`).join('\n');
   if(action==='errors')text+='\nLogin expired: /tt_login shop. Other errors: /tt_dryrun to diagnose, or send the run link to Codex. Ambiguous replies must be checked in Seller Center before retrying. No command clears reply history.';
   return this.say(text);
  }
  const active=(await this.runs()).find(r=>!r.conclusion);
  if(active)return this.say(`A review/login job is already active. Please wait for it to finish.\n${active.html_url}`);
  if(action==='run'&&(await this.gh('/actions/variables/AUTOMATION_ENABLED')).value!=='true')return this.say('Live runs are paused. Use /tt_resume first.');
  const recent=await this.ctx.storage.get('lastDispatch')||0;
  if(Date.now()-recent<60000)return this.say('A job was just requested. Wait one minute, then use /tt_status.');
  await this.ctx.storage.put('lastDispatch',Date.now());
  const inputs={shop};let workflow=action==='run'?'daily-reviews.yml':'dry-run.yml';let link='';
  if(action==='login'){
   workflow='relogin.yml';inputs.session=crypto.randomUUID();const key=token();
   const stub=this.env.CONTROL.get(this.env.CONTROL.idFromName('session:'+inputs.session));
   await stub.fetch('https://internal/create',{method:'POST',body:JSON.stringify({shop,keyHash:await hash(key),expires:Date.now()+35*60000,status:'starting'})});
   link=`${this.env.PUBLIC_URL}/open/${inputs.session}#${key}`;
  }
  try{await this.gh(`/actions/workflows/${workflow}/dispatches`,'POST',{ref:'main',inputs});}catch(e){await this.ctx.storage.delete('lastDispatch');throw e;}
  await this.say(action==='login'?`Starting a private login browser for ${LABELS[shop]}. Allow a few minutes for startup. Open this link on phone or computer:\n${link}\nThe browser is available for 15 minutes after startup. Select the correct shop, then use “Save login” in the viewer. Do not forward this link.`:`Requested ${action==='run'?'live':'dry'} check for ${shop}. Results will arrive here. Use /tt_status for progress.`);
 }
 async alarm(){this.close();await this.ctx.storage.deleteAll();}
 close(){const sockets=[this.agent,this.viewer];this.agent=this.viewer=null;this.pending=[];for(const ws of sockets){try{ws?.close(1000,'Session ended');}catch{}}}
 async fetch(request){
  const url=new URL(request.url),path=url.pathname;
  if(path==='/command'){try{await this.env.WEBHOOK_AUDIT.put('last_do_command',JSON.stringify({at:new Date().toISOString(),received:true}))}catch{}await this.update(await request.json());return reply('ok');}
  if(path==='/webhook-state'){if(request.method==='POST'){await this.ctx.storage.put('webhookAudit',await request.json());return reply('ok');}return json(await this.ctx.storage.get('webhookAudit')||{});}
 if(path==='/bigseller/action'){
  // Dashboard and Telegram actions share one queue and dispatch cooldown.
  const {action}=await request.json();const task=this.commandQueue.then(()=>this.bigSellerAction(action));this.commandQueue=task.catch(()=>{});
  return json(await task);
 }
 if(path==='/summary'){const summary=await request.json();const key=summary.service==='bigseller'?'bigsellerSummary':'summary';const previous=await this.ctx.storage.get(key)||{};await this.ctx.storage.put(key,key==='bigsellerSummary'?{...previous,...summary}:summary);return reply('ok');}
  if(path==='/create'){const s=await request.json();await this.ctx.storage.put('session',s);await this.ctx.storage.setAlarm(s.expires);return reply('ok');}
  const s=await this.ctx.storage.get('session');if(!validSession(s))return reply('Login session expired or finished. Request a new login from the dashboard or Telegram.',410);
  if(path==='/auth'){try{await this.env.WEBHOOK_AUDIT.put('last_browser_auth',JSON.stringify({at:new Date().toISOString(),shop:s.shop}))}catch{};
   const key=(await request.json()).key;
   if(typeof key!=='string'||key.length>100||await hash(key)!==s.keyHash)return reply('Forbidden',403);
   return new Response('ok',{headers:{...headers,'Set-Cookie':`tt_session=${key}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=2100`}});
  }
  if(path==='/ready'){try{await this.env.WEBHOOK_AUDIT.put('last_browser_ready',JSON.stringify({at:new Date().toISOString(),shop:s.shop,status:'ready'}))}catch{};
   s.status='ready';s.expires=Math.min(s.expires,Date.now()+15*60000);await this.ctx.storage.put('session',s);await this.ctx.storage.setAlarm(s.expires);
   await this.say(`Private login browser ready for ${s.shop}. Open the link above. After logging into the correct shop, press Save login. Link expires in 15 minutes.`);return reply('ok');
  }
  if(path==='/finish'){
   s.status='complete';await this.ctx.storage.put('session',s);this.close();return reply('ok');
  }
  if(path==='/poll'){const save=await this.ctx.storage.get('saveRequested')||false;await this.ctx.storage.delete('saveRequested');return json({save,shop:s.shop,service:s.service||'tiktok',expires:s.expires});}
  if(path==='/agent'){try{await this.env.WEBHOOK_AUDIT.put('last_agent_connection',JSON.stringify({at:new Date().toISOString(),shop:s.shop}))}catch{};return this.socket('agent');}
  const cookie=request.headers.get('cookie')?.match(/(?:^|;\s*)tt_session=([^;]+)/)?.[1]||'';
  if(!cookie||await hash(cookie)!==s.keyHash)return reply('Open your private link from Telegram first.',403);
  if(path==='/save'){await this.ctx.storage.put('saveRequested',true);return reply('Requested verification. Wait for Telegram confirmation.');}
  if(path==='/vnc'){try{await this.env.WEBHOOK_AUDIT.put('last_viewer_connection',JSON.stringify({at:new Date().toISOString(),shop:s.shop,agentConnected:!!this.agent,viewerConnected:!!this.viewer}))}catch{};if(!this.agent)return reply('Browser is starting. Please reconnect shortly.',503);return this.socket('viewer');}
  return reply('Not found',404);
 }
 socket(kind){
  if(this[kind])return reply('A browser connection is already active. Close it first.',409);
  const pair=new WebSocketPair(),client=pair[0],server=pair[1];
  // Relay raw bytes: newer compatibility dates default binaryType to 'blob', which corrupts VNC frames.
  server.binaryType='arraybuffer';server.accept();this[kind]=server;
  server.addEventListener('message',e=>{
   const other=kind==='agent'?this.viewer:this.agent;
   if(other){try{other.send(e.data);}catch{this.close();}}
   else if(kind==='agent'&&this.pending.length<16)this.pending.push(e.data);
   else this.close();
  });
  server.addEventListener('close',()=>this.close());server.addEventListener('error',()=>this.close());
  if(kind==='viewer'){for(const data of this.pending)server.send(data);this.pending=[];}
  return new Response(null,{status:101,webSocket:client});
 }
}
export default {async scheduled(_event,env,_ctx){
  try{await env.WEBHOOK_AUDIT.put('cron_started_at',new Date().toISOString())}catch(e){console.warn('Audit start failed')}
  try{const r=await env.LEGACY_BOT.fetch(new Request('https://welivebot.internal/',{method:'GET'}));await env.WEBHOOK_AUDIT.put('legacy_probe',JSON.stringify({at:new Date().toISOString(),ok:r.ok,status:r.status}))}catch(e){try{await env.WEBHOOK_AUDIT.put('legacy_probe',JSON.stringify({at:new Date().toISOString(),ok:false,error:String(e?.message||e).slice(0,200)}))}catch{}}
  try{const r=await fetch('https://api.github.com/repos/'+env.REPO+'/actions/workflows/relogin.yml/runs?per_page=5',{headers:{Authorization:'Bearer '+env.GH_TOKEN,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'tiktok-telegram-control'},signal:AbortSignal.timeout(12000)});let summary={at:new Date().toISOString(),httpStatus:r.status};if(r.ok){const j=await r.json();summary.runs=(j.workflow_runs||[]).map(x=>({status:x.status,conclusion:x.conclusion,created_at:x.created_at,updated_at:x.updated_at,run_started_at:x.run_started_at,html_url:x.html_url})).slice(0,5)}await env.WEBHOOK_AUDIT.put('relogin_workflow',JSON.stringify(summary))}catch(e){try{await env.WEBHOOK_AUDIT.put('relogin_workflow',JSON.stringify({at:new Date().toISOString(),error:String(e?.message||e).slice(0,200)}))}catch{}}
  try{
    const headers={Authorization:'Bearer '+env.GH_TOKEN,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'tiktok-telegram-control'};
    const base='https://api.github.com/repos/'+env.REPO;
    const endpoints=[base+'/actions/workflows/daily-reviews.yml',base+'/actions/workflows/daily-reviews.yml/runs?per_page=16',base+'/actions/variables/AUTOMATION_ENABLED',base+'/contents/.github/workflows/daily-reviews.yml?ref=main'];
    const answers=await Promise.allSettled(endpoints.map(url=>fetch(url,{headers,signal:AbortSignal.timeout(12000)})));
    const audit={checkedAt:new Date().toISOString()};
    for(let i=0;i<answers.length;i++){
      const item=answers[i];if(item.status==='rejected'){audit[['workflow','runs','enabled','file'][i]]={error:String(item.reason).slice(0,150)};continue}
      const response=item.value; const data=await response.json().catch(()=>({}));
      if(i===0)audit.workflow={httpStatus:response.status,state:data.state,name:data.name,path:data.path};
      if(i===1)audit.runs={httpStatus:response.status,total_count:data.total_count,recent:(data.workflow_runs||[]).slice(0,16).map(run=>({status:run.status,conclusion:run.conclusion,event:run.event,created_at:run.created_at,updated_at:run.updated_at,run_started_at:run.run_started_at,run_number:run.run_number,html_url:run.html_url}))};
      if(i===2)audit.enabled={httpStatus:response.status,value:data.value||null};
      if(i===3){let yaml='';try{yaml=atob((data.content||'').replace(/\n/g,''))}catch{}audit.file={httpStatus:response.status,sha:data.sha,scheduleLines:yaml.split('\n').filter(line=>/cron:|schedule:|workflow_dispatch:|env:|AUTOMATION_ENABLED|SHOP/.test(line)).slice(0,35)};}
    }
    await env.WEBHOOK_AUDIT.put('daily_workflow_audit',JSON.stringify(audit));
  }catch(e){try{await env.WEBHOOK_AUDIT.put('daily_workflow_audit',JSON.stringify({checkedAt:new Date().toISOString(),error:String(e?.message||e).slice(0,160)}))}catch{}}

  const control=env.CONTROL.get(env.CONTROL.idFromName('control'));
  const expected=env.PUBLIC_URL.replace(/\/$/,'')+'/telegram';
  let previous={};
  try {const cached=await control.fetch('https://internal/webhook-state');if(cached.ok)previous=await cached.json()}catch{}
  const infoRes=await fetch('https://api.telegram.org/bot'+env.BOT_TOKEN+'/getWebhookInfo',{signal:AbortSignal.timeout(15000)});
  if(!infoRes.ok)throw Error('Telegram getWebhookInfo HTTP '+infoRes.status);
  const info=await infoRes.json();
  if(!info.ok)throw Error('Telegram getWebhookInfo rejected request');
  try{await env.WEBHOOK_AUDIT.put('telegram_info',JSON.stringify({checkedAt:new Date().toISOString(),webhookHost:(()=>{try{return new URL(info.result?.url).host}catch{return ''}})(),webhookPath:(()=>{try{return new URL(info.result?.url).pathname}catch{return ''}})(),pending:info.result?.pending_update_count??0,lastErrorDate:info.result?.last_error_date||null,lastErrorMessage:String(info.result?.last_error_message||'').slice(0,250)}))}catch(e){console.warn('Audit info failed')}
  const repair=!previous.configured||info.result?.url!==expected;
  if(repair){
    const response=await fetch('https://api.telegram.org/bot'+env.BOT_TOKEN+'/setWebhook',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:expected,secret_token:env.WEBHOOK_SECRET,drop_pending_updates:false,allowed_updates:[]}),signal:AbortSignal.timeout(15000)});
    const responseData=await response.json();
    if(!response.ok||!responseData.ok)throw Error('Telegram setWebhook failed HTTP '+response.status);
    const notice='✅ TikTok command webhook reconnected to Cloudflare. Try /tt_help or /tt_login. Existing bot commands are forwarded to your original handler.';
    try {await fetch('https://api.telegram.org/bot'+env.BOT_TOKEN+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:env.OWNER_ID,text:notice}),signal:AbortSignal.timeout(15000)});}catch(e){console.warn('Webhook repair notice send failed')}
  }
  await control.fetch('https://internal/webhook-state',{method:'POST',body:JSON.stringify({configured:true,checkedAt:new Date().toISOString(),repaired:repair,pendingUpdateCount:info.result?.pending_update_count||0})});
  console.log('Telegram webhook audit:',repair?'webhook repaired':'webhook verified');
},async fetch(request,env,ctx){
 const url=new URL(request.url),p=url.pathname;
 const control=()=>env.CONTROL.get(env.CONTROL.idFromName('control'));
 if(p==='/health'){const r=await control().fetch('https://internal/webhook-state');const s=await r.json();return json({ok:true,telegram:{connected:s.configured===true,lastChecked:s.checkedAt||null,pendingUpdates:s.pendingUpdateCount??null}});}
 if(p==='/health/legacy'&&request.method==='GET'){try{const r=await env.LEGACY_BOT.fetch(new Request('https://welivebot.internal/',{method:'GET'}));return json({oldBotWorkerReachable:r.ok,status:r.status})}catch(e){return json({oldBotWorkerReachable:false,reason:'Binding fetch failed'})}}
 if(p==='/telegram'&&request.method==='POST'){
  try{await env.WEBHOOK_AUDIT.put('last_inbound_request',JSON.stringify({at:new Date().toISOString(),secretOk:request.headers.get('X-Telegram-Bot-Api-Secret-Token')===env.WEBHOOK_SECRET}))}catch(e){console.warn('Audit inbound failed')}
  if(request.headers.get('X-Telegram-Bot-Api-Secret-Token')!==env.WEBHOOK_SECRET)return reply('Forbidden',403);
  const body=await request.text();if(body.length>100000)return reply('Too large',413);
  let u;try{u=JSON.parse(body);}catch{return reply('Bad JSON',400);}
  try{await env.WEBHOOK_AUDIT.put('last_parsed_message',JSON.stringify({at:new Date().toISOString(),isCommand:isTikTokUpdate(u),command:parseCommand(u.message?.text)?.action||(parseCallback(u.callback_query?.data)?'button':null),ownerOk:authorised(u.message,env.OWNER_ID)||authorisedCallback(u.callback_query,env.OWNER_ID)}))}catch(e){console.warn('Audit parsed failed')}
  if(isControlUpdate(u)){
    if(!(u.callback_query?authorisedCallback(u.callback_query,env.OWNER_ID):authorised(u.message,env.OWNER_ID)))return reply('ok');
    ctx.waitUntil((async()=>{
      try{
        const result=await control().fetch('https://internal/command',{method:'POST',body});
        try{await env.WEBHOOK_AUDIT.put('last_command_dispatch',JSON.stringify({at:new Date().toISOString(),status:result.status,ok:result.ok}))}catch{}
        if(!result.ok)throw Error('Control service HTTP '+result.status);
      }catch(e){
        try{await env.WEBHOOK_AUDIT.put('last_command_dispatch',JSON.stringify({at:new Date().toISOString(),ok:false,error:String(e?.message||e).slice(0,160)}))}catch{}
        try{await fetch('https://api.telegram.org/bot'+env.BOT_TOKEN+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:env.OWNER_ID,text:'Automation command could not be handled. Try /tt_help or /bs_help, or retry shortly.'}),signal:AbortSignal.timeout(8000)})}catch{}
      }
    })());
    return reply('ok');
  }
  ctx.waitUntil((async()=>{
    let status=0,error='';
    try{
      const forwarded=await env.LEGACY_BOT.fetch(new Request('https://welivebot.internal/',{method:'POST',headers:{'Content-Type':'application/json','X-Telegram-Bot-Api-Secret-Token':env.LEGACY_WEBHOOK_SECRET||env.WEBHOOK_SECRET},body}));
      status=forwarded.status;
      if(!forwarded.ok)error='Original Worker returned HTTP '+forwarded.status;
    }catch(e){error=String(e?.message||e).slice(0,160)}
    try{await env.WEBHOOK_AUDIT.put('last_forward_result',JSON.stringify({at:new Date().toISOString(),method:'service-binding',status,ok:status>=200&&status<300,error}))}catch{}
  })());
  return reply('ok');
 }
 if(p==='/api/bigseller/action'&&request.method==='POST'){
  if(!env.BIGSELLER_DASHBOARD_TOKEN||request.headers.get('Authorization')!==`Bearer ${env.BIGSELLER_DASHBOARD_TOKEN}`)return reply('Forbidden',403);
  const body=await request.text();if(body.length>1000)return reply('Too large',413);
  try{const data=JSON.parse(body);if(typeof data.action!=='string')return reply('Bad request',400);}catch{return reply('Bad JSON',400);}
  try{return await control().fetch('https://internal/bigseller/action',{method:'POST',body});}catch{return new Response(JSON.stringify({ok:false,message:'BigSeller control could not complete. Check status before retrying.'}),{status:502,headers:{...headers,'Content-Type':'application/json'}});}
 }
 if(p==='/api/summary'&&request.method==='POST'){
  if(request.headers.get('Authorization')!==`Bearer ${env.CONTROL_TOKEN}`)return reply('Forbidden',403);
  return control().fetch('https://internal/summary',{method:'POST',body:await request.text()});
 }
 const m=p.match(/^\/(open|viewer|auth|vnc|save|api\/session)\/([a-f0-9-]{36})(?:\/(ready|finish|agent|poll))?$/);
 if(!m)return reply('Not found',404);
 const [,route,id,operation]=m;
 const stub=env.CONTROL.get(env.CONTROL.idFromName('session:'+id));
 if(route==='open'||route==='viewer')return new Response(route==='open'?openHtml:viewerHtml,{headers:{...headers,'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' wss://tiktok-review-control.welivebot.workers.dev; img-src 'self' data:; frame-ancestors 'none'"}});
 if(route==='api/session'){
  if(request.headers.get('Authorization')!==`Bearer ${env.CONTROL_TOKEN}`)return reply('Forbidden',403);
  if(operation==='poll'){
   // Poll implemented inside the session object; no credentials or pixels stored.
   return stub.fetch('https://internal/poll');
  }
  if(!['ready','finish','agent'].includes(operation))return reply('Not found',404);
  return stub.fetch(new Request('https://internal/'+operation,request));
 }
 if(request.headers.get('Origin')!==url.origin)return reply('Forbidden',403);
 if(route==='vnc'&&request.headers.get('Upgrade')?.toLowerCase()!=='websocket')return reply('WebSocket required',400);
 if(route!=='vnc'&&request.method!=='POST')return reply('Method not allowed',405);
 return stub.fetch(new Request('https://internal/'+route,request));
}};
