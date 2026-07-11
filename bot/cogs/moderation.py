"""Moderation cog — ban, kick, warn, mute, word filter, raid protection.

Commands:
  .ban <user_id> [reason]     — hackban (ban por ID, mesmo fora do servidor)
  .kick <member> [reason]     — kick member
  .warn <member> [reason]     — warn (log)
  .mute <member> [duration]   — timeout user
  .unmute <member>            — remove timeout
  .clear [count]              — bulk delete messages
  .slowmode [seconds]         — set slowmode
  .lock / .unlock             — lock/unlock channel
  .warnings <member>          — show warnings
  .delwarn <member> <id>      — remove specific warning

Word filter auto-deletes messages containing banned words (bypass included).
"""

import discord
from discord.ext import commands
import logging
import time
import json
import asyncio
from bot.config import Config
from bot.services.owner_auth import owner_auth
from bot.utils.wordlist import has_banned_word, has_suspicious_pattern

logger = logging.getLogger("kaufy.moderation")

# ──────────────────────────────────────────────
# MOD LOG DATABASE (simple JSON per guild)
# ──────────────────────────────────────────────
_mod_logs: dict[int, dict[int, list[dict]]] = {}  # guild_id -> user_id -> [warnings]
_LOG_LOCK = asyncio.Lock()

_MOD_CACHE_DIR = Config.DATA_DIR / "mod_logs"
_MOD_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_logs(guild_id: int) -> dict[int, list[dict]]:
    path = _MOD_CACHE_DIR / f"guild_{guild_id}.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except:
            return {}
    return {}


def _save_logs(guild_id: int, data: dict[int, list[dict]]):
    path = _MOD_CACHE_DIR / f"guild_{guild_id}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ──────────────────────────────────────────────
# CHECKS
# ──────────────────────────────────────────────

def mod_or_owner():
    """Check: user is owner (ID-only) OR has Manage Messages permission.

    NOTE: Owner ID check is ID-only (no via_secret). The owner secret is
    only required for sensitive commands like .eval/.exec, not for basic
    moderation like .purge, .ban, .kick, etc.
    """
    async def predicate(ctx: commands.Context):
        if ctx.author.id in Config.OWNER_IDS:
            return True
        if ctx.author.guild_permissions.manage_messages:
            return True
        await ctx.reply("⛔ You need `Manage Messages` permission or owner.", delete_after=10)
        return False
    return commands.check(predicate)


# ──────────────────────────────────────────────
# COG
# ──────────────────────────────────────────────

class Moderation(commands.Cog):
    """Moderation commands + auto word filter."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id -> set of channel_id (locked channels) — in-memory
        self._locked: dict[int, set[int]] = {}

    # ─── BAN (hackban by ID) ──────────────────────────────────

    @commands.command(name="ban")
    @mod_or_owner()
    async def ban_user(self, ctx: commands.Context, user_id: int, *, reason: str = "No reason given."):
        """Ban a user by ID (hackban — works even if they're not in the server).

        Usage: .ban <user_id> [reason]
        Example: .ban 123456789012345678 spamming
        """
        if user_id in Config.OWNER_IDS:
            return await ctx.reply("⛔ Cannot ban an owner.", delete_after=10)

        try:
            await ctx.guild.ban(discord.Object(id=user_id), reason=f"[{ctx.author}] {reason}")
            await ctx.reply(f"✅ Banned `{user_id}` | Reason: {reason}")
            logger.info(f"Ban {user_id} by {ctx.author} in {ctx.guild}: {reason}")
        except discord.Forbidden:
            await ctx.reply("⛔ Bot lacks Ban Members permission.", delete_after=10)
        except discord.HTTPException as e:
            if "10007" in str(e):
                await ctx.reply(f"❌ User `{user_id}` does not exist (invalid ID).", delete_after=10)
            else:
                await ctx.reply(f"❌ Ban failed: {e}", delete_after=10)

    @commands.command(name="unban")
    @mod_or_owner()
    async def unban_user(self, ctx: commands.Context, user_id: int, *, reason: str = "No reason given."):
        """Unban a user by ID.

        Usage: .unban <user_id> [reason]
        """
        try:
            await ctx.guild.unban(discord.Object(id=user_id), reason=f"[{ctx.author}] {reason}")
            await ctx.reply(f"✅ Unbanned `{user_id}`")
            logger.info(f"Unban {user_id} by {ctx.author} in {ctx.guild}")
        except discord.NotFound:
            await ctx.reply(f"`{user_id}` is not banned or doesn't exist.", delete_after=10)
        except discord.Forbidden:
            await ctx.reply("⛔ Bot lacks Ban Members permission.", delete_after=10)
        except Exception as e:
            await ctx.reply(f"❌ Unban failed: {e}", delete_after=10)

    # ─── KICK ─────────────────────────────────────────────────

    @commands.command(name="kick")
    @mod_or_owner()
    async def kick_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given."):
        """Kick a member from the server.

        Usage: .kick @member [reason]
        """
        if member.id in Config.OWNER_IDS:
            return await ctx.reply("⛔ Cannot kick an owner.", delete_after=10)
        if member.top_role >= ctx.author.top_role and ctx.author.id not in Config.OWNER_IDS:
            return await ctx.reply("⛔ You cannot kick someone with a higher role.", delete_after=10)

        try:
            await member.kick(reason=f"[{ctx.author}] {reason}")
            await ctx.reply(f"✅ Kicked {member.mention} (`{member.id}`) | Reason: {reason}")
            logger.info(f"Kick {member.id} by {ctx.author} in {ctx.guild}: {reason}")
        except discord.Forbidden:
            await ctx.reply("⛔ Bot lacks Kick Members permission.", delete_after=10)
        except Exception as e:
            await ctx.reply(f"❌ Kick failed: {e}", delete_after=10)

    # ─── MUTE (timeout) ──────────────────────────────────────

    @commands.command(name="mute")
    @mod_or_owner()
    async def mute_member(self, ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = "No reason."):
        """Timeout a member. Duration: 10m, 1h, 1d, etc.

        Usage: .mute @member [duration] [reason]
        Example: .mute @user 30m spamming
        """
        if member.id in Config.OWNER_IDS:
            return await ctx.reply("⛔ Cannot mute an owner.", delete_after=10)
        if member.top_role >= ctx.author.top_role and ctx.author.id not in Config.OWNER_IDS:
            return await ctx.reply("⛔ Cannot mute someone with a higher role.", delete_after=10)

        seconds = _parse_duration(duration)
        if seconds <= 0:
            return await ctx.reply("❌ Invalid duration. Use e.g. 10m, 1h, 2d.", delete_after=10)

        until = discord.utils.utcnow() + discord.timedelta(seconds=seconds)
        try:
            await member.timeout(until, reason=f"[{ctx.author}] {reason}")
            dur_str = _format_duration(seconds)
            await ctx.reply(f"✅ Muted {member.mention} for **{dur_str}** | Reason: {reason}")
            logger.info(f"Mute {member.id} {dur_str} by {ctx.author} in {ctx.guild}: {reason}")
        except discord.Forbidden:
            await ctx.reply("⛔ Bot lacks Moderate Members permission.", delete_after=10)
        except Exception as e:
            await ctx.reply(f"❌ Mute failed: {e}", delete_after=10)

    @commands.command(name="unmute")
    @mod_or_owner()
    async def unmute_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Lifted."):
        """Remove timeout from a member.

        Usage: .unmute @member
        """
        try:
            await member.timeout(None, reason=f"[{ctx.author}] {reason}")
            await ctx.reply(f"✅ Unmuted {member.mention}")
            logger.info(f"Unmute {member.id} by {ctx.author} in {ctx.guild}")
        except discord.Forbidden:
            await ctx.reply("⛔ Bot lacks Moderate Members permission.", delete_after=10)
        except Exception as e:
            await ctx.reply(f"❌ Unmute failed: {e}", delete_after=10)

    # ─── WARN (log) ──────────────────────────────────────────

    @commands.command(name="warn")
    @mod_or_owner()
    async def warn_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason."):
        """Warn a member (stored in mod log).

        Usage: .warn @member [reason]
        """
        guild_id = ctx.guild.id
        async with _LOG_LOCK:
            logs = _load_logs(guild_id)
            user_logs = logs.get(str(member.id), [])
            entry = {
                "id": int(time.time()),
                "mod": ctx.author.id,
                "reason": reason,
                "timestamp": int(time.time()),
            }
            user_logs.append(entry)
            logs[str(member.id)] = user_logs
            _save_logs(guild_id, logs)

        await ctx.reply(
            f"⚠️ Warned {member.mention} (`{member.id}`) | Reason: {reason}\n"
            f"Total warnings: {len(user_logs)}"
        )
        logger.info(f"Warn {member.id} by {ctx.author}: {reason}")

        # Try to DM the user
        try:
            await member.send(f"⚠️ You received a warning in **{ctx.guild.name}**.\nReason: {reason}")
        except:
            pass

    @commands.command(name="warnings")
    @mod_or_owner()
    async def list_warnings(self, ctx: commands.Context, member: discord.Member):
        """Show all warnings for a member.

        Usage: .warnings @member
        """
        guild_id = ctx.guild.id
        logs = _load_logs(guild_id)
        user_logs = logs.get(str(member.id), [])

        if not user_logs:
            return await ctx.reply(f"✅ {member.mention} has no warnings.", delete_after=15)

        lines = [f"⚠️ **Warnings for {member}** (`{member.id}`):"]
        for i, w in enumerate(user_logs, 1):
            ts = f"<t:{w['timestamp']}:R>"
            lines.append(f"`{i}.` **{w['reason']}** — {ts} (by <@{w['mod']}>)")
        await ctx.reply("\n".join(lines))

    @commands.command(name="delwarn")
    @mod_or_owner()
    async def delete_warning(self, ctx: commands.Context, member: discord.Member, warn_id: int):
        """Delete a specific warning by ID.

        Usage: .delwarn @member <warn_id>
        Use .warnings to see IDs.
        """
        guild_id = ctx.guild.id
        async with _LOG_LOCK:
            logs = _load_logs(guild_id)
            user_logs = logs.get(str(member.id), [])
            to_keep = [w for w in user_logs if w["id"] != warn_id]
            if len(to_keep) == len(user_logs):
                return await ctx.reply(f"❌ Warning `{warn_id}` not found.", delete_after=10)
            logs[str(member.id)] = to_keep
            if not to_keep:
                del logs[str(member.id)]
            _save_logs(guild_id, logs)

        await ctx.reply(f"✅ Deleted warning `{warn_id}` for {member.mention}")

    # ─── CLEAR ────────────────────────────────────────────────

    @commands.command(name="purge")
    @mod_or_owner()
    async def purge_messages(self, ctx: commands.Context, count: int = 10):
        """Bulk delete messages in this channel.

        Usage: .purge [count=10]
        Max: 100
        """
        count = min(count, 100)
        try:
            deleted = await ctx.channel.purge(limit=count + 1)  # +1 for the command itself
            await ctx.send(f"🗑️ Deleted {len(deleted) - 1} message(s).", delete_after=3)
            logger.info(f"Purge {len(deleted)-1} msgs in {ctx.channel} by {ctx.author}")
        except discord.Forbidden:
            await ctx.reply("⛔ Bot lacks Manage Messages permission.", delete_after=10)
        except Exception as e:
            await ctx.reply(f"❌ Purge failed: {e}", delete_after=10)

    # ─── SLOWMODE ─────────────────────────────────────────────

    @commands.command(name="slowmode")
    @mod_or_owner()
    async def set_slowmode(self, ctx: commands.Context, seconds: int = 5):
        """Set slowmode in this channel.

        Usage: .slowmode [seconds=5]
        0 to disable.
        """
        seconds = max(0, min(seconds, 21600))
        try:
            await ctx.channel.edit(slowmode_delay=seconds)
            if seconds == 0:
                await ctx.reply("✅ Slowmode disabled.", delete_after=5)
            else:
                await ctx.reply(f"✅ Slowmode set to **{seconds}s**.", delete_after=5)
        except Exception as e:
            await ctx.reply(f"❌ Failed: {e}", delete_after=10)

    # ─── LOCK / UNLOCK ────────────────────────────────────────

    @commands.command(name="lock")
    @mod_or_owner()
    async def lock_channel(self, ctx: commands.Context):
        """Lock this channel (deny send_messages for @everyone)."""
        guild_id = ctx.guild.id
        if guild_id not in self._locked:
            self._locked[guild_id] = set()
        self._locked[guild_id].add(ctx.channel.id)

        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        try:
            await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
            await ctx.reply("🔒 Channel locked.", delete_after=5)
            logger.info(f"Lock {ctx.channel} by {ctx.author}")
        except Exception as e:
            await ctx.reply(f"❌ Failed: {e}", delete_after=10)

    @commands.command(name="unlock")
    @mod_or_owner()
    async def unlock_channel(self, ctx: commands.Context):
        """Unlock this channel."""
        guild_id = ctx.guild.id
        if guild_id in self._locked:
            self._locked[guild_id].discard(ctx.channel.id)

        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None  # reset to default
        try:
            await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
            await ctx.reply("🔓 Channel unlocked.", delete_after=5)
        except Exception as e:
            await ctx.reply(f"❌ Failed: {e}", delete_after=10)

    # ─── WORD FILTER (auto) ───────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Auto-delete messages with banned words (bypass included)."""
        if message.author.bot:
            return
        if message.author.id in Config.OWNER_IDS:
            return  # owners are exempt

        content = message.content

        # Clean up code blocks before checking (code is exempt)
        clean_for_check = content
        # Remove code blocks (```...```)
        import re as _re
        clean_for_check = _re.sub(r'```.*?```', '', clean_for_check, flags=_re.DOTALL)
        # Remove inline code (`...`)
        clean_for_check = _re.sub(r'`[^`]+`', '', clean_for_check)

        banned, word = has_banned_word(clean_for_check)
        if banned:
            logger.info(f"Word filter triggered in #{message.channel}: {message.author} -> {word!r}")
            try:
                await message.delete()
                warning = await message.channel.send(
                    f"⛔ {message.author.mention} Your message was removed (filtered word).",
                    delete_after=5
                )
                # Auto-warn
                guild_id = message.guild.id if message.guild else None
                if guild_id:
                    async with _LOG_LOCK:
                        logs = _load_logs(guild_id)
                        user_logs = logs.get(str(message.author.id), [])
                        user_logs.append({
                            "id": int(time.time()),
                            "mod": self.bot.user.id,
                            "reason": f"Auto-filter: banned word ({word})",
                            "timestamp": int(time.time()),
                        })
                        logs[str(message.author.id)] = user_logs
                        _save_logs(guild_id, logs)
            except discord.Forbidden:
                pass
            except Exception as e:
                logger.warning(f"Word filter error: {e}")

    # ─── INFO ─────────────────────────────────────────────────

    @commands.command(name="modstats")
    @mod_or_owner()
    async def mod_stats(self, ctx: commands.Context):
        """Show moderation stats for this server."""
        guild_id = ctx.guild.id
        logs = _load_logs(guild_id)
        total_warns = sum(len(v) for v in logs.values())
        warned_users = len(logs)
        await ctx.reply(
            f"📊 **Mod Stats**\n"
            f"Total warnings: {total_warns}\n"
            f"Warned users: {warned_users}"
        )


# ──────────────────────────────────────────────
# UTILITY FUNCTIONS
# ──────────────────────────────────────────────

def _parse_duration(dur: str) -> int:
    """Parse duration string like '10m', '1h', '2d' into seconds."""
    if not dur:
        return 0
    dur = dur.strip().lower()
    if dur.isdigit():
        return int(dur) * 60  # default minutes
    unit = dur[-1]
    num = dur[:-1]
    if not num.isdigit():
        return 0
    num = int(num)
    if unit == 's':
        return num
    elif unit == 'm':
        return num * 60
    elif unit == 'h':
        return num * 3600
    elif unit == 'd':
        return num * 86400
    return num * 60


def _format_duration(seconds: int) -> str:
    """Format seconds into human-readable string."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h" if hours else f"{days}d"


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
