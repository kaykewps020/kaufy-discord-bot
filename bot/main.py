"""Kaufy Discord Bot - Entry point.

Environment Variables:
  DISCORD_TOKEN      - Discord bot token (REQUIRED)
  OWNER_IDS          - Comma-separated Discord user IDs of owners
  GUILD_ID           - Discord guild/server ID
  SESSION_TIMEOUT    - Session idle timeout in seconds (default: 600)
  MAX_MESSAGES       - Max messages per user before FIFO eviction (default: 800)
  RESTART_INTERVAL   - Auto-restart interval in seconds (default: 18000 = 5h)
  KAUFY_CMD          - Path to opencode binary (default: opencode)
  MODEL              - Model name (default: opencode/big-pickle)
  TEMPERATURE        - Default temperature (default: 0.8)
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

# ⚡ Load .env BEFORE any config imports so tokens are available
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()  # override, not setdefault

from bot.config import Config

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("kaufy.main")

def validate_config():
    """Validate required configuration."""
    errors = []
    if not Config.DISCORD_TOKEN:
        errors.append("DISCORD_TOKEN is not set")
    if not Config.OWNER_IDS or Config.OWNER_IDS == [0]:
        errors.append("OWNER_IDS is not set (set to your Discord user ID)")
    if not Config.GUILD_ID:
        errors.append("GUILD_ID is not set (or set to 0)")
    if errors:
        for e in errors:
            logger.error(e)
        print("\n❌ Configuration errors:")
        for e in errors:
            print(f"  • {e}")
        print("\nSet environment variables or edit bot/config.py")
        sys.exit(1)

async def main():
    """Main entry point.

    Detects run mode: if KAUFY_RUN_MODE=github_actions, delegates to github_main.
    Otherwise runs as local bot (Termux/start.sh).
    """
    Config.load()

    # GitHub Actions mode?
    if Config.RUN_MODE == "github_actions":
        logger.info("Detected GitHub Actions mode — delegating to github_main")
        from bot.github_main import main as github_main
        await github_main()
        return

    validate_config()

    # Late import to avoid circular imports
    from bot.bot import KaufyBot

    bot = KaufyBot()
    try:
        await bot.start(Config.DISCORD_TOKEN)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await bot.close()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
