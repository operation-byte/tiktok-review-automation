import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

// Exercise the viewer event handlers without opening a private remote session.
function viewer(service='tiktok'){
 const nodes=new Map();let document;
 const element=id=>{
  const attrs={},events={};
  return {id,attrs,events,dataset:{},value:'',style:{setProperty(){}},classList:{toggle(){}},
   setAttribute(k,v){attrs[k]=v},getAttribute(k){return attrs[k]},
   addEventListener(k,fn,options){events[k]={fn,options}},
   querySelector(){return {width:1440,height:900}},setSelectionRange(){},
   focus(){document.activeElement=this},blur(){if(document.activeElement===this){document.activeElement=null;events.blur?.fn()}},
   fire(k,event={}){events[k]?.fn(event)},
  };
 };
 document={activeElement:null,getElementById(id){if(!nodes.has(id))nodes.set(id,element(id));return nodes.get(id)},querySelector(){return element('heading')},documentElement:element('root')};
 let session;
 class RFB{
  constructor(){session=this;this.events={};this.keys=[];}
  addEventListener(k,fn){this.events[k]=fn}
  sendKey(sym,_code,down){if(down)this.keys.push(sym)}
  disconnect(){}
 }
 const context=vm.createContext({RFBModule:RFB,document,location:{pathname:'/viewer/test',search:'?service='+service,protocol:'https:',host:'test'},URLSearchParams,matchMedia:()=>({matches:true}),setTimeout:()=>1,clearTimeout(){},setInterval(){},innerHeight:700,window:{addEventListener(){}},fetch:async()=>({ok:true})});
 vm.runInContext(readFileSync(new URL('./viewer.js',import.meta.url),'utf8').replace(/^import .*\n/,''),context);
 session.events.connect();
 return {nodes,session,document};
}
for(const service of ['tiktok','bigseller'])test(`${service}: tap opens keyboard before noVNC stops propagation; swipe does not`,()=>{
 const {nodes,document}=viewer(service),screen=nodes.get('screen'),input=nodes.get('type');
 assert.equal(screen.events.touchend.options.capture,true);
 screen.fire('touchstart',{touches:[{clientX:10,clientY:20}]});screen.fire('touchend',{touches:[]});
 assert.equal(document.activeElement,input);
 input.blur();
 screen.fire('touchstart',{touches:[{clientX:10,clientY:20}]});screen.fire('touchmove',{touches:[{clientX:90,clientY:20}]});screen.fire('touchend',{touches:[]});
 assert.equal(document.activeElement,null);
});
test('IME commits once, deletion and next-field keys reach the remote browser',()=>{
 const {nodes,session}=viewer(),input=nodes.get('type');
 nodes.get('keyboard').onclick();
 input.fire('compositionstart');input.value+='中';input.fire('input');assert.deepEqual(session.keys,[]);
 input.fire('compositionend');input.fire('input');assert.deepEqual(session.keys,[0x01004e2d]);
 input.value=input.value.slice(1);input.fire('input');
 nodes.get('next').onclick();nodes.get('enter').onclick();
 assert.deepEqual(session.keys,[0x01004e2d,0xff08,0xff09,0xff0d]);
});
test('zoom and pan use local viewport controls',()=>{
 const {nodes,session,document}=viewer();
 nodes.get('zoom').onclick();assert.equal(session.scaleViewport,false);assert.equal(session.clipViewport,true);
 nodes.get('pan').onclick();assert.equal(session.dragViewport,true);
 nodes.get('screen').fire('touchstart',{touches:[{clientX:10,clientY:20}]});nodes.get('screen').fire('touchend',{touches:[]});
 assert.equal(document.activeElement,null);
 nodes.get('zoom').onclick();assert.equal(session.scaleViewport,true);assert.equal(session.dragViewport,false);
});
test('phone draft preserves leading zeros and sends only when requested',()=>{
 const {nodes,session}=viewer(),draft=nodes.get('draft'),send=nodes.get('sendDraft');
 draft.value='001234';
 draft.fire('input');
 assert.deepEqual(session.keys,[]);
 send.onclick();
 assert.deepEqual(session.keys,[48,48,49,50,51,52]);
 assert.equal(draft.value,'');
 draft.value='pending';
 session.events.disconnect();
 assert.equal(draft.value,'');
 send.onclick();
 assert.equal(session.keys.length,6);
});
