import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseCommand, parseCallback, authorised, authorisedCallback, validSession, Control} from './worker.mjs';
test('commands are namespaced and arguments allowlisted',()=>{
 assert.equal(parseCommand('/status'),null);
 assert.deepEqual(parseCommand('/tt_login snackersbox'),{action:'login',shop:'snackersbox'});
 assert.deepEqual(parseCommand('/tt_login konomi_living'),{action:'login',shop:'konomi_living'});
 assert.equal(parseCommand('/tt_login all').error,true);
 assert.equal(parseCommand('/tt_run evil/repo').error,true);
 assert.equal(parseCommand('/tt_run all extra').error,true);
});
test('private owner chat only',()=>{
 const m={from:{id:42},chat:{id:42,type:'private'}};
 assert(authorised(m,'42'));assert(!authorised({...m,chat:{id:42,type:'group'}},'42'));
 assert(!authorised({...m,from:{id:43}},'42'));
});
test('expired and finished sessions reject access',()=>{
 assert(validSession({expires:200,status:'ready'},100));
 assert(!validSession({expires:99,status:'ready'},100));
 assert(!validSession({expires:200,status:'complete'},100));
});
test('duplicate Telegram update cannot dispatch twice',async()=>{
 const data=new Map();const storage={get:async k=>data.get(k),put:async(k,v)=>data.set(k,v)};
 const c=new Control({storage},{OWNER_ID:'42'});let count=0;
 c.command=async()=>{count++}; c.say=async()=>{};
 const update={update_id:123,message:{from:{id:42},chat:{id:42,type:'private'},text:'/tt_run'}};
 await c.update(update);await c.update(update);assert.equal(count,1);
});

test('concurrent duplicate deliveries dispatch once',async()=>{
 const data=new Map();const storage={get:async k=>data.get(k),put:async(k,v)=>data.set(k,v)};
 const c=new Control({storage},{OWNER_ID:'42'});let count=0;c.command=async()=>{count++};c.say=async()=>{};
 const u={update_id:456,message:{from:{id:42},chat:{id:42,type:'private'},text:'/tt_run'}};
 await Promise.all([c.update(u),c.update(u)]);assert.equal(count,1);
});

test('unknown users cannot reach GitHub or Telegram',async()=>{
 const c=new Control({storage:{}},{OWNER_ID:'42'});
 c.command=()=>assert.fail('unauthorised command');c.say=()=>assert.fail('unauthorised message');
 await c.update({update_id:1,message:{from:{id:99},chat:{id:99,type:'private'},text:'/tt_run'}});
});

import worker from './worker.mjs';
test('webhook rejects missing secret before any forwarding',async()=>{
 const r=await worker.fetch(new Request('https://test/telegram',{method:'POST',body:'{}'}),{WEBHOOK_SECRET:'secret'});
 assert.equal(r.status,403);
});
test('cross-origin session authentication is rejected',async()=>{
 const r=await worker.fetch(new Request('https://test/auth/12345678-1234-1234-1234-123456789abc',{method:'POST',headers:{Origin:'https://evil.example'},body:'{}'}),{CONTROL:{idFromName:x=>x,get:()=>({fetch:()=>assert.fail('cross-origin reached session')})}});
 assert.equal(r.status,403);
});

test('session token must match and becomes an HttpOnly cookie',async()=>{
 const key='private-test-key';const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(key)))).map(x=>x.toString(16).padStart(2,'0')).join('');
 const c=new Control({storage:{get:async()=>({expires:Date.now()+10000,status:'ready',keyHash:digest})}},{});
 let r=await c.fetch(new Request('https://internal/auth',{method:'POST',body:JSON.stringify({key:'wrong'})}));assert.equal(r.status,403);
 r=await c.fetch(new Request('https://internal/auth',{method:'POST',body:JSON.stringify({key})}));
 assert.equal(r.status,200);assert.match(r.headers.get('Set-Cookie'),/HttpOnly; Secure; SameSite=Strict/);
 r=await c.fetch(new Request('https://internal/vnc'));assert.equal(r.status,403);
});

test('bare /tt_login chooses a shop, and parseCallback accepts only known login buttons',()=>{
 assert.deepEqual(parseCommand('/tt_login'),{action:'login',shop:''});
 assert.deepEqual(parseCallback('tt_login:snackersbox'),{action:'login',shop:'snackersbox'});
 assert.equal(parseCallback('tt_login:evil'),null);
 assert.equal(parseCallback('tt_run:all'),null);
 assert.equal(parseCallback(undefined),null);
});
test('bare /tt_login replies with one button per shop and never dispatches',async()=>{
 const c=new Control({storage:{get:async()=>undefined,put:async()=>{}}},{OWNER_ID:'42'});
 let sent;c.say=async(text,extra)=>{sent={text,extra}};c.gh=()=>assert.fail('gh called for shop picker');
 await c.command(parseCommand('/tt_login'),1);
 const rows=sent.extra.reply_markup.inline_keyboard;
 assert.equal(rows.length,3);
 assert.deepEqual(rows.map(r=>r[0].callback_data),['tt_login:welive_main','tt_login:snackersbox','tt_login:konomi_living']);
});
test('owner callback query dispatches login once even if delivered twice',async()=>{
 const data=new Map();const storage={get:async k=>data.get(k),put:async(k,v)=>data.set(k,v)};
 const c=new Control({storage},{OWNER_ID:'42'});const calls=[];
 c.command=async(cmd)=>{calls.push(cmd)};c.say=async()=>{};c.tg=async()=>{};
 const u={update_id:789,callback_query:{id:'cb1',from:{id:42},message:{message_id:5,chat:{id:42,type:'private'}},data:'tt_login:snackersbox'}};
 await Promise.all([c.update(u),c.update(u)]);
 assert.deepEqual(calls,[{action:'login',shop:'snackersbox'}]);
});
test('callback query from another user is ignored',async()=>{
 const c=new Control({storage:{get:async()=>undefined,put:async()=>{}}},{OWNER_ID:'42'});
 c.command=()=>assert.fail('unauthorised callback command');c.say=()=>assert.fail('unauthorised callback message');c.tg=()=>assert.fail('unauthorised callback telegram call');
 assert(!authorisedCallback({from:{id:99},message:{chat:{id:99,type:'private'}}},'42'));
 await c.update({update_id:2,callback_query:{id:'cb2',from:{id:99},message:{message_id:6,chat:{id:99,type:'private'}},data:'tt_login:welive_main'}});
});
// Coexistence with the stock bot: only /tt_* commands and tt_login:* buttons stay here.
const routeTelegram=async(update,owner='42')=>{
 const seen={legacy:null,control:null};const waits=[];
 const env={WEBHOOK_SECRET:'secret',OWNER_ID:owner,WEBHOOK_AUDIT:{put:async()=>{}},
  LEGACY_BOT:{fetch:async req=>{seen.legacy=await req.text();return new Response('ok')}},
  CONTROL:{idFromName:x=>x,get:()=>({fetch:async(_u,init)=>{seen.control=init.body;return new Response('ok')}})}};
 const body=JSON.stringify(update);
 const r=await worker.fetch(new Request('https://test/telegram',{method:'POST',body,headers:{'X-Telegram-Bot-Api-Secret-Token':'secret'}}),env,{waitUntil:p=>waits.push(p)});
 await Promise.all(waits);assert.equal(r.status,200);return {...seen,body};
};
const owner={from:{id:42},chat:{id:42,type:'private'}};
test('stock bot commands and messages forward untouched to the original bot',async()=>{
 for(const text of ['/stock','/start','/tt','hello','/help']){
  const s=await routeTelegram({update_id:1,message:{...owner,text}});
  assert.equal(s.legacy,s.body);assert.equal(s.control,null);
 }
});
test('stock bot buttons forward untouched',async()=>{
 const s=await routeTelegram({update_id:2,callback_query:{id:'c',from:{id:42},message:{message_id:7,chat:{id:42,type:'private'}},data:'stock:1'}});
 assert.equal(s.legacy,s.body);assert.equal(s.control,null);
});
test('TikTok commands and login buttons stay with the TikTok control',async()=>{
 for(const update of [{update_id:3,message:{...owner,text:'/tt_login'}},{update_id:4,callback_query:{id:'c',from:{id:42},message:{message_id:7,chat:{id:42,type:'private'}},data:'tt_login:konomi_living'}}]){
  const s=await routeTelegram(update);assert.equal(s.control,s.body);assert.equal(s.legacy,null);
 }
});
test('TikTok commands from other users reach neither bot',async()=>{
 const s=await routeTelegram({update_id:5,message:{from:{id:9},chat:{id:9,type:'private'},text:'/tt_run'}});
 assert.equal(s.control,null);assert.equal(s.legacy,null);
});
test('relay sockets pass VNC bytes as ArrayBuffers, not Blobs',()=>{
 const original=globalThis.WebSocketPair;
 globalThis.WebSocketPair=function(){const mk=()=>({accept(){if(this.binaryType!=='arraybuffer')throw Error('accepted before binaryType set')},addEventListener(){},send(){}});return [mk(),mk()];};
 try{const c=new Control({storage:{}},{});try{c.socket('agent')}catch(e){if(!(e instanceof RangeError))throw e;}assert.equal(c.agent.binaryType,'arraybuffer');}
 finally{globalThis.WebSocketPair=original;}
});
