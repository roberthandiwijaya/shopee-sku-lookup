# Production Deployment Guide

Deploy the Shopee SKU Lookup app on a VPS, accessible via IP address.

Supported distros:
- [Ubuntu 22.04 / 24.04](#2a-server-setup-ubuntu)
- [OpenCloudOS / RHEL-based](#2b-server-setup-opencloudos--rhel-based)

---

## 1. Prerequisites

- VPS with a supported Linux distro (minimum 1 vCPU, 1 GB RAM)
- SSH access as root or a sudo user

## 2a. Server Setup (Ubuntu)

### Update system packages

```bash
sudo apt update && sudo apt upgrade -y
```

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

# Verify
docker --version
docker compose version
```

### Allow your user to run Docker without sudo (optional)

```bash
sudo usermod -aG docker $USER
# Log out and back in for this to take effect
```

### Open firewall port

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8000
sudo ufw enable
```

> **Next:** Skip to [Section 3 — Deploy the App](#3-deploy-the-app).

## 2b. Server Setup (OpenCloudOS / RHEL-based)

These instructions also work for CentOS Stream, Rocky Linux, AlmaLinux, and other RHEL-based distros.

### Update system packages

```bash
sudo dnf update -y
```

### Install Docker + Docker Compose plugin

```bash
# Install yum-utils (provides yum-config-manager)
sudo dnf install -y yum-utils

# Add Docker's official repository (uses the CentOS repo, compatible with RHEL-based distros)
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

# Install Docker
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Start and enable Docker
sudo systemctl enable --now docker

# Verify
docker --version
docker compose version
```

### Allow your user to run Docker without sudo (optional)

```bash
sudo usermod -aG docker $USER
# Log out and back in for this to take effect
```

### Open firewall port

```bash
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --reload
```

## 3. Deploy the App

### Clone the repository

```bash
cd /opt
sudo git clone https://github.com/roberthandiwijaya/shopee-sku-lookup.git shopee
sudo chown -R $USER:$USER /opt/shopee
cd /opt/shopee
```

### Create `.env` from the example

```bash
cp .env.example .env
nano .env
```

Fill in the following values:

```dotenv
# PostgreSQL — no change needed, the Docker internal URL is set in docker-compose.yml
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/shopee_products

# Shopee API — switch to production URL
SHOPEE_BASE_URL=https://partner.shopeemobile.com

# Shopee credentials — fill in your real values
SHOPEE_PARTNER_ID=123456
SHOPEE_PARTNER_KEY=your_real_partner_key
SHOPEE_SHOP_ID=your_real_shop_id

# Redirect URL — replace <VPS_IP> with your server's public IP
SHOPEE_REDIRECT_URL=http://<VPS_IP>:8000/api/auth/callback

# API key — generate a secure random value
# Run: openssl rand -hex 32
API_KEY=<paste_generated_key>

# Session secret — generate a secure random value
# Run: openssl rand -hex 32
SESSION_SECRET_KEY=<paste_generated_key>

# Sync interval (minutes)
SYNC_INTERVAL_MINUTES=60
```

> **Tip:** Generate secure keys with:
> ```bash
> openssl rand -hex 32
> ```

## 4. Production Tweaks to `docker-compose.yml`

Edit `docker-compose.yml` to make three changes:

```bash
nano docker-compose.yml
```

### a) Remove the pgAdmin service

Delete the entire `pgadmin` block (saves memory on a small VPS):

```yaml
  # DELETE this entire block:
  pgadmin:
    image: dpage/pgadmin4
    container_name: pgadmin
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@admin.com
      PGADMIN_DEFAULT_PASSWORD: admin
    ports:
      - "5050:80"
    depends_on:
      - postgres
```

### b) Lock down the PostgreSQL port

Change the `postgres` port mapping so it's only accessible from the host, not from the internet:

```yaml
    ports:
      - "127.0.0.1:5432:5432"
```

Or remove the `ports` section entirely from the `postgres` service — the app container connects via the Docker network, so it doesn't need a host port.

### c) Add restart policies

Add `restart: unless-stopped` to both the `app` and `postgres` services so they come back after a reboot:

```yaml
  postgres:
    image: postgres:16
    container_name: shopee-pg
    restart: unless-stopped
    # ... rest of config

  app:
    build: .
    container_name: shopee-app
    restart: unless-stopped
    # ... rest of config
```

### Final `docker-compose.yml` (for reference)

```yaml
services:
  postgres:
    image: postgres:16
    container_name: shopee-pg
    restart: unless-stopped
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: shopee_products
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build: .
    container_name: shopee-app
    restart: unless-stopped
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/shopee_products
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  pgdata:
```

## 5. Start the App

```bash
cd /opt/shopee
docker compose up -d --build
```

Verify everything is running:

```bash
docker compose ps
docker compose logs app
```

You should see the app listening on port 8000.

## 6. Access

- **Dashboard:** `http://<VPS_IP>:8000`
- **Login:** username `admin`, password `admin` — **change the password immediately**
- **API:**
  ```bash
  curl -H "X-API-Key: YOUR_API_KEY" http://<VPS_IP>:8000/api/products?sku=EXAMPLE
  ```

## 7. Maintenance

### View logs

```bash
docker compose logs -f app
```

### Restart the app

```bash
docker compose restart app
```

### Update to latest code

```bash
cd /opt/shopee
git pull
docker compose up -d --build
```

### Backup the database

```bash
docker exec shopee-pg pg_dump -U postgres shopee_products > backup_$(date +%Y%m%d).sql
```

### Restore from backup

```bash
cat backup_20260219.sql | docker exec -i shopee-pg psql -U postgres shopee_products
```
