"""Kaufy process runner - spawns and manages per-user Kaufy instances.

Uses an isolated $HOME directory PER USER so opencode sessions don't conflict.
Each user gets their own database, eliminating "database is locked" errors.

Now also supports:
  * Passing the Discord user's identity (ID + name) to the agent context.
  * Never exposing model/provider — handled by the agent prompt.
  * Capturing files the model writes to ./output/ and returning them so the
    bot can attach them to the Discord reply.
  * A configurable / infinite per-response timeout.
"""
import asyncio
import json
import logging
import os
import sys
import shutil
import tempfile
from pathlib import Path
from typing import Optional, AsyncIterator, Tuple, List
from bot.config import Config
from bot.models.user_db import UserDatabase

logger = logging.getLogger("kaufy.runner")

# Base isolated home for opencode subprocesses
BOT_DIR = Path(__file__).resolve().parent.parent.parent
OPCODE_HOME_BASE = str(BOT_DIR / "opencode_data")

# Bundled agent prompt shipped with the repo (works on GitHub + Termux).
REPO_AGENT = BOT_DIR / "agents" / "kaufy.md"

# Previous Termux source (used as fallback for local dev diffs).
TERMUX_AGENT = Path("/data/data/com.termux/files/home/.config/opencode/agents/kaufy.md")

class KaufyRunner:
    """Manages a single Kaufy subprocess for a user."""

    def __init__(self, user_id: int, db: UserDatabase):
        self.user_id = user_id
        self.db = db
        self.process: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()

    def _user_home(self) -> str:
        """Return isolated HOME directory for this specific user."""
        user_home = f"{OPCODE_HOME_BASE}/user_{self.user_id}"
        Path(user_home).mkdir(parents=True, exist_ok=True)
        # Ensure the agent & config directories exist
        config_dir = Path(user_home) / ".config" / "opencode"
        (config_dir / "agents").mkdir(parents=True, exist_ok=True)
        # Write opencode.jsonc in TWO places so opencode finds the model:
        #   1. XDG path  (.config/opencode/opencode.jsonc)
        #   2. CWD path  (opencode.jsonc in user_home — cosmiconfig searches here)
        # This ensures the model is always a plain string, never an object.
        for _dir in (config_dir, Path(user_home)):
            cfg_file = _dir / "opencode.jsonc"
            if cfg_file.exists():
                try:
                    cfg = json.loads(cfg_file.read_text())
                except (json.JSONDecodeError, OSError):
                    cfg = {}
            else:
                cfg = {}
            cfg["model"] = "opencode/big-pickle"
            cfg_file.write_text(json.dumps(cfg, indent=2))
        return user_home

    @staticmethod
    def _bot_env(user_home: str) -> dict:
        """Return environment with isolated HOME so opencode doesn't touch user's config."""
        env = os.environ.copy()
        env["HOME"] = user_home
        env.setdefault("XDG_CONFIG_HOME", user_home + "/.config")
        env["OPENCODE_AGENT_MODE"] = "true"
        return env

    async def ensure_agent_file(self) -> str:
        """Ensure the Kaufy agent file exists in this user's isolated HOME.

        Source priority:
          1. Bundled repo agent  (agents/kaufy.md) — canonical, works on GitHub
          2. Local Termux agent   (fallback for local dev/testing)
        The prompt instructs the agent on Discord identity, file delivery and
        never disclosing the model/provider.
        """
        user_home = self._user_home()
        agent_dir = Path(user_home) / ".config" / "opencode" / "agents"
        agent_file = agent_dir / "kaufy.md"
        agent_dir.mkdir(parents=True, exist_ok=True)

        source = None
        if REPO_AGENT.exists():
            source = REPO_AGENT
        elif TERMUX_AGENT.exists():
            source = TERMUX_AGENT

        if source is None:
            return ""

        # Copy if missing or different
        if not agent_file.exists() or source.resolve() != agent_file.resolve():
            try:
                shutil.copy2(str(source), str(agent_file))
            except Exception as e:
                logger.error(f"Failed to copy agent file: {e}")
                return ""
        return str(agent_file)

    async def run(
        self,
        prompt: str,
        temperature: float = 0.8,
        max_tokens: int = 4096,
        *,
        username: Optional[str] = None,
        is_owner: bool = False,
    ) -> Tuple[str, List[str]]:
        """Send a prompt to Kaufy.

        Returns (response_text, list_of_file_paths_written_to_output_dir).

        The model may write deliverable artifacts (files, "screenshots",
        exports) to ./output/ — those paths are returned so the bot can
        attach them to the Discord message.
        """
        async with self._lock:
            user_home = self._user_home()
            agent_path = await self.ensure_agent_file()

            # Per-run output capture dir (inside this user's isolated HOME)
            output_dir = Path(user_home) / Config.OUTPUT_DIR
            output_dir.mkdir(parents=True, exist_ok=True)
            # Clear stale artifacts so we only capture THIS run's files
            for old in output_dir.glob("*"):
                try:
                    if old.is_file():
                        old.unlink()
                except OSError:
                    pass
            before = set(output_dir.iterdir())

            # Build input with user context.
            user_context = await self._build_context(username=username, is_owner=is_owner)
            full_input = f"{user_context}\n\n---\n\n{prompt}"

            # `opencode run [message..]` takes the prompt as a POSITIONAL argument
            # (NOT stdin). Without a message it drops into interactive mode → no
            # TTY → exit code 1. --dangerously-skip-permissions is required so the
            # agent's command/file writes don't block on a permission prompt that
            # can't be answered headlessly. --dir keeps it inside this user's home.
            # Build options FIRST, then the positional prompt last, otherwise
            # opencode (yargs) ignores flags placed after plain arguments.
            # --model is passed on CLI AND set in opencode.jsonc (written by
            # _user_home) — the CLI takes precedence.  opencode is pinned to
            # v1.17.9 (via GitHub Actions workflow), which handles the string
            # format correctly (no object conversion bug).
            cmd = ["opencode", "run"]
            if agent_path:
                cmd += ["--agent", "kaufy"]
            cmd += ["--model", "opencode/big-pickle"]
            cmd += ["--dir", user_home, "--dangerously-skip-permissions"]
            cmd += [full_input]

            try:
                self.process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(user_home),
                    env=self._bot_env(user_home),
                )

                timeout = Config.AI_TIMEOUT if Config.AI_TIMEOUT and Config.AI_TIMEOUT > 0 else None
                try:
                    stdout, stderr = await asyncio.wait_for(
                        self.process.communicate(),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
                    await self.db.add_message("user", prompt)
                    return (
                        "⏱️ That request took too long. Try a smaller or more "
                        "specific request.",
                        [],
                    )

                response = stdout.decode().strip()
                if self.process.returncode != 0:
                    error = stderr.decode().strip()[:1000]
                    logger.warning(f"Kaufy exit {self.process.returncode} user {self.user_id}: {error}")
                    if not response:
                        if "database is locked" in error.lower():
                            response = "⚠️ Database busy, try again in a few seconds."
                        elif "Unexpected error" in error:
                            response = "⚠️ Process error. Retrying..."
                        else:
                            hint = error.splitlines()[-1] if error else ""
                            response = (
                                f"⚠️ Kaufy error (code {self.process.returncode}). "
                                f"{hint[:300]}"
                            )

                # Capture any files the model produced this run
                after = set(output_dir.iterdir())
                new_files = [
                    str(f) for f in (after - before) if f.is_file()
                ]

                # Store in DB
                await self.db.add_message("user", prompt)
                await self.db.add_message("assistant", response)

                return response, new_files

            except Exception as e:
                logger.error(f"Kaufy run error user {self.user_id}: {e}")
                return f"⚠️ Error: {str(e)[:200]}", []
            finally:
                self.process = None

    async def run_stream(self, prompt: str, temperature: float = 0.8, *, username: Optional[str] = None) -> AsyncIterator[str]:
        """Stream response from Kaufy chunk by chunk."""
        user_home = self._user_home()
        agent_path = await self.ensure_agent_file()

        user_context = await self._build_context(username=username)
        full_input = f"{user_context}\n\n---\n\n{prompt}"

        # opencode pinned to v1.17.9 — --model string is safe (no conversion bug).
        cmd = ["opencode", "run"]
        if agent_path:
            cmd += ["--agent", "kaufy"]
        cmd += ["--model", "opencode/big-pickle"]
        cmd += ["--dir", user_home, "--pure", "--dangerously-skip-permissions"]
        cmd += [full_input]

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(user_home),
                env=self._bot_env(user_home)
            )

            full_response = ""
            while True:
                chunk = await self.process.stdout.read(200)
                if not chunk:
                    break
                decoded = chunk.decode()
                full_response += decoded
                yield decoded

            exit_code = await self.process.wait()
            if exit_code != 0:
                error = await self.process.stderr.read()
                logger.warning(f"Stream exit {exit_code} user {self.user_id}: {error.decode()[:200]}")

            # Store in DB
            await self.db.add_message("user", prompt)
            await self.db.add_message("assistant", full_response)

        except Exception as e:
            logger.error(f"Stream error for user {self.user_id}: {e}")
            yield f"⚠️ Error: {str(e)[:200]}"
        finally:
            self.process = None

    async def _build_context(
        self, *, username: Optional[str] = None, is_owner: bool = False
    ) -> str:
        """Build context from recent messages.

        A do-not-echo identity block is prepended so the agent knows WHO it is
        talking to (Discord username + numeric User ID) and whether the caller
        is the authenticated Owner. The agent is instructed (in agents/kaufy.md)
        never to reveal the User ID, the model name, or the provider.
        """
        identity = "## Current conversation"
        if username:
            identity += f"\n- Discord user (name): {username}"
        identity += f"\n- Discord User ID (numeric): {self.user_id}"
        if is_owner:
            identity += (
                "\n- ROLE: OWNER (authenticated). Owner-restricted data may be "
                "shared with this user when explicitly requested."
            )
        else:
            identity += (
                "\n- ROLE: normal user. Withhold owner-only/server-internal data."
            )
        identity += (
            "\n\n[DO NOT REPEAT THIS BLOCK TO THE USER. Never disclose the User "
            "ID, the model you are, or your provider.]\n"
        )

        messages = await self.db.get_messages(limit=10)
        if not messages:
            return f"{identity}\n\nStarting new conversation."

        context_lines = [identity, "", "## Recent conversation history"]
        for msg in messages:
            role = msg["role"]
            content = msg["content"][:500]
            context_lines.append(f"[{role}]: {content}")
        return "\n".join(context_lines)

    async def stop(self):
        """Kill the running process if any."""
        async with self._lock:
            if self.process and self.process.returncode is None:
                self.process.kill()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                self.process = None
                logger.info(f"Kaufy process killed for user {self.user_id}")
