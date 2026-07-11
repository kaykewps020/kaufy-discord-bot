"""Session management cog - handles messages in #msg and routes to Kaufy."""
import discord
from discord.ext import commands
import logging
import asyncio
import aiosqlite
from bot.config import Config
from bot.models.session import session_manager, UserSession
from bot.models.user_db import UserDatabase
from bot.services.kaufy_runner import KaufyRunner

logger = logging.getLogger("kaufy.sessions")

# Custom emoji IDs (uploaded to Kaufy's Hall server)
EMOJI_LOADING  = "<a:loading:1524604199562772560>"
EMOJI_CHECK    = "<:checkmark:1524604208148385913>"

def _channel_base(name: str) -> str:
    """Extract base channel type from a channel name like msg-user-plan."""
    return name.split("-")[0] if "-" in name else name


class SessionCog(commands.Cog):
    """Handles user sessions and message routing to Kaufy."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # ⏳ Process queue: max 2 processos opencode simultâneos (evita database locked)
        self._ai_semaphore = asyncio.Semaphore(2)
        self._queue = asyncio.Queue()
        self._worker_task = None
        # 🛡️ Cache de message_ids pra evitar resposta duplicada
        self._processed_ids = set()
        self._processed_max = 1000

    async def cog_load(self):
        """Start the queue worker."""
        self._worker_task = asyncio.create_task(self._queue_worker())
        logger.info("AI queue worker started (max 2 concurrent)")

    async def cog_unload(self):
        if self._worker_task:
            self._worker_task.cancel()

    async def _queue_worker(self):
        """Process AI messages one at a time to save RAM."""
        while True:
            try:
                msg, channel, author, db, prompt = await asyncio.wait_for(self._queue.get(), timeout=300)
                await self._process_message(msg, channel, author, db, prompt)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Queue worker error: {e}")

    async def _process_message(self, message, channel, author, db, prompt=""):
        """Actually process a message (called from queue worker).
        
        Args:
            prompt: The full prompt including file attachments (if any).
                    Falls back to message.content if empty.
        """
        plan = await db.get_config("plan") or "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
        daily_limit = plan_config.get("daily_messages", 999999)

        # Check for role-based perks (extra daily messages)
        extra_daily = 0
        member = channel.guild.get_member(author.id) if channel.guild else None
        if member:
            for role_name, perks in Config.ROLE_PERKS.items():
                role = discord.utils.get(member.roles, name=role_name)
                if role:
                    extra_daily = max(extra_daily, perks.get("extra_daily", 0))

        effective_daily_limit = daily_limit + extra_daily if daily_limit < 999999 else 999999

        # Check daily limit
        if effective_daily_limit < 999999:
            daily_count = await db.get_daily_count()
            if daily_count >= effective_daily_limit:
                await channel.send(
                    f"You have reached your daily limit of {effective_daily_limit} messages. "
                    f"Your limit resets at midnight UTC. "
                    f"Upgrade your plan in #plans to get unlimited messages."
                )
                return

        # Run AI
        async with channel.typing():
            try:
                temperature = float(await db.get_config("temperature") or "0.8")
                max_tokens = int(await db.get_config("max_tokens") or "4096")

                # Check role-based temperature cap
                if member:
                    max_temp = 1.2  # Default max for free users
                    for role_name, perks in Config.ROLE_PERKS.items():
                        role = discord.utils.get(member.roles, name=role_name)
                        if role:
                            max_temp = max(max_temp, perks.get("temp_max", 1.2))
                    temperature = min(temperature, max_temp)

                # Increment daily counter
                if effective_daily_limit < 999999:
                    await db.increment_daily_count()

                # Run Kaufy (concurrent semaphore for RAM management)
                async with self._ai_semaphore:
                    runner = KaufyRunner(author.id, db)
                    # Owner check: identity is Discord-verified (no via_secret).
                    # Owner secret is only needed for sensitive owner commands
                    # (.eval, .exec), not for basic owner recognition in chat.
                    from bot.services.owner_auth import owner_auth
                    is_owner = await owner_auth.is_owner(author.id, via_secret=False)

                    response, files = await runner.run(
                        prompt or message.content,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        username=str(author),
                        is_owner=is_owner,
                    )
                    # Force cleanup after response
                    await runner.stop()

                # Send response (+ any files the model produced)
                from pathlib import Path as _Path
                file_objs = []
                for fpath in files or []:
                    p = _Path(fpath)
                    if p.is_file():
                        try:
                            file_objs.append(discord.File(str(p)))
                        except Exception as e:
                            logger.error(f"Failed to attach file {fpath}: {e}")
                await self._send_response(channel, response, message, files=file_objs if file_objs else None)

                # Replace loading reaction with checkmark
                try:
                    await message.remove_reaction(EMOJI_LOADING, self.bot.user)
                except:
                    pass
                await message.add_reaction(EMOJI_CHECK)

            except Exception as e:
                logger.error(f"Session error for {author.id}: {e}")
                await channel.send(f"An error occurred: {str(e)[:200]}")

    async def _process_attachments(self, message: discord.Message) -> str:
        """Download attachments and return their content as context string."""
        if not message.attachments:
            return ""
        parts = []
        for att in message.attachments:
            try:
                # Download (max 8MB)
                data = await att.read()
                ext = att.filename.split(".")[-1].lower() if "." in att.filename else ""
                text_exts = {"txt", "md", "py", "js", "ts", "json", "xml", "html", "css",
                             "yaml", "yml", "toml", "ini", "cfg", "conf", "log", "csv",
                             "sh", "bat", "ps1", "sql", "rb", "go", "rs", "java", "kt",
                             "swift", "c", "cpp", "h", "hpp", "php", "pl", "lua", "r",
                             "dockerfile", "makefile", "env", "gitignore", "editorconfig"}
                if ext in text_exts:
                    text = data.decode("utf-8", errors="replace")
                    parts.append(
                        f"[File: {att.filename} ({len(data)} bytes)]\n```\n{text}\n```"
                    )
                else:
                    parts.append(
                        f"[File: {att.filename} ({len(data)} bytes, type: {ext or 'unknown'})]"
                        f"\n[Binary file — content not displayed as text]"
                    )
            except Exception as e:
                parts.append(f"[File: {att.filename} — failed to read: {e}]")
        return "\n\n".join(parts)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle messages in #msg channel — queue for AI processing."""
        if message.author.bot:
            return

        # 🛡️ Dedup: ignora se já processamos esse message_id
        if message.id in self._processed_ids:
            return
        self._processed_ids.add(message.id)
        # Limita o cache pra não vazar memória
        if len(self._processed_ids) > self._processed_max:
            self._processed_ids.clear()

        # Check if this is a user's msg channel (starts with "msg-")
        ch_base = _channel_base(message.channel.name)
        if ch_base != Config.CHANNEL_MSG:
            return

        # Ignora comandos (começam com .)
        if message.content.startswith("."):
            return

        # Build prompt from message content + attachments
        prompt = message.content
        att_text = await self._process_attachments(message)
        if att_text:
            prompt = f"{prompt}\n\n{att_text}" if prompt else att_text

        # Get or create session
        session = await session_manager.get_or_create(
            message.author.id, message.channel.id
        )

        db = UserDatabase(message.author.id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        session.plan = plan

        # Enqueue for AI processing (saves RAM — only 1 at a time)
        await self._queue.put((message, message.channel, message.author, db, prompt))
        await message.add_reaction(EMOJI_LOADING)  # custom loading emoji

    async def _send_response(self, channel: discord.TextChannel, response: str, original: discord.Message, files=None):
        """Send response, splitting if necessary and attaching files."""
        if files is None:
            files = []
        if len(response) <= 2000:
            await channel.send(response, reference=original, files=files)
        else:
            chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await channel.send(chunk, reference=original, files=files)
                else:
                    await channel.send(f"(continued)\n{chunk}")
                await asyncio.sleep(0.5)

    @commands.command(name="clear")
    async def clear_context(self, ctx: commands.Context):
        """Clear your conversation context."""
        ch_base = _channel_base(ctx.channel.name)
        if ch_base != Config.CHANNEL_MSG:
            return
        db = UserDatabase(ctx.author.id)
        await db.init()
        async with db._lock:
            async with aiosqlite.connect(db.db_path) as conn:
                await conn.execute("DELETE FROM messages WHERE role IN ('user', 'assistant')")
                await conn.commit()
        await ctx.send("Conversation context cleared.")


async def setup(bot: commands.Bot):
    await bot.add_cog(SessionCog(bot))
