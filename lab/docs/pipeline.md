# 录制、数据处理与真机回放

Lab 使用独立 Collector 订阅 SONIC 执行反馈和相机视频；录制不发送运动命令。Planner、Pose 和模式切换不单独决定录制资格，实际反馈、权限、会话和新鲜度检查始终保留。

先通过 [OM + Lab 统一安装向导](../README.md#v020-部署配置准备)完成部署，并将相机视频转发到机器人 UDP 18554。

1. 在 OM 控制台连接服务、准备姿态并启动 SONIC。
2. 在 Lab 选择任务，确认相机与机器人数据就绪后开始数采。
3. 结束采集并保存。后台处理完成后得到 LeRobot v2.1 数据，可继续采集其他 episode。
4. 选择已处理的记录进行视频预览或真机回放。Lab 自动校验并上传连续片段，OM 使用实时状态和50Hz逐token调度执行。
5. 正常播完或用户停止后平滑返回 Planner；执行故障由 OM 处理保护退出。

持续任务不依赖浏览器续租。仿真接口保留，当前页面不提供仿真回放入口；不使用录制状态代替真机反馈。

[配置与生命周期](collection.md) · [后台数据处理](background-export.md) · [存储格式](lerobot-storage.md) · [回放页面](replay-console.md) · [OM 执行协议](../../docs/SONIC_REFERENCE_REPLAY.md)
