const {chromium}=require('playwright');
(async()=>{const [,, url,out,w,h]=process.argv;
const b=await chromium.launch();const p=await b.newPage({viewport:{width:+w||1440,height:+h||900}});
p.on('pageerror',e=>console.log('ERR',e.message));
await p.goto(url);await p.waitForTimeout(1500);
await p.screenshot({path:out});await b.close();})();
