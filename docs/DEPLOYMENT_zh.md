# OnboardMimic：G1 首次部署

[English](DEPLOYMENT.md) · 简体中文

**安装 → 配置通信 → 打开页面 → 连接服务 → 准备姿态 → 启动 SONIC。**

目标配置：G1 29-DOF 身体、Orin NX 16 GB、JetPack 5 / L4T 35.3.1、CUDA 11.4、TensorRT 8.5.2。以下命令均以机器人普通用户在 `OnboardMimic` 根目录执行，除非另有说明。

## 1. 完成安装

上述 G1 / JP5 配置优先使用 v0.1.0 预编译包：

- **预编译包（推荐）**：下载完整的 [v0.1.0 安装包](https://github.com/LambdaHumanoid/OnboardMimic/releases/tag/v0.1.0)；安装向导 `python3 scripts/setup.py` 会完成下面第 2、3 步。所需文件集合见[安装包说明](PREBUILT_zh.md)。
- **源码构建**：按[源码构建指南](SOURCE_BUILD_zh.md)安装、下载模型、编译和构建引擎，通过测试后进入第 2 步。编译与模型构建顺序执行。

安装脚本会安装 `python3-venv` 并在虚拟环境内更新 pip；需要联网和 sudo 权限。它不升级 JetPack，不启动运动控制器。向导缺少 ROS2 依赖时会给出 `bash install_scripts/install_ros_dependencies.sh` 修复命令。第 2、3 步为源码构建或手动配置用户保留。

## 2. 配置通信

### 找到 ROS2 和宇树消息工作空间

常见 ROS2 路径是 `/opt/ros/foxy/setup.bash`，宇树工作空间路径因机器而异。可只读查找候选：

```bash
find /opt/ros -maxdepth 2 -name setup.bash 2>/dev/null
find "$HOME" -maxdepth 5 -path '*/install/setup.bash' 2>/dev/null
```

选择实际工作空间并验证，替换下方 `/ABSOLUTE/WORKSPACE`：

```bash
source /opt/ros/foxy/setup.bash
source /ABSOLUTE/WORKSPACE/install/setup.bash
ros2 pkg prefix unitree_hg
ros2 pkg prefix unitree_api
ros2 pkg prefix rmw_cyclonedds_cpp
/usr/bin/python3 -c 'import rclpy; from unitree_hg.msg import LowState; from unitree_api.msg import Request, Response; print("ROS messages OK")'
```

成功时前三条打印对应包的安装目录，最后打印 `ROS messages OK`。这些检查不创建 ROS 节点、不发送控制指令。若缺包可执行[依赖修复](PREBUILT_zh.md#缺少-ros2-或宇树消息包)；仅安装宇树 Python SDK 不能替代 ROS 消息工作空间。

### 创建服务与 DDS 配置

```bash
.venv/bin/python scripts/install_services.py
cp -n onboardmimic/robots/unitree_g1/systemd/cyclonedds.xml.example "$HOME/.config/onboardmimic/cyclonedds.xml"
chmod 600 "$HOME/.config/onboardmimic/cyclonedds.xml"
nano "$HOME/.config/onboardmimic/bridge.env"
```

服务安装器生成私有 token，配置控制台/API 开机自启，但不会立即启动任何服务。重复执行保留 token；已有部署请走[升级指南](UPGRADE_zh.md)。若提示 linger 权限不足，按提示执行 `sudo loginctl enable-linger 用户名` 后重试。

在 `bridge.env` 中填写三个**实际绝对路径**，将示例用户名和工作空间替换成自己的值；不要写 `$HOME` 或 `~`：

```ini
ROS_SETUP=/opt/ros/foxy/setup.bash
UNITREE_ROS_SETUP=/ABSOLUTE/WORKSPACE/install/setup.bash
CYCLONEDDS_URI=file:///home/ROBOT_USER/.config/onboardmimic/cyclonedds.xml
```

`cp -n` 保留已有 XML，不会覆盖现场配置。新模板中的 `auto` 会由连接流程替换成实际机器人有线网卡；不要把 Wi-Fi 网卡填进去。网卡检测要求能直接到达机器人内部 `192.168.123.0/24` 网络，不修改路由。

在当前终端载入配置，并做一次不发指令的检查：

```bash
set -a
source "$HOME/.config/onboardmimic/bridge.env"
set +a
source onboardmimic/robots/unitree_g1/bridge/environment.sh
.venv/bin/python -c 'import rclpy; from unitree_hg.msg import LowState; from unitree_api.msg import Request, Response; print("Bridge Python imports OK")'
.venv/bin/python -m onboardmimic.robots.unitree_g1.bridge.network \
  --template "$CYCLONEDDS_URI" --output /tmp/onboardmimic-dds-check.xml
```

应打印 `Bridge Python imports OK` 和 `Robot DDS NIC: <实际网卡名>`。最后一条只读取网络配置并生成临时 XML，不启动桥接、不释放宇树模式。检查失败时先按第 6 节排查。

## 3. 打开控制台

```bash
systemctl --user start onboardmimic.service onboardmimic-console.service
.venv/bin/python scripts/control.py status
```

首次启动应为 `state: stopped`、`pids: []`。尚未点击“连接服务”时，反馈未就绪是正常的。电脑用 [README 中的 SSH 隧道](../README.md#open-the-console--打开控制台)打开网页；手机先完成[局域网访问配置](CONTROL_EXTENSIONS_zh.md#lan-access)，再打开 `http://ROBOT_IP:8769/remote`。

控制台和 API 此后开机就绪，进程退出后间隔 3 秒自动重启；恢复时保持空闲，不恢复上次策略或运动请求。连接服务、准备和启动仍需手动。PICO 是可选项，先用 Planner 验证基础流程，再按 [PICO 指南](CONTROL_EXTENSIONS_zh.md#pico-setup)配置头显。

## 4. 按网页顺序启动

首次操作时人员在场，机器人有可靠支撑，准备好现场急停。额外安装的其他运动控制器需先按其自身流程停止。

| 页面操作 | 发生什么 | 成功标志 |
| --- | --- | --- |
| **连接服务** | 检查反馈，按需释放宇树原运动模式，启动本项目桥接；不会回正 | 显示“控制服务已连接”，“准备姿态”可用 |
| **准备姿态** | 从当前关节位置向默认姿态插值约 3 秒 | 状态变为 prepared，现场姿态正确 |
| **启动 SONIC** | 策略经过约 1 秒插值接管 | 状态变为 active，Planner 控制可用 |

“连接服务”包含约 3 秒发送源检查，请等它完成。发现另一个控制程序时会拒绝连接，不自动杀死或抢占它。页面刚打开、网络恢复和反馈恢复都不会替你点击这些按钮。

先验证支撑下的准备、停止和接管，再进行地面小幅行走。Planner 默认目标速度 0.5 m/s；WASD / 方向键行走，Q/E 转向，松开停止。更多按键、手机摇杆和 PICO 切换见[控制台说明](CONTROL_EXTENSIONS_zh.md)。

## 5. 停止

- **停止运动**：清除行走/转向请求，SONIC 继续运行。
- **急停**：终止本网关拥有的 SONIC 进程；不是断电急停，也不保证机器人保持站立。

需要关闭后台或升级时，先可靠支撑机器人，点击急停并确认策略进程已退出，再按[升级与回退](UPGRADE_zh.md)停止相关服务。不要把“停止运动”当作“SONIC 已退出”。

## 6. 常见问题

| 现象 | 先检查 |
| --- | --- |
| 找不到 `setup.bash` / `unitree_hg` / `unitree_api` | 第 2 步的路径、ROS 包和 Python 导入检查；工作空间是否完成编译并已 source |
| `No direct robot NIC` / 路由不明确 | `ip -br address`、`ip route get 192.168.123.161`；应为机器人有线网卡的直连路由，不经过网关 |
| `DDS template must select exactly one NIC` | XML 是否采用本页模板，或已有配置是否只指定一个网卡 |
| “连接服务”失败 / 桥接未就绪 | 页面具体原因和下方日志；不要跳过连接直接准备 |
| 发送源或端口被占用 | 是否有另一个运动控制器；按该程序流程停止，不要批量杀进程 |
| 页面能打开，但反馈未就绪 | 未点击“连接服务”时属正常；连接失败则检查 ROS 环境和有线网络 |
| 手机打不开页面 | [局域网访问](CONTROL_EXTENSIONS_zh.md#lan-access)的监听地址、实际机器人 IP、设备互访及 TCP 8769 |
| 缺 venv / pip 依赖冲突 | 用当前安装脚本完成依赖步骤；应自动安装 venv 并更新其 pip，先处理 apt/pip 报错 |
| API 退出或网页短暂断开 | 查看 `journalctl --user -u onboardmimic.service -n 80` 和 `var/logs/runtime-events.log`，可区分停止信号、通信异常、心跳超时和系统校时；不要通过放宽保护阈值绕过 |
| `Model hash` / TensorRT 版本检查失败 | 是否混用了不同包、是否符合支持配置；不要跳过校验启动策略 |

```bash
journalctl --user -u onboardmimic.service -u onboardmimic-console-connect.service \
  -u onboardmimic-bridge.service -n 80 --no-pager
.venv/bin/python -m onboardmimic.robots.unitree_g1.preflight
```

`preflight` 是诊断快照，`physical_commissioning_pending` 不是自动运动许可。反馈与端口细节见[原生桥接说明](NATIVE_BRIDGE.md)，发送源检查边界见[控制权说明](CONTROL_AUTHORITY.md)。

## 进阶：命令行与 HTTP

网页流程走通后，也可用命令行。每次只执行当前阶段需要的一条；准备要等待 prepared，启动要等待 active，再进入下一步。

| 操作 | 命令 |
| --- | --- |
| 连接服务（可能释放宇树模式） | `systemctl --user restart onboardmimic-console-connect.service` |
| 查看状态 | `.venv/bin/python scripts/control.py status` |
| 支撑下准备姿态 | `.venv/bin/python scripts/control.py prepare` |
| 确认姿态后启动策略 | `.venv/bin/python scripts/control.py start` |
| 地面行走条件满足后前进 1 秒 | `.venv/bin/python scripts/control.py forward --speed 0.5 --duration 1` |
| 停止运动，保留 SONIC | `.venv/bin/python scripts/control.py stop` |
| 终止 SONIC | `.venv/bin/python scripts/control.py estop` |

`left/right` 是侧移，`turn-left/turn-right` 是转向。目标速度接受 0..5 m/s，这是输入范围，不是高速行走资格验证。

HTTP 支持 `GET /api/controller`、`POST /api/controller` 和 `POST /api/control`。POST 不自动重试；运动请求获取 epoch/ticket 后带递增 seq，租约约 550ms。网关只监听 `127.0.0.1:18921`，远程客户端使用隧道：

```bash
ssh -N -L 127.0.0.1:18922:127.0.0.1:18921 USER@ROBOT_IP
```

客户端私下配置同一 HTTP token（文件权限 0600），向 `http://127.0.0.1:18922` 发请求。不要把 token、SSH 密码写进仓库。自定义控制器接入和端口规则见[原生桥接](NATIVE_BRIDGE.md)与[控制权说明](CONTROL_AUTHORITY.md)。
