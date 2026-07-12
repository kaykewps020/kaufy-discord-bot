"""Per-user SQLite database with 800-message FIFO eviction."""
import sqlite3
import json
import time
import asyncio
import aiosqlite
from pathlib import Path
from typing import Optional, List, Dict, Any
from bot.config import Config
from bot.utils.helpers import user_db_path

class UserDatabase:
    """Isolated database for a single user. Thread-safe via aiosqlite."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.db_path = user_db_path(user_id)
        self._lock = asyncio.Lock()

    async def init(self):
        """Create tables if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    token_count INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    content BLOB,
                    mime_type TEXT DEFAULT 'text/plain',
                    created_at INTEGER NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service TEXT NOT NULL,
                    token_encrypted TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            await db.execute("""
                INSERT OR IGNORE INTO config (key, value) VALUES ('temperature', '0.8')
            """)
            await db.execute("""
                INSERT OR IGNORE INTO config (key, value) VALUES ('max_tokens', '4096')
            """)
            await db.execute("""
                INSERT OR IGNORE INTO config (key, value) VALUES ('model', 'opencode/big-pickle')
            """)
            await db.execute("""
                INSERT OR IGNORE INTO config (key, value) VALUES ('user_id', ?)
            """, (str(self.user_id),))
            await db.commit()

    async def add_message(self, role: str, content: str, token_count: int = 0):
        """Add a message and enforce 800-message FIFO limit."""
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO messages (role, content, created_at, token_count) VALUES (?, ?, ?, ?)",
                    (role, content, int(time.time()), token_count)
                )
                await db.commit()

                # Enforce 800 message limit: delete oldest if over
                cursor = await db.execute("SELECT COUNT(*) FROM messages")
                row = await cursor.fetchone()
                count = row[0]
                if count > Config.MAX_MESSAGES_PER_USER:
                    excess = count - Config.MAX_MESSAGES_PER_USER
                    await db.execute(f"""
                        DELETE FROM messages WHERE id IN (
                            SELECT id FROM messages ORDER BY id ASC LIMIT ?
                        )
                    """, (excess,))
                    await db.commit()

    async def get_messages(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        """Get recent messages for context."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
            rows = await cursor.fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def get_message_count(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM messages")
            row = await cursor.fetchone()
            return row[0]

    async def get_config(self, key: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_config(self, key: str, value: str):
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                    (key, value)
                )
                await db.commit()

    async def save_token(self, service: str, token_encrypted: str):
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO tokens (service, token_encrypted, created_at) VALUES (?, ?, ?)",
                    (service, token_encrypted, int(time.time()))
                )
                await db.commit()

    async def get_all_tokens(self) -> List[Dict]:
        """Get ALL token records with full data."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM tokens ORDER BY id DESC")
            rows = await cursor.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                # Parse token_encrypted as JSON data for convenience
                try:
                    data = json.loads(d["token_encrypted"])
                    d["data"] = d["token_encrypted"]
                    d["type"] = data.get("type", d["service"])
                except:
                    d["data"] = d["token_encrypted"]
                    d["type"] = d["service"]
                result.append(d)
            return result

    async def get_tokens(self, service: Optional[str] = None) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if service:
                cursor = await db.execute(
                    "SELECT * FROM tokens WHERE service = ? ORDER BY id DESC", (service,)
                )
            else:
                cursor = await db.execute("SELECT * FROM tokens ORDER BY id DESC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def save_file(self, filename: str, content: bytes, mime_type: str = "text/plain"):
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO files (filename, content, mime_type, created_at) VALUES (?, ?, ?, ?)",
                    (filename, content, mime_type, int(time.time()))
                )
                await db.commit()

    async def get_files(self) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, filename, mime_type, created_at FROM files ORDER BY id DESC"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_daily_count(self) -> int:
        """Get today's message count for daily limit."""
        today_key = f"daily_count_{int(time.time() / 86400)}"
        val = await self.get_config(today_key)
        return int(val) if val else 0

    async def increment_daily_count(self) -> int:
        """Increment daily message count. Returns new count."""
        today_key = f"daily_count_{int(time.time() / 86400)}"
        current = await self.get_daily_count()
        new_count = current + 1
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                    (today_key, str(new_count))
                )
                await db.commit()
        return new_count

    async def get_all_config(self) -> Dict[str, str]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT key, value FROM config")
            rows = await cursor.fetchall()
            return {r["key"]: r["value"] for r in rows}

    async def get_stats(self) -> Dict:
        """Get user DB statistics."""
        msg_count = await self.get_message_count()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM files")
            file_count = (await cursor.fetchone())[0]
            cursor = await db.execute("SELECT COUNT(*) FROM tokens")
            token_count = (await cursor.fetchone())[0]
        return {
            "user_id": self.user_id,
            "messages": msg_count,
            "max_messages": Config.MAX_MESSAGES_PER_USER,
            "files": file_count,
            "tokens": token_count,
        }

    async def export(self) -> bytes:
        """Export the entire DB as bytes for backup."""
        async with self._lock:
            with open(self.db_path, "rb") as f:
                return f.read()

    async def import_db(self, data: bytes):
        """Overwrite the entire DB from backup bytes."""
        async with self._lock:
            with open(self.db_path, "wb") as f:
                f.write(data)

    async def close(self):
        """No-op for SQLite, connection per-query."""
        pass
