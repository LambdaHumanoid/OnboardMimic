# SONIC reference-plan v2 执行协议

本文描述当前 SONIC adapter 与 Lab 的真机回放协议。Lab 负责数据与计划，OM 负责机载执行、控制权和保护。

## 执行路径与启用

OML 选取一个连续片段 → 一次上传整个 reference-plan v2 → OM 校验 → SONIC 原生控制循环按 50Hz 逐帧调度 token64 → 直接填入 decoder 的 token 输入、绕过 encoder → 本机真实反馈形成 state/history → 原有命令后处理和保护桥 → DDS publish。

加载不创建或启动控制进程。开始要求用户已经通过原有流程启动 SONIC、在 Planner 模式站稳；回放 API 不自动准备姿态、启动 SONIC 或切换机载服务。

运行端使用显式参数 `--enable-reference-replay`，要求已有 `--physical`、SONIC、`--experimental-protection-socket`、`OM_SONIC_NATIVE_RECEIVER=1` 和 `OM_SONIC_NATIVE_TELEMETRY_PORT`。沿用已有原生/桥/receiver 遥测端口；运行端为子进程创建私有 Unix socket，不增加外网监听端口。没有此参数时回放接口拒绝执行。

**正常完成和用户停止平滑返回 Planner；反馈/权限/执行故障进入保护退出。浏览器关闭或失联不终止已启动任务。** 故障保护退出不调用会取消布防的普通 halt 分支；由现有受保护写入端在 WBC 退出/输出超时后接管。故障保护退出后需要重新准备控制器，不自动恢复遥操。明确的急停仍可取消保护动作，优先级不变。

## HTTP 接口

全部复用运行端 `127.0.0.1:18921` 的 `Authorization: Bearer ...`；远程沿用鉴权隧道，不逐帧发 token。

| 方法 | 路径 | 请求/作用 |
| --- | --- | --- |
| POST | `/api/execution/v2/plans` | `{"plan": <OML reference-plan v2>}`，完整校验后保存一个片段；不运动 |
| POST | `/api/execution/v2/plans/{plan_id}/start` | 通用控制凭据；开始一次回放，返回新的 `run_id` |
| GET | `/api/execution/v2/plans/{plan_id}` | 当前实际进度/停止确认；不续租 |
| POST | `/api/execution/v2/plans/{plan_id}/stop` | 通用控制凭据 + 当前 `run_id`；请求平滑返回 Planner，随后查询接管或故障停止确认 |

`plan_id` 是完整上传 JSON 的规范化 SHA256。一次只保留一个计划；运行/停止确认中拒绝替换。再次回放需重新加载；成功返回 Planner 后可以继续使用当前 SONIC 实例。

开始/停止通过 `/api/controller/ticket` 获取轻量控制凭据：

```json
{"client":"oml-replay-ui","seq":1,"epoch":"<当前epoch>","ticket":"<当前ticket>"}
```

`ticket` 有效期250ms；每次显式命令前取得新凭据，`seq` 按后台 client 单调递增。停止另加当前 `run_id`，仍检查后台 owner/run。已启动任务由 OM 持有，不需要 HTTP 续租，GET 只查询。加载仅需 Bearer 鉴权。

返回字段例（示意值，非真机数据）：

```json
{
  "schema":"onboardmimic.execution.status","schema_version":2,
  "plan_id":"<SHA256>","model_validation":"matched_live",
  "run_id":123,"state":"running","reason":"",
  "source_frame_position":7,"source_frame_index":804,
  "source_reference_id":"<录制源reference_id>",
  "output_commit":{"boundary":"protected_writer_dds_publish_returned_not_motor_execution",
    "timestamp_monotonic_ns":1234567890,"stream":111,"sequence":222,"generation":9},
  "progress_boundary":"protected_writer_dds_publish_returned_not_motor_execution",
  "stop_ack":false,
  "stop_boundary":"owned_wbc_exited_and_protected_writer_terminal_not_contact_verified",
  "task_lifetime":"runtime_owned", "browser_lease_required":false
}
```

正常状态：`empty → loaded → loading → starting → running → completed`；故障退出：`running → stopping → stopped/failed`；用户停止进入返回 Planner 阶段，成功后为 `completed`、`reason=operator_stop`。`loading` 是开始后向本机原生实例装载紧凑 token 计划；`starting` 包含真实历史预热和过渡。尚无同一 run 的实际提交证据时，三个源帧字段为 null，不能按发送数、计时器或 `running` 字样推算执行帧。

`stop_ack=true` 需要原有 owned WBC 已退出、同一个 bridge PID/owner/generation/stream 的新鲜状态确认 fenced，且保护已进入终态（或显式急停已确认）。不表示接触地面已经传感器验证；保护中止返回 failed。正常播完必须逐帧具有实际消费证据并确认返回 Planner；主动停止可在部分帧执行后完成 Planner 接管，`completed` 不单独表示整段已播完。消费证据缺帧会以 consumption_gap 退出，不会只看末帧推断整段执行成功。失联时保持未确认，不把 HTTP 超时当成功停止。

## 校验和控制权

- 只接收 `onboardmimic.lab.reference-plan` **schema_version=2**；不接旧 token-plan v1。
- 当前994维输入/10帧历史布局；单片段至少2帧、50Hz。无固定总时长、总帧数、HTTP JSON或原生紧凑计划大小上限；实际受可用内存约束。原生套接字长度字段为64位，需与OM一起部署。加载超时和执行保护仍保留。
- 仅接受 `sample_profile=lerobot_body_v21`、`reference_evidence=consumed_at_capture` 的计划。校验 spec/contract 摘要与 source_sha256 格式、token64 和身体状态的维度及有限 float32、重力方向、连续 index、严格递增源时钟和对应 offset；拒绝 fixture、非 physical 来源及跨断点计划。精简计划不含完整录制历史或 decoder 输入，不进行这些张量的复算。
- 与实际加载的 decoder/encoder TRT/ONNX、观测/参数/历史/关节映射逐项匹配。允许记录与执行的可执行文件 hash 不同，仍保留原记录来源；开始时另行核验**当前** WBC/保护桥内核进程身份和实际加载 spec，绑定 PID/代数/stream，运行中变化即退出。
- 如果加载时原生实例尚未提供 spec，返回 `model_validation=pending_live_instance`，开始时必须完成实际匹配；不能当作可以开跑。加载时实例已存在则立即匹配。
- 开始要求当前自有 SONIC active、真实 Planner 模式反馈、无模式切换、主备反馈有效、保护 fresh/armed 且无 fence/estop/takeover、站稳、无其他有效遥操 owner。先进入首帧参考，再确认实时关节速度不超过0.6rad/s、基座重力方向与录制起点差不超过0.15，并连续稳定0.2秒。首帧过渡限时 6 秒，超时走保护退出。
- 回放独占控制；拒绝遥操/模式切换/重新准备，普通网页停止也进入回放受控退出。急停保持最高优先级。

## 原生调度、历史和超时

原生服务接收紧凑的 `entry_protocol=3 / period_ns / artifacts / start_q / start_gravity / frames[{offset_ns,token}]`。首帧状态只供进入检查/诊断，**不接收录制的历史，不将录制状态作为 decoder 反馈或电机关节目标**。原生 load 回复必须确认 `entry_protocol=3`（首帧过渡、逐token的50Hz排程、正常结束返回Planner），旧程序不能被新后台直接启动回放。

开始后使用连续真实反馈继续运行 Planner 预热1秒；随后保持第一个 token，通过原有 Pose 命令平滑混合2秒，再等待实时反馈稳定确认。从确认时刻开始新的执行时钟，从源第0帧起每20ms推进一个token；源offset只作为录制证据。`state=starting` 时，顶层字段 `entry_phase` 依次为 `warming / transitioning / aligning`，之后为 `playing`；进度只认实际受保护提交。任何进入阶段都保留租期、反馈、控制权和保护检查。

进入过程参考 FH Navi 的首个 reference 过渡逻辑。录制 state 是当时的反馈，
不是 token 对应的电机位置指令；PD 目标与实测角度之差不能要求必须归零。

计时基于原生 monotonic clock，不依赖HTTP/网络逐帧送达。源 `offset_ns` 不改写；名义执行排程为 `due[i] = i * 20ms`（有界顺延见下文），每个源token按顺序执行，不插值、不跳帧、不批量追赶。状态 `timing.mode=sequential_50hz`，`source_duration_ns` 保留录制时长，`scheduled_duration_ns=N*20ms` 表示播放时长（不含首帧过渡和退出）。源时间连续性、会话和反馈证据校验仍保留；没有固定源时长或播放时长上限。执行比新帧排程落后超过30ms时仍以 `schedule_overrun` 保护退出。末帧等待实际提交确认最多100ms；确认后运行时发送 run 绑定的 finish，开始返回 Planner，超时未确认则保护退出。

参考 OpenLambda `c51574f` 的 `data_collection/human/data_alignment/src/simulation/rollout.py`：按固定50Hz逐token回放。这里采用相同的顺序时间语义，保留 OM 的机载调度、受保护输出确认、租期及独立超时，不直接移植其仿真网络发送循环。


Lab 当前页面不提供仿真回放。仿真接口保留；精简 LeRobot 数据不具备完整推理历史，不支持依赖这些历史的数值复算。

每次回放推理记录内部 mode=3、run/frame 标识，并沿原有 inference index → 实际 transport sequence → 受保护 DDS commit 关联。只有命令完全相符、当前 writer authority 正确的提交才能推进。过渡输出不冒充源帧。回放证据仍用于 OM 内部进度关联，同时发布到 OML telemetry topic，附带实际 `native_mode`、`token_source=replay` 与 run/frame 来源。录制资格与模式无关，但仍要求实际消费、有效历史、进程身份和保护权限。过渡帧的 `scheduled_frame=false` 不会推进源计划帧进度。见 [模式无关录制](SONIC_MODE_INDEPENDENT_RECORDING.md)。

OM 独立工作线程检查消费反馈/故障并维护原生心跳；不再存在浏览器/HTTP 租期。原生服务和控制循环双向检查500ms租期及控制心跳；原生看门狗锁存停止后，LowCommandWriter 也会停止刷新旧命令，即使推理控制循环卡住，现有独立保护桥仍能通过输出超时接管。整个原生进程卡住/退出由现有桥 watchdog/pidfd 覆盖。



## 调度与返回确认

每次控制回调最多推进一个源 token，不批量补发。调度允许有限顺延以消除积压：单步迟到超过30ms、回调间隔超过50ms、滚动1秒内顺延超过50ms或整段顺延超过100ms，均以 `schedule_overrun` 保护退出。状态中的 `schedule` 保留实际时序与停止原因。

正常完成需确认末帧实际提交，再请求返回 Planner。用户停止从当前 token 开始返回，不跳到录制末帧。返回期间保持控制权，用2秒平滑过渡到实时 Planner，并确认关节速度、重力方向连续稳定200ms及新的受保护输出。返回超时为6秒。

成功接管返回 `state=completed`、`completion_ack=true`、`stop_ack=false`，SONIC 继续运行。主动停止另带 `reason=operator_stop`，进度保留最后实际提交帧。故障保护退出使用 `stop_ack` 确认；急停独立于普通停止。

## 任务生命周期与页面

回放由 OM 后台持有；浏览器切标签、刷新、关闭或 Lab 查询断连不会取消已启动任务。重新连接后查询同一 plan/run，不自动再次启动。后台维护原生500ms心跳，看门狗、实际反馈、进程身份、控制权和保护检查持续生效。后台进程重启不自动恢复运动；实时 Planner 指令和 Pose 输入仍有各自的有效期。

Lab 确认 `task_lifetime=runtime_owned`、`browser_lease_required=false` 后使用加载、开始、状态和停止接口。准备和返回阶段显示实际 `entry_phase`，执行进度以受保护提交为准。普通停止等待 Planner 接管确认，故障停止等待保护退出确认。

## 反馈停顿诊断

故障快照的 `admission.feedback_receiver.applied` 分别保留 socket 收包、解码结束、
入队、出队和应用完成时刻。原有 `feedback_timing.inbox_wait_ms` 是从收包到进入
`ingest` 的总时间，包含解码与线程停顿，不能单独称为队列等待；真实队列驻留使用
`queue_wait_ms`。新字段只用于诊断，不改变状态新鲜度所使用的原始收包时间。

`runtime_diagnostics` 保存主进程最近的慢阶段、GC 起止时间/触发线程/调用位置，
以及现有监测线程每约 100 ms 异步读取的逐线程 `schedstat`。阶段覆盖状态接收、
PICO 映射、控制循环和回放处理。GC 回调不读文件、不枚举对象或保存局部变量；
调度记录是累计计数且带独立采样时刻，不能冒充故障瞬间的精确运行队列时间。
这些记录均有界，不关闭主进程 GC、不改变线程优先级或控制阈值。

## 验证

软件测试覆盖计划校验、控制权、实时历史、首帧过渡、逐帧提交、正常完成与主动停止返回 Planner、独立看门狗和故障退出。复现入口：

```bash
python -m unittest tests.sonic.test_reference_replay -q
cmake -S onboardmimic/robots/unitree_g1/native_bridge -B /tmp/om-replay-tests -DBUILD_ROS_BRIDGE=OFF
cmake --build /tmp/om-replay-tests -j2
ctest --test-dir /tmp/om-replay-tests --output-on-failure
```

分别记录当前版本的软件测试、目标机部署检查和现场运动验收结果，并绑定对应源码、模型和运行环境。

During external-token replay, PICO pose mapping is suspended to remove unused Python work from the runtime process. The PICO receiver continues running. Mapping resumes after leaving replay with a new five-frame window and the existing pose-admission checks; stale preview/pose packets are invalidated. Planner operation otherwise keeps pose mapping available. State freshness limits and protective exits are unchanged. Scheduler diagnostics run on a separate 100 ms worker so inventory scans cannot directly delay their sampling.
