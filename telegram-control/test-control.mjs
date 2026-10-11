import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseCommand, authorised, validSession, Control} from './worker.mjs';
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
test('stock commands forward untouched to the original receiver',async()=>{
 const original=globalThis.fetch;let forwarded;
 globalThis.fetch=async(url,init)=>{forwarded={url,init};return new Response('ok')};
 const body=JSON.stringify({message:{text:'/stock'}});
 try{
 const r=await worker.fetch(new Request('https://test/telegram',{method:'POST',body,headers:{'X-Telegram-Bot-Api-Secret-Token':'secret'}}),{WEBHOOK_SECRET:'secret',ORIGINAL_WEBHOOK:'https://old.example'});
 assert.equal(r.status,200);assert.equal(forwarded.url,'https://old.example');assert.equal(forwarded.init.body,body);
 }finally{globalThis.fetch=original}
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
