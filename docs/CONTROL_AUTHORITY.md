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

The lock coordinates cooperating native bridges on the same host/filesystem.
Manage vendor `humanoid` on the robot's other computer and external DDS writers
through their own control interfaces. The bridge records later external writers;
network access controls belong to the deployment.

本机锁协调同主机、同文件系统中遵守该规则的桥接。另一台内部计算机上的宇树
`humanoid` 和外部 DDS 发送程序由各自的控制接口管理。桥接记录后来出现的外部发送源；
网络访问控制由部署环境配置。

The official [G1 low-level example](https://github.com/unitreerobotics/unitree_sdk2/blob/main/example/g1/low_level/g1_ankle_swing_example.cpp)
releases the vendor mode before low-level control. The explicit **Connect services**
request now checks feedback, calls MotionSwitcher `CheckMode` / `ReleaseMode` when
needed, and confirms release before starting the bridge. This connection workflow
holds the same local owner lock during release and refuses an existing owner.
It uses the official RPC wire contract over ROS2 `unitree_api`; no Python SDK path
from another deployment is needed. It neither prepares posture nor starts SONIC.
Boot, page loads, and the bridge itself never invoke `ReleaseMode` automatically.
The deployment uses the LowCmd path with explicit vendor-mode release and local ownership checks.

宇树官方低层示例要求先释放原控制模式，再接管低层控制。“连接服务”显式请求现在
检查反馈、持有同一个本机锁调用运动模式接口、确认释放后启动桥接，并等待实际就绪。
使用 ROS2 `unitree_api` 承载官方 RPC 协议（`CheckMode` / `ReleaseMode`），无需其他部署的 Python SDK 路径。
不会停止其他部署的控制器，也不会回正或启动 SONIC。开机、打开页面和桥接本身不调用
`ReleaseMode`。部署使用 LowCmd 通道，并执行显式模式释放和本机控制权检查。

## Validation scope / 验证范围

Ownership checks cover startup rejection, retention of the incumbent and explicit
successor admission. Record motion checks and network controls separately.
Validate the actual deployed build according
to [VALIDATION.md](VALIDATION.md).

控制权检查覆盖启动拒绝、保留当前控制者和显式后继接管；动作检查和网络控制单独记录。
按[验证范围](VALIDATION.md)核验实际部署版本。
