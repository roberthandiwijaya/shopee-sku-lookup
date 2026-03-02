# Production Deployment Guide

Deploy the Shopee SKU Lookup app with **both Sandbox and Production environments** on a VPS.

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        YOUR VPS                              │
│                                                              │
│   ┌─────────────────────┐    ┌─────────────────────┐        │
│   │     SANDBOX         │    │    PRODUCTION       │        │
│   │    Port 8001        │    │    Port 8000        │        │
│   │                     │    │                     │        │
│   │  • Test changes     │    │  • Live system      │        │
│   │  • Sandbox API      │    │  • Real API         │        │
│   │  • Safe to break    │    │  • Customer data    │        │
│   └─────────────────────┘    └─────────────────────┘        │
│                                                              │
│   Separate PostgreSQL databases for each environment        │
└─────────────────────────────────────────────────────────────┘
```

## 📋 Prerequisites

- VPS with a supported Linux distro (minimum 2 vCPU, 2 GB RAM)
- SSH access as root or a sudo user
- Domain or static IP (for Shopee OAuth callback)

Supported distros:
- [Ubuntu 22.04 / 24.04](#2a-server-setup-ubuntu)
- [OpenCloudOS / RHEL-based](#2b-server-setup-opencloudos--rhel-based)

---

## 1. System Setup

### Update system packages

**Ubuntu:**
```bash
sudo apt update && sudo apt upgrade -y
```

**OpenCloudOS / RHEL:**
```bash
sudo dnf update -y
```

---

## 2a. Server Setup (Ubuntu)

### Install Docker + Docker Compose plugin

```bash
# Install dependencies
sudo apt install -y ca-certificates curl gnupg

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add the Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Start and enable Docker
sudo systemctl enable --now docker

# Verify
docker --version
docker compose version
```

### Open firewall ports

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp   # Production
sudo ufw allow 8001/tcp   # Sandbox
sudo ufw enable
```

---

## 2b. Server Setup (OpenCloudOS / RHEL-based)

### Install Docker + Docker Compose plugin

```bash
# Install dnf-plugins-core
sudo dnf install -y dnf-plugins-core

# Add Docker's official repository
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

# Install Docker
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Start and enable Docker
sudo systemctl enable --now docker

# Verify
docker --version
docker compose version
```

### Open firewall ports

```bash
sudo firewall-cmd --permanent --add-port=8000/tcp   # Production
sudo firewall-cmd --permanent --add-port=8001/tcp   # Sandbox
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --reload
```

---

## 3. Deploy Both Environments

### Option A: Automated Setup (Recommended)

The repository includes a setup script that creates both environments:

```bash
# Clone the repository
cd /opt
sudo git clone https://github.com/roberthandiwijaya/shopee-sku-lookup.git shopee
sudo chown -R $USER:$USER /opt/shopee
cd /opt/shopee

# Create sandbox directory structure
sudo mkdir -p /opt/shopee-sandbox
sudo chown -R $USER:$USER /opt/shopee-sandbox
```

### Option B: Manual Setup

#### 3.1 Production Environment

```bash
cd /opt/shopee

# Create production .env
cp .env.example .env
nano .env
```

**Production `.env`:**
```dotenv
# PostgreSQL
DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/shopee_products

# Shopee API - PRODUCTION
SHOPEE_BASE_URL=https://partner.shopeemobile.com
SHOPEE_PARTNER_ID=YOUR_PROD_PARTNER_ID
SHOPEE_PARTNER_KEY=YOUR_PROD_PARTNER_KEY
SHOPEE_SHOP_ID=YOUR_PROD_SHOP_ID

# API Key (generate: openssl rand -hex 32)
API_KEY=your_secure_api_key_here

# Session Secret (generate: openssl rand -hex 32)
SESSION_SECRET_KEY=your_secure_session_secret_here

# Redirect URL
SHOPEE_REDIRECT_URL=http://YOUR_VPS_IP:8000/api/auth/callback

# Sync interval (minutes)
SYNC_INTERVAL_MINUTES=60
```

#### 3.2 Sandbox Environment

```bash
cd /opt/shopee-sandbox

# Clone/copy code from production
git clone https://github.com/roberthandiwijaya/shopee-sku-lookup.git .

# Create sandbox .env
cp .env.example .env
nano .env
```

**Sandbox `.env`:**
```dotenv
# PostgreSQL
DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/shopee_sandbox

# Shopee API - SANDBOX
SHOPEE_BASE_URL=https://openplatform.sandbox.test-stable.shopee.sg
SHOPEE_PARTNER_ID=YOUR_SANDBOX_PARTNER_ID
SHOPEE_PARTNER_KEY=YOUR_SANDBOX_PARTNER_KEY
SHOPEE_SHOP_ID=YOUR_SANDBOX_SHOP_ID

# API Key (different from production)
API_KEY=sandbox_api_key_here

# Session Secret (different from production)
SESSION_SECRET_KEY=sandbox_session_secret_here

# Redirect URL
SHOPEE_REDIRECT_URL=http://YOUR_VPS_IP:8001/api/auth/callback

# Sync interval (minutes)
SYNC_INTERVAL_MINUTES=60
```

#### 3.3 Create Sandbox docker-compose.yml

```bash
cd /opt/shopee-sandbox
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
```

---

## 4. Deploy Script (Optional but Recommended)

Create a `deploy.sh` script for easy deployment:

```bash
cat > /opt/shopee/deploy.sh << 'SCRIPT'
#!/bin/bash
# Deploy script for Shopee SKU Lookup
# Usage: ./deploy.sh [sandbox|production] [commit-hash]

set -e

ENVIRONMENT=$1
COMMIT=${2:-HEAD}

if [ -z "$ENVIRONMENT" ]; then
    echo "Usage: ./deploy.sh [sandbox|production] [commit-hash]"
    echo "Examples:"
    echo "  ./deploy.sh sandbox           # Deploy HEAD to sandbox"
    echo "  ./deploy.sh production        # Deploy HEAD to production"
    echo "  ./deploy.sh sandbox abc123    # Deploy specific commit"
    exit 1
fi

if [ "$ENVIRONMENT" == "sandbox" ]; then
    DIR="/opt/shopee-sandbox"
    PORT="8001"
    NAME="Sandbox"
    DB_NAME="shopee_sandbox"
    PG_CONTAINER="shopee-sandbox-pg"
elif [ "$ENVIRONMENT" == "production" ]; then
    DIR="/opt/shopee"
    PORT="8000"
    NAME="Production"
    DB_NAME="shopee_products"
    PG_CONTAINER="shopee-pg"
else
    echo "❌ Invalid environment. Use 'sandbox' or 'production'"
    exit 1
fi

echo "🚀 Deploying to $NAME..."
echo "📍 Directory: $DIR"
echo "🌐 Port: $PORT"
echo "📦 Commit: $COMMIT"
echo ""

cd "$DIR"

# Backup database before deployment
echo "💾 Creating database backup..."
mkdir -p backups
BACKUP_FILE="backups/backup_$(date +%Y%m%d_%H%M%S)_pre_deploy.sql"

if docker ps | grep -q "$PG_CONTAINER"; then
    docker exec "$PG_CONTAINER" pg_dump -U postgres "$DB_NAME" > "$BACKUP_FILE" 2>/dev/null && \
        echo "✅ Backup saved: $BACKUP_FILE" || \
        echo "⚠️  Backup skipped"
else
    echo "⚠️  Backup skipped (container not running)"
fi

echo ""

# Pull latest code
echo "📥 Fetching code..."
git fetch origin
git checkout "$COMMIT"

# Rebuild and restart
echo "🔨 Building and starting containers..."
docker compose up -d --build

# Wait for health check
echo "⏳ Waiting for health check (10s)..."
sleep 10

# Verify deployment
echo ""
echo "🔍 Verifying deployment..."
if curl -s "http://localhost:$PORT/api/sync/status" > /dev/null 2>&1; then
    echo ""
    echo "✅ $NAME deployment successful!"
    echo "🌐 http://YOUR_VPS_IP:$PORT"
else
    echo ""
    echo "❌ Deployment may have issues. Check logs: docker compose logs"
    exit 1
fi
SCRIPT

chmod +x /opt/shopee/deploy.sh
cp /opt/shopee/deploy.sh /opt/shopee-sandbox/deploy.sh
```

---

## 5. Start Both Environments

```bash
# Start production
cd /opt/shopee
docker compose up -d --build

# Start sandbox
cd /opt/shopee-sandbox
docker compose up -d --build
```

Verify both are running:
```bash
docker ps | grep shopee
```

You should see 4 containers:
- `shopee-app` (port 8000)
- `shopee-pg` 
- `shopee-sandbox-app` (port 8001)
- `shopee-sandbox-pg`

---

## 6. Access

| Environment | URL | Purpose |
|-------------|-----|---------|
| **Production** | `http://<VPS_IP>:8000` | Live system |
| **Sandbox** | `http://<VPS_IP>:8001` | Testing environment |

**Login:**
- Username: `admin`
- Password: `admin` (change immediately)

---

## 7. Development Workflow

### Safe Development Process

```
1. DEVELOP & TEST in Sandbox
   └─ Visit http://<VPS_IP>:8001
   └─ Test all changes thoroughly

2. DEPLOY to Production (when ready)
   └─ cd /opt/shopee
   └─ ./deploy.sh production
   └─ Verify at http://<VPS_IP>:8000

3. MONITOR both environments
   └─ Check logs: docker compose logs -f
```

### Using the Deploy Script

```bash
# Deploy latest to sandbox
cd /opt/shopee-sandbox
./deploy.sh sandbox

# Deploy specific commit to production
cd /opt/shopee
./deploy.sh production abc123def

# Deploy latest to production
./deploy.sh production
```

---

## 8. Maintenance

### View Logs

```bash
# Production
cd /opt/shopee && docker compose logs -f app

# Sandbox
cd /opt/shopee-sandbox && docker compose logs -f app
```

### Restart Services

```bash
# Production
cd /opt/shopee && docker compose restart app

# Sandbox
cd /opt/shopee-sandbox && docker compose restart app
```

### Update to Latest Code

```bash
# Update both environments
cd /opt/shopee && git pull && docker compose up -d --build
cd /opt/shopee-sandbox && git pull && docker compose up -d --build
```

### Backup Databases

```bash
# Production backup
docker exec shopee-pg pg_dump -U postgres shopee_products > backup_prod_$(date +%Y%m%d).sql

# Sandbox backup
docker exec shopee-sandbox-pg pg_dump -U postgres shopee_sandbox > backup_sandbox_$(date +%Y%m%d).sql
```

### Restore from Backup

```bash
# Production restore
cat backup_prod_20260301.sql | docker exec -i shopee-pg psql -U postgres shopee_products

# Sandbox restore
cat backup_sandbox_20260301.sql | docker exec -i shopee-sandbox-pg psql -U postgres shopee_sandbox
```

---

## 9. Troubleshooting

### Container Won't Start

```bash
# Check logs
docker compose logs app

# Check for port conflicts
netstat -tlnp | grep 8000
netstat -tlnp | grep 8001
```

### Database Connection Issues

```bash
# Check PostgreSQL health
docker compose ps
docker compose logs postgres
```

### Permission Denied

```bash
# Fix ownership
sudo chown -R $USER:$USER /opt/shopee
sudo chown -R $USER:$USER /opt/shopee-sandbox
```

---

## 🎉 You're Ready!

Your dual-environment setup is complete. Use the **sandbox** for testing and **production** for live operations!
