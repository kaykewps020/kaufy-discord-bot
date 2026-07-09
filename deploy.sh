#!/bin/bash
# Fast deploy/restart script for Kaufy Discord Bot
# Usage: ./deploy.sh [restart|backup|restore]

set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BOT_DIR"

# Source environment
if [ -f .env ]; then
    source .env
fi

export DISCORD_TOKEN="${DISCORD_TOKEN:-}"
export OWNER_IDS="${OWNER_IDS:-}"
export GUILD_ID="${GUILD_ID:-}"

fast_restore() {
    echo "📂 Fast restoring latest backup..."
    python3 -c "
from bot.services.backup import backup_service
import asyncio
result = asyncio.run(backup_service.fast_recovery())
print(result)
"
}

start_bot() {
    echo "🚀 Starting Kaufy Bot..."
    nohup python3 -m bot.main > bot.log 2>&1 &
    BOT_PID=$!
    echo $BOT_PID > bot.pid
    echo "✅ Bot started (PID: $BOT_PID)"
}

stop_bot() {
    if [ -f bot.pid ]; then
        PID=$(cat bot.pid)
        echo "🛑 Stopping bot (PID: $PID)..."
        kill $PID 2>/dev/null || true
        rm bot.pid
        echo "✅ Bot stopped"
    else
        echo "ℹ️  No bot running"
    fi
}

case "${1:-restart}" in
    restart)
        echo "🔄 Restarting bot..."
        fast_restore
        stop_bot
        sleep 1
        start_bot
        echo "✅ Restart complete (< 3 minutes)"
        ;;
    backup)
        echo "📦 Creating backup..."
        python3 -c "
from bot.services.backup import backup_service
import asyncio
result = asyncio.run(backup_service.backup_all())
print(result)
"
        ;;
    restore)
        BACKUP_NAME="${2:-latest}"
        echo "📂 Restoring from $BACKUP_NAME..."
        python3 -c "
from bot.services.backup import backup_service
import asyncio
result = asyncio.run(backup_service.restore_latest() if '$BACKUP_NAME' == 'latest' else backup_service.restore_all('$BACKUP_NAME'))
print(result)
"
        ;;
    start)
        start_bot
        ;;
    stop)
        stop_bot
        ;;
    status)
        if [ -f bot.pid ]; then
            PID=$(cat bot.pid)
            if kill -0 $PID 2>/dev/null; then
                echo "✅ Bot running (PID: $PID)"
                python3 -c "
from bot.utils.helpers import format_uptime
import time, os
if os.path.exists('bot.pid'):
    print(f'Check bot.log for details')
"
            else
                echo "❌ Bot not running (stale PID)"
                rm bot.pid
            fi
        else
            echo "❌ Bot not running"
        fi
        ;;
    *)
        echo "Usage: ./deploy.sh [restart|backup|restore|start|stop|status]"
        exit 1
        ;;
esac
