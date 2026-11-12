# 构建预编译发布包

[English](RELEASE_BUILD.md) · 简体中文

打包过程不会执行 ARM64 控制器。使用干净且已提交的源码、与原生源码匹配的 ARM64 二进制和构建报告，以及已审核的模型/引擎快照。原生源码变化后须重新编译二进制。

```bash
python3 scripts/build_prebuilt_release.py \
  --snapshot /PATH/TO/AUDITED_MODEL_ENGINE_SNAPSHOT \
  --native-binary /PATH/TO/NEW/g1_deploy_onnx_ref \
  --build-report /PATH/TO/native_build_report.json \
  --bridge-build /PATH/TO/ARM64_BRIDGE_BINARIES \
  --bridge-report /PATH/TO/bridge_build_report.json \
  --patchelf /PATH/TO/patchelf \
  --version v0.1.1 --output /PATH/TO/EMPTY/OUTPUT
```

`native_build_report.json` 记录 `architecture: aarch64`、`binary_sha256`、`native_source_hashes`（须匹配 `onboardmimic/backends/sonic/native_source_manifest.json`）、`method`、`built_at_utc`，以及链接的 Unitree SDK/DDS 和 ONNX Runtime 库的 `runtime_library_sha256`。还应记录编译器、sysroot 版本及相关验证细节。打包器拒绝源码/哈希不一致或非 AArch64 的二进制。构建报告说明制品的构建方式，不替代目标设备验证。

两份构建报告均须记录 `validation.cxx_static_initialization: "passed"`。使用相同编译器、sysroot 和链接参数构建 `tests/native_startup_smoke.cpp`，在 QEMU 中配合目标 sysroot 运行这个纯测试程序。它不依赖 ROS、GPU、网络或机器人。记录测试程序和目标 CRT 对象的哈希。`Scrt1.o` 以及 `crt1.o`、`crti.o`、`crtn.o` 必须来自目标 glibc；缺文件时 GCC 可能悄悄使用主机工具链中的较新版本。仅做动态加载器 `--list` 检查无法发现 C++ 构造函数未执行的问题。

桥接报告单独记录 `architecture`、原生桥接 CPP/HPP/CMake/构建脚本的 `source_hashes`，以及 `sonic_state_reader` 和 `sonic_command_bridge` 的 `binary_sha256`。两个可执行文件均为必需，校验后纳入包内。ROS2 Foxy 和匹配的目标 `unitree_hg` 运行环境不随包分发，须使用记录的目标 sysroot 单独验证动态加载。

快照提供未经修改的原始模型、转换后的 planner、严格 FP32 引擎、24 组 planner 验证报告、ONNX Runtime 1.16.3 和固定版本的 Unitree DDS 库。原生控制器二进制由单独的构建产物提供。快照中的 SDK 库必须与新控制器链接时使用的库一致。

输出文件使用 `OnboardMimic-v0.1.1-{runtime,models,engines}.tar.gz` 命名，解压到 `OnboardMimic/`。包内包含文件哈希、源码提交、原生构建报告，并分别记录重新编译的原生代码与复用引擎的来源。添加 OnboardMimic 下载链接前，先发布匹配的源码标签和已验证的完整安装包。

源码标签、运行包和链接的指南必须描述同一版本。在清单中记录源码版本，保持包内与独立清单一致，将全部安装制品哈希写入 SHA256SUMS。将运行包、清单、校验值和 Release 说明作为一致的整体发布，再重新下载并验证已上传文件。保留原生构建来源和模型身份记录。

交付前，在干净目录解压三个压缩包，运行 `python3 scripts/check_prebuilt.py --files-only`、核心及可选 PICO 离线测试，检查包内 ELF 架构、共享库依赖和 ABI 版本。这些步骤可在工作站完成，不属于 GPU 推理、重启、头显追踪或真机验证。

发布交叉编译使用 x86-64 Ubuntu 22.04 容器、GCC 10.5 AArch64 交叉编译器，以及目标 JetPack 5 / L4T 35.3.1 头文件和库的只读快照。构建过程不会在机器人上运行控制器或推理。完整源码编译与链接在工作站完成，随后核验安装包校验值。
