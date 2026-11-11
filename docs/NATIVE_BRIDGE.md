# Native bridge and fresh feedback / 原生桥接与新鲜反馈

The command path uses a C++ UDP receiver/validator and one independently timed DDS
writer. Two separate C++ processes subscribe to LowState and IMU; a loopback-only
Python relay selects fresh feedback. The console supports Planner and PICO Pose.

原生 C++ 指令链路包含 UDP 接收/校验器和独立定时的 DDS 写入线程。两个独立 C++
进程订阅 LowState 与 IMU，仅 loopback 的 Python relay 选择新鲜反馈。控制台支持
Planner 和 PICO Pose。

## Services and ports / 服务与端口

The `onboardmimic` service/configuration identifiers are used by the current code.
The API and console start at boot; an enabled PICO receiver also starts in receive-only
mode. The command bridge and its feedback readers start through **Connect services**.
Explicit bridge start brings up `onboardmimic-state-primary`,
`onboardmimic-state-standby`, and `onboardmimic-state-relay`. Stopping the bridge
also stops these read-only services. Their failure restart does not restart SONIC,
prepare posture or release vendor mode.

> 当前代码使用 `onboardmimic` 服务与配置标识。API 和控制台开机启动，已启用的 PICO 接收器也以只接收模式启动。指令桥接及反馈接收器由“连接服务”启动；显式启动桥接会带起 `onboardmimic-state-primary`、`onboardmimic-state-standby` 和 `onboardmimic-state-relay`，停止桥接也会停止它们。这些只读服务故障重启时，不会重启 SONIC、准备姿态或释放宇树模式。

| Process / 进程 | Interface / 接口 |
| --- | --- |
| Primary / standby receivers / 主备接收器 | Loopback TCP 16665 / 16666 |
| Fresh-state relay / 新鲜反馈转发器 | Loopback TCP 16660 |
| Native command bridge / 原生指令桥接 | Loopback UDP 16663; compatibility / 兼容 TCP 16661 |

The compatibility TCP port cannot grant command authority. UDP preserves the wire
protocol and validates CRC, finite values, sender age, stream and sequence.
The 100 ms watchdog, 150 ms damping ramp, 25-frame new-stream validation and
500 ms measured recovery remain unchanged. Forwarding is capped at 500 Hz LowState
and 250 Hz IMU; no stale resend. Source transitions require fresh dual-topic data
and 40 ms settling before new motion requests. Stop and emergency stop stay available.

> 兼容 TCP 端口不能授予指令控制权。UDP 保留原协议并校验 CRC、有限值、发送时效、数据流与序列号。看门狗为 100 ms，阻尼渐变为 150 ms，新流验证 25 帧，实测恢复 500 ms。LowState/IMU 转发上限分别为 500/250 Hz，不重发过期样本。切换反馈源须有新鲜的双主题数据并稳定 40 ms，之后才接受新运动请求；停止与急停仍可用。

## Installation / 安装

Source and prebuilt installations need ROS2 Foxy and a workspace providing
`unitree_hg` and `unitree_api`. The setup wizard configures their paths; manual
installations set `ROS_SETUP` and `UNITREE_ROS_SETUP` to their real setup scripts.
The prebuilt package includes ARM64 `sonic_state_reader` and `sonic_command_bridge`,
but not the target ROS2 installation. Source builds also need ROS2 development
headers, libzmq, msgpack and nlohmann-json headers.

> 源码和预编译安装均需要 ROS2 Foxy，以及提供 `unitree_hg` 和 `unitree_api` 的工作空间。向导配置这些路径；手动安装时，将 `ROS_SETUP` 和 `UNITREE_ROS_SETUP` 指向实际 setup 脚本。预编译包包含 ARM64 `sonic_state_reader` 和 `sonic_command_bridge`，不包含目标 ROS2 环境。源码构建还需要 ROS2 开发头文件、libzmq、msgpack 和 nlohmann-json 头文件。

Set `CYCLONEDDS_URI` in `~/.config/onboardmimic/bridge.env` to a local DDS XML template.
On explicit bridge/receiver startup, the direct robot-network route selects the
actual NIC. A private XML copy is written under `var/`; the original
XML and network routes are unchanged. Missing/ambiguous routes fail instead of
guessing eth0. The supported internal network is 192.168.123.0/24.

> 将 `~/.config/onboardmimic/bridge.env` 的 `CYCLONEDDS_URI` 设为本地 DDS XML 模板。显式启动桥接/接收器时，根据直达机器人网络的路由选择实际网卡，在 `var/` 下生成私有 XML；原 XML 与网络路由保持不变。路由缺失或不明确时失败，不猜测 eth0。支持的内部网络为 192.168.123.0/24。

For installation updates, stop the controller and follow the [upgrade guide](UPGRADE.md). Preserve the current installation and configuration for rollback.

> 更新安装时，先停止控制器，再按[升级指南](UPGRADE_zh.md)操作。保留当前安装目录与配置以便回退。

## Evidence and limits / 验证依据与边界

`bridge_reference.json` records imported reference hashes. Selected C++ sources
were imported with identifiers and deployment paths adapted for this project.
Current G1 installation, boot and Planner/PICO field-acceptance results are recorded
in [VALIDATION.md](VALIDATION.md).

> `bridge_reference.json` 记录导入参考文件的哈希。选用的 C++ 源码已调整标识和部署路径，以适配本项目。当前 G1 安装、开机以及 Planner/PICO 现场验收结果见[验证范围](VALIDATION.md)。

Offline checks cover watchdog/reference transitions, feedback freshness/failover,
lifecycle admission, service migration and NIC/XML handling. The package includes
`bridge_build_report.json` with source and binary hashes. Cross-compilation and
local loader checks do not replace target installation, inference, boot or physical
commissioning.

> 离线检查覆盖看门狗/参考状态切换、反馈时效和故障切换、生命周期准入、服务迁移及 NIC/XML 处理。包内 `bridge_build_report.json` 记录源码和二进制哈希。交叉编译与本地加载器检查不能替代目标机安装、推理、开机或现场调试。
