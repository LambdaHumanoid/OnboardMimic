const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('lab/src/onboardmimic_lab/static/collection.js','utf8');
const bridge=source.slice(0,source.indexOf('})();')+5);
const reasons=['recording_status_snapshot_expired','recording_control_state_changed','recording_control_process_changed',
 'recording_joint_feedback_stale','recording_joint_feedback_invalid','recording_joint_fault',
 'recording_joint_overspeed','recording_robot_unsafe','runtime_not_ready'];
for(const language of ['zh-CN','en']){
 const context={navigator:{language},Event:class{},document:{body:{dataset:{}},documentElement:{},querySelectorAll:()=>[],dispatchEvent(){}},window:{addEventListener(){}}};
 vm.runInNewContext(bridge,context);
 for(const reason of reasons){
  const message=context.window.labLanguage.error('runtime recording readiness lost: '+reason);
  assert.ok(!message.includes(reason));
  assert.ok(!message.includes('操作未完成'));
  if(language==='en'){assert.ok(!/[\u3400-\u9fff]/.test(message));assert.ok(message.startsWith('Recording readiness lost: '));}
  else assert.ok(message.startsWith('录制就绪检查未通过：'));
 }
}
console.log('Recording readiness errors: Chinese and English passed');
