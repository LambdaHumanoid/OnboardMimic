# JP6 development validation / JP6 开发验证

Local testing on 2026-11-12. **These results are not JP6 hardware acceptance.**
本地测试日期：2026-11-12。**以下结果不等于 JP6 实机验收。**

## Test environment / 测试环境

Ubuntu 22.04.5 x86-64, Python3.10.12, RTX5090D, CUDA12.9,
TensorRT10.13.3.9, ONNX Runtime1.16.3, GCC10.5 and MuJoCo3.2.7.
ARM64 object compilation used GCC10.5 and NVIDIA's L4T36.4 packages containing
TensorRT10.3.0.30 / CUDA12.6.68 headers. Those objects were not linked or executed
on a Jetson. Desktop TensorRT engines must not be copied to a Jetson.

桌面环境为 Ubuntu22.04.5 x86-64、Python3.10.12、RTX5090D、CUDA12.9、
TensorRT10.13.3.9、ONNX Runtime1.16.3、GCC10.5、MuJoCo3.2.7。
ARM64 对象编译使用 GCC10.5 与 NVIDIA L4T36.4 软件包中的 TensorRT10.3.0.30 /
CUDA12.6.68 头文件；未在 Jetson 上链接或执行。桌面引擎不能直接用于 Jetson。

## Completed checks / 已完成检查

| Check / 检查 | Result / 结果 |
|---|---|
| Python offline suite / Python 离线测试 | 211 tests passed on Python3.10 / 211 项通过 |
| Native safety / 原生安全逻辑 | 16 tests passed / 16 项通过 |
| Watchdog and writer ownership / 看门狗与控制权 | 2 native tests + 20,000 independent reference transitions passed / 通过 |
| TensorRT8/10 dispatch / API 分支 | Mocked API and integer-buffer tests passed / 模拟 API 与整数缓冲区测试通过 |
| SONIC build / SONIC 构建 | Complete x86 executable; all 7 ARM64 translation units compiled / x86 完整构建、7 个 ARM64 编译单元通过 |
| ROS2 Humble | Pinned Unitree messages, native reader/bridge built; Python message imports passed / 消息包与桥接构建、Python 导入通过 |
| Model pipeline / 模型流程 | ONNX conversion → FP32 engines → native caches completed / 转换、引擎生成与原生缓存完整通过 |
| LayerNorm conversion / LayerNorm 转换 | 44 blocks, 132 cases, max error 9.54e-7 / 44 个模块、132 组测试 |
| Planner numerical checks / Planner 数值检查 | 24 cases passed, max absolute error 6.75e-5 / 24 组通过 |
| Encoder/decoder numerical checks / 策略数值检查 | 3 + 3 cases passed, max absolute error 3.58e-7 / 6 组通过 |
| Python and C++ GPU benchmark / GPU 压力测试 | Each ran 60 s at policy50Hz/planner10Hz, no deadline misses / 各运行 60 秒，无周期超时 |
| Source-validation workflow / 源码验证流程 | Complete desktop diagnostic passed; marked host-only, ineligible for Jetson setup / 桌面诊断通过，不用于放行 Jetson 安装 |
| Console / 控制台 | Desktop keyboard/release-stop, bilingual labels and mobile load passed with a mock backend / 模拟后台下键盘、松开停止、双语及手机加载通过 |

### Closed-loop simulation / 闭环仿真

The newly built SONIC binary ran with a G1 29-DOF MuJoCo model in a separate
network namespace with **only loopback**. No hardware DDS or robot network was
available. Each case included initialization, settling, a 10-second command phase
and return to stand. Simplified sensor feedback and simulated contacts limit what
these tests establish; they cannot demonstrate real-robot stability.

新编译的 SONIC 二进制与 G1 29 自由度 MuJoCo 模型在**仅有 loopback** 的独立网络
命名空间内运行，无法访问真机网络。每项包含初始化、稳定、10 秒指令段与返回站立。
反馈与接触模型经过简化，不能据此证明真机稳定性。

| Case / 场景 | Maximum torso tilt / 最大躯干倾角 | Minimum base height / 最低基座高度 |
|---|---:|---:|
| Stand / 站立 | 2.15° | 0.785 m |
| Forward 0.3m/s / 前进 | 6.60° | 0.760 m |
| Turn 0.15rad/s / 转向 | 4.45° | 0.776 m |

All three completed without nonfinite commands, controller exits or a simulated fall.
The harness is `tests/simulation/sonic_rollout.py`; it requires a separately provided
matching G1 scene and rejects execution outside an isolated network namespace.

三项均未出现非有限数值、控制器退出或仿真摔倒。测试脚本为
`tests/simulation/sonic_rollout.py`，需另行提供匹配的 G1 场景；脚本拒绝在非隔离网络中运行。

## Limits and open checks / 边界与待测项目

- JP6 ARM64 final linking/loading, actual Jetson TensorRT10.3 numerics and timing,
  clean installation, ROS/DDS exchange with hardware, boot recovery and physical
  Planner/PICO acceptance remain outstanding.
- JP5 software branches are retained and regression-tested; this development turn
  did not repeat JP5 hardware tests or change any robot services.
- An expanded mobile-browser layout test found vertical overflow at 568×320 in
  landscape. This existing small-screen limitation is not a JP6 runtime issue;
  the full small-screen layout suite is not counted as passed.

- JP6 ARM64 最终链接/加载、Jetson TensorRT10.3 数值和时延、全新安装、真机 ROS/DDS
  通信、开机恢复以及 Planner/PICO 动作验收仍待完成。
- 保留并回归测试了 JP5 软件分支；本轮未重复 JP5 真机测试，也未改动任何机器人服务。
- 扩展手机布局测试在 568×320 横屏下发现纵向溢出，这是已有的小屏显示限制，与 JP6
  运行环境无关；完整的小屏布局测试未计为通过。

Community testing instructions: [JetPack 6](JETPACK6.md).
社区测试步骤见 [JetPack 6](JETPACK6.md)。
