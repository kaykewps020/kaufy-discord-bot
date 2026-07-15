"""Owner commands cog — full hierarchy: SUPER OWNER > Owner > User.

Super Owner (hardcoded 1519459793876680844): access to EVERYTHING.
Normal Owners (added via .setowner): access to MOST things but NOT critical ones.

All commands are HYBRID — work as .cmd AND /cmd.
"""
import discord
from discord.ext import commands
import asyncio
import json
import logging
import os
import sys
import time
import io
import re
from pathlib import Path
from typing import Optional
from bot.config import Config
from bot.models.session import session_manager
from bot.models.user_db import UserDatabase
from bot.models.key_db import key_db
from bot.services.backup import backup_service
from bot.utils.helpers import format_uptime

logger = logging.getLogger("kaufy.owner")


# ─── Converters ──────────────────────────────────────────────

class UserID(commands.Converter):
    """Accepts raw ID OR mention `<@123>` / `<@!123>`."""
    async def convert(self, ctx: commands.Context, argument: str) -> int:
        arg = argument.strip()
        if arg.isdigit() or (arg.startswith("-") and arg[1:].isdigit()):
            return int(arg)
        if arg.startswith("<@") and arg.endswith(">"):
            cleaned = arg.strip("<@!>")
            if cleaned.isdigit():
                return int(cleaned)
        raise commands.BadArgument(f"`{argument}` is not a valid ID. Use numeric ID or mention.")


# ─── Permission Checks ──────────────────────────────────────

def is_super_owner():
    """SUPER OWNER only — can do absolutely everything."""
    async def predicate(ctx: commands.Context):
        from bot.services.owner_auth import is_super_owner_id
        ok = is_super_owner_id(ctx.author.id)
        if not ok:
            await ctx.reply("❌ This command is restricted to the **Super Owner** only.", ephemeral=True)
        return ok
    return commands.check(predicate)


def is_owner(*, via_secret: bool = False):
    """Owner-level check — includes super owner AND normal owners.
    
    via_secret=True: also need .ownerauth secret (for extra-sensitive ops).
    """
    from bot.services.owner_auth import owner_auth

    async def predicate(ctx: commands.Context):
        ok = await owner_auth.is_owner(ctx.author.id, via_secret=via_secret)
        if not ok:
            if via_secret:
                await ctx.reply("🔒 This command requires authentication. Run `.ownerauth <your_secret>` first.", ephemeral=True)
            else:
                await ctx.reply("❌ Only bot owners can use this command.", ephemeral=True)
        return ok
    return commands.check(predicate)


# ─── Owner Cog ────────────────────────────────────────────────

class OwnerCog(commands.Cog):
    """Owner utility commands — hierarchy: Super Owner > Owner > User."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()
        # Snipe cache: channel_id -> [list of deleted message dicts]
        self._snipe_cache: dict[int, list[dict]] = {}
        self._snipe_max_per_channel = 50
        # Snipedit cache: channel_id -> [list of edited message dicts]
        self._snipedits_cache: dict[int, list[dict]] = {}

    # ═══════════════════════════════════════════════════════════
    # SNIPE — deleted message viewer
    # ═══════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Cache deleted messages for snipe command."""
        if message.author.bot:
            return
        entry = {
            "id": message.id,
            "author_id": message.author.id,
            "author_name": str(message.author),
            "content": message.content or "[no text content]",
            "attachments": [a.url for a in message.attachments],
            "timestamp": message.created_at.isoformat(),
            "channel_id": message.channel.id,
        }
        cid = message.channel.id
        if cid not in self._snipe_cache:
            self._snipe_cache[cid] = []
        self._snipe_cache[cid].insert(0, entry)
        # Trim
        while len(self._snipe_cache[cid]) > self._snipe_max_per_channel:
            self._snipe_cache[cid].pop()

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Cache edited messages for snipe edit command."""
        if before.author.bot or before.content == after.content:
            return
        entry = {
            "id": before.id,
            "author_id": before.author.id,
            "author_name": str(before.author),
            "before": before.content or "[no text]",
            "after": after.content or "[no text]",
            "timestamp": time.time(),
            "channel_id": before.channel.id,
            "jump_url": before.jump_url,
        }
        cid = before.channel.id
        if cid not in self._snipedits_cache:
            self._snipedits_cache[cid] = []
        self._snipedits_cache[cid].insert(0, entry)
        while len(self._snipedits_cache[cid]) > self._snipe_max_per_channel:
            self._snipedits_cache[cid].pop()

    @commands.hybrid_command(name="snipe")
    async def snipe(self, ctx: commands.Context, index: int = 1):
        """View recently deleted messages in this channel.
        
        Usage: .snipe [index=1]  — shows the most recent deleted message
               .snipe 2          — shows the 2nd most recent
        """
        cid = ctx.channel.id
        cache = self._snipe_cache.get(cid, [])
        if not cache:
            return await ctx.send("No recently deleted messages in this channel.")
        if index < 1 or index > len(cache):
            return await ctx.send(f"Invalid index. There are {len(cache)} cached deletions. Use 1-{len(cache)}.")

        entry = cache[index - 1]
        embed = discord.Embed(
            title=f"🗑️ Deleted Message #{index}",
            color=0xE74C3C,
            timestamp=discord.utils.parse_time(entry["timestamp"]) if isinstance(entry["timestamp"], str) else discord.utils.snowflake_time(entry["id"])
        )
        embed.set_author(name=entry["author_name"])
        embed.add_field(name="Content", value=entry["content"][:1024], inline=False)

        if entry["attachments"]:
            embed.add_field(name="Attachments", value="\n".join(entry["attachments"][:5]), inline=False)

        try:
            user = await self.bot.fetch_user(entry["author_id"])
            embed.set_footer(text=f"ID: {entry['author_id']}", icon_url=user.display_avatar.url)
        except:
            embed.set_footer(text=f"ID: {entry['author_id']}")

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="snipedits")
    async def snipedits(self, ctx: commands.Context, index: int = 1):
        """View recently edited messages in this channel.
        
        Usage: .snipedits [index=1]
        """
        cid = ctx.channel.id
        cache = self._snipedits_cache.get(cid, [])
        if not cache:
            return await ctx.send("No recently edited messages in this channel.")
        if index < 1 or index > len(cache):
            return await ctx.send(f"Invalid index. There are {len(cache)} cached edits. Use 1-{len(cache)}.")

        entry = cache[index - 1]
        embed = discord.Embed(
            title=f"✏️ Edited Message #{index}",
            color=0xF39C12,
        )
        embed.set_author(name=entry["author_name"])
        embed.add_field(name="Before", value=entry["before"][:1024], inline=False)
        embed.add_field(name="After", value=entry["after"][:1024], inline=False)
        embed.add_field(name="Jump", value=f"[Click to view]({entry['jump_url']})", inline=False)
        await ctx.send(embed=embed)

    # ═══════════════════════════════════════════════════════════
    # KEY MANAGEMENT — /key gen, list, revoke, stats
    # ═══════════════════════════════════════════════════════════

    @commands.group(name="key", invoke_without_command=True)
    @is_owner()
    async def key_group(self, ctx: commands.Context):
        """Manage activation keys. Use: .key gen|list|revoke|stats"""
        await ctx.send("Available subcommands: `gen`, `list`, `revoke`, `stats`. Use `.key <subcommand>` or `/key <subcommand>`.")

    @key_group.command(name="gen")
    @is_owner()
    async def key_gen(
        self, ctx: commands.Context,
        plan: str = "30d",
        duration_days: int = 30,
        max_uses: int = 1,
        custom_code: Optional[str] = None
    ):
        """Generate one or more activation keys.
        
        Args:
            plan: Plan to activate (free, 7d, 14d, 30d, lifetime)
            duration_days: How many days the plan lasts (default: 30)
            max_uses: How many times this key can be used (default: 1)
            custom_code: Optional custom key code (default: auto-generated)
        """
        valid = list(Config.PLANS.keys())
        if plan not in valid:
            return await ctx.send(f"❌ Invalid plan. Must be one of: {', '.join(valid)}")

        code = await key_db.create_key(
            plan=plan,
            duration_days=duration_days,
            max_uses=max_uses,
            created_by=ctx.author.id,
            custom_code=custom_code,
        )
        embed = discord.Embed(
            title="🔑 Key Generated",
            color=0x2ECC71,
        )
        embed.add_field(name="Code", value=f"`{code}`", inline=False)
        embed.add_field(name="Plan", value=plan, inline=True)
        embed.add_field(name="Duration", value=f"{duration_days} days", inline=True)
        embed.add_field(name="Max Uses", value=str(max_uses), inline=True)
        await ctx.send(embed=embed)

    @key_group.command(name="list")
    @is_owner()
    async def key_list(self, ctx: commands.Context, page: int = 0):
        """List all activation keys. Usage: .key list [page=0]"""
        keys = await key_db.list_keys(page=page)
        if not keys:
            return await ctx.send("No keys found.")

        lines = [f"🔑 **Keys (page {page+1}):**"]
        for k in keys:
            status = "❌ REVOKED" if k["revoked"] else f"✅ {k['uses']}/{k['max_uses']}"
            lines.append(f"`{k['code']}` — {k['plan']} — {status}")
        await ctx.send("\n".join(lines[:20]))

    @key_group.command(name="revoke")
    @is_owner()
    async def key_revoke(self, ctx: commands.Context, code: str):
        """Revoke an activation key. Usage: .key revoke <code>"""
        code = code.strip().upper()
        ok = await key_db.revoke_key(code)
        if ok:
            await ctx.send(f"❌ Key `{code}` has been revoked.")
        else:
            await ctx.send(f"Key `{code}` not found.")

    @key_group.command(name="stats")
    @is_owner()
    async def key_stats(self, ctx: commands.Context):
        """Show key system statistics."""
        stats = await key_db.get_stats()
        embed = discord.Embed(title="🔑 Key System Stats", color=0x9B59B6)
        embed.add_field(name="Total Keys", value=str(stats["total_keys"]), inline=True)
        embed.add_field(name="Active Keys", value=str(stats["active_keys"]), inline=True)
        embed.add_field(name="Revoked Keys", value=str(stats["revoked_keys"]), inline=True)
        embed.add_field(name="Total Redemptions", value=str(stats["total_redemptions"]), inline=True)
        await ctx.send(embed=embed)

    # ═══════════════════════════════════════════════════════════
    # /REDEEM — user-facing key redemption
    # ═══════════════════════════════════════════════════════════

    @commands.hybrid_command(name="redeem")
    async def redeem(self, ctx: commands.Context, code: str):
        """Redeem an activation key to unlock a plan.
        
        Usage: /redeem <key-code>
        You can use this in DMs or in any channel the bot can see.
        """
        code = code.strip().upper()
        result = await key_db.redeem_key(code, ctx.author.id)

        if result is None:
            return await ctx.send("❌ Invalid key. Check the code and try again.", ephemeral=True)

        if "error" in result:
            return await ctx.send(f"❌ {result['error']}", ephemeral=True)

        # Activate plan
        plan_id = result["plan"]
        duration = result["duration_days"]
        expires = int(time.time()) + (duration * 86400)

        db = UserDatabase(ctx.author.id)
        await db.init()
        await db.set_config("plan", plan_id)
        await db.set_config("plan_expires", str(expires))

        embed = discord.Embed(
            title="🎉 Plan Activated!",
            description=f"You redeemed **{plan_id.upper()}** plan!",
            color=0x2ECC71,
        )
        embed.add_field(name="Duration", value=f"{duration} days", inline=True)
        embed.add_field(name="Expires", value=f"<t:{expires}:R>", inline=True)

        # Notify channel manager
        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(ctx.author.id, plan_id)
            except Exception as e:
                logger.warning(f"redeem callback error: {e}")

        await ctx.send(embed=embed)

        # Try to send DM
        try:
            await ctx.author.send(f"🎉 Your **{plan_id.upper()}** plan is now active! Enjoy Kaufy's features.")
        except:
            pass

    # ═══════════════════════════════════════════════════════════
    # SUPER OWNER COMMANDS — critical, can break the bot
    # ═══════════════════════════════════════════════════════════

    @commands.hybrid_command(name="eval")
    @is_super_owner()
    async def eval_code(self, ctx: commands.Context, *, code: str):
        """🔴 Evaluate Python expression. SUPER OWNER ONLY."""
        try:
            result = eval(code)
            await ctx.send(f"```py\n{result}\n```")
        except Exception as e:
            await ctx.send(f"```py\nError: {e}\n```")

    @commands.hybrid_command(name="exec")
    @is_super_owner()
    async def exec_shell(self, ctx: commands.Context, *, cmd: str):
        """🔴 Execute a shell command. SUPER OWNER ONLY."""
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

    @commands.hybrid_command(name="shell")
    @is_super_owner()
    async def shell_cmd(self, ctx: commands.Context, *, cmd: str):
        """🔴 Execute shell command (alias for exec). SUPER OWNER ONLY."""
        await self.exec_shell(ctx, cmd=cmd)

    @commands.hybrid_command(name="sql")
    @is_super_owner()
    async def sql_query(self, ctx: commands.Context, user_id: Optional[int] = None, *, query: str = None):
        """🔴 Run raw SQL on a user database. SUPER OWNER ONLY.
        
        Usage: .sql <user_id> <query>
        Example: .sql 123456789 SELECT * FROM messages LIMIT 5
        """
        if user_id is None:
            return await ctx.send("Usage: `.sql <user_id> <query>` — run raw SQL on a user's database.")
        if query is None:
            # If the command was called as .sql "SELECT..." without user_id
            if user_id and not str(user_id).isdigit():
                query = str(user_id)
                user_id = None
            if not query:
                return await ctx.send("Usage: `.sql <user_id> <query>`")

        # Find the database
        db_path = Config.DB_DIR / f"user_{user_id}.db"
        if not db_path.exists():
            return await ctx.send(f"❌ No database found for user `{user_id}`.")

        try:
            import aiosqlite
            async with aiosqlite.connect(str(db_path)) as conn:
                conn.row_factory = aiosqlite.Row
                cursor = await conn.execute(query)
                if query.strip().upper().startswith("SELECT"):
                    rows = await cursor.fetchall()
                    if rows:
                        # Format as table
                        cols = rows[0].keys()
                        header = " | ".join(cols)
                        lines = [header, "-" * len(header)]
                        for r in rows[:20]:
                            vals = [str(r[c])[:30] for c in cols]
                            lines.append(" | ".join(vals))
                        result = "\n".join(lines)
                        if len(rows) > 20:
                            result += f"\n... and {len(rows) - 20} more rows"
                    else:
                        result = "Query returned no results."
                else:
                    await conn.commit()
                    result = f"✅ Query executed. Rows affected: {cursor.rowcount}"
            await ctx.send(f"```\n{result[:1900]}\n```")
        except Exception as e:
            await ctx.send(f"```\nError: {e}\n```")

    @commands.hybrid_command(name="shutdown")
    @is_super_owner()
    async def shutdown(self, ctx: commands.Context):
        """🔴 Shutdown the bot permanently. SUPER OWNER ONLY."""
        await ctx.send("🛑 Shutting down permanently...")
        logger.info("Permanent shutdown initiated by super owner")
        await backup_service.backup_all()
        flag_path = Path(__file__).parent.parent.parent / ".shutdown_flag"
        flag_path.touch()
        await asyncio.sleep(1)
        await self.bot.close()
        sys.exit(0)

    @commands.hybrid_command(name="reload")
    @is_super_owner()
    async def reload_cog(self, ctx: commands.Context, cog: str):
        """🔴 Reload a cog. SUPER OWNER ONLY. Usage: .reload <cog_name>"""
        try:
            await self.bot.reload_extension(f"bot.cogs.{cog}")
            await ctx.send(f"✅ Reloaded `{cog}`")
        except Exception as e:
            await ctx.send(f"❌ Failed: {e}")

    @commands.hybrid_command(name="maintenance")
    @is_super_owner()
    async def toggle_maintenance(self, ctx: commands.Context):
        """🔴 Toggle maintenance mode. SUPER OWNER ONLY."""
        current = os.getenv("MAINTENANCE_MODE", "false")
        new_val = "false" if current == "true" else "true"
        os.environ["MAINTENANCE_MODE"] = new_val
        await ctx.send(f"🛠️ Maintenance mode: **{new_val}**")

    # ═══════════════════════════════════════════════════════════
    # OWNER COMMANDS — available to all owners (including normal)
    # ═══════════════════════════════════════════════════════════

    @commands.hybrid_command(name="sudo")
    @is_owner()
    async def sudo_cmd(self, ctx: commands.Context, user: UserID, *, command: str):
        """Execute a command as if you were another user.
        
        Usage: .sudo <user_id> <command text>
        This sends the command text to the AI as if it came from that user.
        """
        db = UserDatabase(user)
        await db.init()

        # Find their msg channel
        guild = self.bot.get_guild(Config.GUILD_ID)
        if guild:
            member = guild.get_member(user)
            if member:
                cat_name = f"Kaufy's Chat - {member.display_name}"
                category = discord.utils.get(guild.categories, name=cat_name)
                if category:
                    for ch in category.channels:
                        if ch.name.startswith("msg-"):
                            # Send message as that user
                            webhook = None
                            try:
                                webhook = await ch.create_webhook(name="Kaufy Sudo")
                                await webhook.send(
                                    content=command,
                                    username=member.display_name,
                                    avatar_url=member.display_avatar.url,
                                )
                            except:
                                pass
                            finally:
                                if webhook:
                                    await webhook.delete()
                            await ctx.send(f"✅ Command sent as user `{user}`")
                            return

        # Fallback: just process directly
        from bot.models.session import session_manager as sm
        session = await sm.get_or_create(user, 0)
        await ctx.send(f"✅ Sudo command registered for user `{user}`")

    @commands.hybrid_command(name="userinfo")
    @is_owner()
    async def userinfo(self, ctx: commands.Context, user: UserID = None):
        """Show detailed info about a user.
        
        Usage: .userinfo <user_id>
        Shows plan, messages, tokens, keys used, etc.
        """
        target_id = user or ctx.author.id
        member = None
        guild = self.bot.get_guild(Config.GUILD_ID)
        if guild:
            member = guild.get_member(target_id)

        db = UserDatabase(target_id)
        await db.init()

        plan = await db.get_config("plan") or "free"
        expires = await db.get_config("plan_expires") or "0"
        exp_int = int(expires)
        temp = await db.get_config("temperature") or "0.8"
        max_tokens = await db.get_config("max_tokens") or "4096"
        blacklisted = await db.get_config("blacklisted") or "false"
        custom_prompt = await db.get_config("custom_prompt") or ""
        stats = await db.get_stats()
        daily_count = await db.get_daily_count()

        embed = discord.Embed(
            title=f"👤 User Info: {target_id}",
            color=0x3498DB,
        )
        if member:
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            embed.add_field(name="Display Name", value=member.display_name, inline=True)
            roles = [r.name for r in member.roles if r.name != "@everyone"]
            embed.add_field(name="Roles", value=", ".join(roles[:5]) if roles else "None", inline=True)
            embed.add_field(name="Boosting", value="✅ Yes" if member.premium_since else "❌ No", inline=True)

        embed.add_field(name="Plan", value=f"**{plan.upper()}**", inline=True)
        if exp_int > time.time():
            embed.add_field(name="Expires", value=f"<t:{exp_int}:R>", inline=True)
        else:
            embed.add_field(name="Expires", value="N/A", inline=True)
        embed.add_field(name="Daily Messages", value=f"{daily_count}/day", inline=True)
        embed.add_field(name="Total Messages", value=str(stats['messages']), inline=True)
        embed.add_field(name="Files", value=str(stats['files']), inline=True)
        embed.add_field(name="Temperature", value=temp, inline=True)
        embed.add_field(name="Max Tokens", value=max_tokens, inline=True)
        embed.add_field(name="Blacklisted", value=blacklisted, inline=True)
        if custom_prompt:
            embed.add_field(name="Custom Prompt", value=f"```{custom_prompt[:200]}```", inline=False)

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="blacklist")
    @is_owner()
    async def blacklist_user(self, ctx: commands.Context, user: UserID):
        """Blacklist a user from using the bot.
        
        Usage: .blacklist <user_id>
        """
        db = UserDatabase(user)
        await db.init()
        await db.set_config("blacklisted", "true")
        await session_manager.remove(user)
        await ctx.send(f"⛔ User `{user}` blacklisted from using Kaufy.")

    @commands.hybrid_command(name="whitelist")
    @is_owner()
    async def whitelist_user(self, ctx: commands.Context, user: UserID):
        """Remove a user from the blacklist.
        
        Usage: .whitelist <user_id>
        """
        db = UserDatabase(user)
        await db.init()
        await db.set_config("blacklisted", "false")
        await ctx.send(f"✅ User `{user}` whitelisted.")

    @commands.hybrid_command(name="serverlist")
    @is_owner()
    async def server_list(self, ctx: commands.Context):
        """List all servers the bot is in."""
        if not self.bot.guilds:
            return await ctx.send("The bot is not in any servers.")

        embed = discord.Embed(
            title="🌐 Servers",
            color=0x2ECC71,
        )
        for g in self.bot.guilds:
            member_count = g.member_count or len(g.members)
            embed.add_field(
                name=g.name,
                value=f"ID: `{g.id}` | Members: {member_count} | Owner: {g.owner_id}",
                inline=False,
            )
        embed.set_footer(text=f"Total: {len(self.bot.guilds)} server(s)")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="leaveserver")
    @is_owner()
    async def leave_server(self, ctx: commands.Context, guild_id: str):
        """Make the bot leave a server. Usage: .leaveserver <guild_id>"""
        try:
            gid = int(guild_id)
        except ValueError:
            return await ctx.send("❌ Invalid guild ID. Must be a number.")

        guild = self.bot.get_guild(gid)
        if not guild:
            return await ctx.send(f"❌ Not connected to guild `{gid}`.")

        await ctx.send(f"👋 Leaving `{guild.name}` ({gid})...")
        await guild.leave()

    @commands.hybrid_command(name="dm")
    @is_owner()
    async def dm_user(self, ctx: commands.Context, user: UserID, *, message: str):
        """Send a DM to any user. Usage: .dm <user_id> <message>"""
        try:
            target = await self.bot.fetch_user(user)
            await target.send(f"📨 **Kaufy Admin:** {message}")
            await ctx.send(f"✅ DM sent to `{user}`")
        except Exception as e:
            await ctx.send(f"❌ Failed to DM `{user}`: {e}")

    @commands.hybrid_command(name="broadcast")
    @is_owner()
    async def broadcast(self, ctx: commands.Context, *, message: str):
        """Broadcast a message to all users with active sessions.
        
        Usage: .broadcast <message>
        """
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

    @commands.hybrid_command(name="say")
    @is_owner()
    async def say_in_channel(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        """Make the bot say something in a specific channel.
        
        Usage: .say <#channel> <message>
        """
        try:
            await channel.send(message)
            await ctx.send(f"✅ Message sent to {channel.mention}")
        except Exception as e:
            await ctx.send(f"❌ Failed: {e}")

    @commands.hybrid_command(name="export")
    @is_owner()
    async def export_user_db(self, ctx: commands.Context, user: UserID):
        """Export a user's database as a file.
        
        Usage: .export <user_id>
        """
        db = UserDatabase(user)
        await db.init()
        data = await db.export()
        file = discord.File(io.BytesIO(data), filename=f"user_{user}.db")
        await ctx.send(f"📦 User `{user}` database:", file=file)

    # ═══════════════════════════════════════════════════════════
    # GENERAL COMMANDS — visible to everyone
    # ═══════════════════════════════════════════════════════════

    @commands.hybrid_command(name="ping")
    async def ping(self, ctx: commands.Context):
        """Check bot latency."""
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"🏓 Pong! **{latency}ms**")

    @commands.hybrid_command(name="uptime")
    async def uptime(self, ctx: commands.Context):
        """Show bot uptime."""
        uptime_str = format_uptime(time.time() - self.start_time)
        await ctx.send(f"⏱️ Uptime: **{uptime_str}**")

    @commands.hybrid_command(name="help")
    async def help_cmd(self, ctx: commands.Context):
        """Show all available commands."""
        from bot.services.owner_auth import is_super_owner_id, is_owner_id
        is_so = is_super_owner_id(ctx.author.id)
        is_own = is_owner_id(ctx.author.id)

        lines = ["**🤖 Kaufy Bot — Commands**\n"]

        # Everyone
        lines.append("**📋 General:**")
        lines.append("`/ping` — Bot latency")
        lines.append("`/uptime` — Bot uptime")
        lines.append("`/help` — Show this message")
        lines.append("`/redeem <code>` — Redeem activation key")
        lines.append("`/botinvite` — Invite Kaufy to your server")
        lines.append("`/snipe [index]` — View deleted messages")
        lines.append("`/snipedits [index]` — View edited messages")

        # Owner commands
        if is_own:
            lines.append("\n**👑 Owner Commands:**")
            lines.append("`/key gen` — Generate activation key")
            lines.append("`/key list` — List all keys")
            lines.append("`/key revoke <code>` — Revoke a key")
            lines.append("`/key stats` — Key system stats")
            lines.append("`/userinfo [user]` — Detailed user info")
            lines.append("`/blacklist <user>` — Blacklist user")
            lines.append("`/whitelist <user>` — Unblacklist user")
            lines.append("`/serverlist` — List servers")
            lines.append("`/leaveserver <id>` — Leave server")
            lines.append("`/dm <user> <msg>` — DM a user")
            lines.append("`/broadcast <msg>` — Broadcast to active users")
            lines.append("`/say <#channel> <msg>` — Say in channel")
            lines.append("`/export <user>` — Export user data")
            lines.append("`/setplan <user> <plan>` — Set user plan")
            lines.append("`/activate <user>` — Activate pending plan")
            lines.append("`/deactivate <user>` — Revert to free")
            lines.append("`/approve <user> <plan>` — Approve payment")
            lines.append("`/expire <user>` — Check plan expiry")
            lines.append("`/reset <user>` — Reset conversation")
            lines.append("`/setowner <user>` — Add normal owner")
            lines.append("`/status` — Bot status overview")
            lines.append("`/config` — Bot configuration")
            lines.append("`/sessions` — Active sessions")
            lines.append("`/kill <user>` — Kill session")
            lines.append("`/logs [lines]` — Recent logs")
            lines.append("`/backup` — Force backup")
            lines.append("`/restore [name]` — Restore backup")
            lines.append("`/backups` — List backups")
            lines.append("`/cleanup` — Clean stale sessions")
            lines.append("`/whois <user>` — Extended user info")
            lines.append("`/alldb` — List all user databases")

        # Super owner only
        if is_so:
            lines.append("\n**💀 Super Owner Commands:**")
            lines.append("`/eval <code>` — Evaluate Python")
            lines.append("`/exec <cmd>` — Execute shell command")
            lines.append("`/shell <cmd>` — Execute shell (alias)")
            lines.append("`/sql <user> <query>` — Run SQL on user DB")
            lines.append("`/shutdown` — Kill the bot")
            lines.append("`/reload <cog>` — Reload a cog")
            lines.append("`/maintenance` — Toggle maintenance mode")

        lines.append("\n*Kaufy's Hall — AI-Powered*")
        await ctx.send("\n".join(lines))

    # ═══════════════════════════════════════════════════════════
    # PREFIX-ONLY COMMANDS (not available as slash)
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="ownerauth")
    async def owner_auth_cmd(self, ctx: commands.Context, *, secret: str = ""):
        """Unlock privileged owner mode with owner secret.
        
        Usage: .ownerauth <secret>
        Required once per session for extra-sensitive commands.
        """
        from bot.services.owner_auth import owner_auth

        if ctx.author.id not in Config.OWNER_IDS and ctx.author.id != Config.SUPER_OWNER_ID:
            await ctx.reply("⛔ You are not registered as an owner.", delete_after=10)
            return

        if not secret:
            await ctx.reply("Usage: `.ownerauth <your_secret>`", delete_after=10)
            return

        ok = await owner_auth.authenticate(ctx.author.id, secret)
        if ok:
            await ctx.reply(
                "🔓 Owner privileges unlocked for this session (24h).",
                delete_after=20,
            )
        else:
            await ctx.reply("❌ Authentication failed.", delete_after=15)

        try:
            await ctx.message.delete()
        except:
            pass

    @commands.command(name="setplan")
    @is_owner()
    async def set_plan(self, ctx: commands.Context, user: UserID, plan: str):
        """Set a user's plan. Usage: .setplan <user_id> free|7d|14d|30d|lifetime"""
        valid_plans = list(Config.PLANS.keys())
        if plan not in valid_plans:
            return await ctx.send(f"❌ Must be one of: {', '.join(valid_plans)}")

        db = UserDatabase(user)
        await db.init()
        await db.set_config("plan", plan)
        if plan != "free":
            plan_config = Config.PLANS.get(plan, {})
            duration = plan_config.get("duration_days", 0)
            if duration > 0:
                expires = int(time.time()) + (duration * 86400)
                await db.set_config("plan_expires", str(expires))
        else:
            await db.set_config("plan_expires", "0")

        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(user, plan)
            except Exception as e:
                logger.warning(f"setplan callback error: {e}")

        await ctx.send(f"✅ User `{user}` plan set to **{plan}**")

    @commands.command(name="whois")
    @is_owner()
    async def whois(self, ctx: commands.Context, user: UserID):
        """Extended user info. Usage: .whois <user_id>"""
        await self.userinfo(ctx, user=user)

    @commands.command(name="sessions")
    @is_owner()
    async def list_sessions(self, ctx: commands.Context):
        """Show active sessions."""
        sessions = await session_manager.get_all()
        if not sessions:
            return await ctx.send("No active sessions.")

        lines = [f"📊 **{len(sessions)} Active Session(s):**"]
        for uid, s in sessions.items():
            idle = format_uptime(s.idle_seconds)
            lines.append(f"• `{uid}` | {s.plan} | idle: {idle}")
        await ctx.send("\n".join(lines))

    @commands.command(name="kill")
    @is_owner()
    async def kill_session(self, ctx: commands.Context, user: UserID):
        """Kill a user's session. Usage: .kill <user_id>"""
        session = await session_manager.get(user)
        if not session:
            return await ctx.send(f"❌ No session for user `{user}`")
        await session_manager.remove(user)
        await ctx.send(f"✅ Session killed for user `{user}`")

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
        embed.add_field(name="Super Owner", value=f"`{Config.SUPER_OWNER_ID}`", inline=True)
        embed.add_field(name="Owners", value=str(len(Config.OWNER_IDS)), inline=True)
        await ctx.send(embed=embed)

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
            "Workers": str(Config.MAX_CONCURRENT_WORKERS),
            "Super Owner": f"`{Config.SUPER_OWNER_ID}`",
            "Owners": ", ".join(f"`{o}`" for o in Config.OWNER_IDS),
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

    @commands.command(name="reset")
    @is_owner()
    async def reset_user(self, ctx: commands.Context, user: UserID):
        """Reset a user's conversation. Usage: .reset <user_id>"""
        db = UserDatabase(user)
        await db.init()
        async with db._lock:
            import aiosqlite
            async with aiosqlite.connect(db.db_path) as conn:
                await conn.execute("DELETE FROM messages")
                await conn.commit()
        await session_manager.remove(user)
        await ctx.send(f"✅ User `{user}` reset (messages cleared, session killed)")

    @commands.command(name="setowner")
    @is_super_owner()
    async def set_owner(self, ctx: commands.Context, user: UserID):
        """Add a normal owner. SUPER OWNER ONLY. Usage: .setowner <user_id>"""
        if user not in Config.OWNER_IDS:
            Config.OWNER_IDS.append(user)
        await ctx.send(f"✅ Owner added: `{user}`")

    @commands.command(name="restart")
    @is_super_owner()
    async def restart(self, ctx: commands.Context):
        """🔴 Restart the bot. SUPER OWNER ONLY."""
        await ctx.send("🔄 Restarting...")
        logger.info("Bot restart initiated by owner")
        await backup_service.backup_all()
        os.execv(sys.executable, [sys.executable, "-m", "bot.main"])

    # ─── Backup ──────────────────────────────────────────────

    @commands.command(name="backup")
    @is_owner()
    async def backup(self, ctx: commands.Context):
        """Force a backup."""
        await ctx.send("📦 Creating backup...")
        result = await backup_service.backup_all()
        await ctx.send(result)

    @commands.command(name="restore")
    @is_owner()
    async def restore(self, ctx: commands.Context, backup_name: str = None):
        """Restore from backup."""
        if backup_name:
            result = await backup_service.restore_all(backup_name)
        else:
            result = await backup_service.restore_latest()
        await ctx.send(result)

    @commands.command(name="backups")
    @is_owner()
    async def list_backups(self, ctx: commands.Context):
        """List available backups."""
        backups = await backup_service.list_backups()
        if not backups:
            return await ctx.send("No backups found.")
        lines = ["📦 **Available backups:**"]
        for b in backups[:20]:
            lines.append(f"`{b['filename']}` — {b['size_kb']}KB — {b['timestamp']}")
        await ctx.send("\n".join(lines))

    # ─── Plan Management ─────────────────────────────────────

    @commands.command(name="activate")
    @is_owner()
    async def activate_plan(self, ctx: commands.Context, user: UserID):
        """Activate pending plan."""
        db = UserDatabase(user)
        await db.init()
        pending = await db.get_config("pending_plan")
        if not pending:
            return await ctx.send(f"No pending plan for user `{user}`.")
        plan = Config.PLANS.get(pending)
        if not plan:
            return await ctx.send(f"Invalid pending plan `{pending}`.")
        expires = int(time.time()) + (plan["duration_days"] * 86400)
        await db.set_config("plan", pending)
        await db.set_config("plan_expires", str(expires))
        await db.set_config("pending_plan", "")

        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(user, pending)
            except Exception as e:
                logger.warning(f"activate callback error: {e}")

        await ctx.send(f"✅ Plan `{pending}` activated for `{user}` until <t:{expires}:f>.")

    @commands.command(name="deactivate")
    @is_owner()
    async def deactivate_plan(self, ctx: commands.Context, user: UserID):
        """Deactivate a user's paid plan (back to free)."""
        db = UserDatabase(user)
        await db.init()
        await db.set_config("plan", "free")
        await db.set_config("plan_expires", "0")

        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(user, "free")
            except Exception as e:
                logger.warning(f"deactivate callback error: {e}")

        await ctx.send(f"✅ User `{user}` reverted to free plan.")

    @commands.command(name="approve")
    @is_owner()
    async def approve_payment(self, ctx: commands.Context, user: UserID, plan_id: str):
        """Approve a payment and activate plan. Usage: .approve <user_id> <plan>"""
        plan = Config.PLANS.get(plan_id)
        if not plan:
            return await ctx.send(f"Invalid plan `{plan_id}`.")
        db = UserDatabase(user)
        await db.init()
        expires = int(time.time()) + (plan["duration_days"] * 86400)
        await db.set_config("plan", plan_id)
        await db.set_config("plan_expires", str(expires))
        await db.set_config("pending_plan", "")

        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(user, plan_id)
            except Exception as e:
                logger.warning(f"approve callback error: {e}")

        await ctx.send(f"✅ Payment approved for `{user}`. Plan `{plan_id}` active until <t:{expires}:f>.")

    @commands.command(name="expire")
    @is_owner()
    async def check_expiry(self, ctx: commands.Context, user: UserID):
        """Check plan expiry. Usage: .expire <user_id>"""
        db = UserDatabase(user)
        await db.init()
        plan = await db.get_config("plan") or "free"
        expires = await db.get_config("plan_expires") or "0"
        exp_int = int(expires)
        if exp_int > time.time():
            await ctx.send(f"User `{user}`: Plan `{plan}` expires <t:{exp_int}:R>.")
        else:
            await ctx.send(f"User `{user}`: Plan `{plan}` (no active expiration).")

    @commands.command(name="cleanup")
    @is_owner()
    async def cleanup(self, ctx: commands.Context):
        """Clean stale sessions."""
        count = await session_manager.cleanup_stale(0)
        await ctx.send(f"🧹 Cleaned up {count} session(s)")

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

    @commands.command(name="panel")
    async def resend_panels(self, ctx: commands.Context, user_id: int = None):
        """Re-send panels. Owner can use: .panel <user_id>"""
        from bot.services.owner_auth import is_owner_id

        if user_id is not None and is_owner_id(ctx.author.id):
            target_id = user_id
        else:
            target_id = ctx.author.id
            user_id = None

        member = None
        guild = self.bot.get_guild(Config.GUILD_ID)
        if guild:
            member = guild.get_member(target_id)
        if not member:
            try:
                member = await self.bot.fetch_user(target_id)
                return await ctx.send(f"User `{target_id}` not found in guild.")
            except:
                return await ctx.send(f"User `{target_id}` not found.")

        from bot.cogs.channels import ChannelManager
        cm = self.bot.get_cog("ChannelManager")
        if not cm:
            return await ctx.send("ChannelManager cog not loaded.")

        db = UserDatabase(target_id)
        await db.init()
        plan = await db.get_config("plan") or "free"

        channels = await cm.ensure_user_channels(member, plan)
        if not channels:
            return await ctx.send("Failed to create channels.")

        await cm.send_welcome_panels(member, channels, plan)
        await ctx.send(f"✅ Panels re-sent for `{target_id}`")

    @commands.command(name="panelsetup")
    @is_owner()
    async def panel_setup(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Place a public panel in a channel. Usage: .panelsetup [#channel]"""
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

    @commands.hybrid_command(name="botinvite")
    async def invite_bot(self, ctx: commands.Context):
        """Get the invite link to add Kaufy to your own server."""
        client_id = self.bot.user.id
        invite_url = f"https://discord.com/oauth2/authorize?client_id={client_id}&permissions=8&scope=bot%20applications.commands"
        embed = discord.Embed(
            title="🤖 Invite Kaufy to Your Server",
            description=(
                f"Click the link below to add Kaufy to any server you manage:\n\n"
                f"[**Invite Kaufy**]({invite_url})\n\n"
                f"⚙️ Requires **Administrator** permission for full functionality.\n"
                f"💬 Works in your server's channels and DMs.\n"
                f"🔞 100% uncensored — no filters."
            ),
            color=0x9B59B6,
            url=invite_url,
        )
        embed.set_footer(text="Kaufy's Hall • AI-Powered")
        await ctx.send(embed=embed)

    @commands.command(name="emoji")
    @is_owner()
    async def emoji_manage(self, ctx: commands.Context, action: str = "upload"):
        """Upload custom emojis. Usage: .emoji upload|list"""
        if action == "upload":
            emoji_dir = Path(__file__).parent.parent.parent / "emojis"
            if not emoji_dir.exists():
                return await ctx.send("Emoji directory not found.")

            uploaded = 0
            for ext in ["*.png", "*.gif"]:
                for img_path in sorted(emoji_dir.glob(ext)):
                    name = img_path.stem
                    existing = discord.utils.get(ctx.guild.emojis, name=name)
                    if existing:
                        continue
                    try:
                        with open(img_path, "rb") as f:
                            img_data = f.read()
                        await ctx.guild.create_custom_emoji(
                            name=name, image=img_data, reason="Kaufy bot emojis"
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


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerCog(bot))
