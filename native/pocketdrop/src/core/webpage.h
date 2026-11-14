#pragma once
#include <string>

// The page phones see after scanning. Static (data comes from api/manifest) and
// split into chunks to stay under MSVC's string literal limit.
inline const std::string& web_page() {
    static const std::string page = std::string(R"PD(<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark light"><meta name="theme-color" content="#0f0f13">
<title>PocketDrop</title>
<style>
:root{--bg:#0f0f13;--card:#1a1a21;--line:#2a2a34;--text:#f3f3f6;--mute:#9b9ba8;--acc:#8b7cff;--acc2:#b9afff;--accs:rgba(139,124,255,.16);--ok:#3ecf8e;--bad:#e5484d}
@media (prefers-color-scheme:light){:root{--bg:#f4f4f8;--card:#fff;--line:#e3e3ea;--text:#16161c;--mute:#6b6b78;--acc:#6a58f0;--acc2:#5646d6;--accs:rgba(106,88,240,.12);--ok:#1a9d63;--bad:#d13438}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--text);font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:560px;margin:0 auto;padding:max(16px,env(safe-area-inset-top)) 16px calc(28px + env(safe-area-inset-bottom))}
header{display:flex;align-items:center;gap:11px;margin:6px 2px 18px}
.logo{width:36px;height:36px;border-radius:11px;background:linear-gradient(135deg,#8b7cff,#4fc3f7);display:grid;place-items:center;flex:none}
.logo svg{width:20px;height:20px;stroke:#fff}
h1{font-size:18px;margin:0;letter-spacing:-.01em}
.sub{color:var(--mute);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:12px}
.sum{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px}
.sum b{font-size:22px;letter-spacing:-.02em}
.btn{position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center;gap:9px;width:100%;border:0;border-radius:14px;padding:15px 16px;font:600 16px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#fff;background:var(--acc);text-decoration:none;cursor:pointer}
.btn.sec{background:transparent;color:var(--text);border:1px solid var(--line);margin-top:8px}
.btn[aria-disabled=true]{opacity:.6;pointer-events:none}
.btn svg{width:19px;height:19px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.photo-note{color:var(--mute);font-size:12.5px;line-height:1.45;margin:10px 4px 0;text-align:center}
.bar{height:4px;border-radius:2px;background:var(--line);overflow:hidden;margin-top:10px}
.bar i{display:block;height:100%;background:var(--acc);width:0;transition:width .3s}
.list{padding:6px}
.row{display:flex;align-items:center;gap:12px;padding:9px;border-radius:12px}
.th{width:50px;height:50px;border-radius:11px;background:var(--line);flex:none;display:grid;place-items:center;overflow:hidden;position:relative}
.th img{width:100%;height:100%;object-fit:cover;position:absolute;inset:0}
.th svg,.dl svg,.x svg{width:22px;height:22px;stroke:var(--mute);fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.meta{flex:1;min-width:0;color:inherit;text-decoration:none}
.nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:500}
.dt{color:var(--mute);font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dl{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;flex:none;background:var(--accs)}
.dl svg{width:19px;height:19px;stroke:var(--acc2)}
.miss{opacity:.45}
.txt{white-space:pre-wrap;word-break:break-word;font:14px/1.45 ui-monospace,Menlo,Consolas,monospace;max-height:240px;overflow:auto;margin:0 0 14px}
.two{display:flex;gap:8px}.two .btn{margin:0}
.hint{color:var(--mute);font-size:12.5px;text-align:center;margin:18px 12px 0}
.state{text-align:center;padding:44px 18px}.state b{display:block;font-size:17px;margin-bottom:6px}
.state.quiet{padding:18px}
.more{color:var(--mute);font-size:13px;text-align:center;padding:10px}
.sec-title{color:var(--mute);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin:20px 6px 8px}
.send-top{margin:0 2px 14px}.send-top b{font-size:17px;display:block}
#pick{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;font-size:0}
textarea{display:block;width:100%;min-height:104px;border-radius:12px;border:1px solid var(--line);background:var(--bg);color:var(--text);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:12px;margin:12px 0 8px;resize:vertical}
.code{display:block;width:100%;border-radius:12px;border:1px solid var(--line);background:var(--bg);color:var(--text);font:600 21px/1 ui-monospace,Menlo,Consolas,monospace;letter-spacing:.08em;text-align:center;text-transform:uppercase;padding:14px;margin:12px 0 8px}
.remember{display:flex;align-items:center;gap:9px;margin:12px 2px 0;color:var(--mute);font-size:14px}
.remember input{width:18px;height:18px;accent-color:var(--acc)}
.auth-error{min-height:18px;color:var(--bad);font-size:12.5px;text-align:center;margin-top:8px}
.q{display:flex;align-items:center;gap:12px;padding:12px 2px 0}
.q .th{width:40px;height:40px;border-radius:10px}.q .th svg{width:19px;height:19px}
.pbar{height:3px;border-radius:2px;background:var(--line);overflow:hidden;margin-top:6px}
.pbar i{display:block;height:100%;width:0;background:var(--acc);transition:width .2s}
.q.done .pbar i{background:var(--ok)}.q.done .dt{color:var(--ok)}
.q.err .dt{color:var(--bad)}.q.err .pbar i{background:var(--bad)}
.x{width:36px;height:36px;border-radius:50%;border:0;background:transparent;display:grid;place-items:center;flex:none;padding:0;cursor:pointer}
.x svg{width:18px;height:18px;stroke-width:2.2}
.q.done .x svg{stroke:var(--ok)}
body.dragging #send{outline:2px dashed var(--acc);outline-offset:-2px}
#off{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(18px + env(safe-area-inset-bottom));background:var(--bad);color:#fff;padding:8px 14px;border-radius:99px;font-size:13px;white-space:nowrap}
[hidden]{display:none!important}
</style></head><body>
)PD") + std::string(R"PD(<svg width="0" height="0" style="position:absolute" aria-hidden="true">
<symbol id="i-file" viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></symbol>
<symbol id="i-text" viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></symbol>
<symbol id="i-pdf" viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 15h1.5a1.5 1.5 0 0 0 0-3H9v5"/></symbol>
<symbol id="i-image" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/></symbol>
<symbol id="i-video" viewBox="0 0 24 24"><rect x="2" y="5" width="15" height="14" rx="3"/><path d="m17 10 5-3v10l-5-3"/></symbol>
<symbol id="i-audio" viewBox="0 0 24 24"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></symbol>
<symbol id="i-archive" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4"/></symbol>
<symbol id="i-dl" viewBox="0 0 24 24"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></symbol>
<symbol id="i-up" viewBox="0 0 24 24"><path d="M12 20V9M7 13.5l5-5 5 5M5 4h14"/></symbol>
<symbol id="i-copy" viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></symbol>
<symbol id="i-x" viewBox="0 0 24 24"><path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/></symbol>
<symbol id="i-retry" viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5"/></symbol>
<symbol id="i-link" viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></symbol>
<symbol id="i-share" viewBox="0 0 24 24"><path d="M4 12v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7M16 6l-4-4-4 4M12 2v13"/></symbol>
</svg>
<main>
<header><div class="logo"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg></div>
<div><h1>PocketDrop</h1><div class="sub" id="from">Connecting…</div></div></header>
<section class="card" id="send">
<div class="send-top"><b>Send to <span id="pc">your computer</span></b><span class="sub">Photos, videos, files or text</span></div>
<div id="receive-auth" hidden><div class="sub">Enter the receive code shown in PocketDrop before sending anything to this computer.</div>
<input class="code" id="authcode" type="text" inputmode="text" autocapitalize="characters" autocomplete="off" maxlength="9" placeholder="ABCD-EFGH" aria-label="Receive code">
<label class="remember" id="rememberrow"><input type="checkbox" id="remember"> Remember this phone</label>
<button class="btn" id="authbtn" type="button" style="margin-top:12px">Unlock sending</button><div class="auth-error" id="autherr"></div></div>
<div id="send-actions">
<div class="two">
<label class="btn"><svg><use href="#i-up"/></svg><span>Choose files</span><input type="file" id="pick" multiple aria-label="Choose files to send"></label>
<button class="btn sec" id="notebtn" type="button"><svg><use href="#i-text"/></svg><span>Text</span></button>
</div>
<div id="note" hidden><textarea id="notetext" placeholder="Type or paste text to send"></textarea>
<div class="two"><button class="btn sec" id="notecancel" type="button">Cancel</button><button class="btn" id="notesend" type="button">Send text</button></div></div>
<div id="queue"></div>
</div>
</section>
<div id="app"></div>
<p class="hint" id="hint"></p>
</main>
<div id="off" hidden>Reconnecting to your computer…</div>
)PD") + std::string(R"PD(<script>
"use strict";
const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const enc=encodeURIComponent;
const ic=n=>`<svg><use href="#i-${n}"/></svg>`;
const icons={image:"image",video:"video",audio:"audio",pdf:"pdf",archive:"archive",text:"text",file:"file"};
const MAX_ROWS=400;
const IOS=/iPhone|iPad|iPod/.test(navigator.userAgent)||(navigator.platform==="MacIntel"&&navigator.maxTouchPoints>1);
let data=null,timer=0,ended=false,shareFiles=null;
function fmt(b){const u=["B","KB","MB","GB","TB"];let i=0;while(b>=1024&&i<4){b/=1024;i++}return(i?b.toFixed(b<10?2:b<100?1:0):Math.round(b))+" "+u[i]}
const fileUrl=(f,dl)=>`f/${f.i}/${enc(f.name)}${dl?"?dl=1":""}`;
const plural=(n,w)=>`${n} ${w}${n===1?"":"s"}`;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

async function poll(){
  clearTimeout(timer);
  if(ended)return;
  try{
    const r=await fetch("api/manifest",{cache:"no-store"});
    if(r.status===404)return gone();
    const m=await r.json();
    $("#off").hidden=true;
    if(!data||m.rev!==data.rev||m.ready!==data.ready||m.receiveAuth!==data.receiveAuth||m.receiveOk!==data.receiveOk){data=m;render()}
    else{data=m;progress()}
  }catch(e){$("#off").hidden=false}
  if(!document.hidden)timer=setTimeout(poll,data&&data.ready?2500:700);
}

function gone(){
  ended=true;
  $("#from").textContent="";$("#hint").textContent="";$("#send").hidden=true;
  for(const it of queue)if(it.state==="wait"||it.state==="up"){it.state="error";it.err="Link expired"}
  $("#app").innerHTML=`<div class="card state"><b>This drop has ended</b><div class="sub">Scan the QR code on your computer again to keep going.</div></div>`;
}

function progress(){
  const l=$("#ziplbl"),bar=$("#zipbar");
  if(l&&!data.ready)l.textContent=`Packing… ${Math.round(data.prep*100)}%`;
  if(bar)bar.firstChild.style.width=(data.prep*100)+"%";
}

function render(){
  const m=data,app=$("#app");
  $("#from").textContent="Connected to "+m.pc;
  $("#pc").textContent=m.pc;
  updateSendAuth();
  const nf=m.files.length;
  document.title=nf?`PocketDrop · ${plural(nf,"file")}`:"PocketDrop";
  let h=`<div class="sec-title">From ${esc(m.pc)}</div>`,th="";
  if(!nf&&!m.texts.length){
    app.innerHTML=h+`<div class="card state quiet"><div class="sub">Nothing shared yet. Files you drop onto PocketDrop on your computer show up here.</div></div>`;
    $("#hint").textContent="";return;
  }
  m.texts.forEach((t,k)=>{
    const u=t.trim(),isUrl=/^https?:\/\/\S+$/i.test(u);
    th+=`<div class="card"><pre class="txt">${esc(t)}</pre><div class="two">`+
      (isUrl?`<a class="btn" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${ic("link")}Open</a>`:"")+
      `<button class="btn${isUrl?" sec":""}" data-copy="${k}">${ic("copy")}Copy</button></div></div>`;
  });
  if(nf){
    const single=nf===1&&!m.zip.preferred;
    h+=`<div class="card"><div class="sum"><b>${plural(nf,"file")}</b><span class="sub">${fmt(m.total)}</span></div>`;
    if(single){
      h+=m.files[0].missing?`<div class="btn" aria-disabled="true">File no longer available</div>`:`<a class="btn" href="${fileUrl(m.files[0],1)}" download>${ic("dl")}${IOS?"Save to Files":"Download"}</a>`;
    }else{
      h+=`<a class="btn" id="zipbtn" href="zip/${enc(m.zip.name)}" download${m.ready?"":' aria-disabled="true"'}>${ic("archive")}<span id="ziplbl">${m.ready?(IOS?"Save ZIP to Files · ":"Download all · ")+fmt(m.zip.size):"Packing… "+Math.round(m.prep*100)+"%"}</span></a>`;
      if(!m.ready)h+=`<div class="bar" id="zipbar"><i style="width:${m.prep*100}%"></i></div>`;
    }
    const media=m.files.filter(f=>(f.kind==="image"||f.kind==="video")&&!f.missing);
    const mb=media.reduce((a,f)=>a+f.size,0);
    const canShare=window.isSecureContext&&navigator.share&&navigator.canShare&&media.length&&media.length<=40&&mb<=400*1048576;
    if(canShare){
      h+=`<button class="btn sec" id="share">${ic("share")}<span>${IOS?`Save ${plural(media.length,"item")} to Photos`:`Share ${plural(media.length,"media item")}`}</span></button>`;
      if(IOS)h+=`<div class="photo-note" id="share-note">After preparation, choose Save Image or Save Video in Apple's share sheet.</div>`;
    }else if(IOS&&media.length){
      h+=`<div class="photo-note">To add media to Photos, open its thumbnail, tap Share, then choose Save Image or Save Video.</div>`;
    }
    h+=`</div>`+th+`<div class="card list">`;
    for(const f of m.files.slice(0,MAX_ROWS)){
      const slash=f.path.lastIndexOf("/"),dir=slash>0?f.path.slice(0,slash)+" · ":"";
      const thumb=["image","video","pdf"].includes(f.kind)&&!f.missing?`<img loading="lazy" alt="" src="t/${f.i}" onerror="this.remove()">`:"";
      h+=`<div class="row${f.missing?" miss":""}"><a class="th" href="${fileUrl(f)}" target="_blank" rel="noopener">${ic(icons[f.kind]||"file")}${thumb}</a>`+
        `<a class="meta" href="${fileUrl(f,1)}" download><div class="nm">${esc(f.name)}</div><div class="dt">${esc(dir)}${f.missing?"unavailable":fmt(f.size)}</div></a>`+
        `<a class="dl" href="${fileUrl(f,1)}" download aria-label="${IOS?"Save to Files":"Download"} ${esc(f.name)}">${ic("dl")}</a></div>`;
    }
    if(nf>MAX_ROWS)h+=`<div class="more">+ ${plural(nf-MAX_ROWS,"more file")} in the zip</div>`;
    h+=`</div>`;
  }else h+=th;
  app.innerHTML=h;
  shareFiles=null;
  $("#hint").textContent=nf>1||(m.zip.preferred&&nf)?(IOS?"ZIP files are saved in Files. Tap one there to unzip it.":"The zip keeps your folder structure."):"";
}

async function copyText(t,btn){
  try{await navigator.clipboard.writeText(t)}catch(e){
    const ta=document.createElement("textarea");ta.value=t;ta.setAttribute("readonly","");
    ta.style.cssText="position:fixed;top:0;opacity:0";document.body.appendChild(ta);
    ta.select();ta.setSelectionRange(0,t.length);document.execCommand("copy");ta.remove();
  }
  btn.innerHTML=ic("check")+"Copied";setTimeout(()=>{btn.innerHTML=ic("copy")+"Copy"},1600);
}

async function share(btn){
  const lbl=btn.querySelector("span");
  const note=$("#share-note");
  if(shareFiles){
    try{await navigator.share({files:shareFiles})}
    catch(e){if(e.name!=="AbortError")lbl.textContent="Couldn't open share sheet"}
    return;
  }
  const media=data.files.filter(f=>(f.kind==="image"||f.kind==="video")&&!f.missing);
  btn.setAttribute("aria-disabled","true");
  const out=[];
  try{
    for(let k=0;k<media.length;k++){
      lbl.textContent=`Preparing ${k+1} of ${media.length}…`;
      const b=await(await fetch(fileUrl(media[k]))).blob();
      out.push(new File([b],media[k].name,{type:b.type}));
    }
  }catch(e){lbl.textContent="Couldn't prepare files";btn.removeAttribute("aria-disabled");return}
  btn.removeAttribute("aria-disabled");
  if(!navigator.canShare({files:out})){lbl.textContent="Sharing not supported here";return}
  shareFiles=out;
  lbl.textContent=IOS?"Open Share Sheet":`Share ${plural(out.length,"media item")}`;
  if(note)note.textContent="Choose Save Image or Save Video in the share sheet.";
  if(navigator.userActivation&&navigator.userActivation.isActive){
    try{await navigator.share({files:shareFiles})}
    catch(e){if(e.name!=="AbortError"&&note)note.textContent="Tap Open Share Sheet, then choose Save Image or Save Video."}
  }
}

$("#app").addEventListener("click",e=>{
  const c=e.target.closest("[data-copy]");
  if(c){copyText(data.texts[+c.dataset.copy],c);return}
  const s=e.target.closest("#share");
  if(s)share(s);
});
)PD") + std::string(R"PD(
// ---- sending to the computer: chunked, resumable uploads
const queue=[];
let busy=false,wake=null;
const kindIcon=n=>{const e=(n.split(".").pop()||"").toLowerCase();
  return /^(jpe?g|png|gif|webp|heic|heif|avif|bmp|tiff?)$/.test(e)?"image":/^(mp4|m4v|mov|webm|mkv|avi|3gp)$/.test(e)?"video":
    /^(mp3|m4a|aac|wav|ogg|opus|flac)$/.test(e)?"audio":e==="pdf"?"pdf":/^(zip|7z|rar|gz|tgz)$/.test(e)?"archive":"file"};

function addFiles(list){
  for(const f of Array.from(list)){
    const it={f,name:f.name||"file",size:f.size,sent:0,live:0,state:"wait",tries:0};
    queue.push(it);draw(it);
  }
  pump();
}

// Plain-HTTP pages have no crypto.subtle, so the proof for a remembered phone is computed here.
function sha256(msg){
  const K=[],H=[];
  for(let p=2,n=0;n<64;p++){let prime=true;for(let d=2;d*d<=p;d++)if(p%d===0){prime=false;break}
    if(prime){if(n<8)H[n]=Math.floor((Math.sqrt(p)%1)*4294967296)|0;K[n]=Math.floor((Math.cbrt(p)%1)*4294967296)|0;n++}}
  const len=msg.length,total=((len+9+63)>>6)<<6,buf=new Uint8Array(total);buf.set(msg);buf[len]=0x80;
  const dv=new DataView(buf.buffer);dv.setUint32(total-8,Math.floor(len/536870912));dv.setUint32(total-4,(len<<3)>>>0);
  const w=new Int32Array(64),h=H.slice();
  for(let o=0;o<total;o+=64){
    for(let i=0;i<16;i++)w[i]=dv.getInt32(o+i*4);
    for(let i=16;i<64;i++){
      const a=w[i-15],b=w[i-2];
      w[i]=(w[i-16]+(((a>>>7)|(a<<25))^((a>>>18)|(a<<14))^(a>>>3))+w[i-7]+(((b>>>17)|(b<<15))^((b>>>19)|(b<<13))^(b>>>10)))|0;
    }
    let [a,b,c,d,e,f,g,k]=h;
    for(let i=0;i<64;i++){
      const t1=(k+(((e>>>6)|(e<<26))^((e>>>11)|(e<<21))^((e>>>25)|(e<<7)))+((e&f)^(~e&g))+K[i]+w[i])|0;
      const t2=((((a>>>2)|(a<<30))^((a>>>13)|(a<<19))^((a>>>22)|(a<<10)))+((a&b)^(a&c)^(b&c)))|0;
      k=g;g=f;f=e;e=(d+t1)|0;d=c;c=b;b=a;a=(t1+t2)|0;
    }
    h[0]=(h[0]+a)|0;h[1]=(h[1]+b)|0;h[2]=(h[2]+c)|0;h[3]=(h[3]+d)|0;h[4]=(h[4]+e)|0;h[5]=(h[5]+f)|0;h[6]=(h[6]+g)|0;h[7]=(h[7]+k)|0;
  }
  const out=new Uint8Array(32),ov=new DataView(out.buffer);
  for(let i=0;i<8;i++)ov.setInt32(i*4,h[i]);
  return out;
}
function hmacHex(key,msg){
  const te=new TextEncoder();let k=te.encode(key);if(k.length>64)k=sha256(k);
  const ip=new Uint8Array(64),op=new Uint8Array(64);
  for(let i=0;i<64;i++){const v=k[i]||0;ip[i]=v^0x36;op[i]=v^0x5c}
  const m=te.encode(msg),inner=new Uint8Array(64+m.length);inner.set(ip);inner.set(m,64);
  const ih=sha256(inner),outer=new Uint8Array(96);outer.set(op);outer.set(ih,64);
  return Array.from(sha256(outer),x=>x.toString(16).padStart(2,"0")).join("");
}
const deviceKey="pocketdrop.device";
function savedDevice(){try{const d=JSON.parse(localStorage.getItem(deviceKey)||"null");return d&&d.device&&d.secret?d:null}catch(e){return null}}
function saveDevice(d){try{d?localStorage.setItem(deviceKey,JSON.stringify(d)):localStorage.removeItem(deviceKey)}catch(e){}}
let triedDevice=false;
// A phone remembered on this computer unlocks sending without the code.
async function unlockRemembered(){
  const d=savedDevice();
  if(!d||triedDevice)return;
  triedDevice=true;
  try{
    let r=await fetch(`auth?device=${enc(d.device)}`,{method:"POST"});
    if(r.status===401){saveDevice(null);return}
    if(r.status!==200)return;
    const {nonce}=await r.json();
    r=await fetch(`auth?device=${enc(d.device)}&proof=${hmacHex(d.secret,nonce)}`,{method:"POST"});
    if(r.status===204){data.receiveOk=true;updateSendAuth()}
    else if(r.status===401)saveDevice(null);
  }catch(e){triedDevice=false}
}

function updateSendAuth(){
  const locked=!!(data&&data.receiveAuth&&!data.receiveOk);
  $("#receive-auth").hidden=!locked;
  if(locked)unlockRemembered();
  $("#send-actions").hidden=locked;
  if(locked)setTimeout(()=>$("#authcode").focus(),0);
}

async function unlockSending(){
  const input=$("#authcode"),error=$("#autherr"),button=$("#authbtn");
  const code=input.value.toUpperCase().replace(/[^A-Z0-9]/g,"");
  if(code.length!==8){error.textContent="Enter the full eight-character code.";return}
  button.setAttribute("aria-disabled","true");error.textContent="";
  try{
    const remember=$("#remember").checked;
    const r=await fetch(remember?"auth?remember=1":"auth",{method:"POST",headers:{"Content-Type":"text/plain;charset=utf-8"},body:code});
    if(r.status===200){try{saveDevice(await r.json())}catch(e){}data.receiveOk=true;input.value="";updateSendAuth();return}
    if(r.status===204){data.receiveOk=true;input.value="";updateSendAuth();return}
    error.textContent=r.status===429?"Too many attempts. Scan the PocketDrop QR code again.":"That code didn't match.";
    input.select();
  }catch(e){error.textContent="Couldn't reach the computer."}
  finally{button.removeAttribute("aria-disabled")}
}

function draw(it){
  if(!it.el){
    it.el=document.createElement("div");
    it.el.innerHTML=`<div class="th">${ic(it.text?"text":kindIcon(it.name))}</div><div class="meta"><div class="nm"></div><div class="dt"></div><div class="pbar"><i></i></div></div><button class="x" type="button"></button>`;
    it.el.querySelector(".nm").textContent=it.name;
    it.el.querySelector(".x").onclick=()=>act(it);
    $("#queue").appendChild(it.el);
  }
  it.el.className="q"+(it.state==="done"?" done":it.state==="error"?" err":"");
  const dt=it.el.querySelector(".dt"),bar=it.el.querySelector(".pbar i"),x=it.el.querySelector(".x");
  const done=Math.max(it.sent,it.live);
  if(it.state==="wait")dt.textContent=it.text?"Waiting…":`Waiting · ${fmt(it.size)}`;
  else if(it.state==="up"){dt.textContent=it.text?"Sending…":`${fmt(done)} of ${fmt(it.size)}${it.speed?" · "+fmt(it.speed)+"/s":""}`;bar.style.width=(it.size?done/it.size*100:100)+"%"}
  else if(it.state==="done"){dt.textContent=it.text?"Sent":`Sent${it.saved&&it.saved!==it.name?" as "+it.saved:""} · ${fmt(it.size)}`;bar.style.width="100%"}
  else if(it.state==="error"){dt.textContent=it.err||"Couldn't send"}
  x.innerHTML=ic(it.state==="error"?"retry":it.state==="done"?"check":"x");
  x.setAttribute("aria-label",it.state==="error"?"Retry":it.state==="done"?"Sent":"Cancel");
}

function act(it){
  if(it.state==="error"){if(ended)return;it.state="wait";it.tries=0;it.err=null;draw(it);pump()}
  else if(it.state==="done"){it.el.remove();queue.splice(queue.indexOf(it),1)}
  else{
    it.state="cancel";
    if(it.xhr)it.xhr.abort();
    if(it.id)fetch(`up/${it.id}`,{method:"DELETE"}).catch(()=>{});
    it.el.remove();queue.splice(queue.indexOf(it),1);
  }
}

async function holdWake(){try{if(!wake&&navigator.wakeLock)wake=await navigator.wakeLock.request("screen")}catch(e){}}
function releaseWake(){if(wake){wake.release().catch(()=>{});wake=null}}

async function pump(){
  if(busy||ended)return;
  const it=queue.find(q=>q.state==="wait");
  if(!it){releaseWake();return}
  busy=true;holdWake();
  it.state="up";draw(it);
  try{it.text?await sendText(it):await upload(it)}
  catch(e){if(it.state!=="cancel"){it.state="error";it.err=e.message||"Couldn't send"}}
  if(it.state!=="cancel")draw(it);
  busy=false;pump();
}

function fail(status){
  if(status===401){if(data){data.receiveOk=false;updateSendAuth()}return new Error("Enter the new receive code, then tap retry.")}
  if(status===429)return new Error("Too many receive-code attempts. Scan the QR code again.");
  if(status===507)return new Error("Your computer is out of disk space");
  if(status===404)return new Error("This upload expired. Tap retry.");
  if(status===413)return new Error("Too large to send as text");
  return new Error("Couldn't save on your computer");
}

const crcTab=(()=>{const t=new Uint32Array(256);for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;t[n]=c>>>0}return t})();
async function crcOf(blob){
  const b=new Uint8Array(await blob.arrayBuffer());let c=0xffffffff;
  for(let i=0;i<b.length;i++)c=crcTab[(c^b[i])&255]^(c>>>8);
  return (c^0xffffffff)>>>0;
}
function gf2(mat,vec){let sum=0,i=0;while(vec){if(vec&1)sum^=mat[i];vec>>>=1;i++}return sum>>>0}
function gf2sq(sq,mat){for(let n=0;n<32;n++)sq[n]=gf2(mat,mat[n])}
function crcJoin(a,b,len){
  if(len<=0)return a;
  const odd=new Array(32),even=new Array(32);odd[0]=0xedb88320;let row=1;
  for(let n=1;n<32;n++){odd[n]=row;row<<=1}
  gf2sq(even,odd);gf2sq(odd,even);
  do{
    gf2sq(even,odd);if(len&1)a=gf2(even,a);len=Math.floor(len/2);if(!len)break;
    gf2sq(odd,even);if(len&1)a=gf2(odd,a);len=Math.floor(len/2);
  }while(len);
  return (a^b)>>>0;
}
const hex8=n=>n.toString(16).padStart(8,"0");

async function upload(it){
  if(data&&data.receiveAuth&&!data.receiveOk)throw fail(401);
  if(!it.id){
    const r=await fetch(`up?name=${enc(it.name)}&size=${it.size}`,{method:"POST"});
    if(!r.ok)throw fail(r.status);
    const j=await r.json();
    it.id=j.id;it.sent=j.received||0;it.acc=0;it.accAt=0;it.bad=0;
    if(j.done){it.state="done";it.saved=j.name;return}
  }
  const chunk=(data&&data.chunk)||8388608;
  while(it.sent<it.size){
    if(it.state==="cancel")return;
    let res;
    const slice=it.f.slice(it.sent,Math.min(it.size,it.sent+chunk)),base=it.sent,crc=await crcOf(slice);
    if(it.state==="cancel")return;
    // The whole-file CRC is only known while chunks go out in order from the start.
    const whole=it.accAt===base?crcJoin(it.acc,crc,slice.size):null;
    try{res=await put(it,slice,crc,base+slice.size>=it.size&&whole!==null?whole:null)}
    catch(e){
      if(it.state==="cancel")return;
      if(++it.tries>8)throw new Error("Connection lost. Tap retry.");
      $("#off").hidden=false;
      await sleep(Math.min(15000,1000*2**(it.tries-1)));
      try{
        const s=await fetch(`up/${it.id}`,{cache:"no-store"});
        if(s.status===404){it.id=null;it.sent=0;throw fail(404)}
        if(s.ok){it.sent=(await s.json()).received;$("#off").hidden=true}
      }catch(err){if(err.message&&err.message.startsWith("This upload"))throw err}
      continue;
    }
    it.tries=0;
    if(res.status===409){it.sent=res.body.received;continue}
    if(res.status===422){
      if(res.body.error==="file_checksum"){it.id=null;it.sent=0;throw new Error("The file was damaged on the way. Tap retry.")}
      if(++it.bad>8)throw new Error("Chunks keep arriving damaged. Check your connection and tap retry.");
      it.sent=res.body.received;continue;
    }
    it.bad=0;
    if(res.status!==200){if(res.status===404){it.id=null;it.sent=0}throw fail(res.status)}
    it.sent=res.body.received;
    if(it.accAt===base&&it.sent===base+slice.size){it.acc=whole;it.accAt=it.sent}else it.accAt=-1;
    if(res.body.done){it.state="done";it.saved=res.body.name;return}
  }
}

function put(it,blob,crc,fcrc){
  return new Promise((ok,no)=>{
    const x=new XMLHttpRequest(),base=it.sent;
    it.xhr=x;
    x.open("PUT",`up/${it.id}?offset=${base}&crc=${hex8(crc)}${fcrc===null?"":"&fcrc="+hex8(fcrc)}`);
    x.upload.onprogress=e=>{
      it.live=base+e.loaded;
      const now=performance.now();
      if(!it.st)[it.st,it.sb]=[now,it.live];
      else if(now-it.st>=800){it.speed=(it.live-it.sb)*1000/(now-it.st);[it.st,it.sb]=[now,it.live]}
      draw(it);
    };
    x.onload=()=>{let b={};try{b=JSON.parse(x.responseText)}catch(e){}it.xhr=null;ok({status:x.status,body:b})};
    x.onerror=x.ontimeout=()=>{it.xhr=null;no(new Error("network"))};
    x.onabort=()=>{it.xhr=null;no(new Error("abort"))};
    x.send(blob);
  });
}

async function sendText(it){
  if(data&&data.receiveAuth&&!data.receiveOk)throw fail(401);
  const r=await fetch("text",{method:"POST",headers:{"Content-Type":"text/plain;charset=utf-8"},body:it.body});
  if(r.status!==204)throw fail(r.status);
  it.state="done";
}

$("#pick").addEventListener("change",e=>{addFiles(e.target.files);e.target.value=""});
$("#authbtn").addEventListener("click",unlockSending);
$("#authcode").addEventListener("keydown",e=>{if(e.key==="Enter")unlockSending()});
$("#notebtn").addEventListener("click",()=>{$("#note").hidden=false;$("#notetext").focus()});
$("#notecancel").addEventListener("click",()=>{$("#note").hidden=true});
$("#notesend").addEventListener("click",()=>{
  const t=$("#notetext").value;
  if(!t.trim())return;
  const line=t.trim().split("\n")[0];
  const it={text:true,body:t,name:line.length>60?line.slice(0,60)+"…":line,size:new Blob([t]).size,sent:0,live:0,state:"wait",tries:0};
  queue.push(it);draw(it);
  $("#notetext").value="";$("#note").hidden=true;
  pump();
});
addEventListener("dragover",e=>{if(e.dataTransfer&&Array.from(e.dataTransfer.types).includes("Files")){e.preventDefault();document.body.classList.add("dragging")}});
addEventListener("dragleave",e=>{if(!e.relatedTarget)document.body.classList.remove("dragging")});
addEventListener("drop",e=>{document.body.classList.remove("dragging");if(e.dataTransfer&&e.dataTransfer.files.length){e.preventDefault();addFiles(e.dataTransfer.files)}});
addEventListener("beforeunload",e=>{if(queue.some(q=>q.state==="up"||q.state==="wait")){e.preventDefault();e.returnValue=""}});
document.addEventListener("visibilitychange",()=>{if(!document.hidden){poll();if(busy)holdWake()}});
poll();
</script></body></html>
)PD");
    return page;
}
