"""Utility helpers for the bot."""
import re
import time
import hashlib
from datetime import datetime, timezone

def sanitize_text(text: str, max_len: int = 2000) -> str:
    """Sanitize and truncate text for Discord."""
    text = text.strip()
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text

def user_db_path(user_id: int) -> str:
    """Get the filesystem path for a user's database."""
    from bot.config import Config
    return str(Config.DB_DIR / f"user_{user_id}.db")

def hash_user_id(user_id: int) -> str:
    """One-way hash of user ID for anonymous logging."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:12]

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def timestamp() -> int:
    return int(time.time())

def format_uptime(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    parts = []
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)

def progress_bar(current: int, total: int, width: int = 20) -> str:
    filled = int(width * current / total) if total else 0
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {current}/{total}"

def escape_discord(text: str) -> str:
    """Escape Discord markdown special characters."""
    return re.sub(r'([*_~`|>])', r'\\\1', text)
