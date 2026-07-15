"""Key database — /redeem system for plan activation keys."""
import sqlite3
import json
import time
import asyncio
import logging
import aiosqlite
import secrets
import string
from pathlib import Path
from typing import Optional, List, Dict
from bot.config import Config

logger = logging.getLogger("kaufy.keys")


class KeyDatabase:
    """Manages plan activation keys."""

    def __init__(self):
        self.db_path = str(Config.DB_DIR / "keys.db")

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    plan TEXT NOT NULL,
                    duration_days INTEGER NOT NULL DEFAULT 30,
                    max_uses INTEGER NOT NULL DEFAULT 1,
                    uses INTEGER NOT NULL DEFAULT 0,
                    created_by INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER DEFAULT 0,
                    revoked INTEGER NOT NULL DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS redemptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    redeemed_at INTEGER NOT NULL,
                    FOREIGN KEY (key_id) REFERENCES keys(id)
                )
            """)
            await db.commit()

    @staticmethod
    def generate_key(length: int = 24) -> str:
        """Generate a cryptographically secure key code.
        
        Format: XXXX-XXXX-XXXX-XXXX-XXXX (24 chars, 4-char groups)
        """
        chars = string.ascii_uppercase + string.digits
        raw = ''.join(secrets.choice(chars) for _ in range(length))
        groups = [raw[i:i+4] for i in range(0, length, 4)]
        return '-'.join(groups)

    async def create_key(
        self, plan: str, duration_days: int = 30,
        max_uses: int = 1, created_by: int = 0,
        custom_code: Optional[str] = None
    ) -> str:
        """Create a new activation key. Returns the key code."""
        code = custom_code or self.generate_key()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO keys 
                   (code, plan, duration_days, max_uses, uses, created_by, created_at)
                   VALUES (?, ?, ?, ?, 0, ?, ?)""",
                (code, plan, duration_days, max_uses, created_by, int(time.time()))
            )
            await db.commit()
        return code

    async def redeem_key(self, code: str, user_id: int) -> Optional[Dict]:
        """Redeem a key for a user. Returns plan info or None if invalid."""
        code = code.strip().upper()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM keys WHERE code = ?", (code,)
            )
            row = await cursor.fetchone()
            if not row:
                return None  # Invalid key
            
            key = dict(row)
            
            # Check if revoked
            if key["revoked"]:
                return {"error": "This key has been revoked."}
            
            # Check if expired
            if key["expires_at"] and time.time() > key["expires_at"]:
                return {"error": "This key has expired."}
            
            # Check if max uses reached
            if key["uses"] >= key["max_uses"]:
                return {"error": "This key has already been used."}
            
            # Check if this user already redeemed this key
            cursor = await db.execute(
                "SELECT id FROM redemptions WHERE key_id = ? AND user_id = ?",
                (key["id"], user_id)
            )
            if await cursor.fetchone():
                return {"error": "You have already redeemed this key."}
            
            # Redeem
            await db.execute(
                "UPDATE keys SET uses = uses + 1 WHERE id = ?", (key["id"],)
            )
            await db.execute(
                "INSERT INTO redemptions (key_id, user_id, redeemed_at) VALUES (?, ?, ?)",
                (key["id"], user_id, int(time.time()))
            )
            await db.commit()
            
            return {
                "plan": key["plan"],
                "duration_days": key["duration_days"],
            }

    async def list_keys(self, page: int = 0, per_page: int = 20) -> List[Dict]:
        """List all keys with pagination."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM keys ORDER BY id DESC LIMIT ? OFFSET ?",
                (per_page, page * per_page)
            )
            return [dict(r) for r in await cursor.fetchall()]

    async def revoke_key(self, code: str) -> bool:
        """Revoke a key (make it unusable)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE keys SET revoked = 1 WHERE code = ?", (code,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_stats(self) -> Dict:
        """Get key system stats."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM keys")
            total = (await cursor.fetchone())[0]
            cursor = await db.execute("SELECT COUNT(*) FROM keys WHERE revoked = 1")
            revoked = (await cursor.fetchone())[0]
            cursor = await db.execute("SELECT COUNT(*) FROM redemptions")
            redemptions = (await cursor.fetchone())[0]
            return {
                "total_keys": total,
                "revoked_keys": revoked,
                "active_keys": total - revoked,
                "total_redemptions": redemptions,
            }

    async def close(self):
        pass


# Singleton
key_db = KeyDatabase()

import logging
