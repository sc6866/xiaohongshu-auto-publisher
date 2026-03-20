# 小红书自动化发布系统

这个项目已经打通了核心链路，当前支持：

- 热点扫描与素材池沉淀
- 文案生成与原创性审核
- 图片分析后生成真人测评/攻略文案
- AI 封面生成
- Web 控制台
- 真实发布
- `note_id` 回填
- 最新作品同步
- 24h 数据追踪

仓库地址：

- `https://github.com/sc6866/xiaohongshu-auto-publisher`

默认镜像地址：

- `ghcr.io/sc6866/xiaohongshu-auto-publisher:latest`

## 本地运行

```powershell
python main.py web --host 0.0.0.0 --port 8787
```

## Docker 运行

```powershell
docker compose up -d --build
```

如果使用 GHCR 镜像部署：

```powershell
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

## Windows 一键部署

仓库里带了这几个 PowerShell 脚本：

- `scripts/bootstrap-deploy.ps1`
- `scripts/deploy.ps1`
- `scripts/setup-env.ps1`

新机器首次部署可以直接用：

```powershell
git clone https://github.com/sc6866/xiaohongshu-auto-publisher.git $env:USERPROFILE\deploy\xiaohongshu-auto-publisher; cd $env:USERPROFILE\deploy\xiaohongshu-auto-publisher; powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1 -WithTunnel
```

## Linux VPS 一键部署

如果你有域名和 Linux VPS，优先使用 VPS 脚本，不再依赖 Tunnel。

仓库里带了这几个 Bash 脚本：

- `scripts/bootstrap-vps.sh`
- `scripts/deploy-vps.sh`
- `scripts/setup-env-vps.sh`

VPS 首次部署命令：

```bash
curl -fsSL https://raw.githubusercontent.com/sc6866/xiaohongshu-auto-publisher/main/scripts/bootstrap-vps.sh | bash
```

这套脚本会自动：

- 拉取仓库到 `/opt/xiaohongshu-auto-publisher`
- 交互式生成 `.env`
- 安装 Docker、Nginx
- 拉取 GHCR 镜像并启动容器
- 写入 Nginx 反代配置
- 可选申请 HTTPS 证书

## 重要限制

如果你的 VPS 是 Linux，而你现在能用的小红书 MCP 只在本地 Windows 上登录过，那么：

- VPS 可以正常跑 Web、生成、审核、封面
- 但真实发布仍然依赖一个“VPS 可访问到”的已登录 MCP 地址

也就是说，部署到 VPS 后，`.env` 里的 `XHS_MCP_BASE_URL` 不能随便填，必须填成 VPS 真能访问到的 MCP 服务地址。

## 配置文件

- 公开配置：`config/settings.yaml`
- 私有账号配置：`config/settings.local.yaml`
- 环境变量模板：`.env.example`

## 部署文档

完整说明见：

- `docs/deploy.md`
