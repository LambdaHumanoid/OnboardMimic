# Lab 回放页

当前页面读取 LeRobot v2.1 数据集，提供任务和 episode 选择、视频预览及真机回放。
加载计划时自动校验文件完整性、控制器契约、实际执行反馈和连续性。每次回放使用一个连续片段。

OML 生成 reference-plan v2，并通过 OM 的 `/api/execution/v2/plans` 加载、开始、
查询和停止。OM 负责机载 50Hz 调度、实时 state/history、控制权及保护。
加载不触发动作；开始前由 OM 校验实际控制器就绪状态。显示实际提交进度，正常完成
和用户停止均平滑回到 Planner。浏览器断连不停止已启动回放，执行心跳由 OM 后台维护。细节见 [OM 回放协议](../../docs/SONIC_REFERENCE_REPLAY.md)。

开始或停止请求发出后，如果浏览器没有收到有效响应，页面显示“正在确认启动/停止结果”，
通过只读查询核对同一计划、执行实例和服务归属，不自动重复提交操作。确认执行开始或
返回 Planner 后更新进度；明确的请求拒绝和执行端停机原因仍显示为错误。

服务配置见 [独立服务](independent-services.md)。

Status-query outages are distinct from execution failures. The first two failed browser or Lab-to-OM status polls show a reconnecting state and disable new starts. Three consecutive failures show a persistent connection warning; this does not assert that the runtime has stopped. A successful validated poll clears the warning. Confirmed execution failures retain priority even if a later status poll fails. No command is automatically retried.

Collection actions, episode inspection, and native replay commands fetch the current same-origin web session with no-store before every user operation. This avoids reusing process-local tokens after a web restart. Failure to obtain the session sends no command; submitted commands are never automatically retried. Host and Origin validation remain unchanged.

The recording-list poll is separate from execution polling. A transient list-query failure displays list recovery status, not execution failure. Three consecutive list failures show a list-specific warning; recovery clears it without issuing an execution command. The integrated page regression runs selection, inspection, load and start with concurrent list-query failure.
