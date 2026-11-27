const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();function node(id){if(!nodes.has(id))nodes.set(id,{dataset:{},style:{},classList:{toggle(){}},setAttribute(){},addEventListener(){},append(){},querySelector:()=>node(Symbol()),textContent:''});return nodes.get(id)}
let timer,failed=true,status={state:'active',epoch:'one',pids:[1],capabilities:[],telemetry:{}};
const ctx={console,Date,Math,crypto:{randomUUID:()=> 'test'},AbortSignal,setInterval(){},setTimeout:f=>timer=f,
 document:{body:node('body'),createElement:()=>node(Symbol()),getElementById:node,querySelectorAll:()=>[],addEventListener(){}},
 window:{addEventListener(){},onboardMimicPlatform:{update(){}}},
 fetch:async()=>{if(failed)throw Error('network');return {ok:true,json:async()=>status}}};
vm.runInNewContext(fs.readFileSync('onboardmimic/console/static/app.js','utf8'),ctx);
(async()=>{
 await new Promise(setImmediate);assert.match(node('message').textContent,/连接中断/);
 failed=false;await timer();assert.equal(node('message').textContent,'');
 status={...status,workflow_error:'controller failed'};await timer();assert.equal(node('message').textContent,'controller failed');
 await timer();assert.equal(node('message').textContent,'controller failed');
 status={...status,workflow_error:''};await timer();assert.equal(node('message').textContent,'');
 console.log('console connection recovery and authoritative error removal: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
