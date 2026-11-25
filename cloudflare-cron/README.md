# 定时触发器部署与维护

当前部署（2026-11-25 核对）：Cloudflare Worker 为 `acgnx-cron`，每 5 分钟触发 `H-ym0422/acgnx-watcher` 的 `watch.yml`。抓取、资源推送和去重状态保存由 GitHub Actions 完成。

本部署的 `GH_TOKEN` 已在 2026-11-25 11:14（北京时间）更新；线上程序核对的类型为 **classic Personal Access Token（经典式个人访问令牌）**，更新后已成功触发工作流。令牌在 Cloudflare 以 Secret 保存，文档和状态只记录类型，不保存令牌内容。其他独立部署也可使用具有 Actions 读写权限的细粒度令牌。

## 告警规则

- 单次失败仅记录，连续 3 次定时触发请求失败后再核对 GitHub 运行记录。
- 最近 10 分钟已有新的排队、执行中或成功运行时，暂缓触发故障告警；后续仍继续核对。
- 无法读取运行记录时，提醒明确写“无法核实”，不把它当作监听已停止。
- 同一故障只成功提醒一次；成功触发后发送一次恢复通知。单次失败后正常恢复不发故障或恢复消息。
- 状态保存在绑定 `SCHEDULER_HEALTH` 的 Workers KV 中，Worker 重启或更新不会清空计数。重复或过期的同一轮定时事件不重复计数。
- GitHub 接受触发请求时返回 HTTP 204，空响应正文是正常情况。手动触发不参与定时故障计数。

一次 `401 Bad credentials` 表示该次请求未通过认证；不等于之后全部任务都会停止。定时器会继续每 5 分钟尝试。

## 配置项

| 名称 | 保存位置／类型 | 用途 |
|---|---|---|
| `GH_TOKEN` | Cloudflare Secret | 授权调用 GitHub 工作流触发接口；当前生产环境使用经典式令牌 |
| `REPO` | Worker 普通变量 | `用户名/仓库名` |
| `WORKFLOW` | Worker 普通变量 | 通常为 `watch.yml` |
| `SCHEDULER_HEALTH` | Workers KV 绑定 | 连续失败、最近成功、告警是否送达和令牌类型 |
| `FEISHU_WEBHOOK` | Cloudflare Secret，可选 | 发送定时触发异常／恢复提醒 |
| `FEISHU_SECRET` | Cloudflare Secret，可选 | 飞书机器人开启签名校验时使用 |
| `SECRET` | Cloudflare Secret，可选 | 手动触发的访问密钥；未配置时关闭手动触发 |

Cloudflare 的 `GH_TOKEN` 与仓库 Actions Secrets 的 `ACGNX_ISSUE_DELETE_TOKEN` 用途不同，替换其中一个不会自动更新另一个。正常资源推送使用的 `ACGNX_WEBHOOK_URL` 也与 Worker 的 `FEISHU_WEBHOOK` 分开配置。

## 首次独立部署

1. 安装 Node.js 和 Wrangler，复制 `wrangler.toml.example` 为自己的 `wrangler.toml`，填写 Worker 名称、仓库和工作流。不要覆盖已运行的个人部署。
2. 运行 `npx wrangler login` 登录自己的 Cloudflare 账号。
3. 运行 `npx wrangler kv namespace create SCHEDULER_HEALTH`，将返回的命名空间 ID 填入 `[[kv_namespaces]]`。
4. 运行 `npx wrangler secret put GH_TOKEN`，按提示输入有效令牌。需要告警时，再配置 `FEISHU_WEBHOOK`；需要手动触发时，配置 `SECRET`。
5. 运行 `npx wrangler deploy`，确认定时配置仍为 `*/5 * * * *`，并在下一轮 GitHub Actions 中核对结果。

每五分钟运行一次时，正常约每天读取、写入 KV 各 288 次，低于 Workers Free 的每天 100,000 次读取、1,000 次写入额度。额度及计费以 [Cloudflare 官方说明](https://developers.cloudflare.com/kv/platform/pricing/) 为准。

## 已有部署升级与检查

修改 Python 监听程序只需更新仓库；修改本目录的定时器代码，需要另外更新现有 Worker。部署本次告警优化时，同时上传 `worker.js` 和 `scheduler-health.js`，保留原有 Secrets、普通变量与五分钟定时配置，并添加 `SCHEDULER_HEALTH` KV 绑定。

浏览器访问 `/ping` 只检查 Worker 是否能响应，不触发工作流。手动触发使用 `/?k=访问密钥`，必须配置 `SECRET`；密钥不放进公开文档或截图。原有飞书回调兼容入口仍保留。

KV 状态键为 `scheduler-health:v1:用户名/仓库名:工作流文件名`，包含 `credential_type`、`last_status`、`failures`、`last_success` 等非敏感诊断信息。核对令牌是否更换成功，以运行时记录的类别和实际触发结果为依据，不读取 Secret 明文。

本地回归检查：

```powershell
node --test cloudflare-cron/scheduler-health.test.mjs
```
