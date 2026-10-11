import RFBModule from '@novnc/novnc/lib/rfb.js';
const RFB=RFBModule.default || RFBModule;
const id=location.pathname.split('/').pop();
let rfb, retryTimer, waiting=true;
const status=document.getElementById('status');
function message(txt){status.textContent=txt;}
function connect(){
 clearTimeout(retryTimer);
 if(rfb){try{rfb.disconnect()}catch{}}
 message('Connecting to your secure browser. It may take a few minutes to start…');
 try{
  rfb=new RFB(document.getElementById('screen'),`${location.origin.replace('https:','wss:')}/vnc/${id}`);
  rfb.scaleViewport=true;rfb.resizeSession=false;
  rfb.addEventListener('connect',()=>{waiting=false;message('Browser ready. Sign in to TikTok, then open Product Ratings and tap Verify & save login.');});
  rfb.addEventListener('disconnect',()=>{if(waiting){message('Browser starting. Reconnecting automatically…');retryTimer=setTimeout(connect,7000);}else message('Browser disconnected. Tap Reconnect browser, or request a new link if the session expired.');});
  rfb.addEventListener('securityfailure',()=>{waiting=false;message('Connection rejected. Request a new private link in Telegram.');});
 }catch{retryTimer=setTimeout(connect,7000)}
}
document.getElementById('connect').onclick=()=>{waiting=true;connect()};
document.getElementById('save').onclick=async()=>{
 const btn=document.getElementById('save');btn.disabled=true;
 try{const r=await fetch(`/save/${id}`,{method:'POST',headers:{'Content-Type':'application/json'}});message(r.ok?'Checking your TikTok login. Telegram will confirm when the session is safely saved.':await r.text());}
 catch{message('Could not reach server. Reconnect and try again.')}finally{setTimeout(()=>btn.disabled=false,4000)}
};
document.getElementById('send').onclick=()=>{
 const input=document.getElementById('type');if(!rfb){message('Connect browser first.');return;}
 for(const ch of input.value){const cp=ch.codePointAt(0);const sym=cp<=255?cp:0x01000000|cp;rfb.sendKey(sym,undefined,true);rfb.sendKey(sym,undefined,false)}input.value='';
};
document.getElementById('enter').onclick=()=>rfb?.sendKey(0xff0d);
document.getElementById('backspace').onclick=()=>rfb?.sendKey(0xff08);
connect();
