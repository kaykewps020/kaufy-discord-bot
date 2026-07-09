"""Kaufy process runner - spawns and manages per-user Kaufy instances.

Uses an isolated $HOME directory PER USER so opencode sessions don't conflict.
Each user gets their own database, eliminating "database is locked" errors.
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional, AsyncIterator
from bot.models.user_db import UserDatabase

logger = logging.getLogger("kaufy.runner")

# Base isolated home for opencode subprocesses
BOT_DIR = Path(__file__).parent.parent.parent
OPCODE_HOME_BASE = str(BOT_DIR / "opencode_data")

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
        # Ensure the agent directory exists
        agent_dir = Path(user_home) / ".config" / "opencode" / "agents"
        agent_dir.mkdir(parents=True, exist_ok=True)
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
        """Ensure the Kaufy agent file exists in this user's isolated HOME."""
        user_home = self._user_home()
        agent_dir = Path(user_home) / ".config" / "opencode" / "agents"
        agent_file = agent_dir / "kaufy.md"

        # Source agent file (global config — absolute path, not Path.home())
        global_agent = Path("/data/data/com.termux/files/home/.config/opencode/agents/kaufy.md")

        # Always sync from global config to user's isolated HOME
        if global_agent.exists() and global_agent.resolve() != agent_file.resolve():
            agent_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(str(global_agent), str(agent_file))
        elif not agent_file.exists():
            return ""

        return str(agent_file)

    async def run(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096) -> str:
        """Send a prompt to Kaufy and get the response."""
        user_home = self._user_home()
        agent_path = await self.ensure_agent_file()

        cmd = ["opencode", "run"]
        if agent_path:
            cmd.extend(["--agent", "kaufy"])

        # Build input with user context
        user_context = await self._build_context()
        full_input = f"{user_context}\n\n---\n\n{prompt}"

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._bot_env(user_home)
            )

            stdout, stderr = await asyncio.wait_for(
                self.process.communicate(full_input.encode()),
                timeout=180
            )

            response = stdout.decode().strip()
            if self.process.returncode != 0:
                error = stderr.decode().strip()[:500]
                logger.warning(f"Kaufy exit code {self.process.returncode} for user {self.user_id}: {error}")
                if not response:
                    # Check for specific errors
                    if "database is locked" in error.lower():
                        response = "⚠️ Database busy, try again in a few seconds."
                    elif "Unexpected error" in error:
                        response = "⚠️ Process error. Retrying..."
                    else:
                        response = f"⚠️ Kaufy process error (code {self.process.returncode})"

            # Store in DB
            await self.db.add_message("user", prompt)
            await self.db.add_message("assistant", response)

            return response

        except asyncio.TimeoutError:
            if self.process:
                self.process.kill()
            return "⏱️ Request timed out after 180 seconds. Please try again."
        except Exception as e:
            logger.error(f"Kaufy run error for user {self.user_id}: {e}")
            return f"⚠️ Error: {str(e)[:200]}"
        finally:
            self.process = None

    async def run_stream(self, prompt: str, temperature: float = 0.8) -> AsyncIterator[str]:
        """Stream response from Kaufy chunk by chunk."""
        user_home = self._user_home()
        agent_path = await self.ensure_agent_file()
        cmd = ["opencode", "run"]
        if agent_path:
            cmd.extend(["--agent", "kaufy"])

        user_context = await self._build_context()
        full_input = f"{user_context}\n\n---\n\n{prompt}"

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._bot_env(user_home)
            )

            # Write input and close stdin
            self.process.stdin.write(full_input.encode())
            await self.process.stdin.drain()
            self.process.stdin.close()

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

    async def _build_context(self) -> str:
        """Build context from recent messages."""
        messages = await self.db.get_messages(limit=10)
        if not messages:
            return "Starting new conversation."
        context_lines = []
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
