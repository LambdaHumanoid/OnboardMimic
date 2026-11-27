# OnboardMimic Lab

Lab 是与 OnboardMimic 通过协议交互的独立数采和回放服务，源码位于 `lab/`，随 OnboardMimic 一起发布。唯一版本号来自仓库根目录 `VERSION`，不再维护独立 OnboardMimic Lab 版本。

网页和 Collector 仍为独立进程，OnboardMimic 通过 iframe 嵌入 Lab，将录制和视频处理与控制进程分开。`onboardmimic_lab` 包名和服务名保留；废弃命令与旧格式入口已移除。

## 一键安装 OM + Lab

机器人部署使用 [JP5 安装向导](../docs/PREBUILT_zh.md)或 [JP6 安装向导](../docs/JETPACK6.md)，默认一起安装 OM 与 Lab。向导负责独立环境、后台服务、网页嵌入与数采/回放配置；参数见[统一安装说明](#v020-部署配置准备)。

## 开发环境与命令行

```bash
# 在 Lab 的 Python 环境中，从 OnboardMimic 根目录安装；版本取自根 VERSION。
python -m pip install -e '.[capture]'
onboardmimic-lab --help

# 无需安装包也可从同一份源码执行。
PYTHONPATH=lab/src python -m onboardmimic_lab --help
bash scripts/test.sh --all
python scripts/check_oml_execution.py
```

根目录的 Python 打包配置保留 `onboardmimic-lab` 分发名以兼容现有工具，但它的版本由 OnboardMimic 决定。机器人安装见 OnboardMimic 的 [JP5 部署指南](../docs/DEPLOYMENT_zh.md)或 [JP6 安装入口](../docs/JETPACK6.md)；以上命令不会启动机器人服务或动作。

## 能力与边界

- 采集时核验实际消费与受保护输出，最终保存 motion token、身体 state、视频和元数据，录制与 Planner/Pose/回放模式无关，支持录制中切换输入模式；[模式无关录制](../docs/SONIC_MODE_INDEPENDENT_RECORDING.md)。
- 同时录制 GoPro 视频与机器人数据，两类数据源就绪后开始计时，保存后生成连续片段回放计划；[采集流程](docs/collection.md)。
- 真机执行交给 OnboardMimic，使用实时反馈和现有控制权；正常结束或停止返回 Planner，执行故障走保护退出；[回放接口](../docs/SONIC_REFERENCE_REPLAY.md)。
- [网页嵌入](docs/embedding.md)和独立 Collector 的职责不变。

## 部署

OnboardMimic 和 Lab 使用同一份源码快照，运行环境与服务独立。配置可继续指向现有数据目录；目录复用不表示支持旧数据格式。[部署与切换](../docs/INCREMENTAL_DEPLOYMENT.md)。

许可证与第三方归属见 [NOTICE](NOTICE)、`third_party/openlambda/` 及 OnboardMimic 根目录 LICENSE。

## 连续采集与后台数据处理

“保存”完成后即可开始下一条。原始数据已保存和数据处理完成分别显示；处理失败可重试，
只有处理完成的记录才能回放。后台单任务、低优先级执行，并在新采集期间暂停。
生命周期和失败恢复说明见[后台数据处理](docs/background-export.md)。

## LeRobot v2.1 保存格式

新真机 SONIC 采集统一保存到标准 LeRobot v2.1 的 data、videos 和 meta 目录；
一个任务可关联多条 episode；任务描述属于任务，采集人员属于各条 episode。state32/action64，不伪造未采集
的双手数据。后台导出校验完成后清理临时原始遥测、推理历史和视频流，不保存 JPEG。
旧格式回放入口已移除。字段、时间轴与恢复机制见[存储格式说明](docs/lerobot-storage.md)。

## 独立安装与测试

可从本仓库构建 wheel，再安装到没有 OnboardMimic Python 包和源码目录的环境。
只需 Python、capture 依赖和 FFmpeg；真实录制仍需本机 OnboardMimic 网关及遥测服务。
同机单调时钟约束不等于源码依赖。版本仍由根 VERSION 管理。

```bash
python -m pip wheel --no-deps . -w /path/wheels
# 在独立环境安装得到的 wheel 及 capture 依赖
python -m pip install '/path/wheels/onboardmimic_lab-VERSION-py3-none-any.whl[capture]'
# Lab 单元测试不要求 OnboardMimic 源码；OnboardMimic 联调测试需显式运行
PYTHONPATH=lab/src:lab/tests python -m unittest discover -s lab/tests -q
```

静态资源 registry 通过 JSON 文件契约交互，双方保留独立读取实现，不引入共享运行包。
当前文档入口见 [索引](docs/README.md)。


## v0.2.0 部署配置准备

新安装通过统一向导同时安装 OM 与 Lab，默认启用数采、视频接收和真机回放配置。
在对应平台安装包根目录执行：

```bash
# JP5：交互式向导，默认安装 Lab
python3 scripts/setup.py
# JP5：指定局域网访问地址；使用 PICO 时加 --pico
python3 scripts/setup.py --yes --access lan --origin http://ROBOT_IP:8769
# JP6：默认同时安装 Lab 和 PICO；仅 Planner 可加 --no-pico
bash install_scripts/install_jp6.sh --origin http://ROBOT_IP:8769
```

向导安装独立 `.lab-venv`、capture 依赖和 FFmpeg，配置 Collector、Lab 网页、OM iframe、
真实执行反馈及回放接口，并为运行时与保护桥配置同一个保护端点。数据目录默认是
`~/.local/share/onboardmimic-lab/data`。OM 与 Lab 保持独立服务和环境，通过 HTTP 与遥测通信。
完成后只启动空闲后台；机器人连接、准备姿态和 SONIC 启动由现场操作。

- `--no-lab`：新安装仅配置 OM。
- `--access local`：OM/Lab 网页均仅监听本机，使用向导给出的 SSH 命令同时转发 8769、8773。
- `--access lan --origin http://ROBOT_IP:8769`：指定浏览器访问的 OM 地址；Lab 自动使用同一主机的 8773 端口。省略 `--origin` 时选择检测到的首个局域网地址，多网卡机器建议显式指定。
- JP5 `--install-only`：安装并验证 OM、安装 Lab 环境，不改服务；随后用 `--configure-only` 完成配置。两阶段均可指定 `--no-lab`。

将相机视频转发至机器人 UDP 18554。首次启动 SONIC 后自动生成真实 controller_spec，
新鲜视频和策略执行反馈齐全后，页面允许开始采集。

已有部署先停止控制、回放、采集、数据处理及相关后台。向导会检查配置冲突，保留已有
数据目录、token、保护参数和自定义 drop-in。跨目录或定制部署使用[部署与切换流程](../docs/INCREMENTAL_DEPLOYMENT.md)。

需要单独生成和审核 Lab 配置时，保留以下入口：

```bash
python -m scripts.prepare_lab_install --root /opt/OnboardMimic --home /home/robot --origin http://ROBOT_IP:8769 --output /tmp/om-lab-install
```

按需添加 `--pico` 或 `--access local`。默认只生成配置；`--apply` 在服务停止且配置检查通过后
安装依赖和服务，保持服务停止。常规新安装直接使用上面的统一向导。
