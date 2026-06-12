# Production Deployment Guide

## Architecture overview

```
                    [Clients]
                       │
               HTTPS (443 / TLS)
                       │
              ┌────────▼────────┐
              │   Reverse Proxy  │  (nginx / Caddy)
              │   TLS termination│
              └────────┬────────┘
                       │  :8000
              ┌────────▼────────┐
              │  FastAPI backend │  (4 workers, uvicorn)
              │  License API     │
              └───┬─────────┬───┘
                  │         │
         ┌────────▼──┐  ┌───▼──────┐
         │ PostgreSQL│  │  Redis   │
         │  (primary)│  │  (cache) │
         └───────────┘  └──────────┘
```

---

## 1. System requirements

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 1 GB | 4 GB |
| Disk | 10 GB | 50 GB |
| OS | Ubuntu 22.04 | Ubuntu 22.04 LTS |
| PostgreSQL | 14 | 16 |
| Redis | 7 | 7 |
| Python | 3.11 | 3.12 |

---

## 2. Generate production secrets

```bash
# Ed25519 private key (sign JWTs + refresh tokens)
python -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
k = Ed25519PrivateKey.generate()
priv = k.private_bytes_raw().hex()
pub  = k.public_key().public_bytes_raw().hex()
print(f'ED25519_PRIVATE_KEY_HEX={priv}')
print(f'# Ed25519 public key (share with clients): {pub}')
"

# AES-256 master key (encrypt refresh tokens at rest)
python -c "import secrets; print('AES_MASTER_KEY_HEX=' + secrets.token_hex(32))"

# Admin secret (protect admin endpoints)
python -c "import secrets; print('ADMIN_SECRET=' + secrets.token_urlsafe(40))"
```

**Store these in a secrets manager** (AWS Secrets Manager, HashiCorp Vault, etc.).  
Never commit them to version control.

---

## 3. Database setup

```sql
-- PostgreSQL
CREATE USER AgentMax WITH PASSWORD 'strongpassword';
CREATE DATABASE AGENTMAX_prod OWNER AgentMax;
GRANT ALL PRIVILEGES ON DATABASE AGENTMAX_prod TO AgentMax;
```

Run migrations:

```bash
DATABASE_URL=postgresql+asyncpg://AgentMax:pass@localhost/AGENTMAX_prod \
  alembic upgrade head
```

---

## 4. Backend environment variables

Create `/etc/AgentMax/backend.env`:

```env
ENVIRONMENT=production
DATABASE_URL=postgresql+asyncpg://AgentMax:strongpassword@localhost:5432/AGENTMAX_prod
REDIS_URL=redis://localhost:6379/0

ED25519_PRIVATE_KEY_HEX=<64 hex chars>
AES_MASTER_KEY_HEX=<64 hex chars>
ADMIN_SECRET=<40+ char secret>

HOST=127.0.0.1
PORT=8000
WORKERS=4

CORS_ORIGINS=["https://your-dashboard-domain.com"]

ACCESS_TOKEN_TTL_SECONDS=3600
REFRESH_TOKEN_TTL_SECONDS=604800
CHALLENGE_TTL_SECONDS=300

RATE_LIMIT_AUTH_PER_MINUTE=10
RATE_LIMIT_API_PER_MINUTE=120
RATE_LIMIT_ADMIN_PER_MINUTE=300
```

Set permissions: `chmod 600 /etc/AgentMax/backend.env`

---

## 5. Systemd service

Create `/etc/systemd/system/AgentMax-backend.service`:

```ini
[Unit]
Description=AgentMax License Backend
After=network.target postgresql.service redis.service
Requires=postgresql.service redis.service

[Service]
Type=exec
User=AgentMax
Group=AgentMax
WorkingDirectory=/opt/AgentMax/backend
EnvironmentFile=/etc/AgentMax/backend.env
ExecStart=/opt/AgentMax/.venv/bin/uvicorn backend.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 4 \
    --log-config=none
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=AgentMax-backend

# Security hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/AgentMax/data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable AgentMax-backend
sudo systemctl start AgentMax-backend
sudo journalctl -u AgentMax-backend -f
```

---

## 6. Nginx reverse proxy

```nginx
server {
    listen 443 ssl http2;
    server_name api.AgentMax.io;

    ssl_certificate     /etc/letsencrypt/live/api.AgentMax.io/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.AgentMax.io/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=auth:10m rate=10r/m;
    location /v1/auth/ {
        limit_req zone=auth burst=5 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 60s;
    }
}

server {
    listen 80;
    server_name api.AgentMax.io;
    return 301 https://$host$request_uri;
}
```

---

## 7. Admin dashboard deployment

```bash
cd dashboard
npm ci
VITE_API_URL=https://api.AgentMax.io \
VITE_ADMIN_SECRET=<your-admin-secret> \
npm run build

# Serve the dist/ folder with nginx or Caddy
```

Or deploy to Vercel / Netlify:
```bash
# Vercel
npx vercel --prod

# Netlify
npx netlify deploy --prod --dir=dist
```

---

## 8. Health monitoring

```bash
# Check the health endpoint
curl https://api.AgentMax.io/health

# Expected response:
# {"status": "ok", "version": "1.0.0"}
```

Set up uptime monitoring (UptimeRobot, Better Uptime, etc.) on `/health`.

---

## 9. Backup strategy

```bash
# PostgreSQL daily backup
pg_dump -U AgentMax AGENTMAX_prod | gzip > backup_$(date +%Y%m%d).sql.gz

# Redis RDB snapshot (configured in redis.conf)
# save 3600 1
# dir /var/lib/redis
# dbfilename dump.rdb
```

---

## 10. Security checklist

- [ ] `ENVIRONMENT=production` set (disables `/docs`, `/redoc`, `/openapi.json`)
- [ ] Admin endpoints behind VPN or IP allowlist (nginx `allow`/`deny`)
- [ ] Secrets stored in secrets manager, not `.env` files on disk
- [ ] TLS 1.2+ enforced, TLS 1.0/1.1 disabled
- [ ] PostgreSQL not exposed to the internet (bind to 127.0.0.1)
- [ ] Redis not exposed to the internet (bind to 127.0.0.1, requirepass set)
- [ ] Systemd service runs as non-root user (`AgentMax`)
- [ ] Log rotation configured (`logrotate`)
- [ ] Automated backups tested and verified
- [ ] Monitoring and alerting on `/health` endpoint
