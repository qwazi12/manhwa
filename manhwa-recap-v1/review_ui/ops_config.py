"""Configuration for Manhwa Recap Studio backend and ops."""

from __future__ import annotations

import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
RECAP_ROOT = HERE.parent


class Settings:
    @property
    def data_path(self) -> pathlib.Path:
        # 1. Explicit env var
        custom = os.environ.get("MANHWA_DATA_DIR")
        if custom:
            p = pathlib.Path(custom).resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p
        # 2. Railway volume mount /data if accessible
        railway_data = pathlib.Path("/data")
        try:
            if railway_data.is_dir() and os.access(str(railway_data), os.W_OK):
                return railway_data
        except Exception:
            pass
        # 3. Local fallback under review_ui/data
        local_data = HERE / "data"
        local_data.mkdir(parents=True, exist_ok=True)
        return local_data

    @property
    def resolved_database_url(self) -> str:
        env_url = os.environ.get("DATABASE_URL")
        if env_url:
            if env_url.startswith("postgres://"):
                return env_url.replace("postgres://", "postgresql://", 1)
            return env_url
        db_file = self.data_path / "manhwa.db"
        return f"sqlite:///{db_file}"

    # Backup & Disaster Recovery
    backup_enabled: bool = os.environ.get("BACKUP_ENABLED", "1").lower() in ("1", "true", "yes")
    backup_hour: int = int(os.environ.get("BACKUP_HOUR", "3"))  # 3:00 AM
    backup_timezone: str = os.environ.get("BACKUP_TIMEZONE", "America/New_York")
    backup_keep_local: int = int(os.environ.get("BACKUP_KEEP_LOCAL", "7"))
    backup_keep_drive: int = int(os.environ.get("BACKUP_KEEP_DRIVE", "30"))
    backup_drive_folder_id: str = os.environ.get("BACKUP_DRIVE_FOLDER_ID", "").strip()

    # Disk Space & Retention
    max_disk_usage_percent: float = float(os.environ.get("MAX_DISK_USAGE_PERCENT", "90.0"))
    warn_disk_usage_percent: float = float(os.environ.get("WARN_DISK_USAGE_PERCENT", "80.0"))
    cleanup_retention_days: int = int(os.environ.get("CLEANUP_RETENTION_DAYS", "4"))
    max_tts_cache_mb: int = int(os.environ.get("MAX_TTS_CACHE_MB", "500"))
    max_render_cache_mb: int = int(os.environ.get("MAX_RENDER_CACHE_MB", "2000"))

    # Posting Schedule & Pacing defaults
    post_timezone: str = os.environ.get("POST_TIMEZONE", "America/New_York")
    post_start_hour: int = int(os.environ.get("POST_START_HOUR", "10"))
    post_end_hour: int = int(os.environ.get("POST_END_HOUR", "22"))
    post_interval_hours: int = int(os.environ.get("POST_INTERVAL_HOURS", "2"))
    post_posts_per_day: int | None = int(os.environ.get("POST_POSTS_PER_DAY", "8"))


settings = Settings()
