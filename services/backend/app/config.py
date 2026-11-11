"""Runtime configuration. *_FILE supports Docker secrets without baking credentials into images."""
import os
from pathlib import Path


def env(name: str, default: str = "") -> str:
    path = os.getenv(f"{name}_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.getenv(name, default)


DATABASE_URL = env("DATABASE_URL", "sqlite:///./developer_trends.db")
APP_MODE = env("APP_MODE", "live")
if APP_MODE not in ("live", "demo"):
    raise ValueError("APP_MODE must be live or demo")
