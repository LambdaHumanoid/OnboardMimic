WBC abnormal-stop protection / WBC 异常停止保护
================================================

This module belongs to the protected motor writer, independently of the SONIC
adapter. Physical execution requires the bridge and runtime to be explicitly
configured with the same private protection endpoint. Connect this endpoint
to enable execution, then follow the preparation and arming sequence below.

保护模块位于电机指令写入端，不依赖 SONIC 专有接口。桥接和运行端须显式配置同一
私有保护端点，并按下述准备、布防流程启用执行。

Activation and ownership / 布防与控制权
--------------------------------------

Preparation binds the owned WBC child PID before its first output. Acceptance of
the first valid output arms protection atomically. It remains armed through
standing, walking and mode changes. Child exit or more than 50 ms without a new
accepted command fences the WBC stream and requests protection. Late packets
cannot revive an expired stream. Exit before the first output gives a warning
without issuing a protective motion.

After takeover, both old and replacement WBC streams are rejected. Completion
or abort does not release this fence. An explicit prepare session is required
before another controller can take ownership. Reset cannot interrupt an active
protective action. Motor publication and cancellation serialize on the writer
lock; a canceled command cannot publish after cancellation acknowledgement.

The endpoint is a local SOCK_SEQPACKET socket in a private service-owned directory.
Peer credentials, direct-child ownership and generation checks reject unrelated
or stale requests. Native state is authoritative for armed, fenced, estopped,
phase and completion; a Python monitoring event alone grants no motor authority.

准备阶段绑定本会话 WBC 子进程，首条有效输出到达时布防，站立、行进和模式切换均保持
监测。异常退出或输出超时后隔离旧输出，由保护写入器接管。接管后不能靠晚到命令或
更换 WBC 夺回控制权，须完成保护并显式重新准备。

Protective motion / 保护动作
---------------------------

The G1 executor uses measured joints and pelvis IMU. Feedback must be finite and
fresh within 30 ms, subject to the single-joint isolation exception below. It
uses folded poses and an adaptive ankle adjustment based on measured pitch rate.
Standing entry uses a 1.4 s crouch. Moving entry selects a 0.12 s velocity
transition and 0.8 s crouch; a strongly tilted entry selects a compact catch.
Joint targets, rates and predicted effort remain bounded.

Joint speeds above 15 rad/s trigger braking of the affected joints while the
other joints continue folding. Braking uses zero position gain, zero velocity
target and bounded damping. Below 10 rad/s, each joint rejoins through a 120 ms
gain transition. Excessive speed alone does not permanently abandon the fold.

Any one of the 29 body joints may use the single-fault profile for a 0x200 motor
fault or invalid position/velocity feedback. The affected joint is isolated with
zero drive; healthy joints continue the posture. This profile requires standing
entry: healthy measured joint speed at most 2 rad/s, absolute roll at most
0.2 rad and pitch at most 0.25 rad. It does not cover arbitrary fault codes or
multiple failed joints. Isolation remains latched until successful healthy re-arm.

Invalid/stale IMU or unsupported feedback faults, writer deadlines and measured
joint-envelope violations cause damping fallback. Isolated joints remain zero
in damping and emergency stop. The sequence reaches terminal damping at 7 s;
completion marks the end of the timed trajectory. Verify ground contact onsite.

收身幅度随俯仰变化选择，站立和行进分别使用对应过渡。超速关节先减速，再恢复收身。
单关节指定故障或反馈无效时隔离该关节，其他关节执行受限动作；多故障、未知故障和
反馈失效仍按退化路径处理。完成状态表示定时轨迹结束；接地情况与滚倒方向由现场观察确认。

Stops and replay / 停止与回放
----------------------------

- Stopping Planner movement clears walking requests while WBC and protection stay active.
- Normal replay completion and Stop replay transition back to Planner, retaining WBC control.
- Unexpected WBC exit, replay execution faults or output timeout request protected exit.
- Emergency stop explicitly cancels protection before terminating the owned WBC;
  the runtime must receive writer cancellation acknowledgement before claiming stop.

停止行走、停止回放、异常退出和急停具有不同语义。正常回放结束或主动停止平滑返回
Planner；异常执行故障才进入保护退出。急停优先级最高，可以取消正在执行的收身。
普通停止请求与确认结果不能混为一谈。

Voice / 语音
------------

An abnormal takeover request signals one audio batch per generation:
``控制停止，后滚保护。Controller off. Protection active.`` played twice.
The embedded clip is the selected Yunxi male voice. Audio runs on a separate
thread and never waits in the motor writer. Arming, preparation and normal
Planner/replay stopping do not trigger this warning. Audio failure does not
block protection; an API acknowledgement is not confirmation that sound was heard.

异常接管时非阻塞播放两遍提示；准备姿态、布防和正常返回 Planner 不播放。

Configuration and limits / 配置与边界
-------------------------------------

Configure ``--experimental-protection-socket /PRIVATE_DIR/control.sock`` on both
bridge and runtime. ``run_bridge.sh`` also accepts
``ONBOARDMIMIC_EXPERIMENTAL_PROTECTION_SOCKET``. The directory must already exist,
belong to the service UID and deny access to other users. Voice requires the
bridge's ``--protection-voice`` option. These settings belong to the deployment;
opening the console does not enable or arm them.

Another WBC must explicitly use the same sole writer, command freshness/session
validation and owned-child lifecycle. Route all of its motor commands through
that writer. The posture and joint order are specific to this G1. Active protection
requires power and a functioning host and bridge. Record predicted torque limits,
simulation contact forces and measured physical impacts as separate results.

更换 WBC 必须接入相同控制权和受保护写入端；更换本体还需要独立的动作适配与验证。
部署身份、目标机验证和现场观察的范围见 ``VALIDATION.md``，更新流程见
``INCREMENTAL_DEPLOYMENT.md``。
