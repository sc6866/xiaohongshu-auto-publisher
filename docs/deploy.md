# 部署说明

## 推荐路线

根据你的环境，推荐两条路线：

1. 有域名 + 有 Linux VPS：
   `GitHub -> GHCR -> Linux VPS -> Docker -> Nginx -> 域名`
2. 没有公网 IP：
   `GitHub -> GHCR -> Docker -> Cloudflare Tunnel`

如果你已经有域名和 VPS，优先使用第 1 条，不需要把外网访问继续建立在 Tunnel 上。

## 一、Linux VPS 一键部署

### 1. 前提

- 域名已经解析到 VPS 公网 IP
- VPS 为 Debian/Ubuntu 系 Linux
- 你有 sudo 权限
- 你知道要填写的 `XHS_MCP_BASE_URL`

### 2. 一条命令启动

```bash
curl -fsSL https://raw.githubusercontent.com/sc6866/xiaohongshu-auto-publisher/main/scripts/bootstrap-vps.sh | bash
```

### 3. 这个脚本会做什么

仓库中的 VPS 脚本：

- `scripts/bootstrap-vps.sh`
- `scripts/deploy-vps.sh`
- `scripts/setup-env-vps.sh`

它们会自动完成：

- 克隆或更新仓库到 `/opt/xiaohongshu-auto-publisher`
- 交互式生成 `.env`
- 安装 Docker
- 安装 Nginx
- 拉取 `ghcr.io/sc6866/xiaohongshu-auto-publisher:latest`
- 启动应用容器
- 生成 Nginx 反代配置
- 可选申请 Let's Encrypt HTTPS 证书

### 4. 脚本会询问哪些内容

- 域名 `APP_DOMAIN`
- 是否启用 HTTPS
- `SSL_EMAIL`
- `XHS_WEB_PUBLIC_BASE_URL`
- `XHS_MCP_BASE_URL`
- 可选的 `XHS_PUBLISHER_USER_ID`
- 可选的 `XHS_PUBLISHER_XSEC_TOKEN`
- `DASHSCOPE_API_KEY`
- `BAIDU_OCR_API_KEY`
- `BAIDU_OCR_SECRET_KEY`

### 5. 真实发布的重要限制

这一点必须说清楚：

- 当前真实发布依赖“已登录的小红书 MCP”
- 你现在的 MCP 是 Windows 可执行程序
- 如果你的 VPS 是 Linux，它不能天然继承你本地 Windows 的登录态

所以部署到 VPS 以后：

- Web 控制台、内容生成、审核、封面生成可以正常跑
- 真实发布是否可用，取决于 VPS 能不能访问一个已登录的 MCP 服务地址

也就是说，`.env` 中的：

```env
XHS_MCP_BASE_URL=...
```

必须填成 VPS 真实可访问到的 MCP 地址。否则真实发布会失败。

## 二、Windows 一键部署

如果目标机器是 Windows，可以直接使用：

```powershell
git clone https://github.com/sc6866/xiaohongshu-auto-publisher.git $env:USERPROFILE\deploy\xiaohongshu-auto-publisher; cd $env:USERPROFILE\deploy\xiaohongshu-auto-publisher; powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1 -WithTunnel
```

相关脚本：

- `scripts/bootstrap-deploy.ps1`
- `scripts/deploy.ps1`
- `scripts/setup-env.ps1`

## 三、环境变量模板

环境变量模板见：

- `.env.example`

里面已经区分了：

- 镜像与基础服务
- VPS 域名与 HTTPS
- 外网访问地址
- MCP 地址
- 账号绑定信息
- 通义千问与百度 OCR
- Cloudflare Tunnel

## 四、GHCR 镜像

GitHub Actions 已接通，推送到 `main` 后会自动发布镜像到：

```text
ghcr.io/sc6866/xiaohongshu-auto-publisher:latest
```

## 五、Nginx

项目里保留了 Nginx 示例配置：

- `config/nginx_xhs_web.conf`

但对于 VPS 场景，你不需要手动改它。`deploy-vps.sh` 会自动生成并写入站点配置。

## 六、常用命令

VPS 后续更新部署：

```bash
cd /opt/xiaohongshu-auto-publisher
sudo bash scripts/deploy-vps.sh --https
```

查看应用日志：

```bash
cd /opt/xiaohongshu-auto-publisher
docker compose -f docker-compose.deploy.yml logs -f app
```

查看容器状态：

```bash
cd /opt/xiaohongshu-auto-publisher
docker compose -f docker-compose.deploy.yml ps
```
