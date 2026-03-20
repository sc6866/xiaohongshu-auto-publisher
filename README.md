# 小红书自动化发布系统

这是一个已经打通核心链路的项目，当前支持：

- 热点扫描与素材池沉淀
- 文案生成与原创性审核
- 图片分析后生成真人测评/攻略文案
- AI 封面生成
- Web 控制台
- 真实发布
- `note_id` 回填
- 最新作品同步
- 24h 数据追踪

## 运行方式

项目现在支持两种运行方式：

### 1. 本机直接运行

```powershell
python main.py web --host 0.0.0.0 --port 8787
```

### 2. Docker 长期运行

```powershell
docker compose up -d --build
```

如果你是从 GitHub 镜像部署：

```powershell
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

## 一键部署脚本

仓库里已经带了两个 PowerShell 脚本：

- [scripts/bootstrap-deploy.ps1](/c:/Users/Administrator/Desktop/xiaohongshu/scripts/bootstrap-deploy.ps1)
- [scripts/deploy.ps1](/c:/Users/Administrator/Desktop/xiaohongshu/scripts/deploy.ps1)
- [scripts/setup-env.ps1](/c:/Users/Administrator/Desktop/xiaohongshu/scripts/setup-env.ps1)

如果目标机器还没有代码，可以直接执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-deploy.ps1
```

如果你已经 `git clone` 过仓库，只需要执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1
```

如果还要一起启 Cloudflare Tunnel：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1 -WithTunnel
```

如果你想要“新机器第一次就一条命令跑到底”，推荐直接用这一条：

```powershell
git clone https://github.com/sc6866/xiaohongshu-auto-publisher.git $env:USERPROFILE\deploy\xiaohongshu-auto-publisher; cd $env:USERPROFILE\deploy\xiaohongshu-auto-publisher; powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1 -WithTunnel
```

这条命令会：

- 拉取仓库
- 如果没有 `.env`，自动进入交互式填写
- 填完后自动拉镜像并启动服务
- 同时启动 Cloudflare Tunnel

## 没有公网 IP 怎么外网访问

推荐直接走 Cloudflare Tunnel。

项目里已经预留好了：

- `cloudflared` 容器
- `CF_TUNNEL_TOKEN` 环境变量
- `--profile tunnel` 启动方式

启动示例：

```powershell
docker compose -f docker-compose.deploy.yml --profile tunnel up -d
```

## 推到 GitHub 后自动发镜像

项目里已经加好 GitHub Actions：

- [.github/workflows/docker-publish.yml](/c:/Users/Administrator/Desktop/xiaohongshu/.github/workflows/docker-publish.yml)

推送到 `main` 后会自动发布 GHCR 镜像。

当前仓库地址：

- `https://github.com/sc6866/xiaohongshu-auto-publisher`

默认镜像地址：

- `ghcr.io/sc6866/xiaohongshu-auto-publisher:latest`

## 敏感配置

为了便于推 GitHub：

- 公开配置放在 [settings.yaml](/c:/Users/Administrator/Desktop/xiaohongshu/config/settings.yaml)
- 私有账号配置放在 [settings.local.yaml](/c:/Users/Administrator/Desktop/xiaohongshu/config/settings.local.yaml)
- 本地变量模板在 [.env.example](/c:/Users/Administrator/Desktop/xiaohongshu/.env.example)

`settings.local.yaml` 和 `.env` 已被 `.gitignore` 忽略。

## 部署文档

完整部署说明见：

- [deploy.md](/c:/Users/Administrator/Desktop/xiaohongshu/docs/deploy.md)
