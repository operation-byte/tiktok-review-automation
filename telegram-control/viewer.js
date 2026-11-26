import RFBModule from '@novnc/novnc/lib/rfb.js';
const RFB=RFBModule.default || RFBModule;
const id=location.pathname.split('/').pop();
const bigseller=new URLSearchParams(location.search).get('service')==='bigseller';
const loginCommand=bigseller?'/bs_login':'/tt_login';
const destination=bigseller?'BigSeller → Inventory List':'TikTok Seller Center → Product Ratings';
if(bigseller){document.title='BigSeller secure login';document.querySelector('h1').textContent='BigSeller login';document.getElementById('screen').setAttribute('aria-label','Remote BigSeller browser');}
const $=x=>document.getElementById(x);
const pill=$('pill'),statusText=$('status'),hint=$('hint'),overlay=$('overlay'),overlayText=$('overlayText'),stage=$('stage');
const save=$('save'),keyboard=$('keyboard'),reconnect=$('connect'),input=$('type');
const zoom=$('zoom'),pan=$('pan'),next=$('next'),enter=$('enter');
let zoomed=false,panning=false,composing=false,tapStart=null;
const RETRY_MS=3000,START_LIMIT_MS=5*60000,DROP_RETRIES=5,SAVE_LOCK_MS=20000;
const touch=matchMedia('(pointer: coarse)').matches;
// starting: waiting for the runner; live: screen connected; saving: verification requested;
// dropped: connection lost after being live; ended: session finished or expired.
let state='starting',rfb=null,retryTimer=0,saveTimer=0,dropRetries=0,saveRequested=false;
const viewerStart=Date.now();
let remoteClipboard='';

function enable(btn,on){btn.setAttribute('aria-disabled',String(!on));}
const enabled=btn=>btn.getAttribute('aria-disabled')!=='true';
function render(status,help,cover){
 pill.dataset.state={live:'live',saving:'live',ended:'ended',dropped:'error'}[state]||'wait';
 statusText.textContent=status;hint.textContent=help;
 overlay.hidden=!cover;if(cover)overlayText.textContent=cover;
 enable(save,state==='live');save.textContent=state==='saving'?'Checking login…':'Save login';
 $('entry').hidden=!touch||state!=='live';
 if(state!=='live'){ $('draft').value=''; $('account').value=''; remoteClipboard=''; $('copyRemote').hidden=true; }
 keyboard.hidden=!touch;enable(keyboard,state==='live');
 for(const button of [zoom,pan,next,enter]){button.hidden=!touch;enable(button,state==='live');}
 if(state!=='live'){keyboard.setAttribute('aria-pressed','false');input.blur();}
 reconnect.hidden=!(state==='dropped'||(state==='starting'&&Date.now()-viewerStart>60000));
}
function liveHint(){return touch?'Tap a field to type. Use Zoom for larger text, and Pan to move around. Open '+destination+', then tap Save login.':'Click the screen to type. Open '+destination+', then click Save login.';}
function renderStarting(){
 const late=Date.now()-viewerStart>180000;
 render('Starting…',late?'Still starting after 3 minutes. If Telegram has no “browser ready” message, send '+loginCommand+' for a new link.':'Starting the remote browser. This usually takes under a minute.',late?'Still waiting for the browser…':'Starting the browser…');
}
function setState(next){
 state=next;clearTimeout(retryTimer);
 if(next==='starting')renderStarting();
 else if(next==='live')render('Connected',liveHint());
 else if(next==='saving')render('Checking…','Checking your login. Telegram will confirm when it is saved.');
 else if(next==='dropped')render('Disconnected','The connection dropped. Tap reconnect, or send '+loginCommand+' for a new link if the session expired.','Connection lost.');
 else if(next==='ended')render('Ended',saveRequested?'This login session has finished. Check Telegram for the result.':'This login link has expired. Send '+loginCommand+' in Telegram for a new one.','Session ended.');
}
function fitStage(){
 // Match the box to the remote screen so no space is wasted on letterboxing.
 const canvas=$('screen').querySelector('canvas');
 if(canvas?.width&&canvas?.height)stage.style.setProperty('--ar',String(canvas.width/canvas.height));
}
function connect(){
 clearTimeout(retryTimer);
 if(rfb){const old=rfb;rfb=null;try{old.disconnect()}catch{}}
 try{
  const session=new RFB($('screen'),`${location.protocol==='https:'?'wss':'ws'}://${location.host}/vnc/${id}`);
  session.resizeSession=false;session.focusOnClick=!touch;
  session.qualityLevel=touch?3:5;session.compressionLevel=6;
  session.scaleViewport=!zoomed;session.clipViewport=zoomed;session.dragViewport=panning;
  session.addEventListener('connect',()=>{dropRetries=0;setState('live');fitStage();});
  session.addEventListener('desktopname',fitStage);
  session.addEventListener('clipboard',event=>{
   if(rfb!==session||state!=='live')return;
   remoteClipboard=event.detail.text;
   $('copyRemote').hidden=!remoteClipboard;
  });
  session.addEventListener('disconnect',()=>{if(rfb!==session)return;rfb=null;lost();});
  session.addEventListener('securityfailure',()=>{if(rfb!==session)return;rfb=null;setState('ended');});
  rfb=session;
 }catch{lost();}
}
function lost(){
 if(state==='ended')return;
 if(saveRequested&&state!=='starting'){setState('ended');return;}
 if(state==='starting'){
  if(Date.now()-viewerStart>START_LIMIT_MS){setState('ended');return;}
  renderStarting();retryTimer=setTimeout(connect,RETRY_MS);return;
 }
 setState('dropped');
 if(dropRetries<DROP_RETRIES){dropRetries++;render('Reconnecting…','The connection dropped. Reconnecting automatically…','Reconnecting…');retryTimer=setTimeout(connect,RETRY_MS);}
}
save.onclick=async()=>{
 if(!enabled(save))return;
 setState('saving');saveRequested=true;
 try{
  const r=await fetch(`/save/${id}`,{method:'POST',headers:{'Content-Type':'application/json'}});
  if(r.status===410||r.status===403){setState('ended');return;}
  if(!r.ok)throw Error();
 }catch{saveRequested=false;setState('live');hint.textContent='Could not reach the server. Check your connection, then tap Save login again.';return;}
 // The runner checks the page every few seconds; block repeat taps while it verifies.
 clearTimeout(saveTimer);saveTimer=setTimeout(()=>{if(state==='saving'){saveRequested=false;setState('live');hint.textContent='Not saved yet. Make sure '+destination+' is open, then tap Save login again.';}},SAVE_LOCK_MS);
};
reconnect.onclick=()=>{dropRetries=0;reconnect.hidden=true;render('Reconnecting…','Reconnecting to the remote browser…','Reconnecting…');connect();};

// Keep focus in a local input during the original touch gesture. Focusing after
// a network round trip cannot open the keyboard in iOS Safari.
const SENTINEL='________';
function key(sym){if(rfb&&state==='live'){rfb.sendKey(sym,undefined,true);rfb.sendKey(sym,undefined,false);}}
$('copyRemote').onclick=async()=>{
 if(state!=='live'||!remoteClipboard)return;
 try{await navigator.clipboard.writeText(remoteClipboard);hint.textContent='Copied to your phone.';}
 catch{hint.textContent='Clipboard access was blocked. Allow clipboard access in your browser and try again.';}
};
const draft=$('draft'),kind=$('entryKind');
kind.onchange=()=>{
 draft.value='';
 draft.type=kind.value==='password'?'password':'text';
 draft.autocomplete={password:'current-password',code:'one-time-code',text:'off'}[kind.value];
 const canSave=kind.value==='password'&&typeof PasswordCredential!=='undefined'&&!!navigator.credentials?.store;
 $('remember').hidden=!canSave;
 $('account').hidden=!canSave;
 draft.focus();
};
$('remember').onclick=async()=>{
 const account=$('account').value.trim();
 if(state!=='live'||kind.value!=='password')return;
 if(!account||!draft.value){hint.textContent='Enter the account and password before saving.';return;}
 const service=bigseller?'BigSeller':'TikTok';
 try{
  const credential=new PasswordCredential({id:service+' / '+account,name:service+' / '+account,password:draft.value});
  await navigator.credentials.store(credential);
  hint.textContent='Password submitted to your browser password manager.';
 }catch{hint.textContent='Your browser could not save this password. Use your phone password manager.';}
};
function sendDraft(){
 if(state!=='live'||!rfb||!draft.value)return;
 for(const ch of draft.value){
  const point=ch.codePointAt(0);
  key(point<=255?point:0x01000000|point);
 }
 draft.value='';
 draft.blur();
}
$('sendDraft').onclick=sendDraft;
draft.addEventListener('keydown',event=>{
 if(event.key==='Enter'&&!event.isComposing){event.preventDefault();sendDraft();}
});
function resetInput(){input.value=SENTINEL;input.setSelectionRange(SENTINEL.length,SENTINEL.length);}
function openKeyboard(){
 if(state!=='live'||panning)return;
 if(document.activeElement!==input){resetInput();input.focus({preventScroll:true});}
 keyboard.setAttribute('aria-pressed','true');
}
keyboard.onclick=()=>{
 if(!enabled(keyboard))return;
 if(document.activeElement===input)input.blur();else{setPan(false);openKeyboard();}
};
input.addEventListener('blur',()=>{keyboard.setAttribute('aria-pressed','false');composing=false;resetInput();});
function forwardInput(){
 const value=input.value;
 if(value.length<SENTINEL.length)for(let i=value.length;i<SENTINEL.length;i++)key(0xff08);
 else for(const ch of value.slice(SENTINEL.length)){const cp=ch.codePointAt(0);key(cp<=255?cp:0x01000000|cp);}
 resetInput();
}
input.addEventListener('compositionstart',()=>{composing=true;});
input.addEventListener('compositionend',()=>{composing=false;forwardInput();});
input.addEventListener('input',()=>{if(!composing)forwardInput();});
input.addEventListener('keydown',e=>{
 if(e.isComposing)return;
 const sym={Enter:0xff0d,Tab:0xff09,Escape:0xff1b,ArrowLeft:0xff51,ArrowUp:0xff52,ArrowRight:0xff53,ArrowDown:0xff54}[e.key];
 if(sym){e.preventDefault();key(sym);}
});
// Prevent toolbar buttons from dismissing the keyboard before sending keys.
for(const [button,sym] of [[next,0xff09],[enter,0xff0d]]){
 button.addEventListener('pointerdown',e=>e.preventDefault());
 button.onclick=()=>{if(enabled(button)){setPan(false);key(sym);openKeyboard();}};
}
function setPan(on){
 panning=on;pan.setAttribute('aria-pressed',String(on));
 if(rfb)rfb.dragViewport=on;
 if(on)input.blur();
 hint.textContent=on?'Drag to move around the enlarged screen. Tap Pan again to type.':liveHint();
}
zoom.onclick=()=>{
 if(!enabled(zoom)||!rfb)return;
 zoomed=!zoomed;stage.classList.toggle('zoomed',zoomed);
 zoom.setAttribute('aria-pressed',String(zoomed));zoom.textContent=zoomed?'Fit screen':'Zoom';
 rfb.scaleViewport=!zoomed;rfb.clipViewport=zoomed;
 if(!zoomed)setPan(false);
 pan.disabled=!zoomed;
};
pan.disabled=true;
pan.onclick=()=>{if(enabled(pan)&&zoomed)setPan(!panning);};
// Ignore swipes and multi-touch; a single tap raises the keyboard immediately.
$('screen').addEventListener('touchstart',e=>{
 const t=e.touches[0];tapStart=e.touches.length===1?{x:t.clientX,y:t.clientY}:null;
},{passive:true,capture:true});
$('screen').addEventListener('touchmove',e=>{
 const t=e.touches[0];
 if(!t||e.touches.length!==1||tapStart&&Math.hypot(t.clientX-tapStart.x,t.clientY-tapStart.y)>10)tapStart=null;
},{passive:true,capture:true});
$('screen').addEventListener('touchend',e=>{
 if(touch&&tapStart&&e.touches.length===0)openKeyboard();
 tapStart=null;
},{passive:true,capture:true});
$('screen').addEventListener('touchcancel',()=>{tapStart=null;},{passive:true,capture:true});
// The visual viewport shrinks when the phone keyboard opens. Only change the
// viewer height: the remote desktop and its controls keep their coordinates.
function sizeViewport(){
 const viewport=window.visualViewport;
 document.documentElement.style.setProperty('--visible-height',(viewport?.height||innerHeight)+'px');
}
window.visualViewport?.addEventListener('resize',sizeViewport);
window.addEventListener('resize',sizeViewport);sizeViewport();
setState('starting');connect();
setInterval(()=>{if(state==='starting')renderStarting();},15000);
