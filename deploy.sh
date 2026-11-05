#!/bin/bash
# Deploy script for Shopee SKU Lookup
# Usage: ./deploy.sh [sandbox|production] [commit-hash]

set -e

ENVIRONMENT=$1
COMMIT=${2:-HEAD}

if [ -z "$ENVIRONMENT" ]; then
    echo "Usage: ./deploy.sh [sandbox|production] [commit-hash]"
    echo "Examples:"
    echo "  ./deploy.sh sandbox              # Deploy HEAD to sandbox"
    echo "  ./deploy.sh production           # Deploy HEAD to production"
    echo "  ./deploy.sh sandbox abc123       # Deploy commit abc123 to sandbox"
    echo "  ./deploy.sh production abc123    # Deploy commit abc123 to production"
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
        echo "⚠️  Backup skipped (database may be empty)"
else
    echo "⚠️  Backup skipped (container not running)"
fi

echo ""

# Pull latest code
echo "📥 Fetching code..."
git fetch origin

echo "📋 Checking out commit: $COMMIT"
git checkout "$COMMIT"

# Rebuild and restart
echo "🔨 Building and starting containers..."
docker compose up -d --build

# Wait for health check
echo "⏳ Waiting for health check (10 seconds)..."
sleep 10

# Verify deployment
echo ""
echo "🔍 Verifying deployment..."
if curl -s "http://localhost:$PORT/api/sync/status" > /dev/null 2>&1; then
    echo ""
    echo "✅ $NAME deployment successful!"
    echo ""
    echo "🌐 Access URLs:"
    echo "   Dashboard: http://124.156.195.207:$PORT"
    echo "   API Docs:  http://124.156.195.207:$PORT/docs"
    echo ""
    echo "📊 Status:"
    curl -s "http://localhost:$PORT/api/sync/status" | head -c 200
    echo ""
else
    echo ""
    echo "❌ $NAME deployment may have issues."
    echo "📋 Check logs: docker compose logs -f"
    exit 1
fi
