import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseCommand, authorised, validSession, Control} from './worker.mjs';
test('commands are namespaced and arguments allowlisted',()=>{
 assert.equal(parseCommand('/status'),null);
 assert.deepEqual(parseCommand('/tt_login snackersbox'),{action:'login',shop:'snackersbox'});
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
