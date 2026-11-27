# Lab 文档 / Lab documentation

OM 与 Lab 同仓库、统一版本，运行时通过 HTTP 和遥测协议解耦。
OM owns robot execution and protection; Lab owns recording, datasets and its UI.

## 使用 / Operation

- [OM + Lab 统一安装 / Unified installation](../README.md#v020-部署配置准备)
- [Lab 功能与独立开发 / Features and standalone development](../README.md)
- [采集到回放 / Recording through replay](pipeline.md)
- [采集配置与生命周期 / Collection configuration and lifecycle](collection.md)
- [后台数据处理 / Background processing](background-export.md)
- [LeRobot v2.1 存储 / Storage format](lerobot-storage.md)
- [回放页面 / Replay console](replay-console.md)

## 接口与部署 / Interfaces and deployment

- [独立服务 / Independent services](independent-services.md)
- [iframe、语言与就绪确认 / Embedding, language and page readiness](embedding.md)
- [软件 WBC 契约 / Software WBC contract](wbc-contract.md)
- [SONIC 录制就绪 / SONIC recording readiness](../../docs/SONIC_RECORD_READY.md)
- [模式无关录制 / Mode-independent recording](../../docs/SONIC_MODE_INDEPENDENT_RECORDING.md)
- [OM 真机回放协议 / Physical replay protocol](../../docs/SONIC_REFERENCE_REPLAY.md)
- [部署与切换 / Deployment and activation](../../docs/INCREMENTAL_DEPLOYMENT.md)
