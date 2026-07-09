#!/bin/bash
# Kaufy Discord Bot - Start/Stop Script
# Usage: ./start.sh          (start in background with auto-restart)
#        ./start.sh stop     (stop gracefully)
#        ./start.sh logs     (tail logs)
#        ./start.sh once     (run once, no restart loop)

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$BOT_DIR/bot.pid"
LOG_FILE="$BOT_DIR/bot_output.log"

start_bot() {
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Bot is already running (PID $pid)."
            echo "Use './start.sh stop' first."
            exit 1
        fi
        rm -f "$PID_FILE"
    fi

    # Acquire wake lock to prevent Android from killing Termux
    termux-wake-lock 2>/dev/null && echo "✅ Wake lock acquired"

    echo "Starting Kaufy Discord Bot (auto-restart loop)..."
    cd "$BOT_DIR" || exit 1
    mkdir -p data/user_dbs data/backups

    # Loop: restart on crash, respects self-restart via os.execv
    # .shutdown flag: touch .shutdown_flag to stop the loop
    nohup bash -c '
        while true; do
            # Check for graceful shutdown flag
            if [ -f "'"$BOT_DIR"'"'.shutdown_flag' ]; then
                rm -f "'"$BOT_DIR"'"'.shutdown_flag'
                echo "[$(date "+%Y-%m-%d %H:%M:%S")] Shutdown flag detected. Exiting."
                break
            fi
            echo "[$(date "+%Y-%m-%d %H:%M:%S")] Starting bot process..."
            python3 -m bot.main &
            BOT_PID=$!
            # Wait and check for shutdown flag every second
            while kill -0 "$BOT_PID" 2>/dev/null; do
                if [ -f "'"$BOT_DIR"'"'.shutdown_flag' ]; then
                    kill "$BOT_PID" 2>/dev/null
                    rm -f "'"$BOT_DIR"'"'.shutdown_flag'
                    echo "[$(date "+%Y-%m-%d %H:%M:%S")] Shutdown flag detected. Stopping bot."
                    exit 0
                fi
                sleep 1
            done
            wait "$BOT_PID" 2>/dev/null
            exit_code=$?
            echo "[$(date "+%Y-%m-%d %H:%M:%S")] Bot exited (code=$exit_code). Restarting in 5s..."
            sleep 5
        done
    ' >> "$LOG_FILE" 2>&1 &
    pid=$!
    echo "$pid" > "$PID_FILE"

    sleep 3
    if kill -0 "$pid" 2>/dev/null; then
        echo "✅ Bot running (PID $pid, auto-restart on crash)"
        echo "   Logs: tail -f $LOG_FILE"
    else
        echo "❌ Bot failed to start:"
        tail -10 "$LOG_FILE"
        rm -f "$PID_FILE"
    fi
}

stop_bot() {
    # Create shutdown flag so the loop stops
    echo "shutdown" > "$BOT_DIR/.shutdown_flag"
    
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        echo "Stopping bot (PID $pid)..."
        kill "$pid" 2>/dev/null
        sleep 2
        rm -f "$PID_FILE"
    fi
    
    # Kill any remaining bot processes (max 5s wait)
    for i in 1 2 3 4 5; do
        pids=$(pgrep -f "bot.main" 2>/dev/null)
        if [ -z "$pids" ]; then
            break
        fi
        for p in $pids; do
            kill "$p" 2>/dev/null
        done
        sleep 1
    done
    
    echo "✅ Bot stopped."
}

case "${1:-start}" in
    start)
        start_bot
        ;;
    stop)
        stop_bot
        ;;
    restart)
        stop_bot
        sleep 2
        start_bot
        ;;
    logs)
        tail -f "$LOG_FILE"
        ;;
    once)
        cd "$BOT_DIR" && python3 -m bot.main
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|logs|once}"
        exit 1
        ;;
esac
