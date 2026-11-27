# 独立 OML 服务

统一向导安装 OM 和 Lab 各自的 Python 环境与服务；Lab 也可作为独立 Python 包安装。
OML 网页和 Collector 是两个独立进程；OM 通过 iframe 嵌入网页，通过
HTTP/遥测协议提供状态和执行服务。双方不导入彼此的内部 Python 模块。

常规机器人安装使用 [OM + Lab 统一向导](../README.md#v020-部署配置准备)，自动生成配置并启动这两个空闲服务。以下命令用于开发或自定义部署，使用时填写真实配置路径。

```bash
onboardmimic-lab-collector --config /path/collection.json
onboardmimic-lab-console --host 127.0.0.1 --port 8773 --replay-root /path/data --real-replay-config /path/real.json
```

`real.json` 指定本机 gateway 和私有 token_file。省略 real-replay-config 时不开放
真机回放。配置见 [数采说明](collection.md)。

Collector 默认使用 `~/.local/state/onboardmimic-lab/collector.sock`。目录必须是当前用户所有的0700目录，socket权限0600；不开放TCP采集管理端口。同一socket有独占锁，数据目录继续使用独占锁。自定义位置时，服务使用 `--socket`，网页使用 `--collector-socket`。

Collector 的网关适配器只调用 `GET /api/controller`；配置可选 `gateway`（默认 `http://127.0.0.1:18921`）及私有 `token_file`。控制 ticket/epoch 不传到网页，网关凭据不暴露到浏览器。网关不在时，页面仍能查询已有采集状态，但开始录制被拒绝。

网页写操作必须带同源 Origin 和网页进程的会话令牌。网页重启后令牌失效，浏览器重新获取；Collector 仍持有原录制。丢失应答不自动重试写操作。数据服务不可用时页面报错，不偷偷启动另一个 Collector。

## 生命周期

- 关闭浏览器或重启 Lab 网页服务：录制继续。
- 停止 Collector：结束工作线程；服务再次启动仍遵循原有失败/待审恢复规则，不自动恢复录制。
- OM 就绪状态或相机失效：原有持续检查仍将采集标记失败。
- `systemd` 模板见 `deploy/systemd/`；控制台没有 `PartOf` 或 `BindsTo` 将 Collector 生命周期绑到网页。如需入口链接，给网页服务 ExecStart 追加 `--runtime-console-url` 和目标机器的控制台 URL。
