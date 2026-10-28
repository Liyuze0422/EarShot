#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")"
[[ $EUID == 0 ]] || { echo 'Run: sudo -E bash deploy-relay.sh'; exit 1; }
overrides=()
for name in DOMAIN ADMIN_IP DEEPSEEK_KEY DOUBAO_KEY DOUBAO_MODEL TOKEN_QUOTA SESSION_SECRET MYSQL_ROOT_PASSWORD NEW_ADMIN_PASS; do
  if [[ -v $name ]]; then overrides+=("$name=${!name}"); fi
done
if [[ -f /opt/api/.env ]]; then set -a; source /opt/api/.env; set +a; fi
for setting in "${overrides[@]}"; do export "$setting"; done
: "${DOMAIN:?Set base DOMAIN}"
: "${ADMIN_IP:?Set trusted ADMIN_IP or CIDR}"
: "${DEEPSEEK_KEY:?Set DEEPSEEK_KEY}"
export DOMAIN ADMIN_IP DEEPSEEK_KEY
export NEW_API_IMAGE=calciumion/new-api:v1.0.0-rc.47
export TOKEN_QUOTA=${TOKEN_QUOTA:-1000000}
export DOUBAO_KEY=${DOUBAO_KEY:-}
export DOUBAO_MODEL=${DOUBAO_MODEL:-doubao-1.5-lite-32k-250115}
apt-get update -qq
apt-get install -y -qq ca-certificates curl python3 openssl cron
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com -o /tmp/earshot-get-docker.sh
  sh /tmp/earshot-get-docker.sh
fi
systemctl enable --now docker cron
docker compose version >/dev/null
export SESSION_SECRET=${SESSION_SECRET:-$(openssl rand -hex 32)}
export MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD:-$(openssl rand -hex 24)}
export NEW_ADMIN_PASS=${NEW_ADMIN_PASS:-$(openssl rand -hex 24)}
[[ $NEW_ADMIN_PASS != 123456 ]] || { echo 'Use a non-default NEW_ADMIN_PASS'; exit 1; }
mkdir -p /opt/api/backups /opt/api/logs
python3 - <<'PY'
import os,shlex
keys=['DOMAIN','ADMIN_IP','DEEPSEEK_KEY','DOUBAO_KEY','DOUBAO_MODEL',
      'SESSION_SECRET','MYSQL_ROOT_PASSWORD','NEW_ADMIN_PASS','TOKEN_QUOTA','NEW_API_IMAGE']
with open('/opt/api/.env','w') as f:
 for k in keys:
  v=os.environ[k]
  if '\n' in v or '\r' in v or "'" in v: raise SystemExit('Unsupported newline/quote: '+k)
  f.write(k+'='+shlex.quote(v)+'\n')
 f.write('REDIS_CONN_STRING=redis://redis:6379\nDEBUG=false\nMEMORY_CACHE_ENABLED=true\n')
 f.write('SQL_DSN='+shlex.quote('root:'+os.environ['MYSQL_ROOT_PASSWORD']+'@tcp(mysql:3306)/newapi?charset=utf8mb4&parseTime=True&loc=Local')+'\n')
PY
chmod 600 /opt/api/.env
cat > /opt/api/docker-compose.yml <<'YAML'
services:
  mysql:
    image: mysql:8.0
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: newapi
    volumes: [mysql_data:/var/lib/mysql]
    healthcheck:
      test: [CMD-SHELL, 'MYSQL_PWD="$$MYSQL_ROOT_PASSWORD" mysqladmin ping -h localhost --silent']
      interval: 5s
      timeout: 5s
      retries: 40
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes: [redis_data:/data]
  new-api:
    image: ${NEW_API_IMAGE}
    container_name: new-api
    restart: unless-stopped
    env_file: [.env]
    environment:
      TZ: Asia/Shanghai
      RETRY_TIMES: '3'
    depends_on:
      mysql: {condition: service_healthy}
      redis: {condition: service_started}
    ports: ['127.0.0.1:3000:3000']
    volumes: ['./logs:/app/logs', 'api_data:/data']
    logging:
      driver: json-file
      options: {max-size: 10m, max-file: '7'}
  caddy:
    image: caddy:2
    restart: unless-stopped
    network_mode: host
    volumes: ['./Caddyfile:/etc/caddy/Caddyfile:ro', 'caddy_data:/data', 'caddy_config:/config']
volumes:
  mysql_data:
  redis_data:
  api_data:
  caddy_data:
  caddy_config:
YAML
cat > /opt/api/Caddyfile <<EOF
api.${DOMAIN} {
  route {
    @health {
      path /
      method GET HEAD
    }
    respond @health "EarShot relay" 200
    @v1 path /v1/*
    reverse_proxy @v1 127.0.0.1:3000 {
      flush_interval -1
    }
    respond 404
  }
}
admin.${DOMAIN} {
  route {
    @denied not remote_ip ${ADMIN_IP}
    respond @denied 403
    reverse_proxy 127.0.0.1:3000
  }
}
EOF
docker compose --project-directory /opt/api config --quiet
docker compose --project-directory /opt/api up -d mysql redis new-api
for i in {1..90}; do
  if curl -fsS http://127.0.0.1:3000/api/status >/dev/null; then break; fi
  sleep 2
done
curl -fsS http://127.0.0.1:3000/api/status >/dev/null
python3 bootstrap-relay.py
docker compose --project-directory /opt/api run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile
docker compose --project-directory /opt/api up -d caddy
docker compose --project-directory /opt/api exec -T caddy caddy reload --config /etc/caddy/Caddyfile
cat > /opt/api/backup.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/api
stamp=$(date +%F-%H%M%S)
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --no-tablespaces newapi' | gzip > "backups/mysql-${stamp}.sql.gz"
tar czf "backups/config-${stamp}.tgz" .env docker-compose.yml Caddyfile bootstrap-result.json relay-token.env
SH
chmod 700 /opt/api/backup.sh
cat > /etc/cron.d/earshot-relay <<'CRON'
SHELL=/bin/bash
0 3 * * * root /opt/api/backup.sh >> /opt/api/backup.log 2>&1
0 4 * * * root find /opt/api/logs -type f -name '*.log' -mtime +7 -delete
CRON
chmod 644 /etc/cron.d/earshot-relay
echo "Relay: https://api.${DOMAIN}/v1 ; credentials: /opt/api/relay-token.env (600)"
