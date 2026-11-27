// Offline regression: a lost stop/renew response must not mask confirmed failure.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{id,dataset:{},setAttribute(){}});return nodes.get(id)};
let running=false,timer,pollFailure=false;
let status={plan_id:'plan',run_id:12,state:'running',reason:'',stop_ack:false};
const view=()=>({configured:true,connected:true,available:true,controller_running:running,owned:true,can_stop:true,status:{...status}});
const context={console,performance,crypto:{randomUUID:()=> 'owner'},Event:class{},AbortSignal, setTimeout:f=>{timer=f;},
 document:{getElementById:node,dispatchEvent(){},addEventListener(){}},
 window:{addEventListener(){},labReplaySelection:()=>({verified:true,row:{test_fixture:false}}),
 labLanguage:{text:(zh,en)=>zh,showError:(el,error)=>el.textContent=error}},
 fetch:async(url,options)=>{
  if(url==='/api/session')return {ok:true,json:async()=>({token:'fixture'})};
  if(options?.method==='POST'){
   status={...status,state:'failed',reason:'consumption_timeout',stop_ack:true};
   return {ok:false,status:503,json:async()=>({error:'Service or file unavailable'})};
  }
  if(pollFailure)throw Error('Fetch is aborted');
  return {ok:true,json:async()=>view()};
 }};
vm.runInNewContext(fs.readFileSync('lab/src/onboardmimic_lab/static/native-replay.js','utf8'),context);
(async()=>{
 await new Promise(setImmediate);
 pollFailure=true;await timer();assert.equal(node('replay-real-reason').textContent,'');
 assert.equal(node('replay-real-state').textContent,'正在重新确认回放状态…');
 await timer();await timer();assert.match(node('replay-real-reason').textContent,/replay_status_connection_lost/);
 pollFailure=false;await timer();assert.equal(node('replay-real-reason').textContent,'');
 await node('replay-stop').onclick();
 await new Promise(setImmediate);
 assert.equal(node('replay-real-reason').textContent,'consumption_timeout');
 assert.equal(node('replay-real-state').textContent,'回放失败');
 running=true;
 await node('replay-stop').onclick();
 await new Promise(setImmediate);
 assert.equal(node('replay-real-reason').textContent,'');
 assert.match(node('replay-real-state').textContent,/SONIC/);
 console.log('confirmed failure priority and restarted SONIC error clearing: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
