# Shopee SKU Lookup - Dev/Sandbox/Prod Workflow Guide

## 🏗️ Environment Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         YOUR VPS                                  │
│  ┌──────────────────┐  ┌──────────────────┐                     │
│  │    SANDBOX       │  │   PRODUCTION     │                     │
│  │    (Port 8001)   │  │   (Port 8000)    │                     │
│  │                  │  │                  │                     │
│  │  • Test changes  │  │  • Live system   │                     │
│  │  • Sandbox API   │  │  • Real API      │                     │
│  │  • Safe to break │  │  • Customer data │                     │
│  └──────────────────┘  └──────────────────┘                     │
│           │                       │                              │
│           └───────────┬───────────┘                              │
│                       │                                          │
│            ┌──────────▼──────────┐                               │
│            │   GitHub Repo       │                               │
│            │   (Source of Truth) │                               │
│            └─────────────────────┘                               │
└─────────────────────────────────────────────────────────────────┘
```

## 📁 Directory Structure

```
/opt/
├── shopee/                    # PRODUCTION (Port 8000)
│   ├── .env                   # Production credentials
│   ├── docker-compose.yml
│   └── ...
│
└── shopee-sandbox/            # SANDBOX (Port 8001)
    ├── .env                   # Sandbox credentials (API keys, sandbox URLs)
    ├── docker-compose.yml     # Runs on port 8001
    ├── deploy.sh              # Deployment helper script
    └── ...
```

## 🔄 Development Workflow

### Step 1: Develop & Test in Sandbox

```bash
# 1. SSH to your VPS
ssh root@124.156.195.207

# 2. Go to sandbox directory
cd /opt/shopee-sandbox

# 3. Pull latest changes (or specific commit)
git pull origin main
# OR
git checkout <commit-hash>

# 4. Deploy to sandbox
./deploy.sh sandbox

# 5. Test at:
# http://124.156.195.207:8001
```

### Step 2: Verify in Sandbox

✅ Checklist before promoting to production:
- [ ] Feature works as expected
- [ ] No console errors
- [ ] API calls successful
- [ ] Database updates correctly
- [ ] No breaking changes

### Step 3: Deploy to Production

```bash
# Only AFTER sandbox testing passes
cd /opt/shopee

# Backup first (automatic in deploy.sh)
./deploy.sh production <commit-hash>

# Verify at:
# http://124.156.195.207:8000
```

## 🔧 Setup Instructions

### 1. Create Sandbox Environment

```bash
# Run these commands on your VPS:

# Create sandbox directory
mkdir -p /opt/shopee-sandbox/backups

# Clone repo
git clone https://github.com/roberthandiwijaya/shopee-sku-lookup.git /opt/shopee-sandbox
cd /opt/shopee-sandbox

# Create sandbox .env file
cat > .env << 'EOF'
# PostgreSQL
DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/shopee_sandbox

# Shopee API - SANDBOX CREDENTIALS
SHOPEE_PARTNER_ID=1175052
SHOPEE_PARTNER_KEY=your_sandbox_partner_key_here
SHOPEE_SHOP_ID=225553049
SHOPEE_BASE_URL=https://openplatform.sandbox.test-stable.shopee.sg

# API Key
API_KEY=sandbox_api_key_change_me

# Session Secret
SESSION_SECRET_KEY=sandbox_secret_change_me

# App
SHOPEE_REDIRECT_URL=http://124.156.195.207:8001/api/auth/callback
SYNC_INTERVAL_MINUTES=60
EOF

# Create docker-compose.yml for sandbox (port 8001)
cat > docker-compose.yml << 'EOF'
services:
  postgres:
    image: postgres:16
    container_name: shopee-sandbox-pg
    restart: unless-stopped
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: shopee_sandbox
    volumes:
      - sandbox_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build: .
    container_name: shopee-sandbox-app
    restart: unless-stopped
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/shopee_sandbox
    ports:
      - "8001:8000"
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  sandbox_pgdata:
EOF

# Start sandbox
docker compose up -d --build
```

### 2. Create Deploy Script

```bash
cat > /opt/shopee/deploy.sh << 'SCRIPT'
#!/bin/bash
set -e

ENVIRONMENT=$1
COMMIT=${2:-HEAD}

if [ "$ENVIRONMENT" == "sandbox" ]; then
    DIR="/opt/shopee-sandbox"
    PORT="8001"
    NAME="Sandbox"
elif [ "$ENVIRONMENT" == "production" ]; then
    DIR="/opt/shopee"
    PORT="8000"
    NAME="Production"
else
    echo "Usage: ./deploy.sh [sandbox|production] [commit-hash]"
    exit 1
fi

echo "🚀 Deploying to $NAME..."
cd "$DIR"

# Backup
echo "💾 Backing up database..."
docker exec shopee-${ENVIRONMENT}-pg pg_dump -U postgres shopee_${ENVIRONMENT} > \
    backups/backup_$(date +%Y%m%d_%H%M%S).sql 2>/dev/null || true

# Deploy
echo "📥 Pulling code..."
git fetch origin
git checkout "$COMMIT"

echo "🔨 Building..."
docker compose up -d --build

# Verify
sleep 5
if curl -s "http://localhost:$PORT/api/sync/status" > /dev/null; then
    echo "✅ $NAME deployment successful!"
    echo "🌐 http://124.156.195.207:$PORT"
else
    echo "❌ Deployment failed. Check logs: docker compose logs"
    exit 1
fi
SCRIPT

chmod +x /opt/shopee/deploy.sh
chmod +x /opt/shopee-sandbox/deploy.sh
```

## 📝 Typical Development Flow

```
1. DEVELOP
   └─ Make code changes locally or on sandbox

2. COMMIT & PUSH
   └─ git add . && git commit -m "feat: new feature"
   └─ git push origin main

3. TEST IN SANDBOX
   └─ ./deploy.sh sandbox
   └─ Test thoroughly at http://124.156.195.207:8001

4. (Optional) Get specific commit
   └─ git log --oneline -5
   └─ ./deploy.sh sandbox abc123

5. DEPLOY TO PRODUCTION
   └─ ./deploy.sh production abc123
   └─ Verify at http://124.156.195.207:8000
```

## 🛡️ Safety Features

✅ **Automatic Backups** - Database backed up before every deployment
✅ **Separate Databases** - Sandbox and Production have isolated PostgreSQL databases
✅ **Different Ports** - 8000 (Prod) vs 8001 (Sandbox) - no conflicts
✅ **Different API Keys** - Sandbox uses test credentials
✅ **Rollback Ready** - Use backed up SQL files to restore if needed

## 🔗 Quick Reference

| Environment | URL | Purpose |
|-------------|-----|---------|
| Production | http://124.156.195.207:8000 | Live system |
| Sandbox | http://124.156.195.207:8001 | Testing |

| Command | Purpose |
|---------|---------|
| `./deploy.sh sandbox` | Deploy HEAD to sandbox |
| `./deploy.sh sandbox abc123` | Deploy specific commit to sandbox |
| `./deploy.sh production abc123` | Deploy to production |
| `docker compose logs -f` | View logs |

## ⚠️ Important Notes

1. **Sandbox uses different Shopee credentials** - Don't mix them up!
2. **Always test in sandbox first** - Before touching production
3. **Production has real data** - Be extra careful
4. **Backups are automatic** - But verify they work

## 🆘 Emergency Rollback

If production breaks:
```bash
cd /opt/shopee

# Find latest backup
ls -t backups/ | head -1

# Restore database
docker exec -i shopee-pg psql -U postgres shopee_products < backups/backup_xxx.sql

# Or rollback to previous commit
git log --oneline -5
git checkout <previous-commit>
docker compose up -d --build
```
