#!/data/data/com.termux/files/usr/bin/bash
"""
Kaufy Bot — persistent 24/7 runner for Termux (Android)

Uses:
  - termux-services (runit)  → service management (auto-restart on crash)
  - termux-wake-lock         → keeps CPU awake
  - termux-notification      → persistent notification (Android won't kill)
  - Termux:Boot              → auto-start on device boot

Usage:
  ./persist.sh start           Enable + start the service
  ./persist.sh stop            Stop the service
  ./persist.sh restart         Restart the service
  ./persist.sh status          Check service status
  ./persist.sh logs            Tail service logs
  ./persist.sh notification    Toggle persistent notification on/off
"""
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="/data/data/com.termux/files/usr/var/service/kaufy-bot"
SVDIR="/data/data/com.termux/files/usr/var/service"
NOTIFICATION_ID="kaufy-bot"

ensure_termux_deps() {
    for pkg in termux-services termux-api; do
        if ! dpkg -s "$pkg" &>/dev/null; then
            echo "Installing $pkg..."
            pkg install -y "$pkg"
        fi
    done
}

notification_on() {
    if command -v termux-notification &>/dev/null; then
        termux-notification \
            -i "$NOTIFICATION_ID" \
            --title "Kaufy Bot" \
            --content "Running 24/7 — PID $$" \
            --button1 "Stop" \
            --button1-action "./persist.sh stop" \
            --ongoing \
            --priority max \
            2>/dev/null || true
    fi
}

notification_off() {
    if command -v termux-notification &>/dev/null; then
        termux-notification-remove "$NOTIFICATION_ID" 2>/dev/null || true
    fi
}

case "${1:-start}" in
    start|enable)
        ensure_termux_deps
        echo "Enabling Kaufy Bot service..."

        # Create service dir if it doesn't exist
        if [ ! -f "$SERVICE_DIR/run" ]; then
            mkdir -p "$SERVICE_DIR/log"
            cat > "$SERVICE_DIR/run" << 'RUNEOF'
#!/data/data/com.termux/files/usr/bin/bash
exec 2>&1
cd /data/data/com.termux/files/home/kaufy-bot
export HOME=/data/data/com.termux/files/home/kaufy-bot/opencode_data
export PATH=/data/data/com.termux/files/usr/bin:/data/data/com.termux/files/usr/local/bin:$PATH
exec python3 -m bot.main
RUNEOF
            cat > "$SERVICE_DIR/log/run" << 'LOGEOF'
#!/data/data/com.termux/files/usr/bin/bash
exec svlogd -tt /data/data/com.termux/files/home/kaufy-bot/logs
LOGEOF
            chmod +x "$SERVICE_DIR/run" "$SERVICE_DIR/log/run"
            mkdir -p /data/data/com.termux/files/home/kaufy-bot/logs
        fi

        # Start runsvdir if not running
        if ! pgrep -x runsvdir >/dev/null; then
            echo "Starting runsvdir (service supervisor)..."
            nohup runsvdir "$SVDIR" > /dev/null 2>&1 &
            sleep 2
        fi

        # Enable the service (remove 'down' flag + sv up)
        rm -f "$SERVICE_DIR/down"
        sv up kaufy-bot 2>/dev/null || true

        # Ensure runsv is watching this service
        if ! pgrep -f "runsv.*kaufy-bot" >/dev/null; then
            runsv "$SERVICE_DIR" &
            sleep 1
        fi

        # Wake lock
        termux-wake-lock kaufy-bot 2>/dev/null && echo "Wake lock acquired"

        # Persistent notification
        notification_on
        echo "✅ Kaufy Bot service ENABLED — 24/7 mode"
        echo "   Bot will auto-start on device boot (needs Termux:Boot app)"
        echo "   Check status: ./persist.sh status"
        echo "   See logs:     ./persist.sh logs"
        ;;

    stop|disable)
        echo "Stopping Kaufy Bot service..."
        # Kill specific runsv for our service
        pkill -f "runsv.*kaufy-bot" 2>/dev/null || true
        pkill -f "python.*bot\.main" 2>/dev/null || true
        # Mark as down so it doesn't restart
        touch "$SERVICE_DIR/down" 2>/dev/null || true
        termux-wake-unlock kaufy-bot 2>/dev/null
        notification_off
        echo "✅ Kaufy Bot service DISABLED"
        ;;

    restart)
        echo "Restarting..."
        pkill -f "runsv.*kaufy-bot" 2>/dev/null || true
        pkill -f "python.*bot\.main" 2>/dev/null || true
        sleep 2
        rm -f "$SERVICE_DIR/down"
        runsv "$SERVICE_DIR" &
        sleep 2
        notification_on
        echo "✅ Restarted"
        ;;

    status)
        echo "=== Service status ==="
        if pgrep -f "python.*bot\.main" >/dev/null; then
            echo "Bot process: RUNNING"
            ps aux | grep "python.*bot\.main" | grep -v grep | awk '{print "  PID:",$2,"CPU:",$3"%","MEM:",$4"%"}'
        else
            echo "Bot process: NOT RUNNING"
        fi
        echo ""
        echo "=== runsv ==="
        if pgrep -f "runsv.*kaufy-bot" >/dev/null; then
            echo "Supervisor: ACTIVE"
        else
            echo "Supervisor: INACTIVE"
        fi
        echo ""
        echo "=== Wake lock ==="
        termux-wake-lock-check 2>/dev/null && echo "Active" || echo "Status unknown"
        echo ""
        echo "=== Persistent notification ==="
        if command -v termux-notification-list &>/dev/null; then
            termux-notification-list 2>/dev/null | grep -q "$NOTIFICATION_ID" && \
                echo "Active" || echo "Inactive"
        else
            echo "(termux-api may not have notification-list)"
        fi
        echo ""
        echo "=== Auto-start on boot ==="
        if [ -f "$HOME/.termux/boot/kaufy-bot" ]; then
            echo "ENABLED (Termux:Boot add-on required)"
        else
            echo "DISABLED (run './persist.sh boot-setup')"
        fi
        ;;

    logs)
        mkdir -p /data/data/com.termux/files/home/kaufy-bot/logs
        if [ -f /data/data/com.termux/files/home/kaufy-bot/logs/current ]; then
            tail -f /data/data/com.termux/files/home/kaufy-bot/logs/current
        elif [ -f /data/data/com.termux/files/home/kaufy-bot/bot_output.log ]; then
            tail -f /data/data/com.termux/files/home/kaufy-bot/bot_output.log
        else
            echo "No log files found yet."
        fi
        ;;

    notification)
        if command -v termux-notification-list &>/dev/null && \
           termux-notification-list 2>/dev/null | grep -q "$NOTIFICATION_ID"; then
            notification_off
            echo "Notification removed"
        else
            notification_on
            echo "Notification created"
        fi
        ;;

    boot-setup)
        mkdir -p "$HOME/.termux/boot"
        cat > "$HOME/.termux/boot/kaufy-bot" << 'BOOTEOF'
#!/data/data/com.termux/files/usr/bin/bash
# Auto-start Kaufy Bot on device boot (requires Termux:Boot add-on)
termux-wake-lock kaufy-bot
sleep 15
export SVDIR="/data/data/com.termux/files/usr/var/service"
sv-enable kaufy-bot
BOOTEOF
        chmod +x "$HOME/.termux/boot/kaufy-bot"
        echo "✅ Boot script created at ~/.termux/boot/kaufy-bot"
        echo "   Install Termux:Boot from F-Droid, then reboot."
        ;;

    *)
        echo "Usage: $0 {start|stop|restart|status|logs|notification|boot-setup}"
        exit 1
        ;;
esac
