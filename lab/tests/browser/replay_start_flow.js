// Both replay scripts share the page: list polling must not flash a replay failure.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const flush=()=>new Promise(setImmediate),nodes=new Map(),events={},errors=[],posts=[];
let listFail=false,statusFail=false,heldStart,holdStart=true;
let status={plan_id:'p',run_id:0,state:'loaded',reason:''};
const node=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},value:'',setAttribute(){},getAttribute(){return null},hasAttribute(){return false},replaceChildren(){},append(){},pause(){},load(){},focus(){}});return nodes.get(id)};
const list={tasks:[{id:'task',name:'test',episode_count:1}],episodes:[{id:'episode',task_id:'task',test_fixture:false,export_ready:true}]};
const snapshot=()=>({configured:true,connected:true,available:true,owned:true,controller_running:true,can_start:status.state==='loaded',can_stop:true,status:{...status}});
const response=v=>({ok:true,status:200,json:async()=>v});
const ctx={console,AbortSignal,crypto:{randomUUID:()=> 'owner'},setTimeout(){},Event:class{constructor(type){this.type=type}},
 document:{documentElement:{lang:'zh'},getElementById:node,createElement:()=>node('option'),addEventListener:(n,f)=>(events[n]??=[]).push(f),dispatchEvent:e=>(events[e.type]||[]).forEach(f=>f(e))},
 window:{addEventListener(){},labLanguage:{text:zh=>zh,showError:(n,e)=>{n.textContent=e;errors.push([n,e]);}}},
 fetch:async(url,opts)=>{
  if(url==='/api/session')return response({token:'current'});
  if(opts?.method==='POST'){
   const p=JSON.parse(opts.body);posts.push(p.action);
   if(p.action==='inspect')return response({verified:true,summary:{plan_sha256:'p'},segments:[]});
   if(p.action==='real_load')return response(snapshot());
   if(p.action==='real_start'){status={...status,state:'running',run_id:2};if(holdStart)return new Promise(resolve=>heldStart=()=>resolve(response(snapshot())));return response(snapshot());}
  }
  if(url==='/api/replay'){if(listFail)throw Error('Fetch is aborted');return response(list);}
  if(url==='/api/replay/real'){if(statusFail)throw Error('timed out');return response(snapshot());}
  throw Error(url);
 }};
vm.createContext(ctx);
for(const f of ['replay.js','native-replay.js'])vm.runInContext(fs.readFileSync((process.env.REPLAY_ASSET_DIR||'lab/src/onboardmimic_lab/static')+'/'+f,'utf8'),ctx);
(async()=>{
 await flush();node('tab-replay').onclick();await flush();
 node('replay-task').value='task';node('replay-task').onchange();
 node('replay-episode').value='episode';node('replay-episode').onchange();await flush();
 assert.equal(node('replay-real').disabled,false);
 const start=node('replay-real').onclick();await flush();assert.ok(heldStart);
 listFail=true;node('tab-replay').onclick();await flush();
 assert.equal(node('replay-error').textContent,'');assert.equal(node('replay-real-reason').textContent,'');
 heldStart();await start;await flush();assert.equal(node('replay-real').textContent,'正在回放…');
 assert.ok(errors.every(([,e])=>!e),'No error in any replay error region throughout start');
 node('tab-replay').onclick();await flush();node('tab-replay').onclick();await flush();
 assert.equal(node('replay-error').textContent,'replay_list_connection_lost');
 listFail=false;node('tab-replay').onclick();await flush();assert.equal(node('replay-error').textContent,'');
 assert.deepEqual(posts,['inspect','real_load','real_start']);
 console.log('integrated selection/load/start with concurrent list timeout and recovery, one start only: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
