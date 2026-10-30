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
| issue_commands.py | Issue 权限、兼容命令、原清单快照和执行结果 |
| watcher_core/natural_language.py | 常见中文操作及番名、季度、集数、组名提取 |
| watcher_core/issue_natural.py | 条件冲突校验、唯一规则选择及兼容路由 |
| watcher_core/resource_query.py | 仅读取采集，按季度/集数/字幕组回复资源 |
| watcher_core/network.py | HTTP 与镜像探测 |
| watcher_core/parsing.py | RSS、集数、字幕、季号、组名解析 |
| watcher_core/matching.py | 规则匹配、普通兜底抑制 |
| watcher_core/fallback_wait.py | 逐集 7 天等待、候选保存与指定组优先取消 |
| watcher_core/notifications.py | 飞书等渠道与响应判断 |
| watcher_core/configuration.py | 字段验证与原子配置写入 |
| watcher_core/rules.py | 稳定 ID、重复规则检查、保留 ID 的修改 |
| watcher_core/state_store.py | 状态版本、备份恢复与旧键兼容 |
| watcher_core/health.py | 全局与单部番源可用性、故障／恢复转移 |
| watcher_core/release_identity.py | 资源类型、编号证据、范围覆盖及旧键兼容 |
| watcher_core/outbox_policy.py | 发送前按当前规则重新验证，季度不明时暂缓 |
| watcher_core/remote_checkpoint.py | Actions main 的远端队列／收据检查点 |
| watcher_core/send_budget.py | 重试与新通知共享发送数量和时间预算 |
| watcher_core/rss_search.py | 新旧关键词 RSS 入口、镜像切换和响应有效性校验 |
| watcher_core/collection.py | 双路抓取、逐规则源统计和季度证据 |
| watcher_core/cycle.py | 一轮采集、筛选、等待和队列调度 |
| watcher_core/outbox.py | 持久队列发送及逐条收据 |
| watcher_core/presentation.py | 推送文案 |
| watcher_core/discovery.py | 搜索、选组清单与规则构建 |
| watcher_core/backfill.py | 初始化基线、回溯范围与历史通知 |
| watcher_core/watchlist.py | 规则、源状态、待确认季度和送达记录展示 |
| watcher_core/rule_cli.py | 加番／修改／暂停等命令编排 |

主入口保留兼容函数，集中提供运行依赖；功能模块各自负责采集、队列、展示或命令编排。测试同时覆盖模块规则和真实入口组合，不依赖真实推送接口。故障测试包括大队列分批、远端失败时禁止发送、规则修改后的旧队列、别名／合集覆盖、漫画缓存清理、单部番告警与季度待确认。

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

## Issue 清理认证与一次性维护

`issue-rule.yml` 在规则删除成功且状态保存成功后，使用 `ACGNX_ISSUE_DELETE_TOKEN` 创建独立的 Octokit 客户端执行 `deleteIssue`。不要仅覆盖默认客户端单次请求的 Authorization 请求头，其认证 hook 可能重新写入默认机器人身份。普通回复和归档继续使用默认机器人客户端，避免所有者评论再次触发 Issue 指令。

`cleanup-issues-20261019.yml` 是已完成的一次性维护：验证所有者身份、三条当前规则及每条目标的编号、节点 ID 和标题；只清理固定的 61 条历史 Issue，排除 PR 与后来新增的 Issue。它与状态写入任务共用并发组，不修改 config/state、不发送飞书通知；不要扩展为默认扫描删除所有 Issues。

追番记录 Issue 是整理时的入口，不替代 config.json 或自动生成的《我的番剧》。修改追番列表的固定说明需同时更新 `watcher_core/watchlist.py`，否则下次生成会覆盖手工文案。飞书应用发送支持不等于已实现撤回；当前没有自动撤回与界面自动化实现。
