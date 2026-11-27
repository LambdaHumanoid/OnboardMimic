const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},textContent:'',value:'',setAttribute(){},replaceChildren(){},append(){},appendChild(){}});return nodes.get(id)};
let timer,fail=true,commandFailure=false,lateReject,delayPoll=false;
let status={enabled:true,state:'idle',tasks:[],pending:[],counts:{},camera:{ready:true}};
const ctx={console,Date,AbortSignal,CustomEvent:class{},setTimeout:f=>timer=f,
 document:{getElementById:node,createElement:()=>node(Symbol()),documentElement:{lang:'zh-CN'},dispatchEvent(){},addEventListener(){}},
 window:{labLanguage:{text:(zh,en)=>zh,error:x=>x,showError:(el,x)=>el.textContent=x||''}},
 fetch:async(url,opts)=>{
  if(url==='/api/session')return {ok:true,json:async()=>({token:'test'})};
  if(opts?.method==='POST')return {ok:!commandFailure,json:async()=>commandFailure?{error:'permission denied'}:status};
  if(url==='/api/collection'&&delayPoll)return new Promise((resolve,reject)=>lateReject=reject);
  if(url==='/api/collection'&&fail)throw Error('Fetch is aborted');
  return {ok:true,json:async()=>url==='/api/collection'?status:{lab:{record_ready:true},telemetry:{fresh:true,valid:true}}};
 }};
const code=fs.readFileSync('lab/src/onboardmimic_lab/static/collection.js','utf8');
vm.runInNewContext(code.slice(code.indexOf('(()=>{',code.indexOf('})();')+4)),ctx);
(async()=>{
 await new Promise(setImmediate);assert.equal(node('collection-error').textContent,'Fetch is aborted');
 fail=false;await timer();assert.equal(node('collection-error').textContent,'');
 commandFailure=true;await node('episode-stop').onclick();await timer();
 assert.equal(node('collection-error').textContent,'permission denied'); // successful GET is not successful POST
 status={...status,state:'recording',episode_id:'new'};await timer();assert.equal(node('collection-error').textContent,'');
 status={...status,state:'failed',error:'capture source lost'};await timer();assert.equal(node('collection-error').textContent,'capture source lost');
 await timer();assert.equal(node('collection-error').textContent,'capture source lost');
 status={...status,state:'saved'};await timer();assert.equal(node('collection-error').textContent,'');
 delayPoll=true;const pending=timer();await new Promise(setImmediate);
 commandFailure=true;await node('episode-stop').onclick();lateReject(Error('stale poll timeout'));await pending;
 assert.equal(node('collection-error').textContent,'permission denied');
 console.log('collection polling recovery, operation scope and live failure: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
