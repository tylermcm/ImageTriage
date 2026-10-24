const {chromium}=require('playwright');
(async()=>{const [,,src,out,w,h,dpr]=process.argv;const b=await chromium.launch();const p=await b.newPage({viewport:{width:+w,height:+h},deviceScaleFactor:+(dpr||1)});
p.on('console',m=>console.log('console:',m.text()));p.on('pageerror',e=>console.log('err:',e.message));
await p.goto('file://'+src,{waitUntil:'networkidle'});await p.evaluate(()=>document.fonts.ready);await p.waitForTimeout(500);await p.screenshot({path:out});await b.close()})();
