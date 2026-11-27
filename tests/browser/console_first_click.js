// A server restart must not require a failed first motor command to refresh CSRF.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();function node(id){if(!nodes.has(id))nodes.set(id,{dataset:{},style:{},classList:{toggle(){}},setAttribute(){},addEventListener(){},append(){},querySelector:()=>node(Symbol()),textContent:''});return nodes.get(id)}
let token='old',gets=0,posts=0,fail=false;
const status={state:'stopped',epoch:'one',pids:[],capabilities:[],telemetry:{},prepare_available:true,protection_executor:{configured:true}};
const ctx={console,Date,Math,crypto:{randomUUID:()=> 'test'},AbortSignal,setInterval(){},setTimeout(){},
 document:{body:node('body'),createElement:()=>node(Symbol()),getElementById:node,querySelectorAll:()=>[],addEventListener(){}},
 window:{addEventListener(){},onboardMimicPlatform:{update(){}}},
 fetch:async(url,options)=>{
  if(url==='/api/session'){gets++;return {ok:true,json:async()=>({token})};}
  if(options?.method==='POST'){
   posts++;assert.equal(options.headers['X-Control-Token'],token);
   assert.equal(JSON.parse(options.body).confirm_experimental_protection,true);
   if(fail)throw Error('reply lost');
  }
  return {ok:true,json:async()=>status};
 }};
vm.runInNewContext(fs.readFileSync('onboardmimic/console/static/app.js','utf8'),ctx);
(async()=>{
 await new Promise(setImmediate);
 await node('prepare').onclick();assert.equal(posts,1);assert.equal(gets,1);
 token='restarted-server';await node('prepare').onclick();assert.equal(posts,2);assert.equal(gets,2);
 assert.doesNotMatch(node('message').textContent,/操作未确认/);
 fail=true;await node('prepare').onclick();assert.equal(posts,3);assert.match(node('message').textContent,/reply lost/);
 console.log('first click after server restart refreshes credentials; lost replies never retry: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
