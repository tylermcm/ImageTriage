const {chromium}=require('playwright');
(async()=>{const [,, url,out,x,y]=process.argv;
const b=await chromium.launch();const p=await b.newPage({viewport:{width:1440,height:900}});
await p.goto(url);await p.waitForTimeout(1200);
await p.mouse.move(+x,+y);await p.waitForTimeout(1200);
await p.screenshot({path:out});await b.close();})();
