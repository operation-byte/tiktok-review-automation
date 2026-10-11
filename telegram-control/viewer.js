import RFB from '@novnc/novnc/lib/rfb.js';
const id=location.pathname.split('/').pop();
let rfb;
const status=document.getElementById('status');
function connect(){
 if(rfb)rfb.disconnect();
 status.textContent='Connecting…';
 rfb=new RFB(document.getElementById('screen'),`${location.origin.replace('https:','wss:')}/vnc/${id}`);
 rfb.scaleViewport=true;rfb.resizeSession=false;
 rfb.addEventListener('connect',()=>status.textContent='Log into the requested shop, then press Save login.');
 rfb.addEventListener('disconnect',()=>status.textContent='Disconnected or still starting. Reconnect when the Telegram ready message arrives.');
 rfb.addEventListener('securityfailure',()=>status.textContent='Connection rejected. Request a new login link in Telegram.');
}
document.getElementById('connect').onclick=connect;
document.getElementById('save').onclick=async()=>{
 const r=await fetch(`/save/${id}`,{method:'POST'});status.textContent=await r.text();
};
document.getElementById('send').onclick=()=>{
 const input=document.getElementById('type');
 for(const ch of input.value){const cp=ch.codePointAt(0);rfb?.sendKey(cp<=255?cp:0x01000000|cp,ch,true);rfb?.sendKey(cp<=255?cp:0x01000000|cp,ch,false);}
 input.value='';
};
document.getElementById('enter').onclick=()=>rfb?.sendKey(0xff0d,'Enter');
document.getElementById('backspace').onclick=()=>rfb?.sendKey(0xff08,'Backspace');
connect();
