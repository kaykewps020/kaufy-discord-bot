"""Session management cog — handles messages in #msg and DMs, routes to Kaufy.

All user-facing commands are HYBRID (.cmd AND /cmd).
Supports DM conversations with Kaufy (paid users can chat via DM).
"""
import discord
from discord.ext import commands
import logging
import asyncio
import aiosqlite
import time
import re
from typing import Optional
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


def _channel_owner_name(name: str) -> Optional[str]:
    """Extract the owner's name from a channel name like 'msg-username-plan'.

    Returns the username part (lowercase) or None if it can't be determined.
    """
    parts = name.split("-")
    if len(parts) >= 2:
        return parts[1].lower()
    return None


def _detect_language(text: str) -> str:
    """Detect language from text using character patterns.

    Returns a language code string like 'pt', 'en', 'es', 'fr', etc.
    Falls back to 'en' if uncertain.
    """
    if not text or not text.strip():
        return "en"

    # Count language-specific characters
    pt_chars = sum(1 for c in text if c in 'ãáàâäéèêëíìîïóòôöõúùûüçñÃÁÀÂÄÉÈÊËÍÌÎÏÓÒÔÖÕÚÙÛÜÇÑ')
    en_chars = 0  # English has no special chars, fallback
    es_chars = sum(1 for c in text if c in 'ñáéíóúüÑÁÉÍÓÚÜ¿¡')
    fr_chars = sum(1 for c in text if c in 'éèêëàâäùûüôœîïçÉÈÊËÀÂÄÙÛÜÔŒÎÏÇ')
    de_chars = sum(1 for c in text if c in 'äöüßÄÖÜẞ')

    total = len(text.strip())
    if total == 0:
        return "en"

    # High density of Portuguese-specific chars → Portuguese
    # (ã, õ are strong Portuguese markers)
    if text.count('ã') + text.count('õ') + text.count('Ã') + text.count('Õ') >= 2:
        return "pt"
    if pt_chars >= 3 and pt_chars >= es_chars and pt_chars >= fr_chars:
        return "pt"

    # Spanish: ñ is a strong marker
    if 'ñ' in text or 'Ñ' in text:
        return "es"
    if es_chars >= 3 and es_chars >= fr_chars:
        return "es"

    # French
    if fr_chars >= 3:
        return "fr"

    # German
    if de_chars >= 2:
        return "de"

    # Default to English (or whatever the model detects)
    return "en"


class SessionCog(commands.Cog):
    """Handles user sessions and message routing to Kaufy — multi-worker."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # ⏳ Queue: multiple parallel workers process messages
        self._queue = asyncio.Queue()
        self._worker_tasks = []
        self._num_workers = Config.MAX_CONCURRENT_WORKERS
        # 🛡️ FIFO dedup cache
        self._processed_ids = set()
        self._processed_max = 5000
        self._processed_order = []
        # 🚦 Rate limiter per channel
        self._channel_rate: dict[int, list[float]] = {}
        self._max_per_channel_per_min = Config.MAX_MSG_PER_CHANNEL_PER_MIN
        # 🔄 Per-user active run tracking (for interruption support)
        # Maps user_id -> asyncio.Event to signal interruption
        self._interrupt_events: dict[int, asyncio.Event] = {}
        # Maps user_id -> list of (channel, msg, prompt) that were interrupted
        self._interrupted: dict[int, list] = {}
        # Maps message.id -> bool to prevent DM duplicate queuing
        self._dm_processed: set[int] = set()
        # 🔓 Channels in "open mode" — anyone can talk, bot responds to all
        self._open_channels: set[int] = set()
        # 📝 Stores the current prompt being processed per user (for combining on interrupt)
        self._active_prompts: dict[int, str] = {}

    async def cog_load(self):
        """Start N queue workers."""
        for i in range(self._num_workers):
            task = asyncio.create_task(self._queue_worker(i))
            self._worker_tasks.append(task)
        logger.info(f"Started {self._num_workers} AI queue workers")

    async def cog_unload(self):
        for t in self._worker_tasks:
            t.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)

    def _check_channel_rate(self, channel_id: int) -> bool:
        """True if this channel can send another message."""
        now = time.monotonic()
        window = self._channel_rate.get(channel_id, [])
        window = [t for t in window if now - t < 60]
        if len(window) >= self._max_per_channel_per_min:
            return False
        window.append(now)
        self._channel_rate[channel_id] = window
        return True

    async def _queue_worker(self, worker_id: int):
        """Process messages from the queue — runs in parallel."""
        logger.info(f"Worker {worker_id} started")
        while True:
            try:
                msg, channel, author, db, prompt = await asyncio.wait_for(self._queue.get(), timeout=300)
                logger.debug(f"Worker {worker_id} picked up msg {msg.id} from user {author.id}")
                # Create interrupt event for this user
                self._interrupt_events[author.id] = asyncio.Event()
                try:
                    await self._process_message(msg, channel, author, db, prompt)
                finally:
                    # Clean up interrupt event
                    self._interrupt_events.pop(author.id, None)
                    # Check if there are queued interrupted messages for this user
                    pending = self._interrupted.pop(author.id, None)
                    if pending:
                        for p_msg, p_channel, p_author, p_db, p_prompt in pending:
                            logger.info(f"Re-queuing interrupted message for user {author.id}")
                            await self._queue.put((p_msg, p_channel, p_author, p_db, p_prompt))
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                logger.info(f"Worker {worker_id} cancelled")
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
                import traceback
                logger.error(traceback.format_exc())

    async def _interrupt_user(self, user_id: int) -> bool:
        """Signal interruption for a user's active processing.

        Returns True if there was an active processing to interrupt.
        """
        event = self._interrupt_events.get(user_id)
        if event and not event.is_set():
            event.set()
            logger.info(f"Interrupted active processing for user {user_id}")
            # Give a tiny bit of time for the subprocess to be killed
            await asyncio.sleep(0.5)
            return True
        return False

    async def _process_message(self, message, channel, author, db, prompt=""):
        """Actually process a message (called from queue worker).

        Uses STREAMING — sends partial responses as they arrive.
        Supports interruption: if user sends a new message, old processing stops.
        """
        plan = await db.get_config("plan") or "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
        daily_limit = plan_config.get("daily_messages", 999999)

        # Check for role-based perks
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
                    f"Upgrade your plan to get unlimited messages."
                )
                return

        # Run AI
        async with channel.typing():
            try:
                temperature = float(await db.get_config("temperature") or "0.8")
                max_tokens = int(await db.get_config("max_tokens") or "4096")

                # Channel verification (skip for DMs)
                if hasattr(channel, 'name') and channel.name and not isinstance(channel, discord.DMChannel):
                    ch_name = getattr(channel, 'name', str(channel))
                    if not ch_name.startswith("msg-"):
                        logger.warning(f"Channel mismatch for user {author.id}: channel={ch_name}")
                        return

                # Check role-based temperature cap
                if member:
                    max_temp = 1.2
                    for role_name, perks in Config.ROLE_PERKS.items():
                        role = discord.utils.get(member.roles, name=role_name)
                        if role:
                            max_temp = max(max_temp, perks.get("temp_max", 1.2))
                    temperature = min(temperature, max_temp)

                # Increment daily counter
                if effective_daily_limit < 999999:
                    await db.increment_daily_count()

                # Owner check
                from bot.services.owner_auth import owner_auth
                is_owner = await owner_auth.is_owner(author.id, via_secret=False)

                # Detect language of the user's message
                detected_lang = _detect_language(prompt)
                logger.debug(f"Detected language for user {author.id}: {detected_lang}")

                # Store the active prompt for potential interruption combining
                self._active_prompts[author.id] = prompt

                # ── STREAMING RESPONSE ──────────────────────────
                custom_prompt = await db.get_config("custom_prompt") or ""
                runner = KaufyRunner(author.id, db)
                full_response = ""
                all_files = []
                anything_sent = False
                flush_pos = 0
                # Track thinking blocks that have been sent to avoid re-sending
                sent_thinking_end = 0  # position in full_response up to which thinking was sent

                # Interruption check helper
                interrupt_event = self._interrupt_events.get(author.id)

                async def _check_interrupted() -> bool:
                    """Check if this processing has been interrupted by a new message.
                    Returns True if interrupted and should stop.
                    """
                    if interrupt_event and interrupt_event.is_set():
                        logger.info(f"Processing interrupted for user {author.id}")
                        await runner.stop()
                        return True
                    return False

                async def _flush(force: bool = False):
                    """Send any unsent portion of full_response to Discord.

                    - Extracts <thinking> blocks and sends them as quotes
                    - Sends response text in chunks of ≤1900 chars
                    - Only sends when: force=True OR ≥500 chars OR natural break
                    - Tracks sent thinking to avoid duplicates across flushes
                    """
                    nonlocal full_response, flush_pos, anything_sent, sent_thinking_end

                    unsent = full_response[flush_pos:]
                    if not unsent and not force:
                        return

                    # 1. Extract thinking blocks from the ENTIRE full_response
                    #    (not just unsent) so we catch blocks that span chunks
                    all_thinking = re.finditer(r'<thinking>(.*?)</thinking>', full_response, re.DOTALL)

                    # Send any new complete thinking blocks we haven't sent yet
                    for m in all_thinking:
                        end_pos = m.end()  # position of </thinking>
                        if end_pos > sent_thinking_end:
                            content = m.group(1).strip()
                            if content:
                                quoted = "\n".join(f"> {l}" for l in content.split("\n"))
                                try:
                                    await asyncio.sleep(0.2)
                                    await channel.send(f"💭 **Thinking:**\n{quoted}")
                                    anything_sent = True
                                except Exception as e:
                                    logger.error(f"Thinking send error: {e}")
                            sent_thinking_end = max(sent_thinking_end, end_pos)

                    # Also mark non-thinking content between flushes as processed
                    # for thinking, so we don't re-check old parts
                    sent_thinking_end = max(sent_thinking_end, flush_pos)

                    # 2. Clean thinking + preamble from unsent text for display
                    clean = re.sub(r'<thinking>.*?</thinking>', '', unsent, flags=re.DOTALL)
                    clean = re.sub(r'^! agent.*\n?', '', clean, flags=re.MULTILINE)
                    clean = re.sub(r'^> .*\n?', '', clean, flags=re.MULTILINE)
                    clean = clean.strip()

                    if not clean:
                        # Only had thinking — mark as processed
                        flush_pos = len(full_response)
                        return

                    # 3. Decide if should send now
                    should_send = force or len(clean) >= 500 or (
                        len(clean) >= 100 and any(
                            clean.rstrip().endswith(p) for p in ('.', '!', '?', ':\n', '\n\n')
                        )
                    )
                    if not should_send and not force:
                        return

                    # 4. Send to Discord
                    await asyncio.sleep(0.2)
                    try:
                        if len(clean) <= 1900:
                            await channel.send(clean)
                            anything_sent = True
                        else:
                            for i in range(0, len(clean), 1900):
                                await channel.send(clean[i:i+1900])
                                await asyncio.sleep(0.3)
                                anything_sent = True
                    except Exception as e:
                        logger.error(f"Flush error: {e}")

                    # 5. Mark all as processed
                    flush_pos = len(full_response)

                # Detect language and inject it
                lang_hint = f"[LANGUAGE DETECTED: {detected_lang}]"

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
                    detected_language=detected_lang,
                )

                stream = runner.run_stream(**stream_kwargs)

                # Process events — with interruption checks
                async for event in stream:
                    # Check for interruption between chunks
                    if await _check_interrupted():
                        break

                    if event["type"] == "chunk":
                        chunk = event["text"]
                        if chunk:
                            full_response += chunk
                            await _flush(force=False)

                    elif event["type"] == "file":
                        all_files.append(event["path"])

                    elif event["type"] == "done":
                        await _flush(force=True)

                    elif event["type"] == "error":
                        await _flush(force=True)
                        try:
                            await channel.send(event["text"])
                        except Exception:
                            pass
                        full_response = event["text"]

                await runner.stop()

                # Fallback: if nothing sent, do final flush
                if not anything_sent:
                    await _flush(force=True)

                if not anything_sent and full_response.strip():
                    clean = re.sub(r'<thinking>.*?</thinking>', '', full_response, flags=re.DOTALL).strip()
                    if clean:
                        try:
                            await channel.send(clean[:1900])
                            anything_sent = True
                        except Exception:
                            pass

                # If still nothing sent, extract thinking as last resort
                if not anything_sent and full_response.strip():
                    # Maybe it's ALL thinking tags — extract and send
                    thinking_match = re.search(r'<thinking>(.*?)</thinking>', full_response, re.DOTALL)
                    if thinking_match:
                        content = thinking_match.group(1).strip()
                        if content:
                            quoted = "\n".join(f"> {l}" for l in content.split("\n"))
                            try:
                                await channel.send(f"💭 **Thinking:**\n{quoted}")
                                anything_sent = True
                            except Exception:
                                pass

                if not anything_sent:
                    logger.warning(f"Empty response for user {author.id}: prompt={prompt[:100]!r}")

                # Attach files
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
                            await channel.send("📎 **Generated files:**", files=file_objs)
                        except Exception as e:
                            logger.error(f"Failed to send files: {e}")

                # Reactions
                try:
                    await message.remove_reaction(EMOJI_LOADING, self.bot.user)
                except:
                    pass
                try:
                    await message.add_reaction(EMOJI_CHECK)
                except:
                    pass

            except asyncio.CancelledError:
                logger.info(f"Processing cancelled for user {author.id} (interrupted)")
                # Don't send error — just stop
            except Exception as e:
                logger.error(f"Session error for {author.id}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                try:
                    await channel.send(f"An error occurred: {str(e)[:200]}")
                except:
                    pass
            finally:
                # Clean up active prompt tracking
                self._active_prompts.pop(author.id, None)

    async def _process_attachments(self, message: discord.Message) -> str:
        """Download attachments and return their content as context string."""
        if not message.attachments:
            return ""
        parts = []
        for att in message.attachments:
            try:
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
        """Handle messages in #msg channel OR DMs — queue for AI processing.

        If user has an active processing, it gets INTERRUPTED (stopped).
        The old context + new message are combined for a fresh response.
        """
        if message.author.bot:
            return

        # 🛡️ Check blacklist
        db_check = UserDatabase(message.author.id)
        await db_check.init()
        bl = await db_check.get_config("blacklisted") or "false"
        if bl == "true":
            return

        # Handle DMs
        if isinstance(message.channel, discord.DMChannel):
            # DM dedup: prevent duplicate queuing
            if message.id in self._dm_processed:
                return
            self._dm_processed.add(message.id)
            # Keep DM dedup set bounded
            if len(self._dm_processed) > 2000:
                self._dm_processed.clear()

            plan = await db_check.get_config("plan") or "free"

            # 👑 Owners + paid plans bypass DM restriction (free users are blocked)
            from bot.services.owner_auth import owner_auth
            is_owner_dm = await owner_auth.is_owner(message.author.id, via_secret=False)
            if plan == "free" and not is_owner_dm:
                await message.channel.send(
                    "💬 **DMs are only available for paid users.**\n"
                    "Redeem a key with `/redeem <code>` or purchase a plan to unlock DM chatting."
                )
                return

            # Queue DM message
            prompt = message.content
            att_text = await self._process_attachments(message)
            if att_text:
                prompt = f"{prompt}\n\n{att_text}" if prompt else att_text

            session = await session_manager.get_or_create(message.author.id, message.channel.id)
            db_check.plan = plan

            # Interrupt any existing processing for this user
            await self._interrupt_user(message.author.id)

            await self._queue.put((message, message.channel, message.author, db_check, prompt))
            try:
                await message.add_reaction(EMOJI_LOADING)
            except:
                pass
            return

        # 🛡️ Guild channel dedup FIFO
        if message.id in self._processed_ids:
            logger.debug(f"Dedup: skipping already-processed message {message.id}")
            return
        self._processed_ids.add(message.id)
        self._processed_order.append(message.id)
        while len(self._processed_ids) > self._processed_max:
            oldest = self._processed_order.pop(0)
            self._processed_ids.discard(oldest)

        # Check if this is a user's msg channel (starts with "msg-")
        if not hasattr(message.channel, 'name') or not message.channel.name:
            return
        ch_base = _channel_base(message.channel.name)
        if ch_base != Config.CHANNEL_MSG:
            logger.debug(f"Ignored msg in #{message.channel.name} (base={ch_base}, need={Config.CHANNEL_MSG})")
            return

        # 🔒 Channel open mode: if channel is NOT open, only respond to channel owner
        if message.channel.id not in self._open_channels:
            ch_owner = _channel_owner_name(message.channel.name)
            author_name = str(message.author).lower()
            if ch_owner and author_name != ch_owner:
                logger.debug(f"Ignored non-owner msg in closed channel #{message.channel.name}")
                return

        # Ignore commands (start with .)
        if message.content.startswith("."):
            return

        # Build prompt
        prompt = message.content
        att_text = await self._process_attachments(message)
        if att_text:
            prompt = f"{prompt}\n\n{att_text}" if prompt else att_text

        # Get or create session
        session = await session_manager.get_or_create(message.author.id, message.channel.id)
        plan = await db_check.get_config("plan") or "free"
        session.plan = plan

        # ⚡ INTERRUPTION: if user has active processing, combine old + new and re-queue
        if message.author.id in self._interrupt_events:
            old_event = self._interrupt_events.get(message.author.id)
            if old_event and not old_event.is_set():
                logger.info(f"User {message.author.id} sent new msg while processing — interrupting + combining")
                old_event.set()

                # Get old prompt and combine with new one
                old_prompt = self._active_prompts.get(message.author.id, "")
                if old_prompt:
                    combined_prompt = (
                        f"[Mensagem anterior do usuário]: {old_prompt}\n\n"
                        f"[Nova mensagem do usuário]: {prompt}\n\n"
                        f"[Combine as duas mensagens em uma resposta só. "
                        f"O usuário enviou a primeira, depois enviou a segunda "
                        f"ANTES de você terminar de responder. Responda como se "
                        f"fosse uma única solicitação combinada.]"
                    )
                    prompt = combined_prompt
                    logger.info(f"Combined old+new prompts for user {author.id}")

                # Store the new message to be re-queued AFTER old processing finishes
                pending_list = self._interrupted.setdefault(message.author.id, [])
                pending_list.append((message, message.channel, message.author, db_check, prompt))
                try:
                    await message.add_reaction(EMOJI_LOADING)
                except:
                    pass
                return

        # Normal queue
        await self._queue.put((message, message.channel, message.author, db_check, prompt))
        try:
            await message.add_reaction(EMOJI_LOADING)
        except:
            pass

    # ─── User-Facing Hybrid Commands ──────────────────────────

    @commands.hybrid_command(name="onc")
    async def open_channel(self, ctx: commands.Context):
        """Open your channel so the bot responds to everyone."""
        ch_base = _channel_base(ctx.channel.name) if hasattr(ctx.channel, 'name') and ctx.channel.name else ""
        if isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command only works in #msg channels.")
            return
        if ch_base != Config.CHANNEL_MSG:
            return
        # Only channel owner can open a closed channel
        ch_owner = _channel_owner_name(ctx.channel.name)
        author_name = str(ctx.author).lower()
        if ctx.channel.id not in self._open_channels and ch_owner and author_name != ch_owner:
            await ctx.send("❌ Só o dono do canal pode usar `.onc`.", ephemeral=True)
            return
        self._open_channels.add(ctx.channel.id)
        await ctx.send("✅ **Channel aberto!** Agora respondo todo mundo aqui.")

    @commands.hybrid_command(name="ofc")
    async def close_channel(self, ctx: commands.Context):
        """Close your channel so the bot only responds to you."""
        ch_base = _channel_base(ctx.channel.name) if hasattr(ctx.channel, 'name') and ctx.channel.name else ""
        if isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command only works in #msg channels.")
            return
        if ch_base != Config.CHANNEL_MSG:
            return
        # Only channel owner can close
        ch_owner = _channel_owner_name(ctx.channel.name)
        author_name = str(ctx.author).lower()
        if ch_owner and author_name != ch_owner:
            await ctx.send("❌ Só o dono do canal pode usar `.ofc`.", ephemeral=True)
            return
        self._open_channels.discard(ctx.channel.id)
        await ctx.send("🔒 **Channel fechado!** Só respondo ao dono do canal.")

    @commands.hybrid_command(name="clear")
    async def clear_context(self, ctx: commands.Context):
        """Clear your conversation context (reset chat history)."""
        ch_base = _channel_base(ctx.channel.name) if hasattr(ctx.channel, 'name') and ctx.channel.name else ""
        if isinstance(ctx.channel, discord.DMChannel):
            pass  # DM is fine
        elif ch_base != Config.CHANNEL_MSG:
            return
        db = UserDatabase(ctx.author.id)
        await db.init()
        async with db._lock:
            async with aiosqlite.connect(db.db_path) as conn:
                await conn.execute("DELETE FROM messages WHERE role IN ('user', 'assistant')")
                await conn.commit()
        await ctx.send("✅ Conversation context cleared.")

    @commands.hybrid_command(name="planinfo")
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
        embed.add_field(name="Daily Messages", value="♾️ Unlimited" if pc.get("daily_messages", 0) >= 999999 else f"{pc['daily_messages']}/day")
        embed.add_field(name="Context Memory", value=f"{pc.get('context_messages', 10)} messages")
        embed.add_field(name="Max Tokens", value=f"{pc.get('max_tokens_allowed', 4096)}")
        embed.add_field(name="Thinking Mode", value="✅" if pc.get("thinking") else "❌")
        embed.add_field(name="Web Search", value="✅" if pc.get("web_search") else "❌")
        embed.add_field(name="Priority Queue", value="✅" if pc.get("priority_queue") else "❌")
        embed.add_field(name="File Upload", value="✅" if pc.get("file_upload") else "❌")
        embed.add_field(name="Custom Prompt", value="✅" if pc.get("custom_prompt") else "❌")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="search")
    async def web_search(self, ctx: commands.Context, *, query: str):
        """Search the web — paid plans only. Uses web search results as context."""
        ch_base = _channel_base(ctx.channel.name) if hasattr(ctx.channel, 'name') and ctx.channel.name else ""
        if isinstance(ctx.channel, discord.DMChannel):
            pass
        elif ch_base != Config.CHANNEL_MSG:
            return

        db = UserDatabase(ctx.author.id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
        if not plan_config.get("web_search", False):
            await ctx.send("🌐 Web search is available on paid plans (7d, 14d, 30d, lifetime).")
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
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
            results = []
            for s in snippets[:5]:
                clean = re.sub(r'<[^>]+>', '', s).strip()
                if clean:
                    results.append(clean)
            web_context = "Web search results for: " + query + "\n" + "\n".join(f"- {r}" for r in results) if results else "No results found."
        except Exception as e:
            web_context = f"[Web search failed: {e}]"

        from bot.services.owner_auth import owner_auth
        is_owner = await owner_auth.is_owner(ctx.author.id, via_secret=False)
        runner = KaufyRunner(ctx.author.id, db)
        full_response = ""

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
                await ctx.send(event["text"])
            elif event["type"] == "done":
                pass
            elif event["type"] == "error":
                await ctx.send(event["text"])
                full_response = event["text"]

        await runner.stop()


async def setup(bot: commands.Bot):
    await bot.add_cog(SessionCog(bot))
