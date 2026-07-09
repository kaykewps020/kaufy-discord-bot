"""Role management cog - auto-assigns roles based on purchase/gift/boost status."""
import discord
import json
import time
import logging
from discord.ext import commands
from bot.config import Config
from bot.models.user_db import UserDatabase

logger = logging.getLogger("kaufy.roles")


class RoleManager(commands.Cog):
    """Manages Discord roles for Kaufy's Hall."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild: discord.Guild = None
        self._roles_created = False

    async def setup_guild(self, guild: discord.Guild):
        """Create roles if they don't exist, ordered by hierarchy."""
        self.guild = guild

        # Create roles from bottom to top (Discord shows higher roles first)
        role_configs = [
            (Config.ROLE_PATRON, 0x5865F2, "Purchased any plan"),        # Blurple
            (Config.ROLE_RICH, 0xFEE75C, "Repeat buyer or top gifter"),  # Yellow
            (Config.ROLE_BOOSTER, 0xF47FFF, "Server booster"),           # Fuchsia
        ]

        created_roles = {}
        for name, color, reason in role_configs:
            existing = discord.utils.get(guild.roles, name=name)
            if not existing:
                try:
                    existing = await guild.create_role(
                        name=name,
                        color=discord.Color(color),
                        hoist=True,  # Show separately in member list
                        reason=f"Kaufy {name} role"
                    )
                    logger.info(f"Created role: {name}")
                except Exception as e:
                    logger.warning(f"Failed to create role {name}: {e}")
                    continue
            created_roles[name] = existing

        # Reorder roles (lowest to highest: Patron → Rich → Booster)
        try:
            positions = {}
            for i, (name, _, _) in enumerate(role_configs):
                if name in created_roles:
                    positions[created_roles[name]] = len(role_configs) - i
            if positions:
                await guild.edit_role_positions(positions)
        except Exception as e:
            logger.warning(f"Failed to reorder roles: {e}")

        # Create #boosters channel if it doesn't exist
        await self._ensure_boosters_channel(guild)

        self._roles_created = True
        logger.info("Role setup complete")

    async def _ensure_boosters_channel(self, guild: discord.Guild):
        """Create #boosters channel (visible only to boosters + owners)."""
        existing = discord.utils.get(guild.text_channels, name=Config.CHANNEL_BOOSTERS)
        if existing:
            return

        booster_role = discord.utils.get(guild.roles, name=Config.ROLE_BOOSTER)
        if not booster_role:
            return

        # Build permissions: everyone hidden, boosters + owners visible
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, manage_channels=True
            ),
        }

        # Add owner permissions
        for owner_id in Config.OWNER_IDS:
            member = guild.get_member(owner_id)
            if member:
                overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        # Add booster role permissions
        overwrites[booster_role] = discord.PermissionOverwrite(
            read_messages=True, send_messages=True, read_message_history=True
        )

        try:
            ch = await guild.create_text_channel(
                Config.CHANNEL_BOOSTERS,
                topic="Exclusive channel for server boosters — special perks and early access",
                overwrites=overwrites,
                reason="Kaufy boosters channel"
            )
            logger.info(f"Created #{Config.CHANNEL_BOOSTERS} channel")

            # Send welcome message
            from bot.cogs.panels import EMOJI_BOOSTER, EMOJI_BOTS
            embed = discord.Embed(
                title=f"{EMOJI_BOOSTER} Boosters Lounge",
                description=(
                    f"{EMOJI_BOTS} This channel is exclusive to server boosters!\n\n"
                    f"**Your perks:**\n"
                    f"• Priority in the AI queue\n"
                    f"• +50 extra messages per day\n"
                    f"• Maximum temperature (2.0)\n"
                    f"• Thinking mode access\n"
                    f"• This exclusive channel"
                ),
                color=0xF47FFF
            )
            await ch.send(embed=embed)
        except Exception as e:
            logger.warning(f"Failed to create #{Config.CHANNEL_BOOSTERS}: {e}")

    # ─── Role Assignment ──────────────────────────────────────

    async def assign_patron(self, member: discord.Member):
        """Assign Patron role to a member."""
        await self._assign_role(member, Config.ROLE_PATRON)

    async def assign_rich(self, member: discord.Member):
        """Assign Rich role to a member."""
        await self._assign_role(member, Config.ROLE_RICH)

    async def remove_role(self, member: discord.Member, role_name: str):
        """Remove a specific role from a member."""
        role = discord.utils.get(member.roles, name=role_name)
        if role:
            try:
                await member.remove_roles(role, reason=f"Kaufy role removal")
                logger.info(f"Removed {role_name} from {member.id}")
            except Exception as e:
                logger.warning(f"Failed to remove {role_name}: {e}")

    async def _assign_role(self, member: discord.Member, role_name: str):
        """Assign a role to a member (creates it if needed)."""
        if not self.guild:
            self.guild = member.guild

        role = discord.utils.get(self.guild.roles, name=role_name)
        if not role:
            # Try to create it
            await self.setup_guild(self.guild)
            role = discord.utils.get(self.guild.roles, name=role_name)

        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason=f"Kaufy role assignment")
                logger.info(f"Assigned {role_name} to {member.id}")
            except Exception as e:
                logger.warning(f"Failed to assign {role_name}: {e}")

    # ─── Eligibility Check ────────────────────────────────────

    async def check_and_assign_roles(self, user_id: int):
        """Check user's history and assign appropriate roles."""
        guild = self.bot.get_guild(Config.GUILD_ID)
        if not guild:
            return

        member = guild.get_member(user_id)
        if not member:
            return

        db = UserDatabase(user_id)
        await db.init()

        # Check if user has any plan (current or past)
        plan = await db.get_config("plan") or "free"
        if plan != "free":
            await self.assign_patron(member)

        # Check purchase history for Rich eligibility
        is_rich = await self._check_rich_eligibility(db, user_id)
        if is_rich:
            await self.assign_rich(member)

    async def _check_rich_eligibility(self, db: UserDatabase, user_id: int) -> bool:
        """Check if user qualifies for Rich role.
        
        Qualifies if:
        - Bought 7d plan 2+ times, OR
        - Gifted 3+ times
        """
        # Count 7d plan purchases
        purchase_count = 0
        tokens = await db.get_all_tokens()
        for token in tokens:
            if token.get("type") == "crypto_payment":
                try:
                    data = json.loads(token.get("data", "{}"))
                    if data.get("plan") == "7d":
                        purchase_count += 1
                except:
                    pass

        if purchase_count >= 2:
            return True

        # Count gifts given
        gift_count = 0
        config = await db.get_all_config()
        for key, value in config.items():
            if key.startswith("gift_"):
                try:
                    data = json.loads(value)
                    if data.get("sender") == str(user_id):
                        gift_count += 1
                except:
                    pass

        if gift_count >= 3:
            return True

        return False

    # ─── Booster Detection ────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Detect when a member starts/stops boosting."""
        # Check if boosting started
        if not before.premium_since and after.premium_since:
            logger.info(f"User {after.id} started boosting!")
            await self._assign_role(after, Config.ROLE_BOOSTER)
            await self._notify_boost(after, True)

        # Check if boosting stopped
        elif before.premium_since and not after.premium_since:
            logger.info(f"User {after.id} stopped boosting")
            await self.remove_role(after, Config.ROLE_BOOSTER)
            await self._notify_boost(after, False)

    async def _notify_boost(self, member: discord.Member, started: bool):
        """Notify about boost status change."""
        try:
            if started:
                await member.send(
                    f"🎉 **Thank you for boosting Kaufy's Hall!**\n\n"
                    f"You've been given the **{Config.ROLE_BOOSTER}** role with these perks:\n"
                    f"• Priority in the AI queue\n"
                    f"• +50 extra messages per day\n"
                    f"• Maximum temperature (2.0)\n"
                    f"• Thinking mode access\n"
                    f"• Access to #boosters exclusive channel"
                )
            else:
                await member.send(
                    f"Your boost for Kaufy's Hall has ended. "
                    f"The **{Config.ROLE_BOOSTER}** role has been removed."
                )
        except:
            pass

    # ─── On Ready ─────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self.setup_guild(guild)

        # Register role update callback with payment service
        from bot.services.payment import payment_service
        payment_service.set_role_update_callback(self.check_and_assign_roles)

        # Check existing boosters
        if self.guild:
            for member in self.guild.members:
                if member.premium_since and discord.utils.get(member.roles, name=Config.ROLE_BOOSTER) is None:
                    await self._assign_role(member, Config.ROLE_BOOSTER)


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleManager(bot))
