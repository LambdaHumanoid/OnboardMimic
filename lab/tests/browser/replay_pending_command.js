// Lost mutation replies are pending, not failures or permission to resubmit.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},setAttribute(){}});return nodes.get(id)};
let timer,pending='start',state='loaded',reason='',language='zh',posts=0;
const events={};
const snapshot=()=>({configured:true,connected:!pending,available:true,controller_running:true,
 pending_action:pending,owned:!pending,can_stop:!pending,status:{plan_id:'p',run_id:pending?0:11,state,reason},error:''});
const ctx={console,crypto:{randomUUID:()=> 'owner'},Event:class{},AbortSignal,setTimeout:f=>timer=f,
 document:{getElementById:node,dispatchEvent(){},addEventListener:(n,f)=>events[n]=f},
 window:{addEventListener(){},labReplaySelection:()=>({verified:{plan_sha256:'p'},row:{test_fixture:false}}),
 labLanguage:{text:(zh,en)=>language==='zh'?zh:en,showError:(el,e)=>el.textContent=e}},
 fetch:async(url,opts)=>{if(opts?.method==='POST')posts++;return {ok:true,json:async()=>snapshot()};}};
vm.runInNewContext(fs.readFileSync('lab/src/onboardmimic_lab/static/native-replay.js','utf8'),ctx);
(async()=>{
 await new Promise(setImmediate);
 assert.equal(node('replay-real').textContent,'正在确认启动结果…');
 assert.equal(node('replay-real').disabled,true);
 assert.equal(node('replay-real-hint').hidden,false);
 assert.equal(node('replay-real-reason').textContent,'');
 await node('replay-real').onclick();assert.equal(posts,0);
 language='en';events['lab-language-change']();
 assert.equal(node('replay-real').textContent,'Confirming start result…');
 pending=null;state='running';await timer();
 assert.equal(node('replay-real').textContent,'Replaying…');
 assert.equal(node('replay-real-reason').textContent,'');
 pending='stop';await timer();assert.equal(node('replay-real').textContent,'Confirming stop result…');
 pending=null;state='failed';reason='consumption_timeout';await timer();
 assert.equal(node('replay-real-reason').textContent,'consumption_timeout');
 assert.equal(posts,0);
 console.log('pending command confirmation, bilingual UI, no retries, real failures: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
