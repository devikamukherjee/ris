from pathlib import Path

import pytest

from app.config import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    REPO_ROOT,
    Settings,
    is_loopback,
    load_dotenv,
)


def test_defaults_when_env_empty():
    s = Settings.from_env({})
    assert s.ollama_host == DEFAULT_OLLAMA_HOST
    assert s.ollama_model == DEFAULT_OLLAMA_MODEL
    assert s.db_path == REPO_ROOT / "data" / "permit_guardian.db"


def test_env_overrides_and_relative_db_path():
    s = Settings.from_env(
        {"OLLAMA_HOST": "http://127.0.0.1:9999", "OLLAMA_MODEL": "llama3.1:8b", "PG_DB_PATH": "x/y.db"}
    )
    assert s.ollama_host == "http://127.0.0.1:9999"
    assert s.ollama_model == "llama3.1:8b"
    assert s.db_path == REPO_ROOT / "x" / "y.db"


def test_dotenv_does_not_override_existing(tmp_path: Path):
    f = tmp_path / ".env"
    f.write_text("# comment\n\nOLLAMA_MODEL='from-file'\nOLLAMA_HOST=http://localhost:1\nNOEQUALS\n")
    env = {"OLLAMA_HOST": "http://localhost:2"}
    load_dotenv(f, env)
    assert env == {"OLLAMA_HOST": "http://localhost:2", "OLLAMA_MODEL": "from-file"}


def test_dotenv_missing_file_is_noop(tmp_path: Path):
    env: dict[str, str] = {}
    load_dotenv(tmp_path / "nope", env)
    assert env == {}


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://127.5.5.5", True),
        ("http://[::1]:11434", True),
        ("localhost:11434", True),
        ("http://10.0.0.5:11434", False),
        ("https://api.example.com", False),
        ("http://localhost.example.com", False),
        ("", False),
    ],
)
def test_is_loopback(url, expected):
    assert is_loopback(url) is expected
