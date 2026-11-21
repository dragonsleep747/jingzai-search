import fs from 'node:fs';
const [port,out]=process.argv.slice(2);
const pages=await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const page=pages.find(p=>p.type==='page');
const ws=new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
let id=0;const pending=new Map();const exceptions=[];
ws.addEventListener('message',event=>{
 const message=JSON.parse(event.data);
 if(message.method==='Runtime.exceptionThrown')exceptions.push(message.params.exceptionDetails.text);
 if(pending.has(message.id)){
  const {resolve,reject,timer}=pending.get(message.id);clearTimeout(timer);pending.delete(message.id);
  message.error?reject(new Error(JSON.stringify(message.error))):resolve(message.result);
 }
});
function send(method,params={}){
 return new Promise((resolve,reject)=>{
  const key=++id;const timer=setTimeout(()=>{pending.delete(key);reject(new Error('CDP timeout '+method));},5000);
  pending.set(key,{resolve,reject,timer});ws.send(JSON.stringify({id:key,method,params}));
 });
}
async function evaluate(expression){
 const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
 if(result.exceptionDetails)throw new Error(JSON.stringify(result.exceptionDetails));
 return result.result.value;
}
const checks=[];
try{
 await send('Page.enable');await send('Runtime.enable');
 for(let i=0;i<30;i++){
  if(await evaluate('document.readyState === "complete" && !!document.querySelector(".node")'))break;
  await new Promise(resolve=>setTimeout(resolve,100));
 }
 const panel=await evaluate(`(()=>{
  document.querySelector('.node').dispatchEvent(new MouseEvent('click',{bubbles:true}));
  const panel=document.getElementById('panel');
  return {text:panel.textContent,links:[...panel.querySelectorAll('a')].map(a=>a.href)};
 })()`);
 if(!panel.text.includes('状态：')||!panel.links.some(x=>x.startsWith('file:'))||!panel.links.some(x=>x.startsWith('jingzai:')))throw new Error('missing node actions');
 checks.push('节点详情包含状态、打开原文件和返回软件入口');
 await evaluate("document.getElementById('btnForce').click()");
 await new Promise(resolve=>setTimeout(resolve,450));
 if(!await evaluate("document.getElementById('btnForce').classList.contains('on')"))throw new Error('force layout unavailable');
 checks.push('力导向布局可切换');
 await evaluate("document.getElementById('btnLayer').click()");
 await new Promise(resolve=>setTimeout(resolve,450));
 await evaluate("document.querySelector('.node').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))");
 if(!await evaluate("!document.getElementById('btnExitFocus').disabled"))throw new Error('focus unavailable');
 checks.push('双击聚焦及退出聚焦可用');
 await evaluate("document.getElementById('btnExitFocus').click();document.querySelector('.catchip[data-cat]:not([data-cat=\"\"])').click()");
 if(!await evaluate("!!document.querySelector('.catchip.on:not([data-cat=\"\"])')"))throw new Error('category filter unavailable');
 checks.push('分类筛选可用');
 await evaluate("document.querySelector('.catchip[data-cat=\"\"]').click();document.querySelector('.node').dispatchEvent(new MouseEvent('click',{bubbles:true}))");
 const picture=await send('Page.captureScreenshot',{format:'png'});
 fs.writeFileSync(out+'/graph-interactions.png',Buffer.from(picture.data,'base64'));
 if(exceptions.length)throw new Error(exceptions.join('; '));
 fs.writeFileSync(out+'/result.json',JSON.stringify({ok:true,checks,exceptions},null,2));
 console.log(JSON.stringify({ok:true,checks}));
}catch(error){
 fs.writeFileSync(out+'/result.json',JSON.stringify({ok:false,checks,error:String(error),exceptions},null,2));
 console.error(String(error));process.exitCode=1;
}finally{
 await send('Browser.close').catch(()=>{});ws.close();
}
