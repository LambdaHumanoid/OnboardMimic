# 按变更范围部署（完整源码快照）

`python -m scripts.deploy_incremental` 使用完整 `source-manifest.json` 比较版本，不要求暂存或丢弃工作区。保留旧版本，不在运行中的目录直接修改文件。首次使用需要安装新版 OM Console 和 Lab Console 的资源版本支持；首次切换前仍检查回放/采集是否结束。**该流程不自动启动 SONIC、准备姿态或电机桥。**

| 变更 | 操作 | 控制要求 |
| --- | --- | --- |
| OM/Lab HTML、CSS、JS | 原子切换资源版本指针，不重启任何服务 | 不停止 SONIC；不强制刷新已打开页面 |
| OM Console Python | 仅重启 Console；共享资源协议改动同时更新 Lab Console | 无进行中的控制操作；按部署计划检查后台任务 |
| Lab 页面/回放客户端 Python | 仅重启 Lab Console | 回放、采集已结束 |
| Lab 共享 Python | 更新 Lab Console、Collector | 回放、采集已结束 |
| 原生 SONIC 或共享头文件 | 固定工作目录增量构建 SONIC/桥接受影响目标 | 控制停止；运行中的桥接须确认急停 |
| 桥接原生代码 | 增量构建桥接；控制侧安全切换 | 同上 |
| 仅文档 | 更新完整源码快照，不构建或重启运行服务 | 无控制变更 |
| 核心共享代码、未知路径或安装元数据 | 保守扩大服务范围，计划中明确显示 | 按计划要求 |

先在开发容器内制作完整快照，再上传、解压到**新的空目录**。不要覆盖现役版本；配置、模型、数据和凭据仍独立保存：

```bash
python -m scripts.package_source_snapshot --output /path/to/builds/source.tar.gz
```

区分两个来源目录：`$old_release` 是资源登记中的当前完整源码快照，用于 `plan` 和 `activate`；`$runtime_release` 是包含已验证运行环境、模型及原生构建报告的部署目录，用于 `stage`。前端热更新后的源码目录可能没有这些运行制品，不能直接用作 `stage` 来源。两者一致时可使用同一路径。

在机载候选目录执行（使用现有 Python 环境）：

```bash
python -m scripts.deploy_incremental plan --current "$old_release" --candidate "$new_release"
# 纯前端资源更新无需 stage/build。
# 需要切换后台服务时，先复用已验证的运行制品：
python -m scripts.deploy_incremental stage --current "$runtime_release" --candidate "$new_release"
# 仅需原生构建时执行；先加载 ROS2/CycloneDDS 环境，并保持控制已停止。
# build_spec 必须与目标设备的 JP5/JP6 配置一致。
python -m scripts.deploy_incremental build --current "$runtime_release" --candidate "$new_release" \
  --cache "$native_build_cache" --spec "$build_spec"
python -m scripts.deploy_incremental activate --current "$old_release" --candidate "$new_release"
```

`stage` 验证模型与依赖、复用未变化的原生二进制，绑定新的完整源码标识；新增原生代码必须完成 `build`，未完成的构建会阻止切换。JP6 构建配置使用已经校验的 GoogleTest 缓存，不在每次构建时联网下载。`--offline` 仅用于开发机/交叉构建，不用于绕过真机运行状态检查。

构建缓存以编译器、系统包状态、架构、依赖内容、构建参数及 ROS 环境区分；源码在固定缓存目录按内容同步，未变化文件保留 mtime，已删除的受管文件同步删除。缓存目录从不用于运行机器人；验证后的输出复制到独立发布目录。缓存首次使用需要构建，后续由 CMake 处理头文件依赖和增量编译。工具链或依赖变化会创建新缓存。

页面版本登记在 `~/.config/onboardmimic/assets.json`，原子更新；页面给 CSS/JS 附带已登记的 `asset_version`，切换瞬间也可继续读取旧资源，直到用户主动刷新。保留旧发布目录供旧页面和未重启但代码等价的服务使用。OM/Lab 两个 `/api/deployment-assets` 接口确认实际生效的资源版本；`deployment-state.json`、每版的 `deployment-result.json` 记录来源、重启及未触碰的服务。回退时也不自动启动电机桥。

本地验证包括：真实 HTTP 热更新保持会话、前端更新零服务变更、Python 定向重启、失败回退、活动控制/回放/采集阻断、源码篡改拒绝，以及真实 CMake 未变化目标文件复用。部署记录保存在每个发布目录，后续切换使用上述统一流程。
