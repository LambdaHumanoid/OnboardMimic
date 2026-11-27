// Lost browser-to-Lab replies must be reconciled without retrying robot commands.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('lab/src/onboardmimic_lab/static/native-replay.js','utf8');
const flush=()=>new Promise(setImmediate);
function page({reply='lost',action='start',sessionLost=false}={}){
 const nodes=new Map(),errors=[],posts=[],events={};let timer,language='zh',waiting=null,hold=false,pollFailure=null;
 const node=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},setAttribute(){}});return nodes.get(id)};
 let status={plan_id:'p',run_id:action==='stop'?11:0,state:action==='stop'?'running':'loaded',reason:'',stop_ack:false};
 let owned=action==='stop';
 const snapshot=()=>({configured:true,connected:true,available:true,controller_running:status.state!=='failed',
  owned,can_start:status.state==='loaded',can_stop:owned,status:{...status},error:''});
 const response=(value,ok=true,code=200)=>({ok,status:code,json:async()=>value});
 const ctx={console,crypto:{randomUUID:()=> 'browser-owner'},Event:class{},AbortSignal,setTimeout:f=>timer=f,
  document:{getElementById:node,dispatchEvent(){},addEventListener:(n,f)=>events[n]=f},
  window:{addEventListener(){},labReplaySelection:()=>({episode:'episode',segment:0,verified:{plan_sha256:'p'},row:{test_fixture:false}}),
   labLanguage:{text:(zh,en)=>language==='zh'?zh:en,showError:(el,e)=>{el.textContent=e;errors.push(e);}}},
  fetch:async(url,opts)=>{
   if(url==='/api/session'){if(sessionLost)throw Error('Session unavailable');return response({token:'fixture'});}
   if(opts?.method==='POST'){
    const command=JSON.parse(opts.body);posts.push(command.action);
    if(command.action==='real_load')return response(snapshot());
    hold=true;
    if(reply==='rejected')return response({error:'OM HTTP 409: Start rejected'},false,400);
    if(reply==='unavailable')return response({error:'Service or file unavailable'},false,503);
    throw Error('Fetch is aborted');
   }
   if(pollFailure){if(pollFailure==='backend')return response({...snapshot(),connected:false,error:'timed out'});throw Error('Fetch is aborted');}
   if(hold)return new Promise(resolve=>waiting=()=>{hold=false;resolve(response(snapshot()));});
   return response(snapshot());
  }};
 vm.runInNewContext(source,ctx);
 return {node,errors,posts,failPoll:kind=>pollFailure=kind,click:async()=>{await node(action==='start'?'replay-real':'replay-stop').onclick();await flush();},
  poll:async()=>{await timer();await flush();},
  state:(update,isOwned=true)=>{status={...status,...update};owned=isOwned;},
  release:async()=>{assert.ok(waiting);waiting();await flush();},
  english:()=>{language='en';events['lab-language-change']();}};
}
(async()=>{
 for(const reply of ['lost','unavailable']){
  const p=page({reply});await flush();await p.click();
  assert.equal(p.node('replay-real').textContent,'正在确认启动结果…');
  assert.equal(p.node('replay-real-reason').textContent,'');
  assert.equal(p.node('replay-real').disabled,true);
  await p.click();assert.deepEqual(p.posts,['real_load','real_start']);
  p.english();assert.equal(p.node('replay-real').textContent,'Confirming start result…');
  // Loaded state alone and unrelated runs cannot confirm a submitted start.
  await p.release();assert.equal(p.node('replay-real').textContent,'Confirming start result…');
  p.state({plan_id:'other',run_id:99,state:'running'});await p.poll();
  assert.equal(p.node('replay-real').textContent,'Confirming start result…');
  p.state({plan_id:'p',run_id:11,state:'running'},false);await p.poll();
  assert.equal(p.node('replay-real').textContent,'Confirming start result…');
  p.state({run_id:11,state:'running'});await p.poll();
  assert.equal(p.node('replay-real').textContent,'Replaying…');
  assert.equal(p.node('replay-real-reason').textContent,'');
  assert.ok(p.errors.every(e=>!e),'No transient failure flash before confirmation');
  assert.deepEqual(p.posts,['real_load','real_start']);
 }
 for(const kind of ['browser','backend']){
  const p=page({action:'stop'});await flush();p.failPoll(kind);
  await p.poll();assert.equal(p.node('replay-real-reason').textContent,'');
  assert.equal(p.node('replay-real-state').textContent,'正在重新确认回放状态…');
  assert.equal(p.node('replay-real').disabled,true);
  await p.poll();await p.poll();assert.match(p.node('replay-real-reason').textContent,/replay_status_connection_lost/);
  p.failPoll(null);await p.poll();assert.equal(p.node('replay-real-reason').textContent,'');
  p.state({state:'failed',reason:'runtime_admission_lost',stop_ack:true});await p.poll();
  p.failPoll(kind);await p.poll();assert.equal(p.node('replay-real-reason').textContent,'runtime_admission_lost');
  assert.deepEqual(p.posts,[]);
 }
 const failed=page();await flush();await failed.click();
 failed.state({run_id:11,state:'failed',reason:'consumption_timeout',stop_ack:true});await failed.release();
 assert.equal(failed.node('replay-real-reason').textContent,'consumption_timeout');
 const rejected=page({reply:'rejected'});await flush();await rejected.click();
 assert.match(rejected.node('replay-real-reason').textContent,/Start rejected/);
 await rejected.release();assert.match(rejected.node('replay-real-reason').textContent,/Start rejected/);
 const noSession=page({sessionLost:true});await flush();await noSession.click();await flush();
 assert.equal(noSession.node('replay-real-reason').textContent,'Session unavailable');
 assert.deepEqual(noSession.posts,[]);
 const stopped=page({action:'stop'});await flush();await stopped.click();
 assert.equal(stopped.node('replay-real').textContent,'正在确认停止结果…');
 await stopped.release();assert.equal(stopped.node('replay-real').textContent,'正在确认停止结果…');
 stopped.state({entry_phase:'returning'});await stopped.poll();
 assert.equal(stopped.node('replay-real').textContent,'正在返回 Planner…');
 assert.ok(stopped.errors.every(e=>!e));assert.deepEqual(stopped.posts,['real_stop']);
 console.log('lost start/stop replies, 503, scoped confirmation, no retries, real failures: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
