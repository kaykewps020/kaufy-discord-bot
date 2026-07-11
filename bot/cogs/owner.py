"""Owner commands cog - 20+ utility commands with . prefix. Owner-only."""
import discord
from discord.ext import commands
import asyncio
import json
import logging
import os
import sys
import time
import io
from pathlib import Path
from bot.config import Config
from bot.models.session import session_manager
from bot.models.user_db import UserDatabase
from bot.services.backup import backup_service
from bot.utils.helpers import format_uptime

logger = logging.getLogger("kaufy.owner")

def is_owner(*, via_secret: bool = False):
    """Owner-access check — by default, just requires owner ID.

    via_secret=True: additionally requires .ownerauth <secret> (for sensitive
    commands like .eval, .exec). Most commands don't need this — the owner ID
    alone is sufficient.
    """
    from bot.services.owner_auth import owner_auth

    async def predicate(ctx: commands.Context):
        ok = await owner_auth.is_owner(ctx.author.id, via_secret=via_secret)
        if not ok and via_secret:
            try:
                await ctx.reply(
                    "🔒 This command requires authentication. Run `.ownerauth <your_secret>` first.",
                    delete_after=15,
                )
            except Exception:
                pass
        return ok

    return commands.check(predicate)


class OwnerCog(commands.Cog):
    """Owner-only utility commands with . prefix."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()

    # ─── Help & Panel ──────────────────────────────────────────

    @commands.command(name="ownerauth")
    async def owner_auth_cmd(self, ctx: commands.Context, *, secret: str = ""):
        """Unlock privileged owner mode with the owner secret.

        Usage:  .ownerauth <secret>
        Required once per bot session before owner commands / special reveals
        work — this is the second factor that stops anyone from impersonating
        the owner. Only registered owner IDs may authenticate.
        """
        from bot.services.owner_auth import owner_auth

        if ctx.author.id not in Config.OWNER_IDS:
            # Not a registered owner — don't even hint at the secret format.
            await ctx.reply("⛔ You are not registered as an owner.", delete_after=10)
            return

        if not secret:
            await ctx.reply(
                "Usage: `.ownerauth <your_secret>`", delete_after=10
            )
            return

        ok = await owner_auth.authenticate(ctx.author.id, secret)
        if ok:
            await ctx.reply(
                "🔓 Owner privileges unlocked for this session (24h). "
                "You can now use owner commands and receive internal data.",
                delete_after=20,
            )
        else:
            await ctx.reply(
                "❌ Authentication failed. Wrong secret for this owner ID.",
                delete_after=15,
            )
        # Ephemeral-ish: delete the triggering message so the secret doesn't linger
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @commands.command(name="help")
    async def help_cmd(self, ctx: commands.Context):
        """Show all available commands."""
        is_owner = ctx.author.id in Config.OWNER_IDS
        from bot.cogs.panels import EMOJI_BOTS, EMOJI_OWNER

        lines = [f"{EMOJI_BOTS} **Kaufy Bot — Commands**\n"]

        if is_owner:
            lines.append(f"{EMOJI_OWNER} **Owner Commands:**")
            lines.append("`.ping` — Bot latency")
            lines.append("`.uptime` — Bot uptime")
            lines.append("`.status` — Full status (memory, cpu, sessions)")
            lines.append("`.config` — Bot configuration")
            lines.append("`.restart` — Restart bot")
            lines.append("`.shutdown` — Shutdown bot permanently")
            lines.append("`.backup` — Force backup")
            lines.append("`.restore [name]` — Restore backup")
            lines.append("`.backups` — List backups")
            lines.append("`.users` — Active users")
            lines.append("`.sessions` — Session details")
            lines.append("`.kill <user_id>` — Kill session")
            lines.append("`.db <user_id>` — User DB stats")
            lines.append("`.export <user_id>` — Export user DB")
            lines.append("`.whois <user_id>` — Full user info")
            lines.append("`.alldb` — List all user databases")
            lines.append("`.reset <user_id>` — Reset conversation")
            lines.append("`.setplan <user_id> <plan>` — Set plan")
            lines.append("`.activate <user_id>` — Activate pending plan")
            lines.append("`.deactivate <user_id>` — Revert to free")
            lines.append("`.approve <user_id> <plan>` — Approve payment")
            lines.append("`.expire <user_id>` — Check plan expiry")
            lines.append("`.setowner <user_id>` — Add owner")
            lines.append("`.blacklist <user_id>` — Blacklist user")
            lines.append("`.unblacklist <user_id>` — Unblacklist user")
            lines.append("`.dm <user_id> <msg>` — DM user")
            lines.append("`.broadcast <msg>` — Broadcast to active users")
            lines.append("`.massbroadcast <msg>` — DM ALL users")
            lines.append("`.roles <user_id>` — Check user roles")
            lines.append("`.assign <user_id> <role>` — Assign role")
            lines.append("`.unassign <user_id> <role>` — Remove role")
            lines.append("`.checkroles` — Scan all users for role eligibility")
            lines.append("`.redeem <code> <user_id>` — Redeem gift")
            lines.append("`.gifts` — List pending gifts")
            lines.append("`.giftlookup <code> <user_id>` — Gift details")
            lines.append("`.crypto` — Crypto addresses")
            lines.append("`.panel [user_id]` — Re-send panels")
            lines.append("`.panelsetup [#channel]` — Place fixed public panel")
            lines.append("`.emoji upload` — Upload custom emojis")
            lines.append("`.emoji list` — List server emojis")
            lines.append("`.eval <code>` — Evaluate Python")
            lines.append("`.exec <cmd>` — Execute shell command")
            lines.append("`.logs [n]` — Recent logs")
            lines.append("`.cleanup` — Clean stale sessions")
            lines.append("`.reload <cog>` — Reload cog")
            lines.append("`.maintenance` — Toggle maintenance mode")
        else:
            lines.append(f"{EMOJI_BOTS} **User Commands:**")
            lines.append("`#msg` — Chat with Kaufy")
            lines.append("`#config` — Configure temperature/tokens")
            lines.append("`#plans` — View & purchase plans")
            lines.append("`.help` — Show this message")

        lines.append("\n*Kaufy's Hall — AI-Powered*")
        await ctx.send("\n".join(lines))

    @commands.command(name="panel")
    async def resend_panels(self, ctx: commands.Context, user_id: int = None):
        """Re-send your panels. Owner can use: .panel <user_id>"""
        # If user_id given and caller is owner, resend for that user
        if user_id is not None and ctx.author.id in Config.OWNER_IDS:
            target_id = user_id
        else:
            target_id = ctx.author.id
            user_id = None  # mark as self-service
        member = ctx.guild.get_member(target_id)
        if not member:
            try:
                member = await self.bot.fetch_user(target_id)
                await ctx.send(f"User `{target_id}` not found in this guild. Try their ID.")
                return
            except:
                return await ctx.send(f"User `{target_id}` not found.")

        from bot.cogs.channels import ChannelManager
        cm = self.bot.get_cog("ChannelManager")
        if not cm:
            return await ctx.send("ChannelManager cog not loaded.")

        # Get user's plan
        db = UserDatabase(target_id)
        await db.init()
        plan = await db.get_config("plan") or "free"

        # Ensure channels exist
        channels = await cm.ensure_user_channels(member, plan)
        if not channels:
            return await ctx.send("Failed to create channels.")

        # Re-send panels
        await cm.send_welcome_panels(member, channels, plan)
        await ctx.send(f"✅ Panels re-sent for `{target_id}` in {channels.get('msg', '?')}")

    @commands.command(name="panelsetup")
    @is_owner()
    async def panel_setup(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Fix a public panel in a channel for anyone to start. Usage: .panelsetup [#channel]"""
        target = channel or ctx.channel

        from bot.cogs.panels import WelcomeView, EMOJI_BOTS, EMOJI_OWNER
        embed = discord.Embed(
            title=f"{EMOJI_BOTS} Kaufy — AI Assistant",
            description=(
                f"{EMOJI_OWNER} Click below to start chatting.\n"
                f"{EMOJI_BOTS} Your private channels will be created automatically."
            ),
            color=0x9B59B6
        )
        embed.set_footer(text="Kaufy's Hall • AI-Powered")
        msg = await target.send(embed=embed, view=WelcomeView())
        await ctx.send(f"✅ Fixed panel placed in {target.mention}")

    @commands.command(name="emoji")
    @is_owner()
    async def emoji_manage(self, ctx: commands.Context, action: str = "upload"):
        """Upload custom emojis to the server. Usage: .emoji upload"""
        if action == "upload":
            emoji_dir = Path(__file__).parent.parent.parent / "emojis"
            if not emoji_dir.exists():
                return await ctx.send("Emoji directory not found. Run the download script first.")

            uploaded = 0
            # Upload both .png and .gif files
            for ext in ["*.png", "*.gif"]:
                for img_path in sorted(emoji_dir.glob(ext)):
                    name = img_path.stem  # e.g. "diamond", "dollar", "loading"
                    # Check if already exists
                    existing = discord.utils.get(ctx.guild.emojis, name=name)
                    if existing:
                        continue
                    try:
                        with open(img_path, "rb") as f:
                            img_data = f.read()
                        await ctx.guild.create_custom_emoji(
                            name=name, image=img_data,
                            reason="Kaufy bot emojis"
                        )
                        uploaded += 1
                    except Exception as e:
                        await ctx.send(f"Failed to upload {name}: {e}")

            await ctx.send(f"✅ Uploaded {uploaded} custom emoji(s) to server.")
        elif action == "list":
            if not ctx.guild.emojis:
                return await ctx.send("No custom emojis in this server.")
            lines = ["**Server Emojis:**"]
            for e in ctx.guild.emojis:
                lines.append(f"{e} `:{e.name}:` (animated={e.animated})")
            await ctx.send("\n".join(lines))

    # ─── Core Commands ────────────────────────────────────────

    @commands.command(name="ping")
    @is_owner()
    async def ping(self, ctx: commands.Context):
        """Check bot latency."""
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"🏓 Pong! **{latency}ms**")

    @commands.command(name="uptime")
    @is_owner()
    async def uptime(self, ctx: commands.Context):
        """Show bot uptime."""
        uptime_str = format_uptime(time.time() - self.start_time)
        await ctx.send(f"⏱️ Uptime: **{uptime_str}**")

    @commands.command(name="restart")
    @is_owner()
    async def restart(self, ctx: commands.Context):
        """Restart the bot completely."""
        await ctx.send("🔄 Restarting...")
        logger.info("Bot restart initiated by owner")
        # Backup first
        await backup_service.backup_all()
        # Restart process
        os.execv(sys.executable, [sys.executable, "-m", "bot.main"])

    @commands.command(name="shutdown")
    @is_owner()
    async def shutdown(self, ctx: commands.Context):
        """Shutdown the bot permanently (stops auto-restart loop)."""
        await ctx.send("🛑 Shutting down permanently...")
        logger.info("Permanent shutdown initiated by owner")
        await backup_service.backup_all()
        # Create flag file so start.sh loop doesn't restart
        flag_path = Path(__file__).parent.parent.parent / ".shutdown_flag"
        flag_path.touch()
        await asyncio.sleep(1)
        await self.bot.close()
        sys.exit(0)

    # ─── Backup Commands ──────────────────────────────────────

    @commands.command(name="backup")
    @is_owner()
    async def backup(self, ctx: commands.Context):
        """Force a backup of all user databases."""
        await ctx.send("📦 Creating backup...")
        result = await backup_service.backup_all()
        await ctx.send(result)

    @commands.command(name="restore")
    @is_owner()
    async def restore(self, ctx: commands.Context, backup_name: str = None):
        """Restore from backup. Usage: .restore [backup_name]"""
        if backup_name:
            result = await backup_service.restore_all(backup_name)
        else:
            result = await backup_service.restore_latest()
        await ctx.send(result)

    @commands.command(name="backups")
    @is_owner()
    async def list_backups(self, ctx: commands.Context):
        """List all available backups."""
        backups = await backup_service.list_backups()
        if not backups:
            return await ctx.send("No backups found.")
        lines = ["📦 **Available backups:**"]
        for b in backups[:20]:
            lines.append(f"`{b['filename']}` - {b['size_kb']}KB - {b['timestamp']}")
        await ctx.send("\n".join(lines))

    # ─── User Management ──────────────────────────────────────

    @commands.command(name="users")
    @is_owner()
    async def list_users(self, ctx: commands.Context):
        """List all users with active sessions."""
        sessions = await session_manager.get_all()
        if not sessions:
            return await ctx.send("No active users.")
        lines = ["👥 **Active users:**"]
        for uid, s in sessions.items():
            idle = format_uptime(s.idle_seconds)
            lines.append(f"• `{uid}` | {s.plan} | idle: {idle}")
        await ctx.send("\n".join(lines))

    @commands.command(name="sessions")
    @is_owner()
    async def list_sessions(self, ctx: commands.Context):
        """Show detailed session info."""
        sessions = await session_manager.get_all()
        if not sessions:
            return await ctx.send("No active sessions.")
        await ctx.send(f"📊 **{len(sessions)} active session(s)**")

    @commands.command(name="kill")
    @is_owner()
    async def kill_session(self, ctx: commands.Context, user_id: int):
        """Kill a user's session. Usage: .kill <user_id>"""
        session = await session_manager.get(user_id)
        if not session:
            return await ctx.send(f"❌ No session for user `{user_id}`")
        await session_manager.remove(user_id)
        await ctx.send(f"✅ Session killed for user `{user_id}`")

    @commands.command(name="blacklist")
    @is_owner()
    async def blacklist_user(self, ctx: commands.Context, user_id: int):
        """Blacklist a user. Usage: .blacklist <user_id>"""
        db = UserDatabase(user_id)
        await db.init()
        await db.set_config("blacklisted", "true")
        await session_manager.remove(user_id)
        await ctx.send(f"⛔ User `{user_id}` blacklisted")

    @commands.command(name="unblacklist")
    @is_owner()
    async def unblacklist_user(self, ctx: commands.Context, user_id: int):
        """Unblacklist a user. Usage: .unblacklist <user_id>"""
        db = UserDatabase(user_id)
        await db.init()
        await db.set_config("blacklisted", "false")
        await ctx.send(f"✅ User `{user_id}` unblacklisted")

    @commands.command(name="setplan")
    @is_owner()
    async def set_plan(self, ctx: commands.Context, user_id: int, plan: str):
        """Set a user's plan. Usage: .setplan <user_id> free|7d|14d|30d|lifetime"""
        valid_plans = list(Config.PLANS.keys())
        if plan not in valid_plans:
            return await ctx.send(f"❌ Plan must be one of: {', '.join(valid_plans)}")
        db = UserDatabase(user_id)
        await db.init()
        await db.set_config("plan", plan)
        if plan != "free":
            # Set expiry for paid plans
            plan_config = Config.PLANS.get(plan, {})
            duration = plan_config.get("duration_days", 0)
            if duration > 0:
                expires = int(time.time()) + (duration * 86400)
                await db.set_config("plan_expires", str(expires))
        else:
            await db.set_config("plan_expires", "0")
        
        # Notify channel manager to hide/show #plans
        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(user_id, plan)
            except Exception as e:
                logger.warning(f"setplan callback error: {e}")
        
        await ctx.send(f"✅ User `{user_id}` plan set to **{plan}**")

    # ─── Messaging ────────────────────────────────────────────

    @commands.command(name="dm")
    @is_owner()
    async def dm_user(self, ctx: commands.Context, user_id: int, *, message: str):
        """DM a user. Usage: .dm <user_id> <message>"""
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(f"📨 **Kaufy Admin:** {message}")
            await ctx.send(f"✅ DM sent to `{user_id}`")
        except Exception as e:
            await ctx.send(f"❌ Failed to DM: {e}")

    @commands.command(name="broadcast")
    @is_owner()
    async def broadcast(self, ctx: commands.Context, *, message: str):
        """Broadcast to all users with active sessions."""
        sessions = await session_manager.get_all()
        sent = 0
        for uid in sessions:
            try:
                user = await self.bot.fetch_user(uid)
                await user.send(f"📢 **Broadcast:** {message}")
                sent += 1
            except:
                pass
        await ctx.send(f"📢 Broadcast sent to {sent} user(s)")

    # ─── System Commands ──────────────────────────────────────

    @commands.command(name="eval")
    @is_owner()
    async def eval_code(self, ctx: commands.Context, *, code: str):
        """Evaluate Python code (owner only)."""
        try:
            result = eval(code)
            await ctx.send(f"```py\n{result}\n```")
        except Exception as e:
            await ctx.send(f"```py\nError: {e}\n```")

    @commands.command(name="exec")
    @is_owner()
    async def exec_shell(self, ctx: commands.Context, *, cmd: str):
        """Execute a shell command."""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode()[:1500]
            if stderr.decode():
                output += f"\n⚠️ Stderr:\n{stderr.decode()[:500]}"
            await ctx.send(f"```bash\n{output}\n```")
        except asyncio.TimeoutError:
            await ctx.send("⏱️ Command timed out (30s)")
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name="status")
    @is_owner()
    async def status(self, ctx: commands.Context):
        """Show full bot status."""
        try:
            import psutil as _psutil
            process = _psutil.Process()
            memory = process.memory_info().rss / 1024 / 1024
            cpu = process.cpu_percent()
        except ImportError:
            memory = 0.0
            cpu = 0.0
        sessions = await session_manager.get_all()

        embed = discord.Embed(title="📊 Bot Status", color=0x3498DB)
        embed.add_field(name="Uptime", value=format_uptime(time.time() - self.start_time), inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Memory", value=f"{memory:.1f} MB" if memory else "N/A", inline=True)
        embed.add_field(name="CPU", value=f"{cpu:.1f}%" if cpu else "N/A", inline=True)
        embed.add_field(name="Active Sessions", value=str(len(sessions)), inline=True)
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)), inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="cleanup")
    @is_owner()
    async def cleanup(self, ctx: commands.Context):
        """Force cleanup stale sessions."""
        count = await session_manager.cleanup_stale(0)  # remove all idle
        await ctx.send(f"🧹 Cleaned up {count} session(s)")

    @commands.command(name="db")
    @is_owner()
    async def user_db_stats(self, ctx: commands.Context, user_id: int):
        """Show user database stats. Usage: .db <user_id>"""
        db = UserDatabase(user_id)
        await db.init()
        stats = await db.get_stats()
        await ctx.send(f"📊 **User {user_id} DB:**\n"
                       f"Messages: {stats['messages']}/{stats['max_messages']}\n"
                       f"Files: {stats['files']}\n"
                       f"Tokens: {stats['tokens']}")

    @commands.command(name="export")
    @is_owner()
    async def export_user_db(self, ctx: commands.Context, user_id: int):
        """Export a user's database as file."""
        db = UserDatabase(user_id)
        await db.init()
        data = await db.export()
        file = discord.File(io.BytesIO(data), filename=f"user_{user_id}.db")
        await ctx.send(f"📦 User {user_id} database:", file=file)

    @commands.command(name="config")
    @is_owner()
    async def show_config(self, ctx: commands.Context):
        """Show current bot configuration."""
        config_vars = {
            "Session Timeout": f"{Config.SESSION_TIMEOUT}s",
            "Max Messages": str(Config.MAX_MESSAGES_PER_USER),
            "Restart Interval": f"{Config.RESTART_INTERVAL}s ({Config.RESTART_INTERVAL//3600}h)",
            "Model": Config.MODEL,
            "Temperature": str(Config.TEMPERATURE),
            "Owners": str(Config.OWNER_IDS),
        }
        embed = discord.Embed(title="⚙️ Bot Configuration", color=0x3498DB)
        for k, v in config_vars.items():
            embed.add_field(name=k, value=v, inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="logs")
    @is_owner()
    async def tail_logs(self, ctx: commands.Context, lines: int = 20):
        """Show recent log lines. Usage: .logs [lines=20]"""
        log_file = Path("bot.log")
        if not log_file.exists():
            return await ctx.send("No log file found.")
        with open(log_file) as f:
            all_lines = f.readlines()
            recent = all_lines[-min(lines, len(all_lines)):]
        await ctx.send(f"📋 Last {len(recent)} log lines:\n```\n{''.join(recent)[:1500]}\n```")

    @commands.command(name="maintenance")
    @is_owner()
    async def toggle_maintenance(self, ctx: commands.Context):
        """Toggle maintenance mode."""
        current = os.getenv("MAINTENANCE_MODE", "false")
        new_val = "false" if current == "true" else "true"
        os.environ["MAINTENANCE_MODE"] = new_val
        await ctx.send(f"🛠️ Maintenance mode: **{new_val}**")

    @commands.command(name="reset")
    @is_owner()
    async def reset_user(self, ctx: commands.Context, user_id: int):
        """Reset a user's conversation. Usage: .reset <user_id>"""
        db = UserDatabase(user_id)
        await db.init()
        async with db._lock:
            import aiosqlite
            async with aiosqlite.connect(db.db_path) as conn:
                await conn.execute("DELETE FROM messages")
                await conn.commit()
        await session_manager.remove(user_id)
        await ctx.send(f"✅ User `{user_id}` reset (messages cleared, session killed)")

    @commands.command(name="setowner")
    @is_owner()
    async def set_owner(self, ctx: commands.Context, user_id: int):
        """Add an owner. Usage: .setowner <user_id>"""
        if user_id not in Config.OWNER_IDS:
            Config.OWNER_IDS.append(user_id)
        await ctx.send(f"✅ Owner added: `{user_id}`")

    @commands.command(name="reload")
    @is_owner()
    async def reload_cog(self, ctx: commands.Context, cog: str):
        """Reload a cog. Usage: .reload <cog_name>"""
        try:
            await self.bot.reload_extension(f"bot.cogs.{cog}")
            await ctx.send(f"Reloaded `{cog}`")
        except Exception as e:
            await ctx.send(f"Failed: {e}")

    # ─── Crypto / Gift Commands ──────────────────────────────

    @commands.command(name="activate")
    @is_owner()
    async def activate_plan(self, ctx: commands.Context, user_id: int):
        """Activate a pending plan for a user. Usage: .activate <user_id>"""
        db = UserDatabase(user_id)
        await db.init()
        pending = await db.get_config("pending_plan")
        if not pending:
            return await ctx.send(f"No pending plan for user `{user_id}`.")
        plan = Config.PLANS.get(pending)
        if not plan:
            return await ctx.send(f"Invalid pending plan `{pending}`.")
        expires = int(time.time()) + (plan["duration_days"] * 86400)
        await db.set_config("plan", pending)
        await db.set_config("plan_expires", str(expires))
        await db.set_config("pending_plan", "")
        
        # Notify channel manager to hide #plans
        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(user_id, pending)
            except Exception as e:
                logger.warning(f"activate callback error: {e}")
        
        await ctx.send(f"Plan `{pending}` activated for user `{user_id}` until <t:{expires}:f>.")

    @commands.command(name="deactivate")
    @is_owner()
    async def deactivate_plan(self, ctx: commands.Context, user_id: int):
        """Deactivate a user's paid plan (back to free). Usage: .deactivate <user_id>"""
        db = UserDatabase(user_id)
        await db.init()
        await db.set_config("plan", "free")
        await db.set_config("plan_expires", "0")
        
        # Notify channel manager to show #plans again
        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(user_id, "free")
            except Exception as e:
                logger.warning(f"deactivate callback error: {e}")
        
        await ctx.send(f"User `{user_id}` reverted to free plan.")

    @commands.command(name="redeem")
    @is_owner()
    async def redeem_gift(self, ctx: commands.Context, gift_code: str, user_id: int):
        """Manually redeem a gift code for a user. Usage: .redeem <code> <user_id>"""
        db = UserDatabase(user_id)
        await db.init()

        # Look up the gift
        gift_raw = await db.get_config(f"gift_{gift_code.upper()}")
        if not gift_raw:
            return await ctx.send(f"No gift code `{gift_code}` found for user `{user_id}`.")

        try:
            gift = json.loads(gift_raw)
        except:
            return await ctx.send("Invalid gift data.")

        if gift.get("redeemed"):
            return await ctx.send(f"Gift `{gift_code}` was already redeemed.")

        plan_id = gift.get("plan", "7d")
        plan = Config.PLANS.get(plan_id, Config.PLANS["7d"])
        expires = int(time.time()) + (plan["duration_days"] * 86400)

        await db.set_config("plan", plan_id)
        await db.set_config("plan_expires", str(expires))

        gift["redeemed"] = True
        gift["redeemed_at"] = int(time.time())
        gift["redeemed_by"] = user_id
        await db.set_config(f"gift_{gift_code.upper()}", json.dumps(gift))

        # Notify channel manager to hide #plans
        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(user_id, plan_id)
            except Exception as e:
                logger.warning(f"redeem callback error: {e}")

        await ctx.send(
            f"Gift `{gift_code}` redeemed for user `{user_id}` - "
            f"**{plan['description']}** plan activated until <t:{expires}:f>."
        )

        # Notify user
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(
                f"🎉 Your gift code `{gift_code}` was redeemed by an admin!\n"
                f"**{plan['description']}** plan is now active."
            )
        except:
            pass

    @commands.command(name="approve")
    @is_owner()
    async def approve_payment(self, ctx: commands.Context, user_id: int, plan_id: str):
        """Manually approve a payment and activate plan. Usage: .approve <user_id> <plan_id>"""
        plan = Config.PLANS.get(plan_id)
        if not plan:
            return await ctx.send(f"Invalid plan `{plan_id}`.")
        db = UserDatabase(user_id)
        await db.init()
        expires = int(time.time()) + (plan["duration_days"] * 86400)
        await db.set_config("plan", plan_id)
        await db.set_config("plan_expires", str(expires))
        await db.set_config("pending_plan", "")
        
        # Notify channel manager to hide #plans
        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(user_id, plan_id)
            except Exception as e:
                logger.warning(f"approve callback error: {e}")
        
        await ctx.send(
            f"Payment approved for user `{user_id}`. "
            f"Plan `{plan_id}` activated until <t:{expires}:f>."
        )

    @commands.command(name="gifts")
    @is_owner()
    async def list_gifts(self, ctx: commands.Context):
        """List pending gift codes."""
        sessions = await session_manager.get_all()
        pending = 0
        codes = []
        for uid in sessions:
            db = UserDatabase(uid)
            await db.init()
            gift = await db.get_config("pending_gift")
            if gift:
                pending += 1
                codes.append(f"• `{uid}` -> `{gift}`")
        if codes:
            await ctx.send(f"**{pending} pending gift(s):**\n" + "\n".join(codes[:20]))
        else:
            await ctx.send("No pending gifts.")

    @commands.command(name="giftlookup")
    @is_owner()
    async def gift_lookup(self, ctx: commands.Context, gift_code: str, user_id: int):
        """Look up a gift code details. Usage: .giftlookup <code> <user_id>"""
        db = UserDatabase(user_id)
        await db.init()
        gift_raw = await db.get_config(f"gift_{gift_code.upper()}")
        if not gift_raw:
            return await ctx.send(f"No gift `{gift_code}` for user `{user_id}`.")
        try:
            gift = json.loads(gift_raw)
            plan = gift.get("plan", "?")
            sender = gift.get("sender", "?")
            redeemed = gift.get("redeemed", False)
            created = gift.get("created", 0)
            status = "✅ Redeemed" if redeemed else "⏳ Pending"
            await ctx.send(
                f"**Gift `{gift_code}`**\n"
                f"Plan: {plan}\n"
                f"Sender: {sender}\n"
                f"Recipient: {user_id}\n"
                f"Status: {status}\n"
                f"Created: <t:{created}:R>"
            )
        except:
            await ctx.send("Invalid gift data.")

    @commands.command(name="crypto")
    @is_owner()
    async def crypto_addresses(self, ctx: commands.Context):
        """Show configured crypto addresses."""
        plan = Config.PLANS.get("7d", {})
        addrs = plan.get("crypto_addresses", {})
        lines = ["Crypto addresses (example from 7d plan):"]
        for coin, addr in addrs.items():
            name = Config.CRYPTO_NAMES.get(coin, coin.upper())
            lines.append(f"  {name}: `{addr}`")
        await ctx.send("\n".join(lines))

    @commands.command(name="expire")
    @is_owner()
    async def check_expiry(self, ctx: commands.Context, user_id: int):
        """Check when a user's plan expires. Usage: .expire <user_id>"""
        db = UserDatabase(user_id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        expires = await db.get_config("plan_expires") or "0"
        exp_int = int(expires)
        if exp_int > time.time():
            from datetime import datetime
            dt = datetime.fromtimestamp(exp_int)
            await ctx.send(f"User `{user_id}`: Plan `{plan}` expires at {dt} (<t:{exp_int}:R>).")
        else:
            await ctx.send(f"User `{user_id}`: Plan `{plan}` (no active expiration).")

    # ─── Role Commands ────────────────────────────────────────

    @commands.command(name="roles")
    @is_owner()
    async def check_roles(self, ctx: commands.Context, user_id: int = None):
        """Check a user's roles. Usage: .roles [user_id]"""
        target_id = user_id or ctx.author.id
        member = ctx.guild.get_member(target_id)
        if not member:
            return await ctx.send(f"User `{target_id}` not found in this guild.")

        roles = [r.name for r in member.roles if r.name != "@everyone"]
        patron = discord.utils.get(ctx.guild.roles, name=Config.ROLE_PATRON)
        rich = discord.utils.get(ctx.guild.roles, name=Config.ROLE_RICH)
        booster = discord.utils.get(ctx.guild.roles, name=Config.ROLE_BOOSTER)

        lines = [f"**Roles for {member.display_name}** (`{target_id}`):"]
        lines.append(f"• Patron: {'✅' if patron in member.roles else '❌'}")
        lines.append(f"• Rich: {'✅' if rich in member.roles else '❌'}")
        lines.append(f"• Booster: {'✅' if booster in member.roles else '❌'}")
        lines.append(f"\nAll roles: {', '.join(roles) if roles else 'None'}")

        await ctx.send("\n".join(lines))

    @commands.command(name="assign")
    @is_owner()
    async def assign_role(self, ctx: commands.Context, user_id: int, role_name: str):
        """Manually assign a role. Usage: .assign <user_id> Patron|Rich|Booster"""
        valid_roles = [Config.ROLE_PATRON, Config.ROLE_RICH, Config.ROLE_BOOSTER]
        if role_name not in valid_roles:
            return await ctx.send(f"❌ Role must be one of: {', '.join(valid_roles)}")

        member = ctx.guild.get_member(user_id)
        if not member:
            return await ctx.send(f"User `{user_id}` not found.")

        role = discord.utils.get(ctx.guild.roles, name=role_name)
        if not role:
            return await ctx.send(f"Role `{role_name}` not found.")

        if role in member.roles:
            return await ctx.send(f"User already has `{role_name}`.")

        await member.add_roles(role, reason=f"Manual assignment by owner")
        await ctx.send(f"✅ Assigned `{role_name}` to {member.display_name}")

    @commands.command(name="unassign")
    @is_owner()
    async def unassign_role(self, ctx: commands.Context, user_id: int, role_name: str):
        """Remove a role from a user. Usage: .unassign <user_id> Patron|Rich|Booster"""
        valid_roles = [Config.ROLE_PATRON, Config.ROLE_RICH, Config.ROLE_BOOSTER]
        if role_name not in valid_roles:
            return await ctx.send(f"❌ Role must be one of: {', '.join(valid_roles)}")

        member = ctx.guild.get_member(user_id)
        if not member:
            return await ctx.send(f"User `{user_id}` not found.")

        role = discord.utils.get(ctx.guild.roles, name=role_name)
        if not role:
            return await ctx.send(f"Role `{role_name}` not found.")

        if role not in member.roles:
            return await ctx.send(f"User doesn't have `{role_name}`.")

        await member.remove_roles(role, reason=f"Manual removal by owner")
        await ctx.send(f"✅ Removed `{role_name}` from {member.display_name}")

    @commands.command(name="checkroles")
    @is_owner()
    async def check_all_roles(self, ctx: commands.Context):
        """Check all users and assign correct roles."""
        await ctx.send("🔄 Scanning all users for role eligibility...")
        assigned = 0

        from bot.cogs.roles import RoleManager
        rm = self.bot.get_cog("RoleManager")
        if not rm:
            return await ctx.send("RoleManager cog not loaded.")

        for member in ctx.guild.members:
            if member.bot:
                continue
            db = UserDatabase(member.id)
            await db.init()
            plan = await db.get_config("plan") or "free"
            if plan != "free":
                await rm.assign_patron(member)
                assigned += 1

            is_rich = await rm._check_rich_eligibility(db, member.id)
            if is_rich:
                await rm.assign_rich(member)
                assigned += 1

        await ctx.send(f"✅ Scan complete. Updated {assigned} role assignment(s).")

    # ─── Info Commands ────────────────────────────────────────

    @commands.command(name="whois")
    @is_owner()
    async def whois(self, ctx: commands.Context, user_id: int):
        """Full user info. Usage: .whois <user_id>"""
        member = ctx.guild.get_member(user_id)
        db = UserDatabase(user_id)
        await db.init()

        plan = await db.get_config("plan") or "free"
        expires = await db.get_config("plan_expires") or "0"
        exp_int = int(expires)
        temp = await db.get_config("temperature") or "0.8"
        tokens = await db.get_config("max_tokens") or "4096"
        stats = await db.get_stats()

        lines = [f"**User Info: `{user_id}`**"]
        if member:
            lines.append(f"• Name: {member.display_name}#{member.discriminator}")
            lines.append(f"• Joined: <t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "• Joined: Unknown")
            roles = [r.name for r in member.roles if r.name != "@everyone"]
            lines.append(f"• Roles: {', '.join(roles) if roles else 'None'}")
            lines.append(f"• Boosting: {'Yes' if member.premium_since else 'No'}")

        lines.append(f"\n**Plan:** {plan}")
        if exp_int > time.time():
            lines.append(f"• Expires: <t:{exp_int}:R>")
        lines.append(f"• Temperature: {temp}")
        lines.append(f"• Max Tokens: {tokens}")
        lines.append(f"• Messages: {stats['messages']}")
        lines.append(f"• Files: {stats['files']}")

        await ctx.send("\n".join(lines))

    @commands.command(name="alldb")
    @is_owner()
    async def all_users_db(self, ctx: commands.Context):
        """List all user databases."""
        db_dir = Config.DB_DIR
        if not db_dir.exists():
            return await ctx.send("No user databases found.")

        files = list(db_dir.glob("user_*.db"))
        if not files:
            return await ctx.send("No user databases found.")

        lines = [f"**{len(files)} user database(s):**"]
        for f in sorted(files)[:30]:
            uid = f.stem.replace("user_", "")
            size = f.stat().st_size / 1024
            lines.append(f"• `{uid}` — {size:.1f}KB")

        if len(files) > 30:
            lines.append(f"... and {len(files) - 30} more")

        await ctx.send("\n".join(lines))

    @commands.command(name="massbroadcast")
    @is_owner()
    async def mass_broadcast(self, ctx: commands.Context, *, message: str):
        """Broadcast to ALL users with a DM. Usage: .massbroadcast <message>"""
        db_dir = Config.DB_DIR
        if not db_dir.exists():
            return await ctx.send("No user databases found.")

        files = list(db_dir.glob("user_*.db"))
        sent = 0
        failed = 0

        for f in files:
            uid = f.stem.replace("user_", "")
            try:
                user = await self.bot.fetch_user(int(uid))
                await user.send(f"📢 **Kaufy Announcement:**\n{message}")
                sent += 1
            except:
                failed += 1

        await ctx.send(f"📢 Broadcast sent: {sent} delivered, {failed} failed")


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerCog(bot))
