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
        # 🛡️ Cache FIFO de message_ids pra evitar resposta duplicada
        self._processed_ids = set()
        self._processed_max = 1000
        self._processed_order = []  # FIFO order

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
        
        Uses STREAMING — sends partial responses as they arrive.
        When opencode uses a tool/subagent, the response up to that
        point is flushed to Discord so the user sees progress.
        
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

                # 🔒 VERIFY channel name starts with msg- — anti-conversation-mixing
                ch_name = getattr(channel, 'name', str(channel))
                if not ch_name.startswith("msg-"):
                    logger.warning(f"Channel mismatch for user {author.id}: channel={ch_name}")
                    return

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

                # Owner check: identity is Discord-verified (no via_secret).
                from bot.services.owner_auth import owner_auth
                is_owner = await owner_auth.is_owner(author.id, via_secret=False)

                # Priority queue: paid users skip the semaphore
                priority = is_owner or plan_config.get("priority_queue", False)

                # ── STREAMING RESPONSE ──────────────────────────────────
                # Use run_stream() to get partial chunks. Send text to
                # Discord periodically so the user sees progress, especially
                # when the model pauses to use a tool/subagent.
                
                custom_prompt = await db.get_config("custom_prompt") or ""
                runner = KaufyRunner(author.id, db)
                pending = ""      # raw text accumulating from stream
                response_pending = ""  # ONLY response text (no thinking), for flush timing
                response_sent_len = 0  # how much of response_pending we've sent already
                full_response = ""
                all_files = []
                stream_done = False
                anything_sent = False
                thinking_sent = False

                import re

                async def _extract_and_send_thinking():
                    """Extract <thinking> blocks from pending, send them as quote blocks,
                    and REMOVE them from pending so only response text remains."""
                    nonlocal pending, thinking_sent
                    if not pending.strip():
                        return
                    # Find all thinking blocks
                    blocks = list(re.finditer(r'<thinking>(.*?)</thinking>', pending, re.DOTALL))
                    if not blocks:
                        return
                    for m in blocks:
                        content = m.group(1).strip()
                        if not content:
                            continue
                        lines = content.split("\n")
                        quoted = "\n".join(f"> {l}" for l in lines)
                        block = f"💭 **Thinking:**\n{quoted}"
                        try:
                            await channel.send(block)
                            anything_sent = True
                            thinking_sent = True
                            await asyncio.sleep(0.2)
                        except Exception as e:
                            logger.error(f"Thinking send error: {e}")
                    # Strip ALL thinking tags from pending
                    pending = re.sub(r'<thinking>.*?</thinking>', '', pending, flags=re.DOTALL).strip()

                async def _flush_response(force: bool = False):
                    """Send accumulated response text to Discord.
                    
                    Only sends if:
                    - force=True (stream done or flush forced), OR
                    - we have >= 300 NEW chars since last send, OR
                    - text ends with natural break and has >= 100 new chars
                    """
                    nonlocal response_pending, response_sent_len, anything_sent
                    text = response_pending[response_sent_len:].strip()
                    if not text:
                        return
                    
                    new_len = len(text)
                    
                    # Decide whether to send
                    should_send = force or new_len >= 500 or (
                        new_len >= 100 and any(text.rstrip().endswith(p) for p in ('.', '!', '?', ':\n', '.\n', '\n\n'))
                    )
                    if not should_send and not force:
                        return
                    
                    # Also strip any leftover preamble noise
                    clean = text
                    clean = re.sub(r'^! agent.*\n?', '', clean, flags=re.MULTILINE)
                    clean = re.sub(r'^> .*\n?', '', clean, flags=re.MULTILINE)
                    clean = clean.strip()
                    
                    if not clean:
                        return
                    
                    try:
                        if len(clean) <= 1900:
                            await channel.send(clean)
                            anything_sent = True
                        else:
                            for i in range(0, len(clean), 1900):
                                await channel.send(clean[i:i+1900])
                                await asyncio.sleep(0.4)
                                anything_sent = True
                    except Exception as e:
                        logger.error(f"Flush error: {e}")
                    
                    response_sent_len = len(response_pending)  # mark all as sent

                # Create stream
                stream_kwargs = dict(
                    prompt=prompt or message.content,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    username=str(author),
                    is_owner=is_owner,
                    plan=plan,
                    context_messages=plan_config.get("context_messages", 10),
                    custom_prompt=custom_prompt,
                )

                if priority:
                    stream = runner.run_stream(**stream_kwargs)
                else:
                    async with self._ai_semaphore:
                        stream = runner.run_stream(**stream_kwargs)

                # Process events
                async for event in stream:
                    if event["type"] == "chunk":
                        chunk = event["text"]
                        if chunk:
                            pending += chunk
                            full_response += chunk
                            response_pending = pending  # sync (thinking will be stripped later)
                            
                            # 1. Extract & send thinking blocks immediately when closed
                            if "</thinking>" in pending:
                                await _extract_and_send_thinking()
                                # pending now has thinking stripped; resync
                                response_pending = pending
                                response_sent_len = 0  # restart send tracking
                            
                            # 2. Flush response text if enough accumulated
                            await _flush_response(force=False)

                    elif event["type"] == "file":
                        all_files.append(event["path"])

                    elif event["type"] == "done":
                        stream_done = True
                        # One last thinking extraction + response flush
                        await _extract_and_send_thinking()
                        await _flush_response(force=True)

                    elif event["type"] == "error":
                        stream_done = True
                        await _extract_and_send_thinking()
                        await _flush_response(force=True)
                        await channel.send(event["text"])
                        full_response = event["text"]

                await runner.stop()

                # ── Fallback: if nothing was sent (model gave empty/weird response) ──
                if not anything_sent and full_response.strip():
                    clean = re.sub(r'<thinking>.*?</thinking>', '', full_response, flags=re.DOTALL).strip()
                    if clean:
                        await channel.send(clean[:1900])
                        anything_sent = True
                    elif full_response.strip():
                        await channel.send(full_response[:1900])
                        anything_sent = True
                
                if not anything_sent:
                    logger.warning(f"Empty response for user {author.id}: prompt={prompt[:100]!r}")
                
                # ── Log thinking stats ──
                thinking_matches = list(re.finditer(r'<thinking>(.*?)</thinking>', full_response, re.DOTALL))
                if thinking_matches:
                    thinking_len = sum(len(m.group(1)) for m in thinking_matches)
                    logger.info(f"Found {len(thinking_matches)} thinking block(s), {thinking_len} chars inline")

                # ── Attach files from output/ ──
                if all_files:
                    from pathlib import Path as _Path
                    file_objs = []
                    for fpath in all_files:
                        p = _Path(fpath)
                        if p.is_file():
                            try:
                                file_objs.append(discord.File(str(p)))
                            except Exception as e:
                                logger.error(f"Failed to attach file {fpath}: {e}")
                    if file_objs:
                        try:
                            await channel.send("📎 **Arquivos gerados:**", files=file_objs)
                        except Exception as e:
                            logger.error(f"Failed to send files: {e}")

                # Replace loading reaction with checkmark
                try:
                    await message.remove_reaction(EMOJI_LOADING, self.bot.user)
                except:
                    pass
                try:
                    await message.add_reaction(EMOJI_CHECK)
                except:
                    pass

            except Exception as e:
                logger.error(f"Session error for {author.id}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                try:
                    await channel.send(f"An error occurred: {str(e)[:200]}")
                except:
                    pass

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

        # 🛡️ Dedup FIFO: ignora se já processamos esse message_id
        if message.id in self._processed_ids:
            logger.debug(f"Dedup: skipping already-processed message {message.id}")
            return
        self._processed_ids.add(message.id)
        self._processed_order.append(message.id)
        # FIFO eviction — remove OLDEST, não limpa tudo
        while len(self._processed_ids) > self._processed_max:
            oldest = self._processed_order.pop(0)
            self._processed_ids.discard(oldest)

        # Check if this is a user's msg channel (starts with "msg-")
        ch_base = _channel_base(message.channel.name)
        if ch_base != Config.CHANNEL_MSG:
            logger.debug(f"Ignored msg in #{message.channel.name} (base={ch_base}, need={Config.CHANNEL_MSG})")
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

    @commands.command(name="search")
    async def web_search(self, ctx: commands.Context, *, query: str):
        """Search the web — paid users only. Uses web search results as context for Kaufy."""
        ch_base = _channel_base(ctx.channel.name)
        if ch_base != Config.CHANNEL_MSG:
            return
        db = UserDatabase(ctx.author.id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
        if not plan_config.get("web_search", False):
            await ctx.send("Web search is only available on paid plans (7d, 14d, 30d, lifetime).")
            return

        await ctx.send(f"🔍 Searching for: {query}")
        try:
            import aiohttp
            from urllib.parse import quote
            encoded = quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as resp:
                    html = await resp.text()
            # Simple extraction: get text snippets
            import re
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
            results = []
            for s in snippets[:5]:
                clean = re.sub(r'<[^>]+>', '', s).strip()
                if clean:
                    results.append(clean)
            web_context = "Web search results for: " + query + "\n" + "\n".join(f"- {r}" for r in results) if results else "No results found."
        except Exception as e:
            web_context = f"[Web search failed: {e}]"

        # Now process the query with web context (streaming)
        from bot.services.owner_auth import owner_auth
        is_owner = await owner_auth.is_owner(ctx.author.id, via_secret=False)
        runner = KaufyRunner(ctx.author.id, db)
        full_response = ""
        stream_done = False
        last_sent = 0

        async for event in runner.run_stream(
            prompt=f"Based on web search results, answer: {query}",
            temperature=0.8,
            max_tokens=plan_config.get("max_tokens_allowed", 4096),
            username=str(ctx.author),
            is_owner=is_owner,
            plan=plan,
            context_messages=plan_config.get("context_messages", 10),
            web_context=web_context,
        ):
            if event["type"] == "chunk":
                full_response += event["text"]
                # Flush new text periodically
                await ctx.send(event["text"])
            elif event["type"] == "done":
                stream_done = True
            elif event["type"] == "error":
                await ctx.send(event["text"])
                full_response = event["text"]
                stream_done = True

        await runner.stop()

        # Parse thinking tags
        thinking_content = ""
        import re
        thinking_matches = list(re.finditer(r'<thinking>(.*?)</thinking>', full_response, re.DOTALL))
        if thinking_matches:
            thinking_content = "\n\n".join(m.group(1).strip() for m in thinking_matches)

        # Route thinking
        if (is_owner or plan != "free") and thinking_content and ctx.channel.category:
            thinking_ch = discord.utils.get(
                ctx.channel.category.channels, name__startswith="thinking-"
            )
            if thinking_ch:
                try:
                    await thinking_ch.send(f"**💭 {ctx.author}'s thinking (search):**\n{thinking_content[:1900]}")
                except:
                    pass

    @commands.command(name="planinfo")
    async def plan_info(self, ctx: commands.Context):
        """Show your current plan and its benefits."""
        db = UserDatabase(ctx.author.id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        pc = Config.PLANS.get(plan, Config.PLANS["free"])
        embed = discord.Embed(
            title=f"📊 Your Plan: {plan.upper()}",
            color=0x9B59B6,
        )
        embed.add_field(name="Daily Messages", value="Unlimited" if pc.get("daily_messages", 0) >= 999999 else f"{pc['daily_messages']}/day")
        embed.add_field(name="Context Memory", value=f"{pc.get('context_messages', 10)} messages")
        embed.add_field(name="Max Tokens", value=f"{pc.get('max_tokens_allowed', 4096)}")
        embed.add_field(name="Thinking Mode", value="✅" if pc.get("thinking") else "❌")
        embed.add_field(name="Web Search", value="✅" if pc.get("web_search") else "❌")
        embed.add_field(name="Priority Queue", value="✅" if pc.get("priority_queue") else "❌")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(SessionCog(bot))
