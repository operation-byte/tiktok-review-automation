import {build} from 'esbuild';
import {writeFile} from 'node:fs/promises';
const result=await build({entryPoints:['viewer.js'],bundle:true,minify:true,write:false,format:'iife'});
const script=result.outputFiles[0].text.replace(/<\/script/gi,'<\\/script');
const start=`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="color-scheme" content="light dark"><title>TikTok secure login</title><style>
:root{--bg:#f4f6fa;--card:#fff;--text:#152033;--muted:#5b6678;--line:#dde3ec;--primary:#1764e8;--primary-text:#fff;--soft:#e8eef8;--screen:#1b2130;--ok:#16a34a;--wait:#d97706;--bad:#dc2626;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text)}
@media (prefers-color-scheme:dark){:root{--bg:#0f141c;--card:#171e29;--text:#e7ecf3;--muted:#9aa6b8;--line:#2a3442;--primary:#4c8dff;--primary-text:#06111f;--soft:#232d3b;--screen:#05080d}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg)}
body{min-height:100dvh}
.wrap{max-width:1200px;margin:0 auto;padding:0 12px calc(12px + env(safe-area-inset-bottom))}
button{font:inherit;font-weight:600;border:0;border-radius:12px;padding:0 18px;height:48px;cursor:pointer;background:var(--soft);color:var(--text);touch-action:manipulation;display:inline-flex;align-items:center;justify-content:center;gap:8px}
button.primary{background:var(--primary);color:var(--primary-text)}
button.icon{width:48px;padding:0;flex:none}button.icon svg{width:22px;height:22px}
button[aria-disabled=true]{opacity:.4;cursor:not-allowed}
button[aria-pressed=true]{outline:2px solid var(--primary);outline-offset:-2px}
button:focus-visible,input:focus-visible{outline:3px solid var(--primary);outline-offset:2px}
[hidden]{display:none!important}
.top{display:flex;align-items:center;gap:12px;padding:12px 4px;padding-top:max(12px,env(safe-area-inset-top))}
.top h1{font-size:17px;margin:0;white-space:nowrap}
.pill{display:inline-flex;align-items:center;gap:8px;margin:0 0 0 auto;font-size:14px;color:var(--muted)}
.dot{width:10px;height:10px;border-radius:50%;background:var(--wait);flex:none}
[data-state=live] .dot{background:var(--ok)}[data-state=ended] .dot,[data-state=error] .dot{background:var(--bad)}
/* The box takes the remote screen's exact shape (--ar = width/height), capped so the toolbar stays on screen. */
.stage{--ar:1.6;position:relative;width:min(100%,calc((100dvh - 190px) * var(--ar)));aspect-ratio:var(--ar);margin:0 auto;border-radius:12px;overflow:hidden;background:var(--screen)}
#screen{position:absolute;inset:0}#screen canvas{touch-action:none}
.overlay{position:absolute;inset:0;display:grid;place-content:center;gap:12px;justify-items:center;padding:16px;text-align:center;color:#e7ecf3;font-size:15px;line-height:1.4}
.spin{width:30px;height:30px;border-radius:50%;border:3px solid #ffffff33;border-top-color:#fff;animation:spin 1s linear infinite}
@media (prefers-reduced-motion:reduce){.spin{animation-duration:3s}}
@keyframes spin{to{transform:rotate(360deg)}}
.hint{margin:10px 4px 0;font-size:14px;line-height:1.4;color:var(--muted);text-align:center}
.controls{width:min(100%,640px);margin:12px auto 0;display:grid;gap:8px}
.toolbar{display:flex;gap:8px}.toolbar .primary{flex:1}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;font-size:16px}
main.card{max-width:520px;margin:auto;padding:32px 20px;text-align:center}
main.card h1{font-size:22px;margin:0 0 8px}main.card p{color:var(--muted);line-height:1.5}
main.card .spin{margin:20px auto;border-color:var(--line);border-top-color:var(--primary)}
</style></head><body>`;
const viewerHtml=start+`<div class="wrap"><header class="top"><h1>TikTok login</h1><p class="pill" id="pill" data-state="wait" role="status" aria-live="polite"><span class="dot" aria-hidden="true"></span><span id="status">Starting…</span></p></header><div class="stage" id="stage"><div id="screen" aria-label="Remote TikTok browser"></div><div class="overlay" id="overlay"><div class="spin" aria-hidden="true"></div><span id="overlayText">Starting the browser…</span></div></div><div class="controls"><label class="sr" for="type">Keyboard input for the remote browser</label><input class="sr" id="type" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="go" tabindex="-1"><div class="toolbar"><button class="primary" id="save" aria-disabled="true">Save login</button><button class="icon" id="keyboard" aria-pressed="false" aria-disabled="true" aria-label="Show phone keyboard" title="Keyboard" hidden><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg></button><button class="icon" id="connect" aria-label="Reconnect" title="Reconnect" hidden><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg></button></div></div><p class="hint" id="hint">Starting the remote browser. This usually takes under a minute.</p></div>
<script>${script}</script></body></html>`;
const openHtml=start+`<main class="card"><h1>TikTok secure login</h1><div class="spin" id="spin" aria-hidden="true"></div><p id="status" role="status" aria-live="polite">Opening your private login browser…</p><button class="primary" id="open" hidden>Try again</button><p><small>Only open links from your own Telegram bot. Never send passwords or codes in chat. Links expire automatically.</small></p></main><script>
const id=location.pathname.split('/').pop(),key=location.hash.slice(1),service=new URLSearchParams(location.search).get('service')==='bigseller'?'bigseller':'tiktok';history.replaceState(null,'',location.pathname+location.search);
if(service==='bigseller'){document.title='BigSeller secure login';document.querySelector('h1').textContent='BigSeller secure login';}
const status=document.getElementById('status'),btn=document.getElementById('open'),spin=document.getElementById('spin');let busy=false;
async function open(){if(busy)return;if(!key){spin.hidden=true;status.textContent='This link is missing its private key. Request a new login link from Warehouse or Telegram.';return;}
busy=true;btn.hidden=true;spin.hidden=false;status.textContent='Opening your private login browser…';
try{const r=await fetch('/auth/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});if(r.ok){location.replace('/viewer/'+id+'?service='+service);return;}spin.hidden=true;status.textContent=await r.text();if(r.status>=500)btn.hidden=false;}
catch{spin.hidden=true;status.textContent='Network problem. Check your connection and try again.';btn.hidden=false;}finally{busy=false;}}
btn.onclick=open;open();</script></body></html>`;
await writeFile('pages.mjs','// Generated by npm run build. Includes bundled noVNC.\nexport const viewerHtml='+JSON.stringify(viewerHtml)+';\nexport const openHtml='+JSON.stringify(openHtml)+';\n');
