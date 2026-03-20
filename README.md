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

## 敏感配置

为了便于推 GitHub：

- 公开配置放在 [settings.yaml](/c:/Users/Administrator/Desktop/xiaohongshu/config/settings.yaml)
- 私有账号配置放在 [settings.local.yaml](/c:/Users/Administrator/Desktop/xiaohongshu/config/settings.local.yaml)
- 本地变量模板在 [.env.example](/c:/Users/Administrator/Desktop/xiaohongshu/.env.example)

`settings.local.yaml` 和 `.env` 已被 `.gitignore` 忽略。

## 部署文档

完整部署说明见：

- [deploy.md](/c:/Users/Administrator/Desktop/xiaohongshu/docs/deploy.md)
