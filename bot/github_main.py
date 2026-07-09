"""GitHub Actions entry point for Kaufy Discord Bot.

Runs the bot with GitHub-specific lifecycle management:
- Periodically checks remaining monthly minutes via GitHub API
- Stops gracefully when approaching the 2000-min monthly limit
- Backs up databases to git before each restart
- Self-triggers next workflow run via workflow_dispatch

Environment Variables (set by workflow):
  KAUFY_RUN_MODE          = "github_actions"
  MONTHLY_LIMIT_MINUTES   = 1900  (stop at 1900 min, leave buffer)
  MONTHLY_MINUTES_USED    = currently used minutes this month
  MONTHLY_MINUTES_INCLUDED = included minutes per month (2000)
  GITHUB_TOKEN            = GitHub API token
  GITHUB_REPOSITORY       = owner/repo
  KAUFY_RESTART_INTERVAL  = seconds between restarts (19800 = 5.5h)
"""
import asyncio
import logging
import os
import sys
import json
import time
from pathlib import Path

# Load .env before any config imports
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

from bot.config import Config
from bot.services.backup import backup_service

logger = logging.getLogger("kaufy.github")

# GitHub API
GH_API_BASE = "https://api.github.com"

# How often to check remaining minutes (seconds)
MINUTES_CHECK_INTERVAL = 600  # 10 min

# How long before the 6h job limit to start shutdown (seconds)
JOB_LIMIT_BUFFER = 600  # 10 min before hard limit


async def get_remaining_minutes() -> int:
    """Check GitHub Actions billing and return remaining minutes."""
    token = os.getenv("GITHUB_TOKEN", "")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if not token or not repo:
        logger.warning("GitHub token or repo not set, cannot check minutes")
        return 9999  # Assume plenty

    import aiohttp
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"{GH_API_BASE}/repos/{repo}/actions/billing"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"GitHub billing API error {resp.status}: {text[:200]}")
                    return 9999
                data = await resp.json()
                used = data.get("total_minutes_used", 0)
                included = data.get("included_minutes", 2000)
                remaining = included - used
                logger.info(f"GitHub minutes: {used} used, {included} included, {remaining} remaining")
                return max(0, remaining)
    except Exception as e:
        logger.error(f"Failed to check GitHub minutes: {e}")
        return 9999


async def trigger_next_run() -> bool:
    """Trigger the next workflow run via workflow_dispatch."""
    token = os.getenv("GH_PAT", "")  # Need PAT for workflow_dispatch
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if not token or not repo:
        logger.warning("GH_PAT or repo not set, cannot trigger next run")
        return False

    import aiohttp
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"{GH_API_BASE}/repos/{repo}/actions/workflows/bot.yml/dispatches"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, json={"ref": "main"}, timeout=15) as resp:
                if resp.status == 204:
                    logger.info("✅ Next workflow run triggered")
                    return True
                else:
                    text = await resp.text()
                    logger.warning(f"Failed to trigger next run: {resp.status} {text[:200]}")
                    return False
    except Exception as e:
        logger.error(f"Error triggering next run: {e}")
        return False


async def backup_and_stop(reason: str):
    """Backup databases and stop the bot."""
    logger.info(f"Shutting down: {reason}")
    print(f"\n🔄 {reason}")
    
    # Backup all user databases
    try:
        result = await backup_service.backup_all()
        logger.info(f"Backup result: {result}")
        print(f"📦 {result}")
    except Exception as e:
        logger.error(f"Backup failed: {e}")
    
    # Try to trigger next run (only if we're not at limit)
    if "limit" not in reason.lower():
        try:
            await trigger_next_run()
        except Exception as e:
            logger.error(f"Trigger next run failed: {e}")
    
    print(f"✅ Bot stopped: {reason}")
    sys.exit(0)


async def main():
    """GitHub Actions main loop."""
    logger.info("Starting Kaufy Bot in GitHub Actions mode")
    print("=" * 50)
    print("🚀 Kaufy Discord Bot — GitHub Actions Mode")
    print("=" * 50)

    Config.load()

    # Validate config (duplicated from main.py to avoid circular import)
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

    # Check remaining minutes at startup
    remaining = await get_remaining_minutes()
    monthly_limit = int(os.getenv("MONTHLY_LIMIT_MINUTES", "1900"))

    print(f"📊 GitHub minutes remaining: {remaining} / {remaining + int(os.getenv('MONTHLY_MINUTES_USED', '0'))}")
    
    if remaining <= 0:
        print("❌ No GitHub minutes remaining this month. Stopping to avoid overage.")
        await backup_and_stop("Monthly GitHub minutes limit reached")
        return
    
    if remaining < 60:
        print(f"⚠️ Only {remaining} minutes left. Stopping to avoid hitting the limit.")
        await backup_and_stop(f"Only {remaining} GitHub minutes remaining")
        return

    if remaining < monthly_limit:
        print(f"⚠️ Under monthly threshold ({remaining} < {monthly_limit}). This will be the last run.")
        # Will stop at the end of this run

    # Compute run deadline: 5h30min from now, or job limit, or when minutes run out
    restart_interval = int(os.getenv("KAUFY_RESTART_INTERVAL", "19800"))
    job_deadline = time.time() + restart_interval

    # Import and start the bot
    from bot.bot import KaufyBot

    bot = KaufyBot()

    # Add GitHub-specific shutdown check as a background task
    async def github_lifecycle():
        """Monitor minutes and runtime, shutdown gracefully when needed."""
        start_time = time.time()
        check_count = 0

        while True:
            await asyncio.sleep(MINUTES_CHECK_INTERVAL)
            check_count += 1
            elapsed = time.time() - start_time
            remaining_job_time = job_deadline - time.time()

            # Check 1: Job time limit (5.5h)
            if remaining_job_time <= JOB_LIMIT_BUFFER:
                await backup_and_stop(
                    f"Job time limit approaching ({elapsed/3600:.1f}h elapsed, "
                    f"{remaining_job_time/60:.0f} min remaining before 6h limit)"
                )
                return

            # Check 2: Monthly minutes remaining
            remaining_min = await get_remaining_minutes()
            if remaining_min <= 0:
                await backup_and_stop("Monthly GitHub minutes limit reached")
                return
            if remaining_min < 30:
                await backup_and_stop(
                    f"Only {remaining_min} GitHub minutes remaining"
                )
                return

            # Check 3: Monthly limit threshold
            if remaining_min < monthly_limit and check_count >= 2:
                # If we're under the threshold and we've checked at least twice,
                # this is the last run. Stop after the current check.
                logger.info(f"Under monthly limit threshold ({remaining_min} < {monthly_limit})")
                # Don't stop immediately — let the bot finish naturally
                pass

    # Start lifecycle monitor
    lifecycle_task = asyncio.create_task(github_lifecycle())

    try:
        await bot.start(Config.DISCORD_TOKEN)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        await bot.close()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        # Try to backup before dying
        try:
            await backup_service.backup_all()
        except:
            pass
        sys.exit(1)
    finally:
        lifecycle_task.cancel()
        try:
            await lifecycle_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
        sys.exit(0)
