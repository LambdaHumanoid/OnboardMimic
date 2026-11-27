// Rotate the server token between user operations; each command executes once.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 for(const file of ['collection.js','replay.js','native-replay.js']){
  const src=fs.readFileSync('lab/src/onboardmimic_lab/static/'+file,'utf8');
  const native=file==='native-replay.js';
  const begin=src.indexOf(native?' async function post(':' async function command(');
  const end=src.indexOf(native?' async function query(':file==='replay.js'?" $('replay-episode').onchange":" document.addEventListener('lab-language-change'",begin);
  let token='one',sessions=0,posts=0,failSession=false;
  const ctx={AbortSignal,console,fetch:async(url,opts)=>{
   if(url==='/api/session'){sessions++;assert.equal(opts.cache,'no-store');if(failSession)throw Error('offline');return {ok:true,json:async()=>({token})};}
   posts++;assert.equal(opts.headers['X-Control-Token'],token);
   return {ok:true,json:async()=>({state:'idle',segments:[],verified:false})};
  }};
  vm.createContext(ctx);
  vm.runInContext(`let token='obsolete',busy=false,busyAction='',generation=0,requestError='',errorScope='',latest=null,revision=0,selected='episode',segment=0,error='',data={},segments=[],verified=null,owner='fixture',value={status:{plan_id:'plan',run_id:1}};
   const t=x=>x,scope=()=>'',render=()=>{},taskFields=()=>{},$=()=>({}),window={labLanguage:{showError(){}}};
   ${src.slice(begin,end)}
   globalThis.invoke=()=>${native?"post('real_load')":"command('inspect')"};`,ctx);
  await ctx.invoke();token='two';await ctx.invoke();
  assert.equal(sessions,2,file);assert.equal(posts,2,file);
  failSession=true;await ctx.invoke().catch(()=>{});assert.equal(posts,2,'No POST after failed session fetch');
 }
 console.log('fresh sessions across server restart: collection, inspection, native replay; no duplicate POST: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
