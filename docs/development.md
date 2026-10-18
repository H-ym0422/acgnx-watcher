# 开发与部署

## 本地验证

运行程序只依赖 Python 标准库；建议 Python 3.12。开发检查额外需要 PyYAML、Bash；Actions JS 参数链测试需要 Node.js。

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python check_workflows.py --require-bash
```

测试使用固定 RSS 和模拟的接口返回，不发送真实通知。CI 使用 Linux 检查工作流 YAML/Bash 和全部回归测试。

## 模块

| 文件 | 职责 |
|---|---|
| acgnx_watch.py | 命令入口、采集编排、版本筛选、队列执行与一轮调度 |
| add_rule.py | 搜索向导、回溯、清单与命令行规则管理 |
| issue_commands.py | Issue 参数、原清单快照、业务执行结果 |
| watcher_core/network.py | HTTP 与镜像探测 |
| watcher_core/parsing.py | RSS、集数、字幕、季号、组名解析 |
| watcher_core/matching.py | 规则匹配、普通兜底抑制 |
| watcher_core/notifications.py | 飞书等渠道与响应判断 |
| watcher_core/configuration.py | 字段验证与原子配置写入 |
| watcher_core/rules.py | 稳定 ID、重复规则检查、保留 ID 的修改 |
| watcher_core/state_store.py | 状态版本、备份恢复与旧键兼容 |
| watcher_core/health.py | 源可用性、连续失败与告警转移 |

主入口继续导出原解析函数，兼容原有 add_rule 与测试调用。

## 云端部署

已有仓库升级：合并通过 CI 的 PR，保留 config.json/state.json，让下一轮工作流完成状态迁移和备份。不要重新部署 Worker。

新部署：复制项目到自己的仓库，将 config.example.json 复制为 config.json 并调整规则。
在仓库 Actions Secrets 配置 `ACGNX_WEBHOOK_URL`（飞书群机器人），加签时补 `ACGNX_FEISHU_SECRET`。
其他渠道沿用代码中的对应 Secrets。密钥不得放入公开配置。先手动运行 watch.yml 的测试模式验证通知，再配置 [Worker](../cloudflare-cron/README.md)。

三个写状态工作流共用并发组并检出 main。GitHub 并发组不保证先进先出，大量连续操作仍需检查运行结果。提交历史是恢复记录，不要随意压缩或删除状态提交。

## 贡献与发布

修改解析规则请附固定标题/RSS 样本和期望结果；修改工作流要同时验证生产端 JS 与消费端 Python。
功能改动与状态迁移需写清回滚限制。截图和示例不得包含 Webhook、Token 或其他密钥。

许可证尚未由项目所有者选择，本轮不擅自授予第三方再分发许可。自用功能和仓库简介不依赖选择许可证；准备正式开源推广时再明确许可。
