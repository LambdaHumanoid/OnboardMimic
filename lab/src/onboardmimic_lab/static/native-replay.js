(()=>{
 const $=id=>document.getElementById(id),t=window.labLanguage.text;
 const owner=crypto.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint32Array(4)),x=>x.toString(16).padStart(8,'0')).join('');
 let value={configured:false},busy=false,token='',error='',pollError='',errorScope='',querying=false,generation=0,pendingCommand=null;
 let connectionFailures=0;
 function connectionLost(message){
  connectionFailures++;value={...value,connected:false,can_start:false};
  pollError=connectionFailures>=3?'replay_status_connection_lost':'';
 }
 const labels={empty:['未加载','No plan loaded'],loaded:['已加载，尚未开始','Loaded; not started'],loading:['正在装载到执行器','Loading into executor'],starting:['预热与起始过渡','Warming up and transitioning'],running:['正在回放','Replaying'],stopping:['保护退出中，等待停止确认','Protective exit; awaiting stop confirmation'],completed:['回放完成，停止已确认','Replay completed; stop confirmed'],stopped:['回放停止，停止已确认','Replay stopped; stop confirmed'],failed:['回放失败','Replay failed']};
 const pendingAction=()=>value.pending_action||pendingCommand?.action;
 window.labPhysicalActive=()=>busy||!!pendingAction()||!!(value.status&&['loading','starting','running','stopping'].includes(value.status.state));
 const scope=v=>JSON.stringify([v.controller_running,v.status?.plan_id,v.status?.run_id,v.status?.state]);
 function reconcile(next){
  const p=pendingCommand,s=next.status;
  if(!p||!next.connected||!next.owned||s?.plan_id!==p.plan_id)return;
  const terminal=['completed','stopped','failed'].includes(s.state)&&(s.stop_ack===true||s.completion_ack===true);
  const started=p.action==='start'&&Number.isSafeInteger(s.run_id)&&s.run_id>0&&s.run_id!==p.run_id&&
   (['loading','starting','running','stopping'].includes(s.state)||terminal);
  const stopped=p.action==='stop'&&s.run_id===p.run_id&&
   (s.state==='stopping'||terminal||['returning','settling_planner'].includes(s.entry_phase));
  if(started||stopped){pendingCommand=null;error='';pollError='';}
 }
 function render(){
  const selection=window.labReplaySelection(),s=value.status,pending=pendingAction(),active=s&&['loading','starting','running','stopping'].includes(s.state);
  const returning=active&&['returning','settling_planner'].includes(s.entry_phase);
  const preparing=busy||selection.busy||['loading','starting'].includes(s?.state)||
   (active&&['warming','transitioning','aligning'].includes(s.entry_phase));
  const pendingText=pending==='stop'?t('正在确认停止结果…','Confirming stop result…'):t('正在确认启动结果…','Confirming start result…');
  const label=pending?pendingText:returning?t('正在返回 Planner…','Returning to Planner…'):
   preparing?t('正在准备回放…','Preparing replay…'):
   s?.state==='stopping'?t('正在停止回放…','Stopping replay…'):
   active?t('正在回放…','Replaying…'):t('真机回放','Physical replay');
  const load=$('replay-real');if(load.textContent!==label)load.textContent=label;
  load.setAttribute('aria-busy',String(!!(pending||preparing||returning)));

  load.disabled=!!pending||busy||active||selection.busy||window.labSimulationActive?.()||!value.connected||!value.available||value.controller_running!==true||!selection.verified||selection.row?.test_fixture!==false;
  $('replay-real-hint').textContent=!value.configured?t('尚未配置 OM 真机回放连接。','OM physical replay connection is not configured.'):!value.connected?t('无法确认 SONIC 状态，暂不能真机回放。','SONIC status is unavailable. Physical replay is disabled.'):!value.available?t('OM 尚未启用新版回放接口。','OM replay is not enabled.'):value.controller_running!==true?(value.controller_status==='not_ready'?t('SONIC 尚未就绪，请在 OM 中确认启动状态。','SONIC is not ready. Check its startup status in OM.'):t('正在确认 SONIC 运行状态，请稍候…','Checking SONIC runtime status. Please wait…')):t('请保持 Planner 模式并站稳后开始真机回放。','Stand still in Planner mode before physical replay.');
  if(value.connected&&active)$('replay-real-hint').textContent=t('回放执行中，请等待当前阶段完成。','Replay is active. Wait for the current phase to complete.');
  if(busy)$('replay-real-hint').textContent=t('正在准备真机回放，请稍候…','Preparing physical replay. Please wait…');
  $('replay-real-hint').dataset.attention=(busy||(value.connected&&active))?'busy':(!value.configured||!value.connected||!value.available||value.controller_running!==true?'blocked':'');
  $('replay-real-hint').hidden=!!(value.connected&&active&&!preparing&&!returning);
  if(selection.busy&&!busy)$('replay-real-hint').textContent=t('正在读取并准备所选记录，请稍候…','Reading and preparing the selected recording…');
  if(pending){$('replay-real-hint').textContent=pendingText;$('replay-real-hint').hidden=false;$('replay-real-hint').dataset.attention='busy';}
  $('replay-real-safety').hidden=!value.configured;
  $('replay-physical').hidden=!value.configured||(!busy&&(!s||s.state==='empty'));
  $('replay-execution-details').hidden=$('replay-physical').hidden;
  
  $('replay-stop').disabled=busy||!!pending||!value.can_stop;
  document.dispatchEvent(new Event('lab-physical-status'));
  const historical=value.connected&&value.controller_running===true&&s?.stop_ack===true&&['failed','stopped'].includes(s?.state);
  $('replay-real-state').textContent=!value.connected?t('连接或状态未确认；不会自动开始回放。','Connection or status unconfirmed; no automatic start.'):s?(labels[s.state]?t(...labels[s.state]):s.state):t('等待 OM 状态','Waiting for OM status');
  if(historical)$('replay-real-state').textContent=t('SONIC 已重新就绪；上次回放已结束。','SONIC is ready again; the previous replay has ended.');
  if(value.connected&&s?.state==='completed'&&s.completion_ack)$('replay-real-state').textContent=s.reason==='operator_stop'?t('回放已停止，已返回 Planner','Replay stopped; Planner resumed'):t('回放完成，已返回 Planner','Replay completed; Planner resumed');
  const entryLabels={returning:['平滑返回 Planner','Blending back to Planner'],settling_planner:['确认 Planner 接管','Confirming Planner handoff'],warming:['正在预热实时反馈','Warming live feedback'],transitioning:['正在过渡到首帧','Transitioning to the first frame'],aligning:['正在确认首帧过渡稳定','Confirming a settled first-frame transition']};
  if(value.connected&&['starting','running'].includes(s?.state)&&entryLabels[s.entry_phase])$('replay-real-state').textContent=t(...entryLabels[s.entry_phase]);
  $('replay-real-model').textContent=s?`${value.episode||'—'} · ${s.model_validation==='matched_live'?t('实际模型已匹配','Live model matched'):t('等待实际运行实例匹配；开始时再次核验','Awaiting live model match; rechecked at start')}`:'';
  $('replay-real-state').dataset.attention=!value.connected?'blocked':(active?'busy':'');
  $('replay-physical').dataset.state=historical?'ready':!value.connected?'unconfirmed':s?.state==='completed'&&!s.completion_ack?'unconfirmed':s?.state||'empty';
  if(value.connected&&active&&!busy)$('replay-real-hint').textContent=$('replay-real-state').textContent;
  if(pending){$('replay-real-state').textContent=pendingText;$('replay-real-state').dataset.attention='busy';$('replay-real-hint').textContent=pendingText;}
  if(connectionFailures&&!value.connected&&!pending){$('replay-real-state').textContent=t('正在重新确认回放状态…','Rechecking replay status…');$('replay-real-hint').textContent=$('replay-real-state').textContent;$('replay-real-hint').hidden=false;}
  const position=s?.source_frame_position,total=value.summary?.frames;
  $('replay-real-progress').textContent=position==null?t('尚无本次回放的实际提交帧。','No actual submitted frame for this run yet.'):`${t('实际已提交','Actually submitted')}: ${position+1}${total?'/'+total:''} ${t('帧','frames')}`;
  const bar=$('replay-real-bar');bar.max=total||1;bar.value=position==null?0:position+1;bar.hidden=!total;
  $('replay-real-ack').textContent=!s||['empty','loaded','loading','starting','running'].includes(s.state)?'':value.connected&&s.completion_ack===true?t('Planner 接管已确认，SONIC 继续运行。','Planner handoff confirmed; SONIC remains running.'):value.connected&&s.stop_ack===true?t('停止已确认。下次执行前需重新准备控制器。','Stop confirmed. Prepare the controller again before the next run.'):t('停止尚未确认。','Stop is not confirmed.');
  if(historical)$('replay-real-ack').textContent=t('可选择记录并开始新的回放。','Select a recording to start a new replay.');
  const confirmedFailure=['stopping','failed','stopped'].includes(s?.state)&&!['completed','operator_stop'].includes(s?.reason)?s?.reason:'';
  const failureText=confirmedFailure==='runtime_admission_lost'&&s?.detail?confirmedFailure+': '+s.detail:confirmedFailure;
  window.labLanguage.showError($('replay-real-reason'),historical||busy?'':failureText||pollError||(pending?'':error|| (value.connected?value.error:'')||(['completed','operator_stop'].includes(s?.reason)?'':s?.reason)||s?.detail||(!value.owned&&active?t('此执行属于其他网页服务会话，请在 OM 主页面停止。','This execution belongs to another web-service session. Stop it from the OM main page.'):'')));
 }
 async function post(action,extra={},timeout=1500){
  {const r=await fetch('/api/session',{cache:'no-store',signal:AbortSignal.timeout(1500)});if(!r.ok)throw Error(t('会话不可用','Session unavailable'));token=(await r.json()).token;}
  const submitted=['real_start','real_stop'].includes(action)?{action:action.slice(5),plan_id:value.status?.plan_id,run_id:value.status?.run_id}:null;
  try{
   const r=await fetch('/api/replay',{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':token},body:JSON.stringify({action,owner,plan_id:value.status?.plan_id,run_id:value.status?.run_id,...extra}),signal:AbortSignal.timeout(timeout)});
   const result=await r.json();if(!r.ok){if(r.status===403)token='';const e=Error(result.error||t('请求失败','Request failed'));e.rejected=r.status<500;throw e;}return result;
  }catch(e){
   // A missing response cannot prove that a submitted motion command failed.
   // Poll validated plan/run ownership; never submit the command a second time.
   if(submitted&&!e.rejected)e.unconfirmed=submitted;
   throw e;
  }
 }
 async function query(){
  if(querying||busy)return;querying=true;const current=generation;
  try{const r=await fetch('/api/replay/real',{cache:'no-store',signal:AbortSignal.timeout(1500)});if(!r.ok)throw Error(t('OM 回放状态不可用','OM replay status unavailable'));const next=await r.json();if(current===generation&&!busy){if(!next.connected){value=next;connectionLost(next.error);return;}connectionFailures=0;if(next.connected&&errorScope!==scope(next))error='';if(next.connected&&next.status?.plan_id===value.status?.plan_id&&next.status?.run_id===value.status?.run_id&&(next.status?.stop_ack||next.status?.completion_ack)&&['completed','stopped','failed'].includes(next.status.state))error='';reconcile(next);value=next;pollError='';}}
  catch(e){if(current===generation){connectionLost(e.message);}}
  finally{querying=false;render();}
 }
 async function command(action){
  if(busy||pendingAction())return;generation++;const current=generation;busy=true;error='';pollError='';errorScope=scope(value);render();
  try{
   const selection=window.labReplaySelection();
   if(action==='real_start'){
    if(value.controller_running!==true||!value.connected)throw Error(t('SONIC 尚未运行或状态未确认。','SONIC is not running or its status is unconfirmed.'));
    if(!selection.verified||selection.busy||window.labSimulationActive?.())throw Error(t('回放尚未准备完成。','Replay is not ready.'));
    value=await post('real_load',{episode:selection.episode,segment:selection.segment},120000);
    if(current!==generation)return;
    if(!value.can_start||value.status?.plan_id!==selection.verified.plan_sha256)throw Error(t('回放计划未确认，请重新选择记录。','Replay plan is not confirmed. Select the episode again.'));
   }
   if(current!==generation)return;
   value=await post(action);
   if(current!==generation)return;
  }catch(e){
   if(current!==generation)return;
   errorScope=scope(value);pendingCommand=e.unconfirmed||null;error=pendingCommand?'':e.message;
   value={...value,connected:false,can_start:false};
  }
  finally{busy=false;render();query();}
 }
 $('replay-real').onclick=()=>command('real_start');
 $('replay-stop').onclick=()=>{if(value.can_stop)command('real_stop');};
 document.addEventListener('lab-replay-selection',render);document.addEventListener('lab-language-change',render);
 window.addEventListener('pagehide',()=>{generation++;});
 async function poll(){await query();setTimeout(poll,500);}
 render();poll();
})();
