# 原始保存与后台数据处理

版本由 OM 根目录 VERSION 管理。

## 操作流程

1. 结束采集：flush/fsync 原始视频和遥测，记录文件摘要，进入待保存。
2. 保存：复核原始文件，原子移动至 `episodes/<id>`，确认目录落盘。
3. 页面显示“原始数据已保存”，立即允许下一条采集。数据处理排队状态单独显示。
4. 后台数据处理完成后才允许回放；失败保留原始数据并提供“重试数据处理”。

Collector 的 `state=saved` 现在表示原始数据保存成功，不代表导出完成。
`exports[]` 提供 queued/running/paused/complete/failed 状态、episode 编号和错误。
原始 manifest 使用 `status=raw_saved` 与 `export` 描述待处理状态，软件参考后端导出完成才写
`status=saved` 和 `aligned_manifest`。真机导出完成后发布标准 LeRobot meta，并删除临时原始目录。旧格式不再提供回放。
`POST /api/collection {action:"retry_export", id:"<episode UUID>"}` 重试失败任务。
回放列表展示未处理记录，但 inspect/real_load/simulate 会拒绝 raw_saved。

## 资源和恢复

- 单个后台子进程、nice +10；FFmpeg 解码/滤镜/编码各限制线程数。
- 新采集期间暂停后台进程组（包含 FFmpeg），采集收尾后继续；队列等待不挡采集。
- 后台有效处理时限1200秒（20分钟），不计暂停时间；关闭服务会终止进程组，任务留待重启恢复。
- Linux parent-death guard 防止 Collector 异常退出后遗留解码进程。
- 原始数据不会因为导出失败而删除。重试只清理已中断的 `.export-<uuid>` 临时产物。
- 录制中断仍标记 failed，不将不完整采集自动接受为可回放数据。

## 处理链路

新 SONIC episode 使用 [LeRobot v2.1 格式](lerobot-storage.md)。
保存确认表示原始数据已安全接收，后台整理完成后才形成最终精简数据。
保存为 Parquet、MP4 和标准 meta 索引；实际消费证据、连续片段和75ms因果匹配在
导出前校验。实际采样时钟另存 source 列，标准数据/视频时间轴为50Hz。
成功后删除临时原始流。旧格式不再兼容；软件参考后端仅用于契约测试。

相机 PTS 索引由 `camera_index.py` 统一提供。软件参考后端保留紧凑索引格式用于
契约联调；旧 reference 逐帧 JPEG 格式的导出与读取入口已删除。
