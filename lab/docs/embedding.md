# OnboardMimic 内嵌 Lab

OM 通过 iframe 展示 Lab 数采和回放页面。源码位于同一仓库，网页与 Collector
仍独立运行，通过公开接口交互，不复制界面或导入彼此的内部 Python 模块。

## 地址与来源

统一安装向导根据 `--access` 和 `--origin` 自动设置 iframe 地址及 Lab 允许的来源。普通安装无需再手动执行下方命令；这些参数用于自定义部署。

```bash
onboardmimic-lab-console --host 0.0.0.0 --port 8773 \
  --embed-origin http://ROBOT_IP:8769
```

OM 的 `GET /api/console-config` 提供 `lab_embed.enabled` 和 `lab_embed.url`。
iframe 通常使用 `http://ROBOT_IP:8773/embed`；填写实际可达地址，不写死机器人 IP。
Lab 必须使用独立来源，宿主 HTTPS 时 Lab 也须使用 HTTPS。

`--embed-origin` 是浏览器地址栏中 OM 的完整来源（协议、主机和端口），可重复指定。
不允许路径、通配符或凭据。未配置来源时禁止嵌入；独立 `/` 页面始终禁止嵌入。
SSH 转发时应配置浏览器实际访问的转发来源，并转发 Lab 端口。

## 页面就绪、占位与重试

iframe 初始隐藏并显示占位。OM 向配置的精确来源发送
`{type:"onboardmimic.lab.probe", version:1, nonce:"<本次随机值>"}`。
Lab 确认页面、样式和采集控件完成加载后，回复同一 nonce 的
`onboardmimic.lab.ready`。双方检查来源与窗口身份，OM 同时检查版本和 nonce。

只有收到有效确认才显示 iframe；`load` 事件本身不算嵌入成功。
OM 每 2 秒探测页面，超过 8 秒没有确认则恢复占位，提供重新连接或单独打开 Lab。
重新连接只重建嵌入页面。高度由 OM 控制台布局管理。

页面确认只证明界面已加载，不能代替 Collector、相机、机器人 state 或录制就绪状态。
探测用于显示，不是录制或回放的续租；页面失联不会取消正在执行的后台任务。

## 语言

OM 在 iframe 加载及宿主语言变化时向精确来源发送：

```json
{"type":"onboardmimic.lab.language","version":1,"language":"zh-CN"}
```

英文使用 `en`。Lab 只接受父窗口及允许来源的消息；切换语言不刷新页面、不改变录制。
独立访问 Lab 时默认使用浏览器语言。界面错误按选择的语言解释，技术原文可展开查看。

## 鉴权与生命周期

`frame-ancestors` 只决定谁可嵌入，不授予父页面调用 Lab API 的权限。
Lab 写请求仍需自己的同源 Origin 和网页会话令牌；OM 的网关 token、控制 ticket
及 epoch 不通过 iframe 消息共享。

关闭浏览器、刷新 OM、重载 iframe 或重启 Lab 网页不会停止独立 Collector 的录制。
已启动真机回放由 OM 持有；断连后恢复页面先查询状态，不自动再次开始。
服务配置和停止规则见[独立服务](independent-services.md)与[回放协议](../../docs/SONIC_REFERENCE_REPLAY.md)。
