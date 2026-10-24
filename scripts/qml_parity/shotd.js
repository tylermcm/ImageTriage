const {chromium}=require('playwright');
(async()=>{const [,, url,out,w,h,dpr]=process.argv;
const b=await chromium.launch();const p=await b.newPage({viewport:{width:+w,height:+h},deviceScaleFactor:+dpr});
await p.goto(url);await p.waitForTimeout(1500);
await p.screenshot({path:out});await b.close();})();
