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
                accumulated = ""
                full_response = ""
                all_files = []
                last_sent = 0
                stream_done = False
                sent_messages = []

                # Helper: flush accumulated text to Discord (strip thinking)
                async def _flush():
                    nonlocal accumulated, last_sent, sent_messages
                    if not accumulated.strip():
                        return
                    import re
                    clean = re.sub(r'<thinking>.*?</thinking>', '', accumulated, flags=re.DOTALL).strip()
                    if not clean:
                        return
                    # Only send the *new* part
                    new_part = clean[last_sent:]
                    if not new_part.strip():
                        return
                    if len(new_part) < 8 and not stream_done:
                        return  # too short, wait for more
                    try:
                        msg = await channel.send(new_part[:1900])
                        sent_messages.append(msg)
                        last_sent = len(clean)
                    except Exception as e:
                        logger.error(f"Flush error: {e}")

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
                            accumulated += chunk
                            full_response += chunk
                            # Flush on natural breaks or periodically
                            should_flush = (
                                accumulated.endswith("\n\n")
                                or "</thinking>" in accumulated
                                or (len(accumulated[last_sent:]) > 400 and "\n" in accumulated[-30:])
                            )
                            if should_flush:
                                await _flush()

                    elif event["type"] == "file":
                        all_files.append(event["path"])

                    elif event["type"] == "done":
                        stream_done = True
                        await _flush()

                    elif event["type"] == "error":
                        stream_done = True
                        await channel.send(event["text"])
                        full_response = event["text"]

                await runner.stop()

                # ── Parse ALL thinking tags for thinking channel ──
                import re
                thinking_content = ""
                clean_response = re.sub(r'<thinking>.*?</thinking>', '', full_response, flags=re.DOTALL).strip()
                thinking_matches = list(re.finditer(r'<thinking>(.*?)</thinking>', full_response, re.DOTALL))
                if thinking_matches:
                    thinking_content = "\n\n".join(m.group(1).strip() for m in thinking_matches)
                    logger.info(f"Found {len(thinking_matches)} thinking block(s), {len(thinking_content)} chars")

                # ── Route thinking to #thinking channel (paid users only) ──
                if (is_owner or plan != "free") and thinking_content:
                    if message.channel.category:
                        thinking_ch = discord.utils.get(
                            message.channel.category.channels,
                            name__startswith="thinking-"
                        )
                        if thinking_ch:
                            try:
                                chunks = [thinking_content[i:i+1900] for i in range(0, len(thinking_content), 1900)]
                                for i, chunk in enumerate(chunks):
                                    if i == 0:
                                        await thinking_ch.send(
                                            f"**💭 {author.display_name}'s reasoning:**\n{chunk}"
                                        )
                                    else:
                                        await thinking_ch.send(f"**(continued)**\n{chunk}")
                                    await asyncio.sleep(0.3)
                                logger.info(f"Sent thinking to {thinking_ch.name} ({len(thinking_content)} chars)")
                            except Exception as e:
                                logger.error(f"Failed to send thinking channel: {e}")
                        else:
                            logger.warning(f"Thinking channel not found in category")
                    else:
                        logger.warning("Message has no category — can't route thinking")

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
                await message.add_reaction(EMOJI_CHECK)

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
