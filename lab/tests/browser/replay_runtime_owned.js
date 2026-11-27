// Offline browser refresh/disconnect regression; no robot or motor interfaces.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('lab/src/onboardmimic_lab/static/native-replay.js','utf8');
let state='running',phase='playing',online=true;const commands=[];
function page(){
 const nodes=new Map(),timers=[],events={};
 const node=id=>{if(!nodes.has(id))nodes.set(id,{id,dataset:{},setAttribute(){}});return nodes.get(id)};
 const snapshot=()=>({configured:true,connected:true,available:true,controller_running:true,owned:true,can_stop:true,browser_lease_required:false,status:{plan_id:'plan',run_id:12,state,entry_phase:phase,reason:'',stop_ack:false}});
 const ctx={console,performance,crypto:{randomUUID:()=>Math.random().toString()},Event:class{},AbortSignal,setTimeout:f=>timers.push(f),
 document:{getElementById:node,dispatchEvent(){},addEventListener(){}},
 window:{addEventListener:(n,f)=>events[n]=f,labReplaySelection:()=>({verified:true,row:{test_fixture:false}}),labLanguage:{text:zh=>zh,showError:(el,e)=>el.textContent=e}},
 fetch:async(url,opts)=>{
  if(!online)throw Error('offline');
  if(url==='/api/session')return {ok:true,json:async()=>({token:'test'})};
  if(opts?.method==='POST'){const c=JSON.parse(opts.body);commands.push(c);assert.equal(c.action,'real_stop');state='running';}
  return {ok:true,json:async()=>snapshot()};
 }};
 vm.runInNewContext(source,ctx);return {node,timers,events};
}
(async()=>{
 let p=page();await new Promise(setImmediate);
 assert.equal(p.node('replay-stop').disabled,false);
 for(let i=0;i<4;i++){await p.timers.shift()();await new Promise(setImmediate);}
 assert.deepEqual(commands,[]);
 for(const [next,entry,label] of [['loading','warming','正在准备回放…'],['starting','transitioning','正在准备回放…'],['running','playing','正在回放…'],['running','returning','正在返回 Planner…']]){
  state=next;phase=entry;await p.timers.shift()();await new Promise(setImmediate);
  assert.equal(p.node('replay-real').textContent,label);
  assert.equal(!!p.node('replay-real').disabled,true);
  if(next==='starting')assert.equal(p.node('replay-real-hint').hidden,false);
 }
 assert.deepEqual(commands,[]);
 state='running';phase='playing';
 p.events.pagehide(); // Closing a page never sends stop or renewal.
 online=false;await p.timers.shift()();await new Promise(setImmediate);
 assert.deepEqual(commands,[]);assert.equal(state,'running');
 online=true;p=page();await new Promise(setImmediate); // New browser owner.
 assert.equal(p.node('replay-stop').disabled,false);
 await p.node('replay-stop').onclick();await new Promise(setImmediate);
 assert.equal(commands.length,1);assert.equal(commands[0].run_id,12);
 console.log('browser absence/refresh only query; explicit stop after reconnect: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
