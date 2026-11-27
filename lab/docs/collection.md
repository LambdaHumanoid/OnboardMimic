# 数采配置与生命周期

OML 独立安装、运行；不导入 OM Python 包，不扫描 OM 安装目录。
真机通过 `sonic-native-v2` 接收 OM 发布的实际消费遥测和 controller_spec。
`reference-v1` 保留用于软件 WBC 协议联调；缺失或未知的 capture_adapter 明确报错。

```bash
onboardmimic-lab configure-sonic --spec /path/controller-spec.json --output /path/collection.json --data-root /path/data
onboardmimic-lab validate-collection --config /path/collection.json
onboardmimic-lab-collector --config /path/collection.json
```

配置示例见 `lab/configs/collection.example.json`。配置中的 `controller_spec_file`
允许等待 OM 导出真实 spec；也可使用 configure-sonic 生成的内联 spec。通过私有
`token_file` 访问本机网关，凭据不进入浏览器。旧 configure、prepare、runtime-root
探测及 sonic 调试流采集入口已移除。

录制只订阅遥测、视频和只读状态。开始和持续录制都校验合同、session、实际消费及
新鲜度；支持 Planner/Pose 切换。没有固定采集时长上限。停止后由用户保存或丢弃。
保存先确认原始数据安全落盘，再后台导出 [LeRobot v2.1](lerobot-storage.md)。
数据处理失败保留原始数据并可重试；成功后清理临时数据。

实时录制接管独占的 JSON 解析结果，避免每帧深拷贝完整状态、历史与 encoder 输入。
完整前一帧仍保留用于连续性检查和异步断点归档，交接后不得修改样本；其他调用方
默认保留复制隔离。`controller.jsonl` 保存完整样本，`controller_received` 保留
接收诊断；`controller_write_returned` 保存写入返回时间及 `sample_ref`（文件、
session、index），不再重复写一份完整诊断树。该事件仍只表示缓冲写入返回，不代表 fsync。

相机入口只解析一次 MPEG-TS PTS，并将 `(payload, monotonic_ns, realtime_ns, pts)`
一起交给录制端；时间戳仍是原始 UDP 接收时刻。开始录制时同步重置解析器和队列，
不沿用上一段或空闲期间的残留包。没有共享相机入口的独立 UDP 路径自行解析一次。

SONIC 视频导出只解码原始视频一次，按真实解码 PTS 匹配相机接收时刻，
选择不晚于机器人采样时刻且不超过 75 ms 的图像。缺失、重复或倒退的时间戳
仍按协议拒绝或分段，不生成替代源时间戳。输出保持 50Hz、640×480、H.264，
重复图像复用转换结果和统计；输出完整性与视频帧数校验保留。

录制状态中的 `lab.recording_validation` 保留 OM 返回前最后复核的条件和现场数值，
通过 `runtime.jsonl` 与断点证据的 runtime context 保存。包括快照年龄、控制状态及
进程变化、各反馈流年龄、关节故障和速度。原生执行反馈已有错误时保留其原因，
另列最终复核失败条件，避免用无原因的 `record_ready=false` 掩盖来源。
状态快照和最终复核均先应用接收队列里已到达的反馈，再按原始接收时间检查新鲜度；
不等待新包，不发送控制指令，也不把应用时间改写为接收时间。因反馈不可用而失败的
状态快照不复用，下一次查询重新检查。`captured_feedback_pipeline` 和
`feedback_pipeline` 保存两次检查时各主题的接收、解码、入队、排队、应用时间及
待应用主题；这些元数据经原有数采日志和故障证据路径保存。50 ms 反馈门槛、反馈源
切换稳定期、故障和超速检查不变。

只有 `recording_status_snapshot_expired` 是唯一复核失败条件、原生执行检查通过且
合同/session 不变时，录制端允许一次立即重查；新查询需在 100 ms 内返回且全部
检查通过，否则停止。重查不刷新源时间戳、不跳过帧校验，不延长后续重查预算。
控制状态/进程变化、反馈失效、关节故障或超速不走这条重查路径。

## 当前限制与故障处理

| Path | Current condition | Effect |
| --- | --- | --- |
| Source startup | 15 seconds without both sources ready | Capture fails before recording starts |
| Source freshness | Camera or consumed controller stream older than 0.5 seconds | Capture stops with a source-loss error |
| Runtime readiness | Session/contract change, inactive control, or readiness loss; transient stale readiness gets up to 0.1 seconds of rechecks | Capture stops with the corresponding error |
| Disk reserve | Less than 512 MiB free, checked every 0.5 seconds | Capture stops to preserve existing data |
| Background export | 1200 seconds (20 minutes) of active processing; paused time excluded | Export fails, raw data retained for retry |
| Native real replay | At least 2 input frames; no configured total-duration, frame-count or plan-body size cap | Plan refused before execution |
| Native replay supervision | 0.5-second OM-to-native heartbeat/watchdog (no browser lease), 6-second entry/return deadline, scheduling and actual-output freshness checks | Controlled replay stop; existing protection retained |
| Data segmentation | Unproven index gaps or source time gaps over 60 ms; camera alignment rejection | Segment boundary, not a recording-duration cutoff |

Replay scheduling stops on a callback gap over 50 ms, one-step lateness over
30 ms, accumulated correction over 50 ms within one second, or total
correction over 100 ms. These are execution safeguards, not capture limits.
Task name and collector name accept 100 characters each; task description accepts
500.
