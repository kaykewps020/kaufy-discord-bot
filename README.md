# Kaufy Discord Bot 🤖

Multi-user AI assistant with per-user isolation, running Kaufy (opencode/big-pickle).

## Features

- 💬 **#msg** - Chat with Kaufy AI per user
- ⚙️ **#config** - Configure temperature, tokens, model via buttons
- 💳 **#plans** - Free/Pro/Premium plans with different limits
- 🤔 **#thinking** - Premium users get thinking mode access
- 🔒 **Per-user DB** - Each user has isolated SQLite database
- 📦 **Auto backup** - Periodic backups of all user databases
- ⚡ **Fast restart** - < 3 minutes with backup restore
- 🔄 **Auto restart** - Every 5 hours to prevent GitHub bans
- 🎛️ **20+ Owner commands** - Full admin control

## Quick Start

```bash
# Set environment variables
export DISCORD_TOKEN="your_discord_bot_token"
export OWNER_IDS="your_discord_user_id"
export GUILD_ID="your_guild_id"

# Start
./start.sh
```

## Owner Commands (prefix: `.`)

| Command | Description |
|---------|-------------|
| `.ping` | Check latency |
| `.uptime` | Show uptime |
| `.restart` | Restart bot |
| `.shutdown` | Shutdown bot |
| `.backup` | Force backup |
| `.restore [name]` | Restore from backup |
| `.backups` | List backups |
| `.users` | Active users |
| `.sessions` | Session info |
| `.kill <id>` | Kill user session |
| `.blacklist <id>` | Blacklist user |
| `.unblacklist <id>` | Unblacklist user |
| `.setplan <id> <plan>` | Set user plan |
| `.dm <id> <msg>` | DM user |
| `.broadcast <msg>` | Broadcast message |
| `.eval <code>` | Run Python code |
| `.exec <cmd>` | Run shell command |
| `.status` | Bot status |
| `.cleanup` | Clean stale sessions |
| `.db <id>` | User DB stats |
| `.export <id>` | Export user DB |
| `.config` | Show config |
| `.logs [n]` | View logs |
| `.maintenance` | Toggle maintenance |
| `.reset <id>` | Reset user |
| `.setowner <id>` | Add owner |
| `.reload <cog>` | Reload cog |

## Anti-Ban Measures

- Private repository
- No crypto mining or DDoS
- Auto-restart every 5 hours
- No SSH access exposed
- No untrusted downloads
- 10-minute session timeout
- No permanent hosting

## Architecture

```
Discord → Bot → per-user Kaufy process → per-user SQLite DB
```

Each user gets:
- Isolated channel category
- Private SQLite database (800 msg FIFO)
- Independent Kaufy process
- Separate config (temperature, model, tokens)
