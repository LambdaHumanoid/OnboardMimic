// Exercise production collection rendering with synthetic status, no robot access.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),listeners={};
const node=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},textContent:'',setAttribute(){},replaceChildren(){},append(){},appendChild(){}});return nodes.get(id)};
let language='zh',timer,posts=0;
const status={enabled:true,state:'recording',tasks:[],pending:[],counts:{},camera:{ready:true},discontinuities:{count:1,failed:0,recent:[{state:'saving',previous_index:46864,index:46866,detected_realtime_ns:1e18}]}};
const ctx={console,Date,AbortSignal,CustomEvent:class{},setTimeout:f=>{timer=f;},
 document:{getElementById:node,createElement:()=>node(Symbol()),documentElement:{lang:'zh-CN'},dispatchEvent(){},addEventListener:(k,v)=>listeners[k]=v},
 window:{labLanguage:{text:(zh,en)=>language==='zh'?zh:en,error:x=>x,showError:(el,x)=>el.textContent=x}},
 fetch:async(url,opts)=>{if(opts?.method==='POST')posts++;return {ok:true,json:async()=>url==='/api/collection'?status:{lab:{record_ready:true},telemetry:{fresh:true,valid:true}}};}};
const code=fs.readFileSync('lab/src/onboardmimic_lab/static/collection.js','utf8');
vm.runInNewContext(code.slice(code.indexOf('(()=>{',code.indexOf('})();')+4)),ctx);
(async()=>{
 await new Promise(setImmediate);
 assert.match(node('collection-discontinuities').textContent,/正在保存证据/);
 assert.equal(node('collection-discontinuities').hidden,false);
 status.discontinuities.recent[0].state='saved';await timer();
 assert.match(node('collection-discontinuities').textContent,/采集证据已保存/);
 language='en';listeners['lab-language-change']();
 assert.match(node('collection-discontinuities').textContent,/Capture evidence saved/);
 status.discontinuities.failed=1;status.discontinuities.recent[0].state='failed';await timer();
 assert.match(node('collection-discontinuities').textContent,/Evidence save failed/);
 assert.match(node('collection-discontinuities').textContent,/1 evidence saves failed/);
 status.state='failed';status.discontinuities.recent[0]={kind:'capture_error',state:'saved',previous_index:2369,index:2369,detected_realtime_ns:1e18};await timer();
 assert.match(node('collection-discontinuities').textContent,/last received frame 2369/);
 assert.doesNotMatch(node('collection-discontinuities').textContent,/→/);
 language='zh';listeners['lab-language-change']();
 assert.match(node('collection-discontinuities').textContent,/采集已中止/);
 assert.doesNotMatch(node('collection-discontinuities').textContent,/数据断点/);
 status.discontinuities={count:0,recent:[]};await timer();
 assert.equal(node('collection-discontinuities').hidden,true);
 assert.equal(posts,0);
 console.log('capture gap warning, persistence states, bilingual rendering and reset: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
