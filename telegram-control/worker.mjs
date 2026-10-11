import {viewerHtml, openHtml} from './pages.mjs';
const SHOPS=['welive_main','snackersbox','konomi_living'];
const WORKFLOWS=['daily-reviews.yml','dry-run.yml','relogin.yml'];
const headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY'};
const reply=(text,status=200)=>new Response(text,{status,headers});
const json=value=>new Response(JSON.stringify(value),{headers:{...headers,'Content-Type':'application/json'}});
const hash=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))).map(x=>x.toString(16).padStart(2,'0')).join('');
const token=()=>crypto.randomUUID()+crypto.randomUUID();
export function authorised(message,owner){return message?.chat?.type==='private' && String(message.from?.id)===owner && String(message.chat?.id)===owner;}
export function parseCommand(text=''){
 const parts=text.trim().toLowerCase().split(/\s+/);const name=parts[0].split('@')[0];
 if(!name.startsWith('/tt_'))return null;
 const action=name.slice(4),shop=parts[1]||'all';
 if(!['help','status','errors','run','dryrun','pause','resume','login','loginstatus'].includes(action)||parts.length>2||!['all',...SHOPS].includes(shop)||(action==='login'&&shop==='all'))return {error:true};
 return {action,shop};
}
export const validSession=(s,now=Date.now())=>!!s && s.expires>now && ['starting','ready'].includes(s.status);
const HELP='TikTok reviews\n/tt_status — latest run and login status\n/tt_run [shop|all] — live review check\n/tt_dryrun [shop|all] — no posting\n/tt_pause — pause future live runs\n/tt_resume — enable daily runs\n/tt_login welive_main\n/tt_login snackersbox\n/tt_login konomi_living\n/tt_errors — recent failures and recovery\nShop names: welive_main, snackersbox, konomi_living.\nRelogin opens a private browser link on phone or computer. Never send passwords or OTPs in chat.';
export class Control{
 constructor(ctx,env){this.ctx=ctx;this.env=env;this.agent=null;this.viewer=null;this.pending=[];this.busy=false;this.commandQueue=Promise.resolve();}
 async say(text){const r=await fetch(`https://api.telegram.org/bot${this.env.BOT_TOKEN}/sendMessage`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:this.env.OWNER_ID,text,link_preview_options:{is_disabled:true}}),signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error('Telegram delivery failed');}
 async gh(path,method='GET',body){const r=await fetch(`https://api.github.com/repos/${this.env.REPO}${path}`,{method,headers:{Authorization:`Bearer ${this.env.GH_TOKEN}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'tiktok-telegram-control','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error(`GitHub ${r.status}`);return r.status===204?null:r.json();}
 async update(u){const task=this.commandQueue.then(()=>this.handleUpdate(u));this.commandQueue=task.catch(()=>{});return task;}
 async handleUpdate(u){
  if(!authorised(u.message,this.env.OWNER_ID)||!Number.isSafeInteger(u.update_id))return;
  // Storage write before dispatch prevents retries from posting twice.
  let seen=await this.ctx.storage.get('seen')||[];if(seen.includes(u.update_id))return;
  seen.push(u.update_id);await this.ctx.storage.put('seen',seen.slice(-256));
  if(this.busy){await this.say('Another command is being handled. Please try again shortly.');return;}
  this.busy=true;
  try{await this.command(parseCommand(u.message.text),u.update_id);}catch(e){await this.say(`TikTok command could not complete (${String(e.message).slice(0,80)}). Use /tt_status before retrying; a request may already have started.`);}finally{this.busy=false;}
 }
 async runs(){return (await this.gh('/actions/runs?per_page=30')).workflow_runs.filter(r=>WORKFLOWS.some(w=>r.path===`.github/workflows/${w}`));}
 async command(cmd,updateId){
  if(!cmd||cmd.error)return this.say(HELP);
  const {action,shop}=cmd;
  if(action==='help')return this.say(HELP);
  if(['pause','resume'].includes(action)){
   if(shop!=='all')return this.say('Pause/resume currently applies to all review shops. Use /tt_pause or /tt_resume.');
   await this.gh('/actions/variables/AUTOMATION_ENABLED','PATCH',{name:'AUTOMATION_ENABLED',value:action==='resume'?'true':'false'});
   return this.say(action==='pause'?'Future live review runs paused. An already-running shop is allowed to finish and save its history.':'Daily reviews enabled for 09:15 Singapore time. Use /tt_run for a check now.');
  }
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
  await this.gh(`/actions/workflows/${workflow}/dispatches`,'POST',{ref:'main',inputs});
  await this.say(action==='login'?`Login requested for ${shop}. Tap your private link below; it will show a waiting screen until the browser is ready:\n${link}\nSign into TikTok in the browser screen and tap Verify & save login. Do not forward this link or share passwords in chat.`:`Requested ${action==='run'?'live':'dry'} check for ${shop}. Results will arrive here. Use /tt_status for progress.`);
 }
 async alarm(){this.close();await this.ctx.storage.deleteAll();}
 close(){const sockets=[this.agent,this.viewer];this.agent=this.viewer=null;this.pending=[];for(const ws of sockets){try{ws?.close(1000,'Session ended');}catch{}}}
 async fetch(request){
  const url=new URL(request.url),path=url.pathname;
  if(path==='/command'){await this.update(await request.json());return reply('ok');}
  if(path==='/summary'){await this.ctx.storage.put('summary',await request.json());return reply('ok');}
  if(path==='/create'){const s=await request.json();await this.ctx.storage.put('session',s);await this.ctx.storage.setAlarm(s.expires);return reply('ok');}
  const s=await this.ctx.storage.get('session');if(!validSession(s))return reply('Login session expired or finished. Request /tt_login again.',410);
  if(path==='/auth'){
   const key=(await request.json()).key;
   if(typeof key!=='string'||key.length>100||await hash(key)!==s.keyHash)return reply('Forbidden',403);
   return new Response('ok',{headers:{...headers,'Set-Cookie':`tt_session=${key}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=2100`}});
  }
  if(path==='/ready'){
   s.status='ready';s.expires=Math.min(s.expires,Date.now()+15*60000);await this.ctx.storage.put('session',s);await this.ctx.storage.setAlarm(s.expires);
   await this.say(`Login browser ready for ${s.shop}. Open your private link above and finish TikTok verification. Tap Verify & save login when Seller Center Product Ratings appears. You have 15 minutes.`);return reply('ok');
  }
  if(path==='/finish'){
   s.status='complete';await this.ctx.storage.put('session',s);this.close();return reply('ok');
  }
  if(path==='/poll'){const save=await this.ctx.storage.get('saveRequested')||false;await this.ctx.storage.delete('saveRequested');return json({save,shop:s.shop,expires:s.expires});}
  if(path==='/agent')return this.socket('agent');
  const cookie=request.headers.get('cookie')?.match(/(?:^|;\s*)tt_session=([^;]+)/)?.[1]||'';
  if(!cookie||await hash(cookie)!==s.keyHash)return reply('Open your private link from Telegram first.',403);
  if(path==='/save'){await this.ctx.storage.put('saveRequested',true);return reply('Requested verification. Wait for Telegram confirmation.');}
  if(path==='/vnc'){if(!this.agent)return reply('Browser is starting. Please reconnect shortly.',503);return this.socket('viewer');}
  return reply('Not found',404);
 }
 socket(kind){
  if(this[kind])return reply('A browser connection is already active. Close it first.',409);
  const pair=new WebSocketPair(),client=pair[0],server=pair[1];server.accept();this[kind]=server;
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
export default {async fetch(request,env){
 const url=new URL(request.url),p=url.pathname;
 const control=()=>env.CONTROL.get(env.CONTROL.idFromName('control'));
 if(p==='/health')return json({ok:true});
 if(p==='/telegram'&&request.method==='POST'){
  if(request.headers.get('X-Telegram-Bot-Api-Secret-Token')!==env.WEBHOOK_SECRET)return reply('Forbidden',403);
  const body=await request.text();if(body.length>100000)return reply('Too large',413);
  let u;try{u=JSON.parse(body);}catch{return reply('Bad JSON',400);}
  if(parseCommand(u.message?.text)){if(!authorised(u.message,env.OWNER_ID))return reply('ok');return control().fetch('https://internal/command',{method:'POST',body});}
  return fetch(env.ORIGINAL_WEBHOOK,{method:'POST',headers:{'Content-Type':'application/json'},body,redirect:'follow'});
 }
 if(p==='/api/summary'&&request.method==='POST'){
  if(request.headers.get('Authorization')!==`Bearer ${env.CONTROL_TOKEN}`)return reply('Forbidden',403);
  return control().fetch('https://internal/summary',{method:'POST',body:await request.text()});
 }
 const m=p.match(/^\/(open|viewer|auth|vnc|save|api\/session)\/([a-f0-9-]{36})(?:\/(ready|finish|agent|poll))?$/);
 if(!m)return reply('Not found',404);
 const [,route,id,operation]=m;
 const stub=env.CONTROL.get(env.CONTROL.idFromName('session:'+id));
 if(route==='open'||route==='viewer')return new Response(route==='open'?openHtml:viewerHtml,{headers:{...headers,'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"}});
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
