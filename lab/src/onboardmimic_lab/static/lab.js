(()=>{
 const el=document.getElementById('runtime-state'),t=window.labLanguage.text;
 let latest=null,failed=false;
 const states={stopped:['已停止','Stopped'],active:['运行中','Active'],running:['运行中','Running'],starting:['启动中','Starting'],stopping:['停止中','Stopping'],failed:['异常','Failed']};
 function render(){
  if(failed){el.textContent=t('遥操状态暂不可用，请检查 OnboardMimic 网关。','Teleoperation status unavailable. Check the OnboardMimic gateway.');return;}
  if(!latest){el.textContent=t('正在读取遥操状态…','Reading teleoperation status…');return;}
  const s=latest,state=states[s.state]?t(...states[s.state]):s.state||t('未知','Unknown');
  const mode=s.input_mode==='pose'?'Pose':s.input_mode==='planner'?'Planner':s.input_mode||t('未知','Unknown');
  const ready=s.lab?s.lab.record_ready:s.pose_input_ready;
  const readiness=s.lab?(ready?t('执行器数采就绪','Executor ready for recording'):t('等待执行器就绪','Waiting for executor readiness')):(ready?t('Pose 数据就绪','Pose data ready'):t('等待 Pose 数据','Waiting for Pose data'));
  el.textContent=`${t('遥操状态','Teleoperation')}: ${state} · ${mode} · ${readiness}`;
 }
 document.addEventListener('lab-language-change',render);
 async function poll(){try{const r=await fetch('/api/runtime',{cache:'no-store',signal:AbortSignal.timeout(3000)});if(!r.ok)throw Error();latest=await r.json();failed=false;}catch{failed=true;}render();setTimeout(poll,1500);}
 // Readiness acknowledges rendering only, never recording or robot readiness.
 const allowed=(document.body.dataset.embedOrigins||'').split(' ').filter(Boolean);
 window.addEventListener('message',event=>{
  const m=event.data;
  if(window.parent===window||event.source!==window.parent||!allowed.includes(event.origin))return;
  if(!m||m.type!=='onboardmimic.lab.probe'||m.version!==1||typeof m.nonce!=='string'||m.nonce.length>128)return;
  const panel=document.getElementById('collection-panel');
  if(document.readyState!=='complete'||!window.labCollectionUIReady||!panel)return;
  if([...document.querySelectorAll('link[rel="stylesheet"]')].some(link=>!link.sheet))return;
  event.source.postMessage({type:'onboardmimic.lab.ready',version:1,nonce:m.nonce},event.origin);
 });
 render();poll();
})();
