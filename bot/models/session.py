"""Session management model for per-user Kaufy instances."""
import time
import asyncio
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from bot.models.user_db import UserDatabase

logger = logging.getLogger("kaufy.session")

@dataclass
class UserSession:
    """Represents a single user's active session with Kaufy."""
    user_id: int
    channel_id: int
    thread_id: Optional[int] = None
    db: Optional[UserDatabase] = None
    last_active: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    is_active: bool = True
    context_messages: list = field(default_factory=list)
    plan: str = "free"  # free | pro | premium

    async def init_db(self):
        self.db = UserDatabase(self.user_id)
        await self.db.init()

    async def touch(self):
        self.last_active = time.time()

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_active

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "thread_id": self.thread_id,
            "last_active": self.last_active,
            "created_at": self.created_at,
            "is_active": self.is_active,
            "idle_seconds": self.idle_seconds,
            "age_seconds": self.age_seconds,
            "plan": self.plan,
        }


class SessionManager:
    """Manages all active user sessions."""

    def __init__(self):
        self.sessions: Dict[int, UserSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def get_or_create(self, user_id: int, channel_id: int) -> UserSession:
        async with self._lock:
            if user_id in self.sessions:
                session = self.sessions[user_id]
                session.channel_id = channel_id
                await session.touch()
                return session
            session = UserSession(user_id=user_id, channel_id=channel_id)
            await session.init_db()
            self.sessions[user_id] = session
            logger.info(f"Session created for user {user_id}")
            return session

    async def get(self, user_id: int) -> Optional[UserSession]:
        async with self._lock:
            return self.sessions.get(user_id)

    async def remove(self, user_id: int):
        async with self._lock:
            if user_id in self.sessions:
                session = self.sessions[user_id]
                if session.db:
                    await session.db.close()
                del self.sessions[user_id]
                logger.info(f"Session removed for user {user_id}")

    async def cleanup_stale(self, timeout: int = 600):
        """Remove sessions that have been idle for too long."""
        now = time.time()
        to_remove = []
        async with self._lock:
            for uid, session in self.sessions.items():
                if now - session.last_active > timeout:
                    to_remove.append(uid)
        for uid in to_remove:
            await self.remove(uid)
            logger.info(f"Stale session cleaned up for user {uid}")
        return len(to_remove)

    async def get_all(self) -> Dict[int, UserSession]:
        async with self._lock:
            return dict(self.sessions)

    async def start_cleanup_loop(self, interval: int = 60):
        """Periodically clean up stale sessions."""
        from bot.config import Config
        while True:
            await asyncio.sleep(interval)
            try:
                count = await self.cleanup_stale(Config.SESSION_TIMEOUT)
                if count:
                    logger.info(f"Cleanup: removed {count} stale sessions")
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    def start_background_cleanup(self):
        """Start the cleanup loop as a background task."""
        loop = asyncio.get_event_loop()
        self._cleanup_task = loop.create_task(self.start_cleanup_loop())

    def stop_background_cleanup(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None


# Singleton
session_manager = SessionManager()
