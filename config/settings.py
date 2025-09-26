from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("ENV", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_dir: str = str(PROJECT_ROOT / "logs")

    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'database.sqlite3'}")
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
    user_agent: str = os.getenv("USER_AGENT", "")
    user_agent_list_path: str = os.getenv("USER_AGENT_LIST_PATH", str(PROJECT_ROOT / "services" / "user_agent_pc.txt"))
    cookie_file: str = os.getenv("COOKIE_FILE", str(PROJECT_ROOT / "cookie.json"))
    proxy_url: str | None = os.getenv("PROXY_URL")
    proxies_file: str | None = os.getenv("PROXIES_FILE")
    anticaptcha_key: str | None = os.getenv("ANTICAPTCHA_KEY")
    target_url: str = os.getenv("TARGET_URL", "")
    playwright_timeout_ms: int = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "60000"))
    trash_dir: str = os.getenv("TRASH_DIR", str(PROJECT_ROOT / "Trash"))


def get_settings() -> Settings:
    return Settings()


