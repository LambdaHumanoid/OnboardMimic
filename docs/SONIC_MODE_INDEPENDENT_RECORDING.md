# SONIC 模式无关录制

录制捕获当前 decoder 实际使用的 motion token、实时 state/history、原始网络输出及匹配的受保护输出证据。Planner、Pose、回放和输入模式切换本身不决定录制资格；录制仍不发运动命令。

## 会话与历史

原生 StateLogger 在编码模式切换、朝向参考重置和进入回放时保留真实历史与连续会话。
原生进程或保护桥会话/代数变化产生新会话，不把重启拼成连续片段。

回放原有的 1 秒实时历史预热、2 秒过渡、独占权限及退出保护均保留。此改动不把录制状态输入真机，不放松模式切换本身的运动准入条件。

## 协议

controller-spec v2 明确声明：

```json
{"record_modes":["planner","pose","replay"],"recording_policy":"actual_decoder_consumption"}
```

每个实际消费样本包含：

```json
{"native_mode":1,"token_source":"encoder","replay":null}
```

`native_mode` 是原生编码模式，不是网页当前请求的模式。`token_source` 为 `encoder`、`external` 或 `replay`。回放样本的 native_mode=3，保留 run/frame/scheduled_frame；未调用 encoder 时 encoder_input 及 encoder_input_monotonic_ns 均为 null，不捏造诊断输入。OML 和 OM 回放计划校验器均校验这些字段。回放进度仍只消费 scheduled_frame=true 的证据。

OML 对明确声明此策略的 SONIC 契约不以 input_mode/mode_switch_pending 阻止录制，
模式切换可持续录在同一 episode。其他 WBC 仍按自己的契约检查。spec 内容变化会改变
contract hash，配置必须使用实际导出的 spec，不能改摘要冒充匹配。

## 保留的门槛

实际推理与命令成功、严格匹配的受保护 commit、当前进程身份/模型、同会话、历史完整、反馈新鲜和保护布防全部保留。故障、保护接管、停机、过期、模型/进程变化仍会撤销就绪；真实丢帧继续分段，不因本次支持切换而填补缺失数据。真机采集同时要求新鲜相机视频；全部数据源就绪后才开始录制计时。

## 验证边界

协议测试覆盖模式切换时会话连续、实际推理与受保护输出关联、录制资格和回放准入。真机验收按实际源码、模型与部署环境记录。最终数据采用 [LeRobot v2.1](../lab/docs/lerobot-storage.md)；完整推理历史和原始输出属于采集期间的校验数据，不是最终训练数据必须保存的字段。
