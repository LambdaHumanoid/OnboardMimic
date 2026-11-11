# Motor-command ownership / 电机指令控制权

**Keep the current owner; reject later entrants. / 保持当前控制者，拒绝后来者。**

Both native bridge variants acquire the same nonblocking OS lock before creating
DDS endpoints or command ports. The lock is independent of installation paths and
port settings. The second bridge fails with `CONTROL_OWNER_BUSY`; it never stops,
restarts or changes the incumbent. The lock is held until the first bridge exits.
The lock file remains in place; process exit releases the kernel lock automatically.

两种原生桥接实现均在创建 DDS 端点或指令端口前获取同一个非阻塞系统锁。后启动者返回
`CONTROL_OWNER_BUSY`，不停止、重启或改变当前控制者。锁不依赖工程目录和端口号，
当前桥接退出后由内核自动释放；锁文件保留，不要删除它来抢占。

Before admission, the bridge also observes actual LowCmd publisher identities for
three seconds, without publishing commands. Foreign commands reject admission.
Registered but silent endpoints alone do not count as active control.
`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` is required and checked at runtime.

接管前还会静默观察三秒实际发送源。发现其他控制程序仍在发电机指令就拒绝接管；
仅注册但未发送的端点不算活动控制器。强制使用并检查 Cyclone DDS 通信后端。

After admission, the observer is **advisory**. An unexpected foreign writer or
observer error is logged but does not interrupt the incumbent's output. The
original command watchdog, feedback checks and operator emergency stop still
apply. There is no automatic controller recovery or vendor mode switching.

取得控制权后，监测只负责记录异常外部发送源或监测错误，不会因此切断当前输出。
原有指令看门狗、反馈保护和人工急停仍然有效，不会自动重启或切换宇树控制模式。

## Vendor control and limits / 宇树原控制及边界

The lock covers cooperating native bridges on the same host/filesystem. It does
not stop vendor `humanoid` on the robot's other internal computer, or an arbitrary
program that bypasses the lock. A later external DDS writer is detected, not
network-filtered. Continued SONIC publishing alone cannot guarantee that the motor
controller ignores those external packets.

本机锁约束遵守该规则的桥接，不能直接约束另一台内部计算机上的宇树 `humanoid`，
也不能过滤绕过锁直接发送的 DDS 数据。保持 SONIC 发布本身不等于电机端只接受 SONIC。

The official [G1 low-level example](https://github.com/unitreerobotics/unitree_sdk2/blob/main/example/g1/low_level/g1_ankle_swing_example.cpp)
releases the vendor mode before low-level control. The explicit **Connect services**
request now checks feedback, calls MotionSwitcher `CheckMode` / `ReleaseMode` when
needed, and confirms release before starting the bridge. This connection workflow
holds the same local owner lock during release and refuses an existing owner.
It uses the official RPC wire contract over ROS2 `unitree_api`; no Python SDK path
from another deployment is needed. It neither prepares posture nor starts SONIC.
Boot, page loads, and the bridge itself never invoke `ReleaseMode` automatically.
The alternative official `user_lowcmd` path has not been qualified on this robot's
firmware; this change does not migrate to that path or claim firmware-level ownership.

宇树官方低层示例要求先释放原控制模式，再接管低层控制。“连接服务”显式请求现在
检查反馈、持有同一个本机锁调用运动模式接口、确认释放后启动桥接，并等待实际就绪。
使用 ROS2 `unitree_api` 承载官方 RPC 协议（`CheckMode` / `ReleaseMode`），无需其他部署的 Python SDK 路径。
不会停止其他部署的控制器，也不会回正或启动 SONIC。开机、打开页面和桥接本身不调用
`ReleaseMode`。当前固件上的 `user_lowcmd` 替代方案尚未验收，
本次没有迁移到该通道，也不声称已实现固件级排他控制。

## Validation / 验证 — 2026-11-04

On ARM64, both bridge variants passed ROS and SDK foreign-writer cases in domain
94 / loopback on diagnostic topics, with all motor commands disabled:
startup foreign-writer rejection; later local contender rejection; incumbent
continuation with a foreign writer; and explicit successor admission after exit.
Observed maximum DDS write gaps were 5.0–7.7 ms in these short isolated runs.
Real-network admission tests rejected the live vendor stream with zero DDS writes.
No posture, policy startup, walking, or vendor mode change was executed. These are software ownership tests, not physical motion-stability acceptance.

在 ARM64、domain 94、仅 loopback 的诊断主题上，禁用所有电机指令后，
两种桥接实现各通过 ROS/SDK 两种发送源的隔离测试：拒绝带冲突启动、拒绝后来本机桥接、
保持当前输出、退出后手动允许后继接管。短时隔离测试最大发送间隔 5.0–7.7 ms。
真实网络上有宇树指令时，两桥接均拒绝接管且发送电机指令数为 0。未回正、启动
SONIC、行走或切换宇树控制模式。这些是软件互斥测试，不是实机运动稳定性验收。

Reproduce with `tests/bridge_authority_probe.py` using ROS2, Unitree SDK on
`PYTHONPATH`, domain 94, Cyclone DDS and inline loopback-only XML. Provide
`--binary`, `--contender`, `--output`, and the corresponding diagnostic `--topic`.

> 复现测试时使用 `tests/bridge_authority_probe.py`，准备 ROS2、位于 `PYTHONPATH` 的 Unitree SDK、domain 94、Cyclone DDS 和内联的仅 loopback XML；提供 `--binary`、`--contender`、`--output` 及对应的诊断 `--topic`。
