(()=>{
 const $=id=>document.getElementById(id),t=window.labLanguage.text;
 let data=null,selectedTask='',selected='',segment=null,segments=[],busy=false,verified=null,error='',pollError='',tab='collection',token='',revision=0;
 let listFailures=0;
 window.labReplaySelection=()=>({episode:selected,segment,verified,busy,row:data?.episodes?.find(r=>r.id===selected)});
 window.labSimulationActive=()=>['running','stopping'].includes(data?.job?.state);
 function options(el,rows,value){
  const key=JSON.stringify(rows);
  if(el.dataset.options!==key){el.replaceChildren();for(const [v,label] of rows){const o=document.createElement('option');o.value=v;o.textContent=label;el.append(o);}el.dataset.options=key;}
  el.value=value;
 }
 function activate(name,focus=false){tab=name;for(const key of ['collection','replay']){const active=key===name,b=$('tab-'+key);b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;$(key+'-view').hidden=!active;if(active&&focus)b.focus();}if(name==='replay')refresh();}
 for(const name of ['collection','replay']){$('tab-'+name).onclick=()=>activate(name);$('tab-'+name).onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();activate(e.key==='Home'?'collection':e.key==='End'?'replay':name==='collection'?'replay':'collection',true);}};}
 function render(){
  const tasks=data?.tasks||[],rows=(data?.episodes||[]).filter(r=>r.task_id===selectedTask),row=rows.find(r=>r.id===selected),active=window.labSimulationActive()||window.labPhysicalActive?.();
  options($('replay-task'),[['',t('选择任务…','Choose a task…')],...tasks.map(r=>[r.id,`${r.name||t('未命名任务','Untitled task')} · ${r.episode_count} episodes · ${r.id.slice(-8)}`])],selectedTask);
  $('replay-task').disabled=busy||active||!tasks.length;
  $('replay-task-person').textContent=row?t('本段采集人员：','Episode collector: ')+(row.collector_name||t('未填写','Not specified')):'';
  options($('replay-episode'),[['',t('选择一条数据…','Choose an episode…')],...rows.map(r=>[r.id,`episode${r.episode_index??'—'} · ${r.created_realtime_ns?new Date(r.created_realtime_ns/1e6).toLocaleString(document.documentElement.lang):''} · ${Number(r.duration_s||0).toFixed(1)}s${r.export_ready===false?' · '+t('数据处理未完成','Data not processed'):''}`])],selected);
  $('replay-episode').disabled=busy||active||!rows.length;
  $('replay-empty').textContent=!data?t('正在读取数据列表…','Loading episodes…'):!selectedTask&&tasks.length?t('请选择任务查看 episode。','Choose a task to view episodes.'):!rows.length?t('暂无已保存的数据。','No saved episodes.') : '';
  $('replay-details').hidden=!row;
  if(row){$('replay-frames').textContent=`${row.frames} ${t('帧','frames')}`;$('replay-controller').textContent=row.execution_id;$('replay-fixture').textContent=row.test_fixture?t('测试数据','Test data'):'';$('replay-contract').textContent=row.contract_sha256;}
  $('replay-segment-picker').hidden=segments.length<=1;
  options($('replay-segment'),[['',t('选择时间范围…','Choose a time range…')],...segments.map(s=>[String(s.index),`${s.start_s.toFixed(2)}–${s.end_s.toFixed(2)}s · ${(s.end_s-s.start_s).toFixed(2)}s`])],segment==null?'':String(segment));
  $('replay-segment').disabled=busy||active;
  $('replay-validation-state').textContent=row?.export_ready===false&&row.export?.state==='failed'?t('数据处理失败，原始数据已保留。请在数采页重试数据处理。','Data processing failed; raw data is retained. Retry from the collection tab.'):row?.export_ready===false?t('原始数据已保存，等待数据处理完成后才能回放。可继续采集下一条。','Raw data saved. Replay requires completed data processing; you can record the next episode.'):busy?t('正在准备回放…','Preparing replay…'):verified?'':segments.length>1&&segment==null?t('请选择一段连续的时间范围。','Choose a continuous time range.'):'';
  $('replay-validation-state').dataset.attention=row?.export_ready===false?'blocked':busy?'busy':(!verified&&segments.length>1&&segment==null?'blocked':'');
  const video=$('replay-recorded-video'),url=selected&&row?.export_ready!==false?'/api/replay/preview?episode='+encodeURIComponent(selected):'';
  video.hidden=!row||row.export_ready===false;
  if(!row||row.export_ready===false)$('replay-preview-error').hidden=true;
  if(url&&video.getAttribute('src')!==url){$('replay-preview-error').hidden=true;video.src=url;}
  if(!url&&video.hasAttribute('src')){video.pause();video.removeAttribute('src');video.load();}
  $('replay-video-placeholder').hidden=!!row&&row.export_ready!==false;
  window.labLanguage.showError($('replay-error'),error||pollError||'');
  if(listFailures&&!error&&!pollError&&!busy){$('replay-empty').textContent=t('正在重新获取记录列表…','Refreshing the recording list…');}
  document.dispatchEvent(new Event('lab-replay-selection'));
 }
 async function refresh(){try{const r=await fetch('/api/replay',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error(t('回放服务暂不可用','Replay service unavailable'));const wasWaiting=data?.episodes?.find(e=>e.id===selected)?.export_ready===false;data=await r.json();listFailures=0;pollError='';if(selected&&!data.episodes.some(e=>e.id===selected)){revision++;selected='';verified=null;segments=[];error='';}render();if(wasWaiting&&data.episodes.find(e=>e.id===selected)?.export_ready===true)command('inspect');}catch(e){listFailures++;pollError=listFailures>=3?'replay_list_connection_lost':'';render();}}
 async function command(action){
  if(busy)return;const current=revision,episode=selected,chosen=segment;error='';busy=true;render();
  try{{const r=await fetch('/api/session',{cache:'no-store',signal:AbortSignal.timeout(1500)});if(!r.ok)throw Error(t('会话不可用','Session unavailable'));token=(await r.json()).token;}
   const r=await fetch('/api/replay',{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':token},body:JSON.stringify({action,episode,segment:chosen,job_id:data?.job?.id}),signal:AbortSignal.timeout(120000)}),result=await r.json();if(!r.ok){if(r.status===403)token='';throw Error(result.error||t('操作失败','Operation failed'));}
   if(current!==revision)return;
   if(action==='inspect'){segments=result.segments||[];verified=result.verified?result.summary:null;}else data.job=result;
  }catch(e){if(current===revision){error=e.message;if(action==='inspect')verified=null;}}finally{busy=false;render();}
 }
 $('replay-episode').onchange=()=>{revision++;selected=$('replay-episode').value;segment=null;segments=[];verified=null;error='';render();if(selected&&data?.episodes?.find(e=>e.id===selected)?.export_ready!==false)command('inspect');};
 $('replay-task').onchange=()=>{revision++;selectedTask=$('replay-task').value;selected='';segment=null;segments=[];verified=null;error='';render();};
 $('replay-segment').onchange=()=>{revision++;segment=$('replay-segment').value===''?null:Number($('replay-segment').value);verified=null;error='';render();if(segment!==null)command('inspect');};
 $('replay-recorded-video').onloadeddata=()=>{$('replay-preview-error').hidden=true;};
 $('replay-recorded-video').onerror=()=>{$('replay-preview-error').hidden=false;};
 document.addEventListener('lab-language-change',render);
 document.addEventListener('lab-physical-status',()=>{const active=window.labPhysicalActive?.();$('replay-task').disabled=busy||active||window.labSimulationActive()||!data?.tasks?.length;$('replay-episode').disabled=busy||active||window.labSimulationActive()||!selectedTask;$('replay-segment').disabled=busy||active||window.labSimulationActive();});
 document.addEventListener('lab-collection-status',e=>{$('tab-recording').hidden=!['arming','recording','stopping','saving'].includes(e.detail.state);});
 async function poll(){if(tab==='replay'&&!busy)await refresh();setTimeout(poll,2000);}render();poll();
})();
