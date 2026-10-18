import {test} from 'node:test';
import assert from 'node:assert/strict';
import worker, {Control, parseBigSellerCommand, isControlUpdate} from './worker.mjs';

test('BigSeller commands remain owner controls with no arbitrary arguments',()=>{
 assert.deepEqual(parseBigSellerCommand('/bs_login@Welivestockcount_bot'),{action:'login',service:'bigseller'});
 assert.equal(parseBigSellerCommand('/stock'),null);
 assert.equal(parseBigSellerCommand('/bs_run another-account').error,true);
 assert(isControlUpdate({message:{text:'/bs_login'}}));
});

const fixture=()=>{
 const data=new Map(),calls=[];
 const c=new Control({storage:{get:async k=>data.get(k),put:async(k,v)=>data.set(k,v),delete:async k=>data.delete(k)}},{PUBLIC_URL:'https://control.example',CONTROL:{idFromName:x=>x,get:()=>({fetch:async(_url,opts)=>{calls.push(JSON.parse(opts.body));return new Response('ok');}})}});
 c.gh=async(path,method,body)=>{if(path.startsWith('/actions/runs'))return {workflow_runs:[]};if(path.includes('/dispatches')){calls.push({path,body});return null;}if(path.includes('/variables/'))return {value:'true'};throw Error('Unexpected GitHub call');};
 return {c,data,calls};
};

test('BigSeller login creates an isolated session and dispatches the existing repo',async()=>{
 const {c,calls}=fixture();
 const result=await c.bigSellerAction('login');
 assert(result.ok);assert.match(result.loginUrl,/\/open\/.*\?service=bigseller#/);
 assert.equal(calls[0].shop,'bigseller');assert.equal(calls[0].service,'bigseller');
 assert(calls[0].keyHash);assert(!calls[0].key);
 assert.equal(calls[1].path,'/actions/workflows/bigseller-login.yml/dispatches');
 assert.equal((await c.bigSellerAction('run')).ok,false);
});

test('run and dryrun dispatch explicit stock-write modes',async()=>{
 for(const [action,expected] of [['run','false'],['dryrun','true']]){
  const {c,calls}=fixture();await c.bigSellerAction(action);
  assert.equal(calls[0].path,'/actions/workflows/bigseller-sync.yml/dispatches');
  assert.equal(calls[0].body.inputs.dry_run,expected);
 }
});

test('paused live sync cannot dispatch; dryrun can',async()=>{
 const {c,calls}=fixture();c.bigSellerEnabled=async()=>false;
 assert.equal((await c.bigSellerAction('run')).ok,false);assert.equal(calls.length,0);
 assert.equal((await c.bigSellerAction('dryrun')).ok,true);
});

test('dashboard cannot dispatch without the control bearer secret',async()=>{
 for(const token of ['', 'wrong']){
  const r=await worker.fetch(new Request('https://test/api/bigseller/action',{method:'POST',headers:{Authorization:'Bearer '+token},body:'{"action":"login"}'}),{BIGSELLER_DASHBOARD_TOKEN:'private'});
  assert.equal(r.status,403);
 }
});

test('dashboard token permits BigSeller controls and cannot authenticate a browser agent',async()=>{
 let forwarded;
 const env={BIGSELLER_DASHBOARD_TOKEN:'dashboard-only',CONTROL_TOKEN:'runner-only',CONTROL:{idFromName:x=>x,get:()=>({fetch:async(url,options)=>{forwarded={url,body:options?.body};return Response.json({ok:true,message:'Started'});}})}};
 const action=await worker.fetch(new Request('https://test/api/bigseller/action',{method:'POST',headers:{Authorization:'Bearer dashboard-only'},body:'{"action":"login"}'}),env);
 assert.equal(action.status,200);assert.equal(forwarded.url,'https://internal/bigseller/action');
 const agent=await worker.fetch(new Request('https://test/api/session/12345678-1234-1234-1234-123456789abc/poll',{headers:{Authorization:'Bearer dashboard-only'}}),env);
 assert.equal(agent.status,403);
});

test('ordinary bot forwarding preserves downstream authentication',async()=>{
 let forwarded;const waits=[];
 const env={WEBHOOK_SECRET:'telegram',LEGACY_WEBHOOK_SECRET:'downstream',WEBHOOK_AUDIT:{put:async()=>{}},LEGACY_BOT:{fetch:async request=>{forwarded=request;return new Response('ok');}}};
 const response=await worker.fetch(new Request('https://test/telegram',{method:'POST',headers:{'X-Telegram-Bot-Api-Secret-Token':'telegram'},body:'{"message":{"text":"/stock"}}'}),env,{waitUntil:p=>waits.push(p)});
 await Promise.all(waits);assert.equal(response.status,200);
 assert.equal(forwarded.headers.get('X-Telegram-Bot-Api-Secret-Token'),'downstream');
});

test('BigSeller summaries preserve last successful import across failed checks',async()=>{
 const {c,data}=fixture();
 for(const summary of [{service:'bigseller',status:'stock updated',rows:4,lastImportAt:'2026-10-15T12:00:00+08:00'},{service:'bigseller',status:'login required',login_needed:true}])await c.fetch(new Request('https://internal/summary',{method:'POST',body:JSON.stringify(summary)}));
 assert.equal(data.get('bigsellerSummary').lastImportAt,'2026-10-15T12:00:00+08:00');
 assert.equal(data.get('bigsellerSummary').status,'login required');
 assert.equal(data.has('summary'),false);
});

test('BigSeller login reminder button is owner-only and duplicate deliveries dispatch once',async()=>{
 const {c,calls}=fixture();c.env.OWNER_ID='42';c.tg=async()=>new Response('ok');c.say=async()=>{};
 const update={update_id:900,callback_query:{id:'login',data:'bs_login',from:{id:42},message:{message_id:5,chat:{id:42,type:'private'}}}};
 assert(isControlUpdate(update));
 await Promise.all([c.update(update),c.update(update)]);
 assert.equal(calls.filter(x=>x.path?.endsWith('/dispatches')).length,1);
 assert.equal(calls[0].service,'bigseller');
 const {c:other,calls:otherCalls}=fixture();other.env.OWNER_ID='42';
 await other.update({...update,callback_query:{...update.callback_query,from:{id:9}}});
 assert.equal(otherCalls.length,0);
});

test('unrelated export failures preserve an unresolved BigSeller login requirement',async()=>{
 const {c,data}=fixture();
 for(const summary of [{service:'bigseller',status:'login required',login_needed:true},{service:'bigseller',status:'export failed'}])await c.fetch(new Request('https://internal/summary',{method:'POST',body:JSON.stringify(summary)}));
 assert.equal(data.get('bigsellerSummary').login_needed,true);
});

test('BigSeller status keeps offering login after an unrelated failure',async()=>{
 const {c,data}=fixture();data.set('bigsellerSummary',{status:'export failed',login_needed:true});
 const result=await c.bigSellerAction('status');
 assert.match(result.message,/Login required.*\/bs_login/);
});
