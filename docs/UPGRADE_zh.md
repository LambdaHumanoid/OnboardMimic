# 升级与回退

[English](UPGRADE.md) · 简体中文

这里的“旧部署”指**已有 OnboardMimic 安装目录和服务配置**。保留旧目录用于回退，不代表两套程序同时运行。首次安装不需要本页；其他工程的服务也不是 `--upgrade` 的迁移对象。

升级顺序：**备份 → 停止 → 新目录安装 → 更新配置路径 → 迁移服务 → 打开页面**。配置和 token 沿用，准备姿态和启动 SONIC 仍需手动触发。

## 1. 备份现有配置

先记下旧目录。在新的 `OnboardMimic` 源码或安装包根目录执行，替换旧路径：

```bash
old_root=/ABSOLUTE/OLD/INSTALLATION
new_root="$(pwd)"
backup_dir="$HOME/.config/onboardmimic/manual-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir/user-units" "$backup_dir/config"
chmod 700 "$backup_dir"
cp -a "$HOME/.config/systemd/user/"onboardmimic* "$backup_dir/user-units/"
for name in installed_services.json bridge.env cyclonedds.xml http.token; do
  if [ -f "$HOME/.config/onboardmimic/$name" ]; then
    cp -a "$HOME/.config/onboardmimic/$name" "$backup_dir/config/"
  fi
done
printf 'Backup: %s\n' "$backup_dir"
```

备份必须在编辑任何路径**之前**完成。上面的变量用于本页后续命令；换终端后重新设置。确认 `old_root` 指向当前服务：

```bash
systemctl --user show onboardmimic.service --property=ExecStart
```

已安装 PICO 的用户，还要备份系统服务、可能存在的系统 drop-in 和设备配置：

```bash
cp -a /etc/systemd/system/onboardmimic-pico-receiver.service "$backup_dir/"
if [ -d /etc/systemd/system/onboardmimic-pico-receiver.service.d ]; then
  cp -a /etc/systemd/system/onboardmimic-pico-receiver.service.d "$backup_dir/"
fi
for relative in onboardmimic/inputs/pico/devices.json; do
  if [ -f "$old_root/$relative" ]; then
    cp -a "$old_root/$relative" "$backup_dir/pico-devices.json"
    break
  fi
done
```

备份里含私有 token 和设备序列号，不上传到仓库。若用了其他目录的 DDS XML 或外部设备配置，也保留对应文件。

## 2. 停止当前部署

人员在场、机器人可靠支撑时，先使用当前控制台急停，确认 SONIC 策略进程退出。只点“停止运动”不会退出 SONIC。随后停止全部七个用户服务：

```bash
systemctl --user stop onboardmimic-console.service onboardmimic-console-connect.service \
  onboardmimic.service onboardmimic-bridge.service onboardmimic-state-primary.service \
  onboardmimic-state-standby.service onboardmimic-state-relay.service
```

旧版本没有某个 unit 时，可忽略该 unit 的 `not loaded`；已有服务必须确实停止。已启用 PICO 的用户还需执行：

```bash
sudo systemctl stop onboardmimic-pico-receiver.service
```

安装器不会替用户停止运动、释放宇树模式或抢占其他控制程序。

若这次升级不再使用 PICO，在完成备份和停止后执行 `sudo systemctl disable onboardmimic-pico-receiver.service`，并将 `~/.config/systemd/user/onboardmimic.service.d/60-pico.conf` 移到备份目录，再执行 `systemctl --user daemon-reload`。之后可使用 `--no-pico`；保留原设备配置和镜像便于恢复。

## 3. 在新目录安装

对于支持的 G1 / JP5 配置，在新目录下载并解压完整的 [v0.1.0 安装包](PREBUILT_zh.md)，再执行下方安装命令，不更改服务。

```bash
python3 scripts/setup.py --yes --install-only --pico
# 不使用 PICO 时改成 --no-pico
```

定制源码构建则按[源码指南](SOURCE_BUILD_zh.md)完成依赖、控制器、引擎和测试后，直接进入第 4 步。

这一步不改服务。不要覆盖原目录，也不要删除旧模型、缓存或虚拟环境。

PICO 用户把自己的设备配置复制到新目录：

```bash
cp -n "$backup_dir/pico-devices.json" "$new_root/onboardmimic/inputs/pico/devices.json"
```

`cp -n` 不覆盖已有文件；若新目录里已生成空白示例，请人工合并实际序列号。不复制旧 `runs/live/status.json` 或历史追踪数据。

## 4. 更新路径和迁移服务

源码构建完成后，从新目录迁移标准服务：

```bash
.venv/bin/python scripts/install_services.py --upgrade
```

保留已有通信配置，必要时按[首次部署](DEPLOYMENT_zh.md)核对；局域网监听使用[控制台指南](CONTROL_EXTENSIONS_zh.md#lan-access)中的新模块命令。PICO 用户继续本页“迁移接收器”，之后进入第 5 步。

以下向导命令仅适用于匹配的 OnboardMimic 预编译包：

```bash
python3 scripts/setup.py --yes --configure-only --upgrade --access lan --pico
# 仅 SSH 访问用 --access local；未使用 PICO 的部署用 --no-pico
```

向导备份并迁移服务、沿用 token/ROS 配置，自动迁移标准网页监听和 PICO drop-in 中的路径，配置 PICO 接收服务，然后启动空闲后台并打印地址。成功后跳到第 5 步核对状态，无需再手改文件。API/网页以后退出会自动恢复，策略不会自动启动。

遇到自定义 unit 或无法识别的旧路径时会停止并保留原文件，不猜测参数。下面的手动流程只用于这些定制部署：

### 手动迁移定制配置

先检查用户服务的 drop-in：

```bash
systemctl --user cat onboardmimic.service onboardmimic-console.service
```

自定义配置除了安装根目录，还需要核对 Python 包路径和 `WorkingDirectory`。API 使用 `-m onboardmimic.core.runtime`，控制台使用 `-m onboardmimic.console.server`，工作目录设为新的工程根目录；PICO 数据路径使用 `onboardmimic/inputs/pico/runs/live/status.json`。保留自定义端口、速度及其他参数，不能仅替换根目录：

```bash
systemctl --user edit onboardmimic.service
systemctl --user edit onboardmimic-console.service
.venv/bin/python scripts/install_services.py --upgrade
```

安装器确认全部相关服务停止、原 unit 未被未知方式改写后，备份并迁移基础服务，保留 token、`bridge.env` 和用户 drop-in。标准生成的监听/PICO drop-in 可自动迁移，定制参数保留人工核对。若 `bridge.env` 引用了旧仓库内的 XML，也需更新；位于 `~/.config/onboardmimic/` 的配置通常不需要改路径。

如果提示自定义 unit 或 drop-in 冲突，先核对差异，不删除检查代码。直接改过主 `.service` 的用户，需要先将自定义参数迁入 drop-in，再按安装器记录的原生成内容恢复主 unit。迁移不会启动服务。

### PICO 用户：迁移接收器

`install_services.py --upgrade` **不处理系统级 PICO 服务**。保持全部服务停止，从新目录执行：

```bash
.venv/bin/python scripts/install_pico_service.py --preview
.venv/bin/python scripts/install_pico_service.py
```

这会让接收容器的代码、设备配置、输出目录以及 API 的 `--pose-status` 指向新安装目录。若已有自定义 API 参数或系统 PICO unit/drop-in，请对照预览与备份人工合并，避免用默认配置覆盖定制内容。确认 `60-pico.conf` 和系统服务中的路径一致后再启动。

## 5. 打开新控制台

PICO 用户先启动接收服务；纯 Planner 跳过第一条：

```bash
sudo systemctl enable --now onboardmimic-pico-receiver.service
systemctl --user start onboardmimic.service onboardmimic-console.service
.venv/bin/python scripts/control.py status
```

应为 `state: stopped`、`pids: []`。确认网页、手机入口及需要时的 PICO 接收状态，再按[首次部署](DEPLOYMENT_zh.md)手动连接、准备和启动。系统服务与用户服务的代码路径可分别通过 `systemctl cat` 和 `systemctl --user cat` 核对。

后台/网页此后开机就绪；设置为开机启动的接收器也只接收数据，不自动进入 Pose 或启动 SONIC。

## 回退

先按第 2 步停止当前部署，再恢复第 1 步的备份：

1. 将备份的用户 `.service` 文件、各自 drop-in 目录恢复到 `~/.config/systemd/user/`；先另存并移走对应的新 drop-in 目录，避免残留新路径。
2. 恢复原 `installed_services.json`，按需恢复 `bridge.env`、XML 和 token，保留原文件权限。旧版本没有安装记录时，移走新版记录。
3. PICO 用户恢复 `/etc/systemd/system/onboardmimic-pico-receiver.service` 及其原系统 drop-in；代码和设备配置指向保留的旧目录。确认旧服务引用的 Docker 镜像仍存在。
4. 执行下面的 reload，再按原部署配置手动启动空闲后台、网页及需要的接收器。

```bash
systemctl --user daemon-reload
# 恢复过 PICO 系统服务时执行：
sudo systemctl daemon-reload
```

不要把整个备份目录覆盖到 systemd 目录，也不要启动两套控制程序。升级启用的控制台/API 自启和用户 linger 不会自动撤销；如需完全手动启动，执行 `systemctl --user disable onboardmimic.service onboardmimic-console.service`。不要关闭其他服务也在使用的 linger。
