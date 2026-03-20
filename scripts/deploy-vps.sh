#!/usr/bin/env bash
set -euo pipefail

WITH_HTTPS="false"
FORCE_RECREATE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --https)
      WITH_HTTPS="true"
      shift
      ;;
    --force-recreate)
      FORCE_RECREATE="true"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_PATH="$REPO_ROOT/.env"
COMPOSE_FILE="$REPO_ROOT/docker-compose.deploy.yml"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "deploy-vps.sh is intended for Linux VPS only." >&2
  exit 1
fi

if command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  SUDO=""
fi

step() {
  echo
  echo "==> $1"
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

read_env_value() {
  local key="$1"
  if [[ ! -f "$ENV_PATH" ]]; then
    return 0
  fi
  grep -E "^${key}=" "$ENV_PATH" | head -n 1 | cut -d '=' -f 2-
}

install_base_packages() {
  step "Installing base packages"
  $SUDO apt-get update
  $SUDO apt-get install -y curl git nginx
}

install_docker() {
  step "Installing Docker"
  curl -fsSL https://get.docker.com | sh
  if has_cmd systemctl; then
    $SUDO systemctl enable docker --now || true
  fi
}

install_certbot() {
  step "Installing certbot"
  $SUDO apt-get update
  $SUDO apt-get install -y certbot python3-certbot-nginx
}

write_nginx_config() {
  local domain="$1"
  local target="/etc/nginx/sites-available/xiaohongshu-auto-publisher.conf"
  $SUDO tee "$target" >/dev/null <<EOF
server {
    listen 80;
    server_name ${domain};

    client_max_body_size 25m;
    proxy_connect_timeout 30s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;

    location = /healthz {
        proxy_pass http://127.0.0.1:8787/healthz;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /media/ {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        expires 1h;
    }
}
EOF

  $SUDO ln -sf "$target" /etc/nginx/sites-enabled/xiaohongshu-auto-publisher.conf
  if [[ -f /etc/nginx/sites-enabled/default ]]; then
    $SUDO rm -f /etc/nginx/sites-enabled/default
  fi
  $SUDO nginx -t
  if has_cmd systemctl; then
    $SUDO systemctl enable nginx --now || true
    $SUDO systemctl reload nginx
  else
    $SUDO service nginx reload || true
  fi
}

if ! has_cmd git || ! has_cmd curl || ! has_cmd nginx; then
  install_base_packages
fi

if ! has_cmd docker; then
  install_docker
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "Missing compose file: $COMPOSE_FILE" >&2
  exit 1
fi

if [[ ! -f "$ENV_PATH" ]]; then
  bash "$REPO_ROOT/scripts/setup-env-vps.sh" "$ENV_PATH"
fi

for key in APP_DOMAIN DASHSCOPE_API_KEY BAIDU_OCR_API_KEY BAIDU_OCR_SECRET_KEY XHS_MCP_BASE_URL; do
  value="$(read_env_value "$key")"
  if [[ -z "$value" ]]; then
    echo "Missing required .env value: $key" >&2
    exit 1
  fi
done

APP_DOMAIN="$(read_env_value APP_DOMAIN)"
ENABLE_HTTPS="$(read_env_value ENABLE_HTTPS)"
SSL_EMAIL="$(read_env_value SSL_EMAIL)"
XHS_MCP_BASE_URL="$(read_env_value XHS_MCP_BASE_URL)"

step "Pulling latest image"
docker compose -f "$COMPOSE_FILE" pull

step "Starting application container"
if [[ "$FORCE_RECREATE" == "true" ]]; then
  docker compose -f "$COMPOSE_FILE" up -d --force-recreate app
else
  docker compose -f "$COMPOSE_FILE" up -d app
fi

step "Configuring Nginx"
write_nginx_config "$APP_DOMAIN"

if [[ "$WITH_HTTPS" == "true" || "$ENABLE_HTTPS" == "true" ]]; then
  if [[ -z "$SSL_EMAIL" ]]; then
    echo "SSL_EMAIL is required for HTTPS." >&2
    exit 1
  fi
  if ! has_cmd certbot; then
    install_certbot
  fi
  step "Requesting HTTPS certificate"
  $SUDO certbot --nginx -d "$APP_DOMAIN" -m "$SSL_EMAIL" --agree-tos --non-interactive --redirect
fi

step "Checking local health"
sleep 4
curl -fsS http://127.0.0.1:8787/healthz || true

echo
echo "VPS deployment completed."
echo "Domain: https://${APP_DOMAIN}"
echo "Note: real Xiaohongshu publishing still requires a reachable logged-in MCP endpoint at: ${XHS_MCP_BASE_URL}"
