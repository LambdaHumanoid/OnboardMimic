/* Receive-only 24-joint diagnostic preview. No control requests or external CDN. */
(()=>{
 const anchor=document.querySelector('#input-mode-panel');if(!anchor)return;
 const root=document.createElement('details');root.id='pico-preview';root.open=true;
 root.innerHTML=`<summary>SONIC · SMPL 输入骨架 <span id="pico-preview-state">等待数据</span></summary><div class="pico-tools"><button type="button" data-view="front">正面</button><button type="button" data-view="side">侧面</button><button type="button" data-view="oblique">斜侧</button><span>拖动画面旋转 · 滚轮缩放</span></div><div class="pico-stage"><canvas aria-label="SONIC SMPL 输入骨架"></canvas><div class="pico-empty">等待 PICO 全身姿态</div></div><div class="pico-legend"><span style="color:#38bdf8">● 人体左侧</span><span style="color:#fb923c">● 人体右侧</span><span id="pico-preview-rate">—</span></div><small>与 SONIC Pose 数据包共用 SMPL 关节坐标 · 以骨盆居中 · 非机器人反馈。发送不代表策略已消费。</small>`;
 const css=document.createElement('style');css.textContent=`#pico-preview{margin-top:10px;border-top:1px solid #42516a;padding-top:10px}#pico-preview summary{cursor:pointer;padding:5px 0}#pico-preview-state{font-size:12px;color:#94a3b8;margin-left:10px}.pico-tools,.pico-legend{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:8px 0;font-size:12px}.pico-tools button{min-height:36px;padding:6px 12px;color:#dbeafe;background:#21334b;border:1px solid #526786;border-radius:6px}.pico-stage{position:relative;background:radial-gradient(ellipse at 50% 30%,#182d46,#080f1d);border-radius:10px;overflow:hidden}.pico-stage canvas{display:block;width:100%;height:330px;}.pico-empty{position:absolute;inset:0;display:flex;justify-content:center;align-items:center;color:#a5b4ca;pointer-events:none}.pico-empty[hidden]{display:none}#pico-preview small{color:#94a3b8;display:block;line-height:1.5}`;
 document.head.append(css);anchor.append(root);
 const canvas=root.querySelector('canvas'),ctx=canvas.getContext('2d'),empty=root.querySelector('.pico-empty'),state=root.querySelector('#pico-preview-state'),rate=root.querySelector('#pico-preview-rate');
 // Packet order: SMPL body 0..21, then left/right hand tips (39,54).
 const parents=[-1,0,0,0,1,2,3,4,5,6,7,8,9,9,9,12,13,14,16,17,18,19,20,21];
 const left=new Set([1,4,7,10,13,16,18,20,22]),right=new Set([2,5,8,11,14,17,19,21,23]);
 let yaw=0,zoom=1,data=null,last=0,drag=null,timer;
 const color=i=>left.has(i)?'#38bdf8':right.has(i)?'#fb923c':'#d8e6f5';
 function draw(){
  const w=canvas.clientWidth,h=canvas.clientHeight;if(!w||!h)return;
  const dpr=Math.min(devicePixelRatio||1,2);canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
  ctx.strokeStyle='#25364c';ctx.lineWidth=1;
  for(let i=1;i<8;i++){ctx.beginPath();ctx.moveTo(w*i/8,0);ctx.lineTo(w*i/8,h);ctx.stroke()}
  for(let i=1;i<6;i++){ctx.beginPath();ctx.moveTo(0,h*i/6);ctx.lineTo(w,h*i/6);ctx.stroke()}
  if(!data?.available||performance.now()-last>600)return;
  const origin=data.joints[0],scale=Math.min(h/2.2,w/2.4)*zoom,c=Math.cos(yaw),s=Math.sin(yaw);
  const points=data.joints.map(p=>{const x=-(p[1]-origin[1]),y=p[2]-origin[2],z=p[0]-origin[0];return [w/2+(-x*c+z*s)*scale,h*.5-y*scale,x*s+z*c]});
  // Front view faces the person: their left side is screen right.
  const order=points.map((_,i)=>i).sort((a,b)=>points[a][2]-points[b][2]);
  for(const i of order){const p=points[i],parent=parents[i];if(parent<0)continue;const q=points[parent];ctx.strokeStyle=color(i);ctx.lineWidth=4;ctx.lineCap='round';ctx.beginPath();ctx.moveTo(q[0],q[1]);ctx.lineTo(p[0],p[1]);ctx.stroke()}
  for(const i of order){const p=points[i];ctx.fillStyle=color(i);ctx.beginPath();ctx.arc(p[0],p[1],i===15?9:3.5,0,Math.PI*2);ctx.fill()}
 }
 function clear(reason){data=null;state.textContent=reason;state.style.color='#fbbf24';empty.textContent=reason;empty.hidden=false;rate.textContent='—';draw()}
 root.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{yaw={front:0,side:Math.PI/2,oblique:.65}[b.dataset.view];zoom=1;draw()});
 canvas.onpointerdown=e=>{if(e.pointerType!=='mouse'||e.button!==0)return;drag={id:e.pointerId,x:e.clientX};canvas.setPointerCapture(e.pointerId)};
 canvas.onpointermove=e=>{if(!drag||drag.id!==e.pointerId)return;yaw+=(e.clientX-drag.x)*.01;drag.x=e.clientX;draw()};
 canvas.onpointerup=canvas.onpointercancel=canvas.onlostpointercapture=()=>{drag=null};
 canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.6,Math.min(1.8,zoom-e.deltaY*.001));draw()},{passive:false});
 // Preview controls never generate keyboard walking commands; stop keys still propagate.
 root.addEventListener('keydown',e=>{if(!['Escape',' '].includes(e.key))e.stopPropagation()});
 async function poll(){
  clearTimeout(timer);
  if(!document.hidden&&root.open&&root.getClientRects().length>0){
   const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),900);
   try{const response=await fetch('/api/pico/skeleton',{cache:'no-store',signal:controller.signal});if(!response.ok)throw Error();const x=await response.json();
    if(!x.available)clear(x.reason||'等待全身姿态');else{data=x;last=performance.now();state.textContent=x.delivery==='published'?'最近发送 · SMPL':'待发送预览 · SMPL';if(x.consistency&&!x.consistency.ready)state.textContent+=' · '+x.consistency.reason;state.style.color=x.consistency&&!x.consistency.ready?'#fbbf24':'#4ade80';empty.hidden=true;rate.textContent=`帧 ${x.frame_index} · Pose 50 Hz · 预览最高 10 Hz`;draw()}
   }catch(e){clear('预览连接中断')}finally{clearTimeout(timeout)}
  }
  timer=setTimeout(poll,100);
 }
 // Separate watchdog clears the old skeleton even while a fetch is pending.
 setInterval(()=>{if(data&&performance.now()-last>600)clear('姿态已断流')},100);
 root.addEventListener('toggle',draw);document.addEventListener('visibilitychange',()=>{if(document.hidden)clear('页面已暂停预览')});
 new ResizeObserver(draw).observe(canvas);poll();
})();
