# SONIC 录制就绪条件

`GET /api/controller` 的 `lab.record_ready` 表示当前 SONIC 实例可提供经过验证的
执行数据。进程启动、模型加载或页面就绪本身都不代表可以录制。

## OM 判断

同时满足以下条件才返回 `true`：

- 当前 session/contract 已产生实际推理输入、真实历史和匹配的受保护输出；
  最新有效消费样本年龄不超过 60 ms，没有推理或关联故障。
- 数据来自真实原生实例，原生源及运行端均声明 physical；测试 fixture 不可录制。
- 当前网关拥有的 SONIC 进程存活、处于 active，机器人反馈有效。
- 保护连接 configured/fresh/armed，没有 fenced、estopped、接管或连接错误。
- 输出 stream、保护 generation、WBC PID 与当前实例一致；原生与保护桥的
  可执行程序身份和实际加载的 controller_spec 匹配。

进程身份验证使用内核进程信息和私有保护连接，不依赖遥测自报 PID。
验证工作在后台完成，不在运动线程中读取或哈希程序文件。

Planner、Pose、回放及输入模式切换本身不撤销录制资格；当前契约明确声明
`recording_policy=actual_decoder_consumption`。进程重启、模型/spec/session 变化、
反馈失效或保护接管仍会撤销就绪。见[模式无关录制](SONIC_MODE_INDEPENDENT_RECORDING.md)。

## Lab 开始条件

Lab 校验当前 spec 摘要、session 和执行数据，同时要求新鲜相机视频。
两类数据源都就绪后才正式录制并计时。录制期间持续检查权限、会话、数据与新鲜度；
模式无关不等于忽略数据故障。当前操作流程见[采集说明](../lab/docs/collection.md)。

```json
{"lab":{"record_ready":true,"contract_sha256":"<当前实际spec摘要>","session_id":"<当前会话>","native_consumption_fresh":true,"reason":""}}
```

数据过期时可保留 spec/session 关联值，但 `record_ready=false`；关联值仍存在不代表
当前就绪。`native_telemetry_not_enabled` 表示遥测未启用，
`native_consumption_stale_or_warming` 表示正在预热或尚无新鲜有效数据。
这些状态由当前运行证据决定，没有独立的“协议未对齐”放行开关。

录制结束后按[后台数据处理](../lab/docs/background-export.md)生成
[LeRobot v2.1](../lab/docs/lerobot-storage.md) 数据。采集期间用于验证的完整历史和
原始输出不作为最终训练数据长期保存。回放使用实时机器人反馈重建历史，见
[真机回放协议](SONIC_REFERENCE_REPLAY.md)。

## 接收积压与诊断

原生推理/命令映射与保护桥提交仍逐条关联、校验。接收端对已就绪的两路 socket
交替批量读取，任一路暂时读空后继续处理另一路；每批最多 64 条、处理预算 4 ms，
单条校验完成后才检查预算。批次之间检查发布子进程、心跳和退出信号；批次内每条
已确认回放帧和故障状态立即通知主进程，不合并或丢弃帧，不修改源时间戳。
录制 60 ms 与回放 100 ms 的新鲜度要求保持不变。

`consumption-receiver-*.jsonl` 每约 100 ms 记录一个 `receiver_loop` 窗口，包含
读取、解析、关联校验、发布入队、IPC 通知和轮询的 wall/thread CPU 时间、上下文
切换、两路最大接收延迟、批次预算命中和 GC 重叠记录。审计端异步补充该接收线程
的 Linux `schedstat` 累计运行/排队时间、接收进程 RSS，以及采样时间、审计延迟和队列深度；它不冒充
接收窗口起止时刻的精确采样。`process_cpu_ns` 包括同进程其他线程，可结合 thread CPU
判断开销位置，但不能单独证明 CPU 排队或 GIL 竞争。
超过 20 ms 的批次处理或批次间暂停会保存 timing incident；正常的
空闲轮询等待不触发。`lab.diagnostics.stages.loop` 只携带窗口摘要，详细阶段记录
保存在异步审计与事故文件中，不向控制进程传递完整张量。

机载 `ProcessReceiver` 将审计 JSON 编码、事故归档和文件写入放在独立进程，仍使用
有界 FIFO 队列，审计丢弃计数、写入失败和进程死亡明确报告。审计状态只提供诊断，
不能确认任何执行帧或刷新控制反馈。普通 `Receiver` 保留线程实现，便于独立工具使用。

审计 IPC 将普通记录合批传递（最多 16 条，4 ms 后在下一次接收循环提交；空闲
时还包含最多一次 10 ms poll 等待，调度延迟另计）。故障、慢处理和窗口摘要立即
提交，关闭时排空尾批。记录仍逐条写入，保留原始时间戳和 FIFO；容量仍按记录条数
计数，包含尚未发送的尾批，不按批次数扩容。此等待仅影响异步诊断，不延迟执行证据 IPC。

对于已确认消费或已明确拒绝的推理帧，后续映射按确切的 `(stream, seq)` 记录在
有界索引中；相应提交无需继续占用关联缓存等待 TTL。先到的提交也会在映射到达时
移出缓存。每条提交仍先检查新鲜度、连续性、session、writer generation 和控制权；
未知映射的提交仍保留以支持乱序关联。索引最多与关联缓存容量相同，匹配有效期为
200 ms，session 或 generation 变化清空。原始接收记录保留，后到的重复提交另记
`redundant_commit`，不制造新的执行进度或刷新反馈时间戳。

接收子进程使用引用计数释放无环的 JSON 数据树和有界历史容器，关闭该子进程内的
自动循环垃圾回收，避免在执行确认途中扫描 Python 堆。该策略仅作用于专用 spawn
子进程，退出时恢复原设置；主控制进程、审计和发布进程仍使用各自的正常 GC。
`receiver_loop.automatic_gc_enabled`、`gc_counts` 和异步 RSS 用于核对策略与内存趋势。
不得把该设置扩大到共享运行时，或向关键路径引入需要循环 GC 才能释放的逐帧对象图。

相同原生 session/manifest 的周期广播不再重建 spec，contract 摘要只在已验证 manifest
改变时重算，session 摘要随原生/保护桥 session 或 generation 变化失效。逐帧输入、历史、
输出、时间戳和保护身份校验不变；runtime 的就绪检查仍独立核对当前 spec 与执行证据。
