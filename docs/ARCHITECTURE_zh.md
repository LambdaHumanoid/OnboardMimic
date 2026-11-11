# OnboardMimic 架构

[English](ARCHITECTURE.md) · 简体中文

OnboardMimic 负责集成已有的人形 S0 运控。目前实现的真机组合只有 **Unitree G1 + SONIC**。
测试里另有双关节本体和不同协议的模拟控制器，用来验证模块边界；它们不是新增的真机支持。

## 模块分工

| 模块 | 职责 | 不应包含 |
| --- | --- | --- |
| `core/` | 生命周期、指令租约、会话和时效验证、模式切换、进程监控、适配器契约 | DDS 消息、关节顺序、模型加载、人体重定向 |
| `console/` | 电脑/手机界面、需认证的本机接口代理、按能力展示控制项 | 控制器私有协议和直接电机输出 |
| `inputs/pico/` | PICO SDK 容器、人体追踪接收、设备配置、只读骨架预览 | SONIC 姿态编码、机器人准入和控制权 |
| `robots/unitree_g1/` | 29 关节反馈和阈值、ROS/DDS、宇树模式连接、原生桥接、单发送源准入 | SONIC 模型转换与 Planner 消息格式 |
| `backends/sonic/` | 启动配置、SONIC 协议、进度解码、PICO→SONIC 映射、模型清单、构建与安装打包 | 控制台布局、其他本体的硬件假设 |

SONIC 原生程序内部仍有 G1 专属策略代码。将它放入后端模块，并不代表其模型能控制任意本体。
为 SONIC 适配新机器人，还需要对应模型和原生实现，不能只增加硬件适配器。

## 选择组合

`core/registry.py` 使用延迟加载的工厂注册本体、后端和输入设备。
`select(robot, backend)` 在启动服务和通信前，检查支持关系、关节数、非 NaN/Inf 的参考关节角和能力声明。
适配器构造函数不得连接硬件或发送指令。

```python
from onboardmimic.core.registry import select
robot, backend = select('unitree_g1', 'sonic')
```

安装的 API 和控制台服务记录同一组 `--robot` / `--backend`，新安装统一使用 `onboardmimic*` 服务名。
控制台发现后台的本体/后端不匹配会拒绝启动；急停仍可以尝试发送。

安装入口调用 `backend.installer(robot)`。目前安装方案只支持指定 Orin NX/JP5 环境上的 G1/SONIC。
安装完成不等于获得运动许可：开机只启动空闲 API 和页面，连接、准备姿态、启动策略仍分别手动执行。

## 控制台本体 / WBC 选择接口

电脑和手机的顶栏共用从注册表读取的紧凑本体 / WBC 选择框。`GET /api/platform` 返回 `schema_version: 1`、
`robots`、`backends`、经过校验的兼容 `pairs`、控制台的 `selected: {robot_id, backend_id}`
以及 `switching`。注册信息表示适配能力，不代表模型已经就绪或硬件已连接。
实际运行的组合与状态仍以 `GET /api/controller` 为准。

当前服务返回 `switching.supported: false`，原因是 `service_configuration_required`。
下拉框可以展开，显示部署时配置和不可选的“敬请期待”；更换支持的组合需在停止控制器后同步配置两项服务。
目前没有改变运行组合的 HTTP 接口，也不会由页面自动重启服务。

`console/static/platform.js` 在 `window.onboardMimicPlatform` 上预留
`setSelectionHandler(async pair => …)`，供后续生命周期切换实现接入。
前端同时要求绑定处理函数、服务端声明支持、运行组合匹配、控制器停止且没有策略进程，才允许更改选择。展开下拉框不发送控制请求。
不兼容组合不可选择；应用后重新读取服务端配置。处理函数还须在服务端重新检查控制权和停止状态，
原子地应用配置，并保留用户手动准备和启动的流程。仅绑定前端处理函数不能开启当前版本的切换。

## 新增本体

在 `robots/<id>/adapter.py` 实现 `RobotAdapter`：

- `id`、`label`、`joint_count`、`state_topics`：本体标识和反馈契约。
- `telemetry(latest, target, now)`：返回 `q`、`dq`、`fresh`、`valid`、`faults`、
  `upright`、`max_speed_rad_s` 等规范化状态。使用单调时钟，关节顺序必须与后端参考角一致。
- `safe(telemetry)`：本体特定的准入和运行阈值。缺失、损坏、过期反馈不能通过。
- `guard(owned_pid)`：检查当前控制权和通信状态；不得为了抢占而停止其他控制者。
- `ingest`、`feedback_ready`、`feedback_status`：可选反馈健康状态；G1 用于主备反馈与接管准入检查。
- `connection()`：控制台显式连接、状态查询和取消连接流程。
- `service_units(root, config)`、`environment_template(root)`：本体服务与配置模板。
  硬件服务不能因此获得开机自动输出电机指令的行为。

用 `register_robot` 注册，并补齐兼容的后端方案、离线协议测试和真机验收。
控制权需要跨安装目录互斥：G1 沿用 `/tmp/sonic-motor-control.lock`，不要改名或删除来抢占。

启动前严格检查准入：发现其他控制器或检查不完整时拒绝准备姿态。控制器运行后，
`core/admission.py` 将这类检查失败作为诊断告警，保留当前控制者。持续异常每 5 秒
最多记录一次 `running_admission_warning`，恢复后记录 `running_admission_recovered`，
日志位于 `var/logs/runtime-events.log`。进程诊断只记录限量的 PID、程序名和错误信息，
不记录完整命令参数。桥接共享锁继续拒绝后来者，通信看门狗、实测反馈限制和急停
继续有效。这项进程检查不代表能隔离外部 DDS 发送源。

## 新增 S0 后端

在 `backends/<id>/adapter.py` 实现 `S0Backend`：

- `compatible_robots`、`capabilities`、`supported_inputs`：显式支持矩阵。
- `baseline(robot)`：本体硬件顺序下的参考关节角，必须有限且维数一致。
- `launch_spec(robot)`：返回 `LaunchSpec(argv, cwd, env)`，仅在明确准备请求通过检查后调用。
  使用参数列表，不拼接 shell 命令。
- `progress(log)`：将控制器反馈转成 `BackendProgress`；不能仅凭时间经过推断准备或接管成功。
- `decode_status(topic, value)`：返回规范化的 `{mode, playing}`；无关消息返回 `None`。
- `transport(gateway, robot)`：后端协议收发，提供 `thread`、`stop`、`ready`、`last_tick`、
  `error`、`stage`、`shutdown_reason`。反馈经 `gateway.ingest`，生命周期更新后才发送获准指令。不得从模型就绪推断运动已获许可。
- `prepare_seconds`、`handoff_seconds`、准备/接管超时和 `max_speed`：后端约束及界面显示信息。
- `pose_reader(input_id, path)`：可选映射线程，提供 `thread`、`stop`、`snapshot()`；
  快照包括 `ready`、`reason` 和后端数据包。
- `installer(robot)`：可选组合安装流程；不支持的组合必须在修改安装前拒绝。

当前公共生命周期要求后端提供 `planner` 待命/速度模式，`pose` 可选。Pose 断流后返回 Planner 并等待确认。
没有这种回退能力的控制器需要扩展生命周期，不能虚报能力。SONIC 编码器索引和 ZMQ 格式留在后端内部。

Planner 接口表达方向、目标速度和朝向。核心负责序列号、550 ms 租约和朝向积分，后端负责转成自己的消息。
SONIC 传输适配器保留原协议数值约定。

## 新增追踪输入

实现 `TrackingInput`，通过 `register_input` 注册。`status()`、`skeleton()` 仅用于只读诊断。
每个支持该输入的后端还需提供明确的映射；接收到人体骨架不代表可以接管机器人。
PICO SDK 坐标保留在 `inputs/pico/`，SMPL/手腕转换和 SONIC Pose 编码位于 `backends/sonic/pico/`。

## 路径与兼容

- `backends/sonic/resources.json`：模型、SDK 固定版本和校验值。
- `backends/sonic/native_source_manifest.json`：原生源码校验值。
- `backends/sonic/deploy/`：原生 CMake、源码、策略配置和参考动作。
- `backends/sonic/deploy/onboard/`：模型转换、TensorRT 验证和缓存工具。
- `var/`：运行日志、私有 DDS 配置和连接状态，忽略提交。
- `inputs/pico/runs/`、`devices.json`：采集和设备配置，忽略提交。
- 根目录 `scripts/`、`install_scripts/`：保留的命令入口；具体实现位于对应模块。

在仓库根目录通过 `python -m onboardmimic.core.runtime --help`、
`python -m onboardmimic.console.server --help` 查看入口。包内文件使用模块方式执行，避免依赖工作目录导入。
通用 HTTP 路由为 `/api/controller`、`/api/control`。
配置目录统一为 `~/.config/onboardmimic`；控制权锁继续与已有桥接互斥，协议字节和第三方版权标识保持原值。

升级器识别基线生成的服务，将标准 LAN/PICO 覆盖配置重新生成为新路径，保留 token，并复制已有的 PICO 设备配置。
自定义覆盖配置保留并要求核对，不会自动停止运行中的服务。目标机验证完成前保留旧安装目录，恢复步骤见[升级指南](UPGRADE_zh.md)。

## 验证

按源码指南安装测试依赖后运行 `PYTHON=python3 bash scripts/test.sh --all`。
`tests/test_adapters.py` 覆盖双关节本体、不同 S0 反馈协议、组合不匹配、能力拒绝和各后端速度限制。
现有 G1 生命周期、PICO 映射、HTTP 权限、控制台、安装升级和打包检查继续保留。

不连接 ROS 或电机的原生检查：

```bash
cmake -S onboardmimic/robots/unitree_g1/native_bridge -B /tmp/onboardmimic-bridge-test -DBUILD_ROS_BRIDGE=OFF
cmake --build /tmp/onboardmimic-bridge-test
ctest --test-dir /tmp/onboardmimic-bridge-test --output-on-failure
python3 onboardmimic/robots/unitree_g1/native_bridge/differential_test.py \
  --reference onboardmimic/robots/unitree_g1/bridge/lowcmd_transport.py \
  --binary /tmp/onboardmimic-bridge-test/core_test
```

离线测试不能替代目标机安装、推理、开机和动作验收。已发布 G1 配置的范围见[验证说明](VALIDATION.md)。
