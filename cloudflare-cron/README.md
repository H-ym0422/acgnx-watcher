# 定时器部署

这是不含密钥的 Cloudflare Worker 模板，每五分钟触发主分支的 `watch.yml`。

1. 安装 Node.js 和 Wrangler，在本目录复制 `wrangler.toml.example` 为 `wrangler.toml`。
2. 把 `REPO` 改成自己的 `用户名/仓库名`，运行 `npx wrangler login` 登录。
3. 在 GitHub 创建仅授权目标仓库的细粒度令牌，授予 Actions 读写权限。
4. 运行 `npx wrangler secret put GH_TOKEN`，按提示输入令牌。不要把令牌写进配置文件。
5. 运行 `npx wrangler deploy`，在 Cloudflare 查看定时触发日志，再确认 GitHub Actions 收到运行。

网页访问只显示状态文字，不会触发任务。模板无需 Contents 写权限；写入去重状态使用工作流自身的 GITHUB_TOKEN。

此模板尚未部署到你的 Cloudflare 账户。已有 Worker 可以继续使用，替换前请检查自己的变量设置。
