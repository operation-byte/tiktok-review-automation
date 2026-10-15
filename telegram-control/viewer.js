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
const RETRY_MS=3000,START_LIMIT_MS=5*60000,DROP_RETRIES=5,SAVE_LOCK_MS=20000;
const touch=matchMedia('(pointer: coarse)').matches;
// starting: waiting for the runner; live: screen connected; saving: verification requested;
// dropped: connection lost after being live; ended: session finished or expired.
let state='starting',rfb=null,retryTimer=0,saveTimer=0,dropRetries=0,saveRequested=false;
const viewerStart=Date.now();

function enable(btn,on){btn.setAttribute('aria-disabled',String(!on));}
const enabled=btn=>btn.getAttribute('aria-disabled')!=='true';
function render(status,help,cover){
 pill.dataset.state={live:'live',saving:'live',ended:'ended',dropped:'error'}[state]||'wait';
 statusText.textContent=status;hint.textContent=help;
 overlay.hidden=!cover;if(cover)overlayText.textContent=cover;
 enable(save,state==='live');save.textContent=state==='saving'?'Checking login…':'Save login';
 keyboard.hidden=!touch;enable(keyboard,state==='live');
 if(state!=='live'){keyboard.setAttribute('aria-pressed','false');input.blur();}
 reconnect.hidden=!(state==='dropped'||(state==='starting'&&Date.now()-viewerStart>60000));
}
const LIVE_HINT=touch?'Log in and open '+destination+', then tap Save login. Tap ⌨ to type'+(matchMedia('(orientation: portrait)').matches?'; turn your phone sideways for a bigger screen.':'.'):'Click the screen to type. Open '+destination+', then click Save login.';
function renderStarting(){
 const late=Date.now()-viewerStart>180000;
 render('Starting…',late?'Still starting after 3 minutes. If Telegram has no “browser ready” message, send '+loginCommand+' for a new link.':'Starting the remote browser. This usually takes under a minute.',late?'Still waiting for the browser…':'Starting the browser…');
}
function setState(next){
 state=next;clearTimeout(retryTimer);
 if(next==='starting')renderStarting();
 else if(next==='live')render('Connected',LIVE_HINT);
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
  session.scaleViewport=true;session.resizeSession=false;session.focusOnClick=true;
  session.addEventListener('connect',()=>{dropRetries=0;setState('live');fitStage();});
  session.addEventListener('desktopname',fitStage);
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

// Phones only raise their keyboard for a focused text field, so a hidden field forwards each key live.
const SENTINEL='________';
function key(sym){if(rfb&&state==='live'){rfb.sendKey(sym,undefined,true);rfb.sendKey(sym,undefined,false);}}
function resetInput(){input.value=SENTINEL;input.setSelectionRange(SENTINEL.length,SENTINEL.length);}
keyboard.onclick=()=>{
 if(!enabled(keyboard))return;
 const open=keyboard.getAttribute('aria-pressed')!=='true';
 keyboard.setAttribute('aria-pressed',String(open));
 if(open){resetInput();input.focus();}else input.blur();
};
input.addEventListener('blur',()=>keyboard.setAttribute('aria-pressed','false'));
input.addEventListener('input',()=>{
 const v=input.value;
 if(v.length<SENTINEL.length)for(let i=v.length;i<SENTINEL.length;i++)key(0xff08);
 else for(const ch of v.slice(SENTINEL.length)){const cp=ch.codePointAt(0);key(cp<=255?cp:0x01000000|cp);}
 resetInput();
});
input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();key(0xff0d);}});

setState('starting');connect();
// Refresh the waiting text (and offer reconnect after a minute) without disturbing the retry timer.
setInterval(()=>{if(state==='starting')renderStarting();},15000);
