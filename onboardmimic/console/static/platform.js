/* Registry-driven robot/WBC selector. Reading configuration never sends control. */
(()=>{
 const $=id=>document.getElementById(id);
 let catalog=null,status={},selectionHandler=null,pending=false,draft=null,selectionError=false;
 const same=(a,b)=>a?.robot_id===b?.robot_id&&a?.backend_id===b?.backend_id;
 const stopped=()=>status.mode==='onboard_real'&&status.state==='stopped'&&
   Array.isArray(status.pids)&&!status.pids.length&&!status.control_transferred;
 const matched=()=>catalog&&status.mode==='onboard_real'&&same(catalog.selected,{robot_id:status.robot_id,backend_id:status.backend});
 const editable=()=>!!(catalog?.switching.supported&&selectionHandler&&stopped()&&matched()&&!pending);

 function options(element,items,value){
  const soon=document.createElement('option');soon.value='';soon.disabled=true;
  soon.dataset.i18n='';soon.textContent='敬请期待';
  element.replaceChildren(...items.map(item=>{
   const option=document.createElement('option');option.value=item.id;option.textContent=item.label;
   option.disabled=item.disabled||false;option.selected=item.id===value;return option;
  }),soon);
 }
 function fill(){
  const {robots,backends,pairs}=catalog;
  const switchSupported=catalog.switching.supported&&selectionHandler;
  options($('robot-select'),robots.filter(r=>switchSupported||r.id===draft.robot_id),draft.robot_id);
  options($('wbc-select'),backends.filter(b=>switchSupported||b.id===draft.backend_id),draft.backend_id);
 }
 function render(){
  if(!catalog)return;
  const enabled=editable(),dirty=!same(draft,catalog.selected);
  // Browsing a dropdown is always allowed. Only actual compatible choices can
  // change the draft, and only through the lifecycle selection extension.
  $('robot-select').disabled=$('wbc-select').disabled=pending;
  for(const option of $('robot-select').options)option.disabled=!option.value||
   (option.value!==draft.robot_id&&(!enabled||!catalog.pairs.some(p=>p.robot_id===option.value)));
  for(const option of $('wbc-select').options)option.disabled=!option.value||
   (option.value!==draft.backend_id&&!enabled)||!catalog.pairs.some(p=>p.robot_id===draft.robot_id&&p.backend_id===option.value);
  $('platform-apply').hidden=!(catalog.switching.supported&&selectionHandler);
  $('platform-apply').disabled=!enabled||!dirty;
  $('platform-status').dataset.state=matched()?'matched':'waiting';
  $('platform-status').textContent=pending?'切换中':matched()?'配置已匹配':status.mode==='onboard_real'?'配置不匹配':'等待后台确认';
  $('platform-note').textContent=selectionError?'切换未确认，请检查后台配置。':pending?'正在切换配置，请等待后台确认':
   !catalog.switching.supported||!selectionHandler?'当前为部署时配置；切换本体或 WBC 需停止控制器并重新配置服务。':
   !stopped()?'控制器停止后可切换本体与 WBC。':dirty?'待切换的配置尚未生效。':'选择兼容的本体与 WBC，确认后切换。';
 }
 async function load(){
  try{
   const response=await fetch('/api/platform',{cache:'no-store',signal:AbortSignal.timeout(3000)});
   if(!response.ok)throw Error();
   const data=await response.json();
   if(data.schema_version!==1||!data.pairs.some(p=>same(p,data.selected))||
      !data.robots.some(r=>r.id===data.selected.robot_id)||!data.backends.some(b=>b.id===data.selected.backend_id))throw Error();
   catalog=data;draft={...data.selected};fill();render();return true;
  }catch{
   catalog=null;$('robot-select').disabled=$('wbc-select').disabled=true;
   $('platform-apply').hidden=true;$('platform-status').dataset.state='waiting';
   $('platform-status').textContent='配置读取失败';
   $('platform-note').textContent='无法读取本体与 WBC 配置，正在重试。';
   return false;
  }
 }
 $('robot-select').onchange=()=>{
  if(!editable()||!catalog.pairs.some(p=>p.robot_id===$('robot-select').value)){fill();render();return}
  selectionError=false;draft.robot_id=$('robot-select').value;
  if(!catalog.pairs.some(p=>same(p,draft)))draft.backend_id=catalog.pairs.find(p=>p.robot_id===draft.robot_id).backend_id;
  fill();render();
 };
 $('wbc-select').onchange=()=>{
  if(editable()&&catalog.pairs.some(p=>p.robot_id===draft.robot_id&&p.backend_id===$('wbc-select').value)){
   selectionError=false;draft.backend_id=$('wbc-select').value;
  }
  fill();render();
 };
 $('platform-apply').onclick=async()=>{
  if(!editable()||same(draft,catalog.selected))return;
  selectionError=false;pending=true;render();
  try{
   // A future lifecycle implementation must validate and apply the pair on the
   // server. Never reinterpret a draft or a successful click as an active pair.
   await selectionHandler({...draft});await load();
  }catch{selectionError=true}
  finally{pending=false;render()}
 };
 window.onboardMimicPlatform={
  update(value){status=value;render()},
  // Extension point only. The shipped server advertises switching.supported=false.
  setSelectionHandler(handler){selectionHandler=typeof handler==='function'?handler:null;if(catalog)fill();render()},
 };
 async function retry(){if(!await load())setTimeout(retry,5000)}
 retry();
})();
