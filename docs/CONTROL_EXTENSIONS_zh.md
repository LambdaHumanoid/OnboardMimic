# G1 控制台：Planner 与 PICO Pose

[English](CONTROL_EXTENSIONS.md) · 简体中文

当前源码只提供一条使用路径：**安装检查 → 连接服务 → 准备姿态 → 启动 SONIC → Planner 或 PICO Pose**。PICO 是可选项，没有头显也能完整使用 Planner。

本页对应 `main` 分支的 OnboardMimic 目录布局。不要将源码覆盖进正在运行的安装，升级请按[迁移指南](UPGRADE_zh.md)操作。

## 启动控制台

**使用预编译安装向导的用户：**运行 `python3 scripts/setup.py` 并看到 `Console ready` 后，控制台已启动并设为开机就绪。选择 `lan` 时直接打开打印的地址；选择 `local` 时在电脑执行向导给出的 SSH 隧道命令。接着按下方网页步骤操作，无需再次安装或启动服务。

<details>
<summary>源码构建或手动配置：创建服务并打开控制台</summary>

先按 [部署说明](DEPLOYMENT_zh.md) 完成环境配置，再在项目根目录执行：

```bash
.venv/bin/python scripts/install_services.py
systemctl --user start onboardmimic.service onboardmimic-console.service
```

这里的 `install_services.py` 生成服务定义和 token，启用用户 linger，并将控制台和后台 API 设为开机自启（无需先 SSH 登录）。它仅安装和启用服务，上面的 start 命令负责本次启动；统一向导 `setup.py` 则会自动启动空闲后台。桥接仍由“连接服务”按钮启动，准备姿态和启动 SONIC 必须手动触发。若 linger 权限不足，请按提示执行 sudo 命令后重新安装。已有 unit 内容不同时，按[升级说明](UPGRADE_zh.md)停止旧部署后用 `--upgrade` 迁移；自定义 drop-in 保留。控制台默认只监听 `127.0.0.1:8769`。

```bash
ssh -N -L 8769:127.0.0.1:8769 USER@ROBOT_HOST
# 浏览器：http://127.0.0.1:8769/
```

</details>

### 按网页顺序操作

电脑控制台入口为 `/` 或 `/real`；手机控制台入口为 `/remote` 或 `/real/remote`，推荐横屏，左侧行走摇杆、右侧转向摇杆，松手停止对应输入。安装向导选择 `lan` 后即可手机直连；其他安装方式见[局域网访问配置](#lan-access)；电脑可继续通过 SSH 隧道访问。

1. **连接服务**：先可靠支撑机器人，再点击此按钮。连接流程会检查反馈，按需请求宇树释放原运动模式，再启动桥接。启动检查包含约 3 秒的等待，请等页面显示“控制服务已连接”且“准备姿态”按钮可用；此步骤不会回正或启动 SONIC。
2. **准备姿态**：可靠支撑下显式执行，等待实际回正完成，姿态过渡时间为 3 秒。
3. **启动 SONIC**：独立执行，等待控制器确认接管，接管过渡时间为 1 秒。
4. 启动后显示 **Planner / PICO Pose** 两个标签。切换标签只切换页面，不会自动改变机器人的控制模式。

急停始终可见，会终止本会话控制器；它不是断电操作，也不保证机器人保持站立。打开页面或恢复网络不会自动准备或启动。

如果连接失败，页面会显示失败原因；请检查是否重复启动了其他控制程序。宇树模式释放失败或桥接未就绪时，准备按钮保持禁用。重复连接不会自动替换正在运行的其他控制程序。技术细节见[控制权说明](CONTROL_AUTHORITY.md)。

<a id="lan-access"></a>

## 手机与局域网访问 / Phone & LAN access

新手在安装向导选择 `lan` 即可，无需修改服务文件。向导最后打印电脑地址 `http://实际IP:8769/` 和手机地址 `http://实际IP:8769/remote`。选择 `local` 时使用 SSH 隧道。已选择 `lan` 的用户可直接访问，不需要执行下方手动配置。手机应填写机器人的实际 Wi-Fi 或热点 IP，不能填写 `127.0.0.1` 或 `0.0.0.0`。

<details>
<summary>手动配置：将仅本机访问改为局域网访问</summary>

若已有向导生成的 `90-setup-access.conf`，应修改它，不要重复创建其他监听配置。

仅通过 `install_services.py` 创建、尚未配置 LAN 的服务监听 `127.0.0.1:8769`。这时手机仅连接同一 Wi-Fi 还不能直接访问。手动配置时，在尚未启动 SONIC 的情况下，使用机器人普通用户执行：

```bash
# 在 OnboardMimic 根目录查看实际绝对路径，用于替换下方 /ABSOLUTE/OnboardMimic
pwd
systemctl --user edit onboardmimic-console.service
```

在编辑器中填入以下内容，**替换两处 `/ABSOLUTE/OnboardMimic`**；路径中的引号保留，`%h` 由 systemd 自动替换为用户主目录。如果已经配置过监听地址，修改现有 drop-in，不要重复添加不同的 `ExecStart`。

```ini
[Service]
ExecStart=
WorkingDirectory=/ABSOLUTE/OnboardMimic
ExecStart="/ABSOLUTE/OnboardMimic/.venv/bin/python" -m onboardmimic.console.server --host 0.0.0.0 --port 8769 --token-file "%h/.config/onboardmimic/http.token" --robot "unitree_g1" --backend "sonic"
```

保存后执行：

```bash
systemctl --user daemon-reload
systemctl --user restart onboardmimic-console.service
hostname -I
```

选择机器人在手机所连 Wi-Fi 或机器人热点上的 IP，手机浏览器打开 `http://ROBOT_IP:8769/remote`，推荐横屏；电脑也可直接访问 `http://ROBOT_IP:8769/`。不要在手机上填写 `127.0.0.1` 或 `0.0.0.0`，也不要假设所有机器的热点地址相同。仍无法访问时，检查网络是否允许设备互访及 TCP `8769` 是否放通。

这项配置随开机启动保留，只更改网页监听地址，不启动运动策略。页面可控制真机，只在可信局域网内开放，不映射到公网；网页会话 token 是同源防护，不是用户登录系统。恢复仅本机访问时，将该配置的 `--host` 改回 `127.0.0.1`，再次重启控制台服务。

</details>

## Planner 行走

支持前后左右行走和左右转向，按住移动、松开停止。WASD / 方向键行走，Q/E 转向，空格/Esc 停止运动。电脑方向按钮支持鼠标左键按住操作；手机页使用双摇杆触控，按主要方向发送离散指令，摇杆幅度不会改变目标速度。

只有一个**目标速度**设置，所有行走方向共用。默认 0.5 m/s，页面或 HTTP `speed` 可设置 0..5 m/s；这个输入范围是应用的校验范围，不是稳定行走保证。需要调整默认值时，为网关 `onboardmimic.core.runtime` 添加 `--default-speed`，或在每条行走请求里显式传入 `speed`。

```bash
.venv/bin/python scripts/control.py forward --speed 0.4 --duration 1
.venv/bin/python scripts/control.py left --speed 0.4 --duration 1
.venv/bin/python scripts/control.py turn-left --duration 1
.venv/bin/python scripts/control.py stop
```

`stop` 清除运动请求，SONIC 继续运行。失焦、触摸取消或超过 550ms 没有续租，运动请求失效。HTTP 控制仅接受 `client/seq/epoch/ticket/move/turn/speed/stop`，其他参数会被拒绝。控制器仍校验会话、反馈与控制冲突。

<a id="pico-setup"></a>

## PICO 全身 Pose（可选，默认关闭）

在统一安装向导中选择 PICO，或加 `--pico`，会安装映射依赖、下载并构建镜像、生成设备配置和接收服务，然后启动只接收服务。Docker 操作通过 sudo 执行，不要求先加入 docker 组或重新登录；若未安装 Docker，按提示执行 `sudo apt-get install -y docker.io` 后重试。完成后直接进入下方[头显连接与使用](#pico-headset)，无需重复构建镜像或安装服务。

<a id="pico-headset"></a>

### 头显连接与使用

完成头显应用的设备配对与全身追踪标定，检查网页人体预览和身体数据就绪状态。若使用独立追踪器角色配置，在 `onboardmimic/inputs/pico/devices.json` 中填写自己的序列号；个人设备序列号不随仓库分发。

头显和机器人须处于可互通的网络，在 XRoboToolkit 中填写**机器人的实际 IP**；接收端口为 TCP `63901`，不是网页端口 `8769`。热点地址由本机网络配置决定，不假设每台机器都是 `10.42.0.1`。接收状态位于 `onboardmimic/inputs/pico/runs/live/status.json`，可用 `sudo journalctl -u onboardmimic-pico-receiver.service -n 50` 检查连接。接收服务只接收人体追踪，开机不会自动进入 Pose 或启动 SONIC。

完成准备姿态并启动 SONIC 后，先停止行走并站稳、让操作者姿态与机器人对齐，再打开 PICO Pose 标签，点击 **进入 PICO Pose** 按钮。切换标签本身不接管控制。也支持 `scripts/control.py pose` / `planner`。模式状态以 `g1_debug` 的 encoder_mode/play 确认为准，切换期间不接受 Planner 指令。数据过期请求零速 Planner，重连不自动进入 Pose；模式确认超时回退，Planner 回退再次超时则终止本会话控制器。软件回退不保证机器人不会跌倒。

人体预览只读接收文件；不代表策略已接管。自定义接收目录时，可给控制台加 `--pico-runs /ABSOLUTE/RUNS_PARENT`。

<details>
<summary>源码或手动安装：构建并配置 PICO 接收器</summary>

以下三步仅用于不走统一向导的手动安装。已通过向导完成 PICO 配置的用户跳过本节。

PICO 分为三步：**检查 Docker → 构建接收镜像 → 配置设备和接收服务**。预编译包已包含脚本，不包含接收镜像；`--with-pico` 只安装映射依赖，不会替你完成下面的接收器配置。

### 1. 检查 Docker

在机器人普通用户下执行：

```bash
command -v docker
docker info --format '{{.Architecture}}'
```

应找到 Docker 并显示 ARM64 架构（例如 `aarch64`）。若未安装，先完成目标机器的 Docker 安装；若 daemon 未运行，启动 `docker.service`。若出现 socket `permission denied`，先用 `sudo docker info` 确认 daemon 正常，再按本机权限要求配置当前用户。允许使用 Docker 用户组的机器可执行：

```bash
sudo usermod -aG docker "$USER"
# 退出当前 SSH 并重新登录后，再运行 docker info
```

Docker 用户组具有接近 root 的权限，按机器管理要求授权。不要用 `sudo` 运行整个 `install_pico_service.py`，它需要把用户服务写入当前机器人用户的目录。

### 2. 构建镜像和填写设备配置

从仓库根目录执行：

```bash
.venv/bin/python -m pip install -r onboardmimic/backends/sonic/pico/requirements.txt
cp -n onboardmimic/inputs/pico/devices.example.json onboardmimic/inputs/pico/devices.json
nano onboardmimic/inputs/pico/devices.json
# 一条命令下载、校验固定版本依赖并构建镜像：
bash onboardmimic/inputs/pico/prepare_container.sh
docker image inspect onboardmimic-pico-receiver:20261020 --format '{{.Architecture}}'
```

最后应显示 `arm64`。构建须在机器人 ARM64 上完成，需要网络；x86 电脑上脚本只下载依赖，不会生成 ARM64 镜像。角色序列号填写自己的追踪器，不随仓库发布；`cp -n` 保留已有设备配置。独立追踪器数量不等于 Body 数据就绪。

### 3. 配置并启动只接收数据的服务

先完成[首次部署](DEPLOYMENT_zh.md)中的服务创建。若此前已启动 SONIC，先可靠支撑机器人、急停并确认策略退出，再停止以下服务；仅点击“停止运动”不够。安装器会检查七个用户服务均已停止：

```bash
systemctl --user stop onboardmimic-console.service onboardmimic-console-connect.service \
  onboardmimic.service onboardmimic-bridge.service onboardmimic-state-primary.service \
  onboardmimic-state-standby.service onboardmimic-state-relay.service
```

已有 PICO 系统服务时，也先执行 `sudo systemctl stop onboardmimic-pico-receiver.service`。跨目录升级请先按[升级指南](UPGRADE_zh.md)备份并迁移配置，再继续：

```bash
.venv/bin/python scripts/install_pico_service.py --preview
.venv/bin/python scripts/install_pico_service.py
sudo systemctl enable --now onboardmimic-pico-receiver.service
systemctl --user start onboardmimic.service onboardmimic-console.service
systemctl is-active onboardmimic-pico-receiver.service
```

最后应显示 `active`。配置脚本安装接收服务、设置容器读写权限，并为 API 添加 `--pose-status`；脚本本身不启停服务。API 从空闲 Planner 状态启动，页面应有 PICO 接收状态，没有头显连接时提示等待数据是正常的。

已有自定义 API unit 或不同的 `60-pico.conf` 时，安装器会拒绝覆盖，请按预览内容人工合并。容器使用 CPU、host 网络，没有设备或运动控制凭据挂载，默认不录制数据；接收服务运行不等于 SONIC 已接管。

</details>
