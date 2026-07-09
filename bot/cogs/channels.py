"""Channel management cog - creates per-user category and channels."""
import time
import discord
from discord.ext import commands
import logging
from bot.config import Config
from bot.models.session import session_manager
from bot.models.user_db import UserDatabase

logger = logging.getLogger("kaufy.channels")

def _sanitize_name(name: str) -> str:
    """Sanitize a name for use in Discord channel/category names."""
    return name.lower().replace(" ", "-")[:30]

def _channel_name(base: str, username: str, plan: str) -> str:
    """Build channel name: {base}-{username}-{plan}"""
    safe_user = _sanitize_name(username)
    return f"{base}-{safe_user}-{plan}"

def _category_name(username: str) -> str:
    """Build category name: Kaufy's Chat - {username}"""
    return f"Kaufy's Chat - {username}"

def _get_user_plan_sync(user_id: int) -> str:
    """Synchronous helper to get user plan (used before DB init in some flows)."""
    import sqlite3
    db_path = Config.DB_DIR / f"user_{user_id}.db"
    if not db_path.exists():
        return "free"
    try:
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("SELECT value FROM config WHERE key='plan'")
        row = c.fetchone()
        conn.close()
        return row[0] if row else "free"
    except:
        return "free"


class ChannelManager(commands.Cog):
    """Manages per-user channels with Kaufy Chat naming."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild: discord.Guild = None
        self._ready = False

    async def setup_guild(self, guild: discord.Guild):
        self.guild = guild
        self._ready = True
        logger.info(f"Guild setup: {guild.name} ({guild.id})")

    async def ensure_user_channels(self, member: discord.Member, plan: str = None) -> dict:
        """Create or get user's channels. Category: Kaufy's Chat - name.
        
        #plans channel is ONLY created for free users.
        Paid users don't see #plans (they already bought).
        """
        guild = member.guild
        if plan is None:
            db = UserDatabase(member.id)
            await db.init()
            plan = await db.get_config("plan") or "free"

        cat_name = _category_name(member.display_name)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, manage_channels=True
            ),
        }

        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            category = await guild.create_category(
                cat_name, overwrites=overwrites,
                reason=f"Kaufy Chat for {member}"
            )

        channels = {}
        # Always create msg and config
        base_names = [Config.CHANNEL_MSG, Config.CHANNEL_CONFIG]
        for base in base_names:
            ch_name = _channel_name(base, member.display_name, plan)
            existing = discord.utils.get(category.channels, name=ch_name)
            if not existing:
                existing = await guild.create_text_channel(
                    ch_name, category=category, overwrites=overwrites,
                    reason=f"Kaufy Chat for {member}"
                )
            channels[base] = existing

        # #plans: only create for FREE users. Paid users don't need it.
        if plan == "free":
            plans_name = _channel_name(Config.CHANNEL_PLANS, member.display_name, plan)
            existing_plans = discord.utils.get(category.channels, name=plans_name)
            if not existing_plans:
                existing_plans = await guild.create_text_channel(
                    plans_name, category=category, overwrites=overwrites,
                    reason=f"Kaufy plans for {member}"
                )
            channels["plans"] = existing_plans
        else:
            # Paid user — make sure #plans is hidden/deleted if it exists
            await self._hide_plans_channel(member, category)

        return channels

    async def _hide_plans_channel(self, member: discord.Member, category: discord.CategoryChannel = None):
        """Hide or delete the #plans channel for a paid user."""
        guild = member.guild
        if category is None:
            cat_name = _category_name(member.display_name)
            category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            return

        for ch in category.channels:
            parts = ch.name.rsplit("-", 2)
            if len(parts) == 3:
                base = parts[0]
                if base == Config.CHANNEL_PLANS:
                    try:
                        # Deny read access instead of deleting (preserves message history)
                        overwrite = ch.overwrites_for(member)
                        overwrite.read_messages = False
                        await ch.set_permissions(member, overwrite=overwrite)
                        logger.info(f"Hidden #plans for paid user {member.id}: {ch.name}")
                    except Exception as e:
                        logger.warning(f"Failed to hide plans channel: {e}")

    async def _show_plans_channel(self, member: discord.Member):
        """Show the #plans channel when user reverts to free."""
        guild = member.guild
        cat_name = _category_name(member.display_name)
        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            return

        for ch in category.channels:
            parts = ch.name.rsplit("-", 2)
            if len(parts) == 3:
                base = parts[0]
                if base == Config.CHANNEL_PLANS:
                    try:
                        overwrite = ch.overwrites_for(member)
                        overwrite.read_messages = True
                        await ch.set_permissions(member, overwrite=overwrite)
                        logger.info(f"Shown #plans for free user {member.id}: {ch.name}")
                    except Exception as e:
                        logger.warning(f"Failed to show plans channel: {e}")

    async def ensure_thinking_channel(self, member: discord.Member, plan: str = "free") -> discord.TextChannel:
        """Create or get thinking channel for paid users."""
        guild = member.guild
        cat_name = _category_name(member.display_name)
        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            return None

        ch_name = _channel_name(Config.CHANNEL_THINKING, member.display_name, plan)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, manage_channels=True
            ),
        }

        existing = discord.utils.get(category.channels, name=ch_name)
        if existing:
            return existing

        return await guild.create_text_channel(
            ch_name, category=category, overwrites=overwrites,
            reason=f"Kaufy thinking for {member}"
        )

    async def rename_channels_for_plan(self, member: discord.Member, new_plan: str):
        """Rename all user's channels when plan changes."""
        guild = member.guild
        cat_name = _category_name(member.display_name)
        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            return

        for channel in category.channels:
            parts = channel.name.rsplit("-", 2)
            if len(parts) == 3:
                base, username, _ = parts
                new_name = f"{base}-{username}-{new_plan}"
                try:
                    await channel.edit(name=new_name)
                except Exception as e:
                    logger.warning(f"Failed to rename channel {channel.name}: {e}")

    async def send_welcome_panels(self, member: discord.Member, channels: dict, plan: str = "free"):
        """Send welcome/config panels to user's channels. Plans panel only for free users."""
        from bot.cogs.panels import get_welcome_panel, get_config_panel, get_plans_panel
        from bot.cogs.panels import EMOJI_BOTS, EMOJI_OWNER, EMOJI_MANAGER, EMOJI_SHOP, EMOJI_CART, EMOJI_BOOSTER

        is_paid = plan != "free"

        # Welcome in msg channel
        if is_paid:
            desc = (
                f"{EMOJI_BOTS} I am Kaufy, your unrestricted technical AI assistant.\n\n"
                f"{EMOJI_MANAGER} Ask me anything in this channel\n"
                f"{EMOJI_SHOP} Configure your experience in #config\n"
                f"{EMOJI_BOOSTER} Your **{plan.upper()}** plan is active"
            )
        else:
            desc = (
                f"{EMOJI_BOTS} I am Kaufy, your unrestricted technical AI assistant.\n\n"
                f"{EMOJI_MANAGER} Ask me anything in this channel\n"
                f"{EMOJI_SHOP} Configure your experience in #config\n"
                f"{EMOJI_CART} Check plans in #plans\n"
                f"{EMOJI_BOOSTER} 10 free messages per day"
            )

        embed = discord.Embed(
            title=f"{EMOJI_BOTS} Welcome to Kaufy Hall",
            description=desc,
            color=0x9B59B6
        )
        await channels["msg"].send(embed=embed, view=get_welcome_panel())

        # Config panel
        await channels["config"].send(
            embed=discord.Embed(
                title=f"{EMOJI_MANAGER} Configuration Panel",
                description=f"{EMOJI_MANAGER} Adjust your AI experience below using the buttons and dropdowns.",
                color=0x3498DB
            ),
            view=get_config_panel(member.id)
        )

        # Plans panel — ONLY for free users
        if not is_paid and "plans" in channels:
            await channels["plans"].send(
                embed=discord.Embed(
                    title=f"{EMOJI_CART} Plans and Subscription",
                    description=f"{EMOJI_SHOP} Choose your plan to unlock features. Crypto payment only.",
                    color=0x2ECC71
                ),
                view=get_plans_panel(member.id)
            )

    async def on_user_join(self, member: discord.Member):
        """Setup channels for new member."""
        try:
            channels = await self.ensure_user_channels(member, "free")
            await self.send_welcome_panels(member, channels, "free")
            logger.info(f"Channels created for {member}")
        except Exception as e:
            logger.error(f"Channel setup failed for {member}: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.on_user_join(member)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Bot foi adicionada a um servidor — cria canal de boas-vindas."""
        logger.info(f"Joined guild: {guild.name} ({guild.id})")
        await self.setup_guild(guild)

        # Tenta criar um canal #kaufy pra welcome
        welcome_ch = discord.utils.get(guild.text_channels, name="kaufy")
        if not welcome_ch:
            try:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
                }
                welcome_ch = await guild.create_text_channel(
                    "kaufy", overwrites=overwrites,
                    reason="Kaufy Bot welcome channel",
                    topic="Kaufy AI Assistant — ask anything"
                )
            except Exception as e:
                logger.warning(f"Could not create #kaufy channel: {e}")
                welcome_ch = guild.system_channel or guild.text_channels[0]

        # Envia mensagem de boas-vindas
        from bot.cogs.panels import get_welcome_panel, EMOJI_BOTS, EMOJI_OWNER, EMOJI_MANAGER, EMOJI_SHOP, EMOJI_CART
        embed = discord.Embed(
            title=f"{EMOJI_BOTS} Kaufy Bot Online!",
            description=(
                f"{EMOJI_BOTS} Kaufy is here. Your unrestricted AI assistant.\n\n"
                f"{EMOJI_OWNER} Commands with `.` prefix (owner only).\n"
                f"{EMOJI_MANAGER} Send a message in #msg to start chatting.\n"
                f"{EMOJI_SHOP} Configure in #config.\n"
                f"{EMOJI_CART} Check plans in #plans."
            ),
            color=0x9B59B6
        )
        embed.set_footer(text="Kaufy's Hall • AI-Powered")
        try:
            await welcome_ch.send(embed=embed, view=get_welcome_panel())
        except Exception as e:
            logger.error(f"Failed to send welcome: {e}")

        # Se for o guild principal (Kaufy's Hall), cria canais base
        if guild.id == Config.GUILD_ID:
            try:
                await self._ensure_base_channels(guild)
            except Exception as e:
                logger.error(f"Base channel setup failed: {e}")

    async def _ensure_base_channels(self, guild: discord.Guild):
        """Cria canais base #msg, #config, #plans se não existirem (sem emoji no nome pra lookup funcionar)."""
        base = {
            Config.CHANNEL_MSG: "Send messages to Kaufy here",
            Config.CHANNEL_CONFIG: "Configure your AI experience",
            Config.CHANNEL_PLANS: "View subscription plans",
            Config.CHANNEL_THINKING: "AI reasoning process (paid plans)",
        }
        for name, topic in base.items():
            existing = discord.utils.get(guild.text_channels, name=name)
            if not existing:
                try:
                    await guild.create_text_channel(
                        name,
                        topic=topic,
                        reason="Kaufy base channel"
                    )
                except Exception as e:
                    logger.warning(f"Could not create #{name}: {e}")

    async def _ensure_owner_plan(self):
        """Garante que o owner tenha plano lifetime."""
        for owner_id in Config.OWNER_IDS:
            try:
                db = UserDatabase(owner_id)
                await db.init()
                plan = await db.get_config("plan") or "free"
                if plan == "free":
                    lifetime = Config.PLANS.get("lifetime", {})
                    expires = int(time.time()) + lifetime.get("duration_days", 36500) * 86400
                    await db.set_config("plan", "lifetime")
                    await db.set_config("plan_expires", str(expires))
                    logger.info(f"Owner {owner_id} auto-assigned lifetime plan")
            except Exception as e:
                logger.warning(f"Could not set owner plan for {owner_id}: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        await self._ensure_owner_plan()
        
        # Register callback with payment service to hide/show #plans on plan changes
        from bot.services.payment import payment_service
        payment_service.set_plan_activated_callback(self._on_plan_status_changed)
        
        for guild in self.bot.guilds:
            await self.setup_guild(guild)
            # Se for o guild principal, garante canais base
            if guild.id == Config.GUILD_ID:
                await self._ensure_base_channels(guild)

    async def _on_plan_status_changed(self, user_id: int, plan_id: str):
        """Callback called by payment service when plan is activated or expires.
        
        plan_id == "free" means plan expired → show #plans
        plan_id != "free" means plan activated → hide #plans
        """
        guild = self.bot.get_guild(Config.GUILD_ID)
        if not guild:
            return
        
        member = guild.get_member(user_id)
        if not member:
            return

        cat_name = _category_name(member.display_name)
        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            return

        if plan_id == "free":
            # Plan expired → show #plans channel
            await self._show_plans_channel(member)
            # Also rename channels back to free
            await self.rename_channels_for_plan(member, "free")
            logger.info(f"Plan expired for {user_id}: showing #plans, renaming to free")
        else:
            # Plan activated → hide #plans channel
            await self._hide_plans_channel(member, category)
            # Rename channels to new plan
            await self.rename_channels_for_plan(member, plan_id)
            logger.info(f"Plan activated for {user_id}: hiding #plans, renaming to {plan_id}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelManager(bot))
