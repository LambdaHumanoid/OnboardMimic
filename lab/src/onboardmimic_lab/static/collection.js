// Presentation-only bridge: accept language from the configured parent, never controls.
(()=>{
 let language=navigator.language.toLowerCase().startsWith('zh')?'zh-CN':'en';
 const allowed=(document.body.dataset.embedOrigins||'').split(' ').filter(Boolean);
 function apply(){
  document.documentElement.lang=language;
  for(const el of document.querySelectorAll('[data-zh][data-en]'))el.textContent=language==='en'?el.dataset.en:el.dataset.zh;
  for(const el of document.querySelectorAll('[data-placeholder-zh]'))el.placeholder=language==='en'?el.dataset.placeholderEn:el.dataset.placeholderZh;
  document.dispatchEvent(new Event('lab-language-change'));
 }
 const errors={
   'LeRobot export dependencies are not installed':['LeRobot 保存组件未安装，请完成服务更新。','LeRobot export components are missing; complete the service update.'],
   'Episode alignment is not ready':['本段原始数据已保存，数据处理尚未完成，暂不能回放。','Raw data is saved; data processing must finish before replay.'],
   'only a failed export can be retried':['只有数据处理失败的记录可以重试，请刷新状态。','Only failed data processing jobs can be retried. Refresh the status.'],
   'export timed out':['后台数据处理超时，原始数据已保留，可重试。','Background data processing timed out. Raw data is retained; retry is available.'],
   'export worker failed; see export.log':['后台数据处理进程异常，原始数据已保留；详情见导出日志。','Background data processing failed. Raw data is retained; see the export log.'],
   'missing source packet PTS':['视频缺少原始时间戳，无法可靠地处理数据。','Video is missing source timestamps; reliable data processing is unavailable.'],
   'decoded frame PTS differs from source packets':['解码帧与原始视频时间戳不一致，已拒绝数据处理。','Decoded frame timestamps do not match the source; data processing rejected.'],

   'schedule_overrun':['回放执行落后于机载调度时限，已停止并请求保护退出。','Replay missed its onboard scheduling deadline; stopped with protective exit requested.'],
   'consumption_gap':['未能连续确认回放帧的执行结果，已停止并请求保护退出。','Replay frame execution could not be confirmed continuously; stopped with protective exit requested.'],
   'Recorded timing exceeds 50Hz scheduling budget':['录制帧过于密集，无法在允许的时间偏差内逐帧回放；计划未加载。','Recorded frames are too dense for frame-by-frame replay within the timing budget; plan not loaded.'],
   'start_pose_transition_timeout':['首帧过渡未能在时限内稳定，录制内容尚未开始播放。','The first-frame transition did not settle in time; playback of recorded frames has not started.'],
   'capture stopped before sources were ready':['数据源尚未就绪，采集已结束。请检查相机视频和机器人遥测。','Capture ended before sources were ready. Check camera video and robot telemetry.'],
   'invalid task metadata':['任务描述应为不超过 500 个字符的文本，可留空。','Task description must be text up to 500 characters; it may be empty.'],
   'invalid task name':['任务名称应为不超过 100 个字符的文本。','Task name must be text up to 100 characters.'],
   'invalid collector name':['采集人员应为不超过 100 个字符的文本，可留空。','Collector must be text up to 100 characters; it may be empty.'],
   'unknown task id':['所选任务不存在，请刷新后重新选择。','The selected task does not exist. Refresh and select again.'],
   'runtime readiness/session changed':['录制就绪状态或控制会话发生变化。','Recording readiness or the control session changed.'],
   'runtime has not confirmed recording readiness for this contract':['运行时尚未确认当前数据契约可以录制。','Runtime has not confirmed recording readiness for this contract.'],
   'runtime recording session changed':['控制会话已变化，录制已停止。','The control session changed; recording stopped.'],
   'runtime recording contract changed':['数据契约已变化，录制已停止。','The recording contract changed; recording stopped.'],
   'save/discard the pending episode before starting another':['请先保存或丢弃待处理记录，再开始新采集。','Save or discard the pending episode before starting another.'],
   'task must contain 1..500 characters':['请填写 1–500 个字符的任务描述。','Enter a task description of 1–500 characters.'],
   'no active capture':['当前没有进行中的采集。','No capture is active.'],
   'another episode is selected':['请先处理当前选中的记录。','Finish processing the selected episode first.'],
   'Wait for confirmed stop or Planner handoff before loading':['请等待停止或 Planner 接管确认后再回放。','Wait for confirmed stop or Planner handoff before replaying.'],
   'OM runtime-owned replay is required':['OM 回放接口版本不匹配，请同步更新 OM 和 Lab。','OM replay API version mismatch. Update OM and Lab together.'],
   'lease_expired':['机载回放执行器未及时收到后台心跳，已停止并请求保护退出。','The onboard replay executor missed its runtime heartbeat; stopped with protective exit requested.'],
   'runtime_admission_lost':['回放运行条件检查未通过，已停止回放并请求保护退出。','Replay runtime checks failed; replay stopped with protective exit requested.'],
   'Protected writer unavailable':['无法确认保护桥状态，已停止回放并请求保护退出。','Protection bridge status could not be confirmed; replay stopped with protective exit requested.'],
   'consumption_timeout':['未及时收到有效的 SONIC 执行反馈，已停止回放并触发保护退出。','Valid SONIC execution feedback was not received in time; replay stopped with protective exit triggered.'],
   'planner_return_timeout':['返回 Planner 未能在限定时间内稳定，已保护退出。','Planner return did not settle in time; protective exit triggered.'],
   'final_frame_unconfirmed':['末帧提交未确认，已保护退出。','Final frame submission was not confirmed; protective exit triggered.'],
   'only a completed capture can be saved':['只有完整结束的采集才能保存。','Only a completed capture can be saved.'],
   'stop capture before discarding':['请先结束采集，再丢弃记录。','Stop capture before discarding it.'],
   'at least 2 GiB free disk space is required':['可用磁盘空间不足，需要至少 2 GiB。','At least 2 GiB of free disk space is required.'],
   'disk space below 512 MiB':['可用磁盘空间低于 512 MiB，采集已停止。','Free disk space is below 512 MiB; capture stopped.'],
   'collector exited during capture':['数采服务在录制期间退出，记录不完整。','The collector exited during capture; the episode is incomplete.'],
   'required stream lost freshness':['必要的数据流已过期或中断。','A required data stream is stale or disconnected.'],
   'waiting for consumed references and camera timed out':['等待 SONIC 执行反馈和相机视频超时。','Timed out waiting for SONIC execution feedback and camera video.'],
   'telemetry session mismatch':['遥测会话与当前录制不一致。','Telemetry session does not match this recording.'],
   'invalid telemetry envelope':['收到的遥测数据格式无效。','Invalid telemetry message format.'],
   'native_spec_unavailable':['尚未收到 SONIC 数据契约，请先启动 SONIC。','SONIC contract unavailable; start SONIC first.'],
   'native_consumption_stale_or_warming':['正在等待最新的 SONIC 执行反馈，请稍候。','Waiting for current SONIC execution feedback. Please wait.'],
   'native_consumption_fault':['SONIC 执行反馈校验未通过，暂不能录制。','SONIC execution feedback failed validation; recording is unavailable.'],
   'native_telemetry_error':['遥测接收服务异常。','Telemetry receiver failed.'],
   'native_telemetry_worker_unavailable':['遥测子进程不可用。','Telemetry worker unavailable.'],
   'recording_status_snapshot_expired':['机器人状态查询已过期，重新确认未能及时完成。','Robot status snapshot expired; fresh confirmation did not complete in time.'],
   'recording_control_state_changed':['检查期间控制状态发生变化，采集已停止。','Control state changed during validation; capture stopped.'],
   'recording_control_process_changed':['检查期间控制进程发生变化，采集已停止。','Controller process changed during validation; capture stopped.'],
   'recording_joint_feedback_stale':['关节或 IMU 反馈已过期，采集已停止。','Joint or IMU feedback is stale; capture stopped.'],
   'recording_joint_feedback_invalid':['关节或 IMU 反馈数据无效，采集已停止。','Joint or IMU feedback is invalid; capture stopped.'],
   'recording_joint_fault':['检测到关节故障，采集已停止。','A joint fault was detected; capture stopped.'],
   'recording_joint_overspeed':['关节速度超过录制安全检查限值，采集已停止。','Joint speed exceeded the recording safety-check limit; capture stopped.'],
   'recording_robot_unsafe':['机器人安全状态检查未通过，采集已停止。','Robot safety validation failed; capture stopped.'],
   'runtime_not_ready':['机器人未通过录制就绪检查，具体原因未由后台提供。','Robot recording readiness failed; the backend did not provide a specific reason.'],
   'runtime_inactive':['SONIC 尚未进入运行状态。','SONIC is not active.'],
   'runtime_feedback_unavailable':['真机关节或 IMU 反馈未就绪。','Robot joint or IMU feedback is unavailable.'],
   'protected_output_unavailable':['受保护输出尚未就绪。','Protected output is unavailable.'],
   'protected_session_mismatch':['保护模块与控制会话不匹配。','Protected writer and control session do not match.'],
   'runtime_identity_mismatch':['运行进程身份校验失败。','Runtime process identity does not match.'],
   'camera recording buffer overflow':['相机录制缓冲区已满，采集已停止。','Camera recording buffer overflow; capture stopped.'],
   'replay_list_connection_lost':['记录列表连接持续中断，正在重连；当前回放状态请查看执行区域。','Recording list connection remains unavailable. Reconnecting; see the execution panel for replay status.'],
   'replay_status_connection_lost':['状态连接持续中断，正在重连；尚不能确认回放是否停止。','Status connection remains unavailable. Reconnecting; replay stop is unconfirmed.'],
   'Feedback or mode unavailable':['机器人反馈不够新鲜或控制模式未就绪，已停止回放并请求保护退出。','Robot feedback is stale or the control mode is not ready. Replay stopped and protective exit was requested.'],
   'Failed to fetch':['无法连接服务，请检查网络。','Cannot reach the service. Check the connection.'],
   'unauthorized':['页面会话已失效，请刷新后重试。','Page session expired. Refresh and try again.'],
   '数采服务不可用，请检查 collector 服务':['数采服务不可用，请检查数采后台。','Collection service unavailable; check the collector.'],
   '等待 OM 导出真实原生 spec / Waiting for OM native spec':['正在等待 OM 导出真实数据契约。','Waiting for OM to export the native contract.']
 };
 const choose=pair=>language==='en'?pair[1]:pair[0];
 function translate(raw){
   raw=String(raw||'');if(!raw)return '';
   if(/^OM HTTP \d+: /.test(raw))return translate(raw.replace(/^OM HTTP \d+: /,''));
   if(raw.startsWith('runtime_admission_lost: '))return translate(raw.slice('runtime_admission_lost: '.length));
   if(raw.includes('start_pose_transition_timeout'))return choose(errors.start_pose_transition_timeout);
   if(errors[raw])return choose(errors[raw]);
   for(const [prefix,zh,en] of [
     ['采集尚未开始，缺少数据源 / Capture stopped while waiting for: ','采集尚未开始，仍缺少：','Capture has not started; still waiting for: '],
     ['等待数据源超时 / Waiting for sources timed out: ','等待数据源超时：','Waiting for sources timed out: '],
     ['数据源失联 / Stream lost freshness: ','数据源已中断或过期：','Data sources disconnected or stale: ']]){
     if(raw.startsWith(prefix))return choose([zh,en])+raw.slice(prefix.length).split(', ').map(k=>k==='camera'?choose(['相机视频','camera video']):k==='controller'?choose(['SONIC 执行反馈','SONIC execution feedback']):k).join('、');
   }
   for(const [prefix,zh,en] of [['runtime recording readiness lost: ','录制就绪检查未通过：','Recording readiness lost: '],['finalization failed: ','录制文件收尾失败：','Recording finalization failed: '],['camera unavailable: ','相机不可用：','Camera unavailable: ']]){
     if(raw.startsWith(prefix))return choose([zh,en])+translate(raw.slice(prefix.length));
   }
   if(raw.includes('Address already in use'))return choose(['相机端口被其他进程占用。','Camera port is already in use.']);
   if(raw.includes('timeout')||raw.includes('timed out')||raw.includes('aborted'))return choose(['请求超时或已取消，请检查服务连接。','Request timed out or was cancelled. Check the service connection.']);
   if(language==='en'&&!/[\u3400-\u9fff]/.test(raw))return raw;
   if(language!=='en'&&/[\u3400-\u9fff]/.test(raw)&&!/[A-Za-z]{3}/.test(raw))return raw;
   return choose(['操作未完成，请查看技术详情。','Operation failed. See technical details.']);
 }
 const renderedErrors=new WeakMap();
 function showError(el,raw){
   raw=String(raw||'');
   const previous=renderedErrors.get(el);
   if(previous?.raw===raw&&previous.language===language)return;
   const wasOpen=previous?.raw===raw&&el.querySelector('details')?.open;
   const message=translate(raw);el.textContent=message;
   if(raw&&message!==String(raw)){
     const details=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('pre');
     summary.textContent=choose(['技术详情（原文）','Technical details (original)']);body.textContent=String(raw);
     details.open=!!wasOpen;
     details.append(summary,body);el.appendChild(details);
   }
   renderedErrors.set(el,{raw,language});
 }
 window.labLanguage={text:(zh,en)=>language==='en'?en:zh,error:translate,showError};
 window.addEventListener('message',event=>{
  const m=event.data;
  if(window.parent===window||event.source!==window.parent||!allowed.includes(event.origin))return;
  if(!m||m.type!=='onboardmimic.lab.language'||m.version!==1||!['zh-CN','en'].includes(m.language))return;
  if(language===m.language)return;
  language=m.language;apply();
 });
 apply();
})();
(()=>{
 const panel=document.getElementById('collection-panel');if(!panel)return;
 const $=id=>document.getElementById(id);
 const t=window.labLanguage.text;
 const labels={idle:['就绪','Ready'],arming:['等待数据源','Waiting for sources'],recording:['正在数采','Recording'],stopping:['正在结束采集','Stopping recording'],review:['待保存','Review'],saving:['正在保存','Saving'],saved:['原始数据已保存','Raw data saved'],discarded:['已丢弃','Discarded'],failed:['数据不完整','Incomplete']};
 let token='',busy=false,busyAction='',latest=null,requestError='',pollError='',errorScope='',generation=0,runtime=null,taskSelection=null;
 function taskFields(){const row=latest?.tasks?.find(x=>x.id===taskSelection);$('task-name').value=row?.name||'';$('episode-task').value=row?.description||'';}
 const scope=s=>JSON.stringify([s?.episode_id,s?.task_id,s?.state]);
 function render(s){
   panel.hidden=!s.enabled;if(!s.enabled)return;latest=s;document.dispatchEvent(new CustomEvent('lab-collection-status',{detail:s}));
   $('collection-state').textContent=(labels[s.state]?t(...labels[s.state]):s.state);
   $('collection-state').dataset.state=s.state;
   const gaps=s.discontinuities||{count:0,recent:[]},gap=gaps.recent?.at(-1);
   const notice=$('collection-discontinuities');
   if(notice){
     notice.hidden=!gaps.count;
     if(gap){
       const when=new Date(gap.detected_realtime_ns/1e6).toLocaleTimeString();
       const saved=gap.state==='saved'?t('采集证据已保存','Capture evidence saved'):gap.state==='failed'?t('证据保存失败','Evidence save failed'):t('正在保存证据…','Saving evidence…');
       const message=(gap.kind==='capture_error'?
         t(`采集已中止；${when}，最后收到帧 ${gap.previous_index}。${saved}。`,
           `Capture stopped at ${when}; last received frame ${gap.previous_index}. ${saved}.`):t(`检测到 ${gaps.count} 次数据断点；最近 ${when}，帧 ${gap.previous_index} → ${gap.index}。${saved}。`,
         `${gaps.count} data discontinuities detected; latest ${when}, frames ${gap.previous_index} → ${gap.index}. ${saved}.`))+
         (s.state==='recording'?t('采集仍在继续。',' Recording continues.'):'')+
         (gaps.failed?t(` ${gaps.failed} 份证据保存失败，请保留当前记录。`,` ${gaps.failed} evidence saves failed; keep this recording.`):'')+
         (gap.reason&&gap.kind!=='capture_error'?' '+window.labLanguage.error(gap.reason):'');
       if(notice.textContent!==message)notice.textContent=message;
     }
   }
   const c=s.counts||{};
   $('collection-counts').textContent=`${s.elapsed_s||0}s · reference ${c['controller.jsonl']||c['sonic.jsonl']||0} · video ${c['camera.jsonl']||0} · pose ${c['pico.jsonl']||0}`;
   $('collection-id').textContent=s.episode_index==null?'—':'episode'+s.episode_index;
   const idle=['idle','saved','discarded'].includes(s.state);
   if(taskSelection===null){taskSelection=s.task_id||'';taskFields();}
   const taskOptions=[['',t('新任务','New task')],...(s.tasks||[]).map(x=>[x.id,`${x.name||t('未命名任务','Untitled task')} · ${x.id.slice(-8)}`])];
   const picker=$('collection-task'),key=JSON.stringify(taskOptions);
   if(picker.dataset.options!==key){picker.replaceChildren();for(const [value,label] of taskOptions){const o=document.createElement('option');o.value=value;o.textContent=label;picker.append(o);}picker.dataset.options=key;}
   picker.value=taskSelection;picker.disabled=busy||!idle;
   $('task-new').disabled=busy||!idle;
   $('task-collector').disabled=busy||!idle;
   const camera=s.camera||{};
   $('collection-camera-state').textContent=camera.ready?t('相机：视频流已收到','Camera: video stream received'):
     camera.error?t('相机：不可用 — ','Camera: unavailable — ')+window.labLanguage.error(camera.error):
     t('相机：未收到新鲜视频，请连接相机并启动视频转发。','Camera: no fresh video. Connect the camera and start video forwarding.');
   $('collection-camera-state').dataset.ready=String(!!camera.ready);
   const stateReady=runtime?.feedback_continuity?.ready===true;
   $('collection-robot-state').textContent=runtime?(stateReady?t('真机 state：关节 / IMU 反馈正常','Robot state: joint / IMU feedback ready'):t('真机 state：反馈未就绪或已过期','Robot state: feedback unavailable or stale')):t('真机 state：状态服务不可用','Robot state: status service unavailable');
   $('collection-robot-state').dataset.ready=String(stateReady);
   $('collection-record-ready').textContent=runtime?.lab?.record_ready?t('SONIC 执行反馈：已就绪','SONIC execution feedback: ready'):t('SONIC 执行反馈：未就绪','SONIC execution feedback: not ready')+(runtime?.lab?.reason?' — '+window.labLanguage.error(runtime.lab.reason):'');
   $('episode-start').disabled=busy||!idle||s.pending?.length>0||!camera.ready||!stateReady||runtime?.lab?.record_ready!==true;
   let reason='';
   if(busy){
     const pending={start:['正在启动采集，请稍候。','Starting recording, please wait.'],stop:['正在结束采集，请稍候。','Stopping recording, please wait.'],save:['正在提交保存请求，请稍候。','Submitting the save request, please wait.'],discard:['正在丢弃当前记录，请稍候。','Discarding the current episode, please wait.'],select:['正在加载待处理记录，请稍候。','Loading the pending episode, please wait.']};
     reason=t(...(pending[busyAction]||['正在处理，请稍候。','Processing, please wait.']));
   }
   else if(idle&&s.pending?.length){
     const records=s.pending.map(row=>`${row.task||t('未命名任务','Untitled task')} (episode${row.episode_index??'—'})`).join('、');
     reason=t(`暂不能开始数采：有待处理记录 ${records}。请点击下方“继续处理”，保存或丢弃后再开始。`,
       `Recording is unavailable while episodes are pending: ${records}. Select “Resume” below, then save or discard before starting.`);
   }else if(['review','failed'].includes(s.state))reason=t('请先保存或丢弃当前记录，再开始新的数采。失败记录只能丢弃。','Save or discard the current episode before recording again. Failed episodes can only be discarded.');
   else if(s.state==='arming')reason=t('正在等待数据源，尚未开始录制计时。请查看上方相机和机器人状态。','Waiting for sources; recording timer has not started. Check camera and robot status above.');
   else if(s.state==='recording')reason=t('正在采集。请先点击“结束采集”，再保存或丢弃本段记录。','Recording. Click “Stop”, then save or discard this episode.');
   else if(s.state==='stopping')reason=t('正在结束采集并整理数据，请稍候。','Stopping recording and finalizing data, please wait.');
   else if(s.state==='saving')reason=t('正在校验并保存原始数据，请稍候。','Verifying and saving raw data, please wait.');
   else if(!idle)reason=t('采集状态未知，请刷新页面确认。','Unknown recording state. Refresh to check the status.');
   else if(!camera.ready)reason=t('暂不能开始数采：未收到相机视频，请连接相机并启动视频转发。','Cannot start recording: no camera video. Connect the camera and start video forwarding.');
   else if(!stateReady)reason=t('暂不能开始数采：真机关节 / IMU 反馈未就绪。','Cannot start recording: joint / IMU feedback is not ready.');
   else if(runtime?.lab?.record_ready!==true)reason=t('暂不能开始数采：正在等待有效的 SONIC 执行反馈。','Cannot start recording: waiting for valid SONIC execution feedback.');
   $('episode-start-reason').textContent=reason;
   $('episode-start').setAttribute('aria-describedby','episode-start-reason');
   $('episode-task').disabled=busy||!idle||!!taskSelection;
   $('task-name').disabled=busy||!idle||!!taskSelection;
   $('episode-stop').disabled=busy||!['arming','recording'].includes(s.state);
   $('episode-save').disabled=busy||s.state!=='review';
   $('episode-discard').disabled=busy||!['review','failed'].includes(s.state);
   // Completed/discarded episode diagnostics are historical, not live source errors.
   window.labLanguage.showError($('collection-error'),(!idle?s.error:'')||pollError||requestError);
   const exports=$('collection-exports'),exportKey=JSON.stringify([s.exports||[],document.documentElement.lang,busy]);
   if(exports.dataset.renderKey!==exportKey){
   exports.dataset.renderKey=exportKey;exports.replaceChildren();
   const jobLabels={queued:['等待数据处理','Queued for data processing'],running:['后台数据处理中','Processing data in background'],paused:['采集中，数据处理已暂停','Data processing paused during capture'],complete:['数据处理完成，可回放','Data processed, ready to replay'],failed:['数据处理失败，原始数据已保留','Data processing failed; raw data retained']};
   for(const job of (s.exports||[]).filter(job=>['queued','running','paused','failed'].includes(job.state))){
     const line=document.createElement('p');line.textContent=`${job.task_name||t('未命名任务','Untitled task')} · episode${job.episode_index??'—'}：${t(...(jobLabels[job.state]||jobLabels.queued))}`;
     if(job.progress && ['running','paused'].includes(job.state)){
       const phases={verify_raw:['1/4 检查原始数据','1/4 Checking source data'],video:['2/4 视频处理','2/4 Video processing'],verify_output:['3/4 结果校验','3/4 Validating output'],saving:['4/4 正在保存','4/4 Saving']};
       const phase=phases[job.progress.stage];
       if(phase){
         const detail=document.createElement('strong');
         const percent=job.progress.percent;
         detail.textContent=` · ${t(...phase)}${Number.isFinite(percent)?' '+percent+'%':''}`;
         line.append(detail);
       }
     }
     if(job.state==='failed'){
       const button=document.createElement('button');button.textContent=t('重试数据处理','Retry data processing');button.disabled=busy;button.onclick=()=>command('retry_export',{id:job.id});line.append(' ',button);
       const detail=document.createElement('span');window.labLanguage.showError(detail,job.error||'');line.append(detail);
     }
     exports.append(line);
   }
   }
   const recovered=$('episode-recovered');recovered.replaceChildren();
   if(idle)for(const row of s.pending||[]){
     const b=document.createElement('button');b.type='button';b.textContent=`${t('继续处理','Resume')}: ${row.task} (episode${row.episode_index??'—'})`;
     b.disabled=busy;b.onclick=()=>command('select',{id:row.id});recovered.appendChild(b);
   }
 }
 async function command(action,extra={}){
   if(busy)return;busy=true;busyAction=action;generation++;requestError='';errorScope=scope(latest);if(latest)render(latest);
   try{
     {const r=await fetch('/api/session',{cache:'no-store',signal:AbortSignal.timeout(1500)});if(!r.ok)throw Error(t('会话不可用','Session unavailable'));token=(await r.json()).token;}
     const r=await fetch('/api/collection',{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':token},body:JSON.stringify({action,...extra}),signal:AbortSignal.timeout(15000)});
     const s=await r.json();if(!r.ok){if(r.status===403)token='';throw Error(s.error||t('请求失败','Request failed'));}if(['start','select'].includes(action)&&s.task_id){taskSelection=s.task_id;latest=s;taskFields();}render(s);
   }catch(e){requestError=e.message;window.labLanguage.showError($('collection-error'),requestError);}finally{busy=false;busyAction='';if(latest)render(latest);}
 }
 document.addEventListener('lab-language-change',()=>{if(latest)render(latest)});
 $('collection-task').onchange=()=>{taskSelection=$('collection-task').value;taskFields();if(latest)render(latest);};
 $('task-new').onclick=()=>{taskSelection='';taskFields();if(latest)render(latest);$('task-name').focus();};
 $('episode-start').onclick=()=>command('start',{...(taskSelection?{task_id:taskSelection}:{task_name:$('task-name').value,task_description:$('episode-task').value}),collector_name:$('task-collector').value});
 $('episode-stop').onclick=()=>command('stop');$('episode-save').onclick=()=>command('save');
 $('episode-discard').onclick=()=>command('discard');
 async function poll(){
   const current=generation;
   try{const [r,rt]=await Promise.all([fetch('/api/collection',{cache:'no-store',signal:AbortSignal.timeout(3000)}),fetch('/api/runtime',{cache:'no-store',signal:AbortSignal.timeout(2500)}).catch(()=>null)]);const nextRuntime=rt?.ok?await rt.json():null;if(!r.ok)throw Error(t('数采状态不可用','Collection status unavailable'));const next=await r.json();if(!busy&&current===generation){runtime=nextRuntime;pollError='';if(errorScope!==scope(next))requestError='';render(next);}}
   catch(e){if(!busy&&current===generation){pollError=e.message;window.labLanguage.showError($('collection-error'),pollError);for(const id of ['collection-camera-state','collection-robot-state','collection-record-ready']){$(id).textContent=t('状态连接中断，无法确认数据源','Status connection lost; sources cannot be verified');$(id).dataset.ready='false';}for(const id of ['episode-start','episode-stop','episode-save','episode-discard'])$(id).disabled=true;}}
   setTimeout(poll,500);
 }poll();
 window.labCollectionUIReady = true;
})();
