"""Backup and restore service for user databases."""
import asyncio
import json
import logging
import shutil
import tarfile
import time
import io
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from bot.config import Config
from bot.models.user_db import UserDatabase

logger = logging.getLogger("kaufy.backup")

class BackupService:
    """Handles periodic backup and fast restore of user databases."""

    def __init__(self):
        self._backup_task: Optional[asyncio.Task] = None

    async def backup_all(self, user_ids: List[int] = None) -> str:
        """Backup all user databases to a tar.gz file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{timestamp}.tar.gz"
        backup_path = Config.BACKUP_DIR / backup_name

        db_dir = Config.DB_DIR
        if not db_dir.exists():
            return "No databases to backup."

        if user_ids is None:
            user_ids = [int(p.stem.replace("user_", ""))
                        for p in db_dir.glob("user_*.db")]

        with tarfile.open(backup_path, "w:gz") as tar:
            for uid in user_ids:
                db_file = db_dir / f"user_{uid}.db"
                if db_file.exists():
                    tar.add(db_file, arcname=f"user_{uid}.db")

            # Also backup config
            config_file = Config.BASE_DIR / "config.local.json"
            if config_file.exists():
                tar.add(config_file, arcname="config.json")

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        logger.info(f"Backup {backup_name} created ({size_mb:.2f} MB, {len(user_ids)} users)")
        return f"✅ Backup {backup_name} ({size_mb:.1f} MB, {len(user_ids)} users)"

    async def restore_all(self, backup_name: str) -> str:
        """Restore all user databases from a backup file."""
        backup_path = Config.BACKUP_DIR / backup_name
        if not backup_path.exists():
            return f"❌ Backup {backup_name} not found."

        user_count = 0
        with tarfile.open(backup_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.startswith("user_") and member.name.endswith(".db"):
                    tar.extract(member, path=Config.DB_DIR)
                    user_count += 1
                elif member.name == "config.json":
                    tar.extract(member, path=Config.BASE_DIR)

        logger.info(f"Restored {user_count} users from {backup_name}")
        return f"✅ Restored {user_count} users from {backup_name}"

    async def list_backups(self) -> List[Dict]:
        """List available backups with metadata."""
        backups = []
        for f in sorted(Config.BACKUP_DIR.glob("backup_*.tar.gz"), reverse=True):
            size_kb = f.stat().st_size / 1024
            timestamp = f.stem.replace("backup_", "").replace("_", " ")
            backups.append({
                "filename": f.name,
                "size_kb": round(size_kb, 1),
                "timestamp": timestamp,
            })
        return backups

    async def start_auto_backup(self, interval: int = 3600):
        """Periodic auto-backup loop."""
        while True:
            await asyncio.sleep(interval)
            try:
                await self.backup_all()
            except Exception as e:
                logger.error(f"Auto-backup failed: {e}")

    async def restore_latest(self) -> str:
        """Fast restore from latest backup (for <3min restart)."""
        backups = await self.list_backups()
        if not backups:
            return "No backups found."
        latest = backups[0]["filename"]
        return await self.restore_all(latest)

    async def fast_recovery(self) -> str:
        """Quick recovery: restore latest backup + ensure dirs exist."""
        Config.init_dirs()
        result = await self.restore_latest()
        return result

    def start_background_backup(self, interval: int = 3600):
        """Start auto-backup in background."""
        loop = asyncio.get_event_loop()
        self._backup_task = loop.create_task(self.start_auto_backup(interval))

    def stop_background_backup(self):
        if self._backup_task:
            self._backup_task.cancel()
            self._backup_task = None


# Singleton
backup_service = BackupService()
