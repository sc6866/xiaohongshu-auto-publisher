# 部署说明

## 推荐方案

你现在没有公网 IP，最适合的线上方案是：

`GitHub 仓库 -> GHCR 镜像 -> Docker 部署 -> Cloudflare Tunnel 外网访问`

这套方案的优点是：

- 不需要公网 IP
- 不需要自己维护复杂反代穿透
- 手机和外网都能直接访问 Web 控制台
- 后面换机器时只要 `git pull` 或 `docker compose pull` 就能迁移

## 架构说明

推荐按下面的职责拆分：

- `app` 容器：运行主应用、Web 控制台、内容生成、数据库
- `cloudflared` 容器：把本机 `app:8787` 暴露到外网
- Windows 宿主机：继续运行已经登录的小红书 MCP 程序

也就是说，真实发布链路仍然依赖宿主机上已登录的 `xiaohongshu-mcp-windows-amd64.exe`。

## 1. GitHub 与镜像发布

项目里已经加好了：

- `.github/workflows/docker-publish.yml`

当你把仓库推到 GitHub 的 `main` 分支后，GitHub Actions 会自动构建镜像并推送到：

`ghcr.io/sc6866/xiaohongshu-auto-publisher:latest`

## 2. 先清理本地敏感配置

为了安全推仓库，项目已经做了这些处理：

- `config/settings.yaml` 已改成可公开版本
- `config/settings.local.yaml` 用来放本机私有账号信息
- `.gitignore` 已忽略 `config/settings.local.yaml`、数据库、日志、上传内容

你需要确认不要把下面这些文件提交上去：

- `config/settings.local.yaml`
- `.env`
- `data/`
- `logs/`

## 3. 本地 GitHub 推送

如果你准备把当前目录变成 Git 仓库，可以这样做：

```powershell
git init
git add .
git commit -m "init xiaohongshu automation project"
git branch -M main
git remote add origin https://github.com/sc6866/xiaohongshu-auto-publisher.git
git push -u origin main
```

推上去以后，等 GitHub Actions 跑完，就会生成 GHCR 镜像。

## 4. 服务器或另一台机器部署

### 4.0 一键部署脚本

仓库已经自带一键部署脚本：

- `scripts/bootstrap-deploy.ps1`
- `scripts/deploy.ps1`
- `scripts/setup-env.ps1`

用法分两种。

第一种，目标机器还没有代码：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-deploy.ps1
```

第二种，代码已经存在，只更新并部署：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1
```

如果你希望脚本连 Cloudflare Tunnel 一起拉起：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1 -WithTunnel
```

脚本会自动做这些事：

- 检查 `git` 或 `docker`
- 自动拉取或更新 GitHub 仓库
- 如果 `.env` 不存在，则自动进入交互式配置
- 检查必要的 API Key 是否已经填写
- 调用 `docker compose -f docker-compose.deploy.yml pull`
- 调用 `docker compose -f docker-compose.deploy.yml up -d`
- 可选同时启动 `cloudflared`

如果你希望“新机器第一次部署”直接一条命令完成，推荐这一条：

```powershell
git clone https://github.com/sc6866/xiaohongshu-auto-publisher.git $env:USERPROFILE\deploy\xiaohongshu-auto-publisher; cd $env:USERPROFILE\deploy\xiaohongshu-auto-publisher; powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1 -WithTunnel
```

运行到 `.env` 阶段时，脚本会自动逐项询问：

- DASHSCOPE API Key
- 百度 OCR API Key
- 百度 OCR Secret Key
- Cloudflare Tunnel Token
- MCP 地址
- 可选的 `user_id / xsec_token`

### 4.1 准备 `.env`

复制一份：

```powershell
Copy-Item .env.example .env
```

然后填写这些变量：

```env
XHS_APP_IMAGE=ghcr.io/sc6866/xiaohongshu-auto-publisher:latest
XHS_WEB_PUBLIC_BASE_URL=https://xhs.你的域名.com
XHS_MCP_BASE_URL=http://host.docker.internal:18090
XHS_MCP_AUTO_START=false
XHS_PUBLISHER_USER_ID=
XHS_PUBLISHER_XSEC_TOKEN=
DASHSCOPE_API_KEY=
BAIDU_OCR_API_KEY=
BAIDU_OCR_SECRET_KEY=
CF_TUNNEL_TOKEN=
```

说明：

- `XHS_MCP_BASE_URL` 指向宿主机上的小红书 MCP
- `CF_TUNNEL_TOKEN` 是 Cloudflare Tunnel 的 token

### 4.2 拉镜像部署

如果你使用 GHCR 镜像部署，执行：

```powershell
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

如果还要一起启动 Cloudflare Tunnel：

```powershell
docker compose -f docker-compose.deploy.yml --profile tunnel up -d
```

## 5. Cloudflare Tunnel

### 5.1 为什么推荐它

因为你没有公网 IP，而 Cloudflare Tunnel 可以让外网访问你的本地服务，不需要开放入站端口。

### 5.2 你要做的准备

你需要：

- 一个 Cloudflare 账号
- 一个已接入 Cloudflare 的域名
- 在 Cloudflare Zero Trust 里创建 Tunnel
- 拿到 Tunnel Token

### 5.3 这个项目里的接法

项目已经在 `docker-compose.yml` 和 `docker-compose.deploy.yml` 里加好了：

- `cloudflared` 服务
- `--profile tunnel` 启动方式
- `CF_TUNNEL_TOKEN` 环境变量注入

你只需要把 Cloudflare 后台生成的 token 写进 `.env`，然后运行：

```powershell
docker compose -f docker-compose.deploy.yml --profile tunnel up -d
```

## 6. 本机继续保留的小红书 MCP

这一点很重要：

当前真实发布依赖 Windows 上已登录的小红书 MCP 程序，所以 Docker 不是替代它，而是调用它。

你需要保证宿主机上：

```powershell
http://127.0.0.1:18090/health
```

可以正常返回健康状态。

然后容器通过：

`http://host.docker.internal:18090`

去访问它。

## 7. 健康检查与验证

应用启动后可以检查：

```powershell
curl http://127.0.0.1:8787/healthz
```

如果 Cloudflare Tunnel 正常，外网域名也应该能打开控制台。

## 8. 常用命令

本地构建运行：

```powershell
docker compose up -d --build
```

GHCR 镜像部署：

```powershell
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

查看日志：

```powershell
docker compose logs -f app
docker compose -f docker-compose.deploy.yml logs -f app
docker compose -f docker-compose.deploy.yml logs -f cloudflared
```

## 9. 参考文档

- Cloudflare Tunnel Docker: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/deploy-tunnels/deployment-guides/docker/
- GitHub Container Registry: https://docs.github.com/packages/working-with-a-github-packages-registry/working-with-the-container-registry
