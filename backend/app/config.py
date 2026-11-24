"""Runtime configuration, read from environment variables and an optional ``.env``.

Standard library only: ``app.doctor`` imports this before ``make setup`` has
installed any dependencies.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b-instruct"


@dataclass(frozen=True)
class Settings:
    """Application settings.

    ollama_host: base URL of the Ollama server, the only host the app may call.
    ollama_model: Ollama model name, optionally with a ``:tag``.
    db_path: SQLite database file.
    """

    ollama_host: str = DEFAULT_OLLAMA_HOST
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    db_path: Path = REPO_ROOT / "data" / "permit_guardian.db"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        db = env.get("PG_DB_PATH")
        db_path = Path(db) if db else cls.db_path
        if not db_path.is_absolute():
            db_path = REPO_ROOT / db_path
        return cls(
            ollama_host=env.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST,
            ollama_model=env.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL,
            db_path=db_path,
        )


def load_dotenv(path: Path, env: MutableMapping[str, str] | None = None) -> None:
    """Load ``KEY=value`` lines from ``path`` into ``env`` without overriding existing keys.

    Blank lines and ``#`` comments are skipped; surrounding quotes are stripped.
    A missing file is not an error.
    """
    env = os.environ if env is None else env
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        env.setdefault(key, value)


def get_settings() -> Settings:
    """Settings from the process environment, after loading the repo-root ``.env``."""
    load_dotenv(REPO_ROOT / ".env")
    return Settings.from_env()


def is_loopback(url: str) -> bool:
    """True if ``url`` points at this machine (localhost, 127.0.0.0/8 or ::1)."""
    host = urlparse(url if "//" in url else f"//{url}").hostname
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
