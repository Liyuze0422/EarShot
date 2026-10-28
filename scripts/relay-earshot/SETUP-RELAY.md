# EarShot relay：模式 B 五步部署

本机没有 VPS/域名/ADMIN_IP；此交付是可执行部署包，不表示线上已经部署。

1. **准备 Ubuntu VPS 与 DNS**：`api.DOMAIN`、`admin.DOMAIN` 指向服务器，开放 80/443；不要开放 3000/MySQL/Redis。将本目录上传到服务器 `/root/relay-earshot`。
2. **设置输入**：在服务器终端运行下列命令；密码不写入代码、不随安装包分发。
   ```bash
   export DOMAIN=example.com ADMIN_IP=203.0.113.10 TOKEN_QUOTA=1000000
   read -rs -p 'DeepSeek key: ' DEEPSEEK_KEY; export DEEPSEEK_KEY; echo
   # 可选：read -rs -p 'Doubao key: ' DOUBAO_KEY; export DOUBAO_KEY; echo
   # NEW_ADMIN_PASS、SESSION_SECRET、MYSQL_ROOT_PASSWORD 留空时自动生成。
   ```
3. **一次运行部署及 API 初始化**：`cd /root/relay-earshot && sudo -E bash deploy-relay.sh`。脚本固定 `calciumion/new-api:v1.0.0-rc.47`，创建 MySQL/Redis/new-api/Caddy，等待就绪，通过 `/api/setup`、登录、option/channel/token API 初始化；重复执行保留数据库和已消费额度，不创建重复令牌。任何 API 失败会中止并返回非零，不报告成功。
4. **在客户端保存中转信息**：地址 `https://api.DOMAIN/v1`；服务器令牌在 `/opt/api/relay-token.env`（权限 600），后台强密码在 `/opt/api/.env`。在 EarShot 设置页填中转地址、令牌、快/准档位，令牌以当前 Windows 账户 DPAPI 加密存到 `%LOCALAPPDATA%/EarShot/credentials`；JSON 只存引用。已有 `TP_BASE_URL` 继续直连；显式启用中转前先移除旧环境变量。直连兜底需要**另一个有效 DeepSeek 官方 key**，不能用中转令牌代替。
5. **执行验收**：在 Windows PowerShell 设置 `TP_RELAY_BASE_URL`、`TP_RELAY_API_KEY`、`EARSHOT_ADMIN_URL`、`EARSHOT_SSH_TARGET`，运行 `./verify.ps1 -LocalEvidence local-evidence.json -Output verification.json`。10 次/模型采集真实首正文 token P50/P95；缺配置/缺执行证据显示 FAIL，不以静态代码代替运行结果。第 4 项需要手机流量等白名单外请求证据；证据 JSON 字段为 `url`、`http_status`、`source_ip`、`timestamp`，导入路径用 `-OfflistEvidence`。

## 必须说明的差异与假设

- 当前上游的首次启动没有 root/123456 默认账户；使用 setup API 直接建立强密码 root，再验证 123456 被拒绝。不会先主动设置弱密码。旧数据库已有 root 时不擅自重置账号。
- 根路径 `/` 仅提供静态 GET/HEAD 健康应答 200（为满足 HTTPS 验收）；真正的代理仅 `/v1/*`，`/panel`、`/api/*` 等均 404。
- 没有豆包 key：不建豆包渠道；`earshot-fast` 暂映射 DeepSeek 并在结果标明，不能声称 fast 性能达标。指定豆包旧模型若已下线或无价格条目，初始化明确失败；不擅自换模型。
- 初始化拷贝上游已配置的 ModelRatio/CompletionRatio 到两个别名，不擅自定价；上游模型没有价格配置时必须先通过管理 API 配置，再重跑。
- 毫秒阈值是验收目标，不是供应商承诺；重试 3 次不等于首字必达标。
- 每日 03:00 备份真实 MySQL 数据与部署配置；04:00 清理 `/opt/api/logs` 中七天前日志。备份包含凭据，仅 root 可读。

## 回退

- 客户端：设置页清空中转地址，或设 `TP_RELAY_ENABLED=false`；旧 TP_* 路径保留。
- 服务器：`docker compose --project-directory /opt/api down`（不带 `-v`，保留数据），可用备份恢复数据库。不要删除数据卷。
- Git：`origin` 保留原作者仓库；本任务以 `fork` 推送 `POP-YU/EarShot`，通过上游 PR 合并，不冒充拥有原仓库写权限。

## 核源

部署 API/字段以 [new-api v1.0.0-rc.47](https://github.com/QuantumNous/new-api/tree/v1.0.0-rc.47) 的 `controller/setup.go`、`user.go`、`channel.go`、`token.go`、`router/channel-router.go` 和 `constant/channel.go` 为依据。服务器部署仍需现场运行验收。
