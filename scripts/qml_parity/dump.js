const {chromium}=require('playwright');
(async()=>{const [,,url,sel,query]=process.argv;
const b=await chromium.launch();const p=await b.newPage({viewport:{width:+(process.argv[5]||1440),height:+(process.argv[6]||900)}});
await p.goto(url+(query||''));await p.waitForTimeout(1500);
const out=await p.evaluate((sel)=>[...document.querySelectorAll(sel)].filter(e=>!['path','circle','rect','line','polyline'].includes(e.tagName.toLowerCase())).slice(0,120).map(e=>{const r=e.getBoundingClientRect();const c=getComputedStyle(e);
const t=(e.childElementCount?'':e.textContent||'').trim().slice(0,28);
return `${e.tagName.toLowerCase()}.${(e.className&&e.className.baseVal!==undefined?e.className.baseVal:e.className+'').replace(/ /g,'.')} [${r.x.toFixed(1)},${r.y.toFixed(1)} ${r.width.toFixed(1)}x${r.height.toFixed(1)}] ${c.fontSize}/${c.fontWeight} ${c.color.replace(/oklch\(([\d.]+).*/,'L$1')} bg=${c.backgroundColor.replace(/oklch\(([\d.]+).*/,'L$1')} "${t}"`}),sel);
console.log(out.join('\n'));await b.close();})();
