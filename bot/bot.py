"""Kaufy Discord Bot - Main bot client."""
import discord
from discord.ext import commands
import logging
import os
import sys
import asyncio
from bot.config import Config
from bot.models.session import session_manager
from bot.models.key_db import key_db
from bot.services.backup import backup_service
from bot.services.payment import payment_service
from bot.cogs.panels import get_welcome_panel, get_config_panel, get_plans_panel, get_thinking_panel, ConfigView, PlansView, ThinkingView

logger = logging.getLogger("kaufy.bot")

class KaufyBot(commands.Bot):
    """Custom bot class with Kaufy-specific functionality."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=".",
            intents=intents,
            help_command=None,
            case_insensitive=True
        )

        self.ready_once = False

    async def setup_hook(self):
        """Initialize bot components."""
        Config.init_dirs()

        # Initialize key database
        await key_db.init()
        logger.info("Key database initialized")

        # Load cogs
        cogs = [
            "bot.cogs.owner",
            "bot.cogs.sessions",
            "bot.cogs.channels",
            "bot.cogs.roles",
            "bot.cogs.moderation",
            "bot.cogs.verify",
            "bot.cogs.screenshot",
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"Loaded cog: {cog}")
            except Exception as e:
                logger.error(f"Failed to load cog {cog}: {e}")

        # Start background services
        session_manager.start_background_cleanup()
        backup_service.start_background_backup()
        payment_service.start_background_polling()

        # Register all persistent views (survive restarts)
        self.add_view(get_welcome_panel())
        self.add_view(ConfigView())
        self.add_view(PlansView())
        self.add_view(ThinkingView())

        # Sync commands globally (avoids duplicating with guild sync)
        await self.tree.sync()

    async def on_ready(self):
        """Called when the bot is ready."""
        if self.ready_once:
            return
        self.ready_once = True

        logger.info(f"Bot logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")

        # Set up guilds
        for guild in self.guilds:
            logger.info(f"Guild: {guild.name} ({guild.id})")

        # Set status
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name="Kaufy's Hall"
        )
        await self.change_presence(
            status=discord.Status.online,
            activity=activity
        )

        # Auto-restart timer
        self.loop.create_task(self._auto_restart_timer())

    async def _auto_restart_timer(self):
        """Auto-restart every Config.RESTART_INTERVAL seconds."""
        await asyncio.sleep(Config.RESTART_INTERVAL)
        logger.info(f"Auto-restart triggered after {Config.RESTART_INTERVAL}s")

        if Config.RUN_MODE == "github_actions":
            # GitHub mode: backup, trigger next run, then exit
            await backup_service.backup_all()
            await asyncio.sleep(1)
            try:
                from bot.github_main import trigger_next_run
                await trigger_next_run()
            except Exception as e:
                logger.warning(f"Could not trigger next run: {e}")
            logger.info("Exiting for restart (GitHub Actions will pick up)")
            await self.close()
            sys.exit(0)
        else:
            # Local mode: start.sh loop handles restart
            await backup_service.backup_all()
            await asyncio.sleep(1)
            logger.info("Exiting for restart (start.sh loop will restart)")
            await self.close()
            sys.exit(0)

    async def on_member_join(self, member: discord.Member):
        """Handle new members."""
        # Handled by ChannelManager cog
        pass

    async def on_error(self, event_method, *args, **kwargs):
        """Global error handler."""
        logger.error(f"Error in {event_method}: {sys.exc_info()}")
