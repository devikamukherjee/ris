import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import Settings
from app.doctor import FAIL, PASS, WARN, check_ollama, check_python, normalise_model, parse_node_version


@pytest.fixture
def fake_ollama():
    """A local HTTP server answering /api/tags with a fixed model list."""
    models = ["qwen2.5:7b-instruct", "llama3.1:latest"]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"models": [{"name": m} for m in models]}).encode()
            self.send_response(200 if self.path == "/api/tags" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def by_name(checks):
    return {c.name: c for c in checks}


def test_normalise_model():
    assert normalise_model("llama3.1") == "llama3.1:latest"
    assert normalise_model("qwen2.5:7b-instruct") == "qwen2.5:7b-instruct"


@pytest.mark.parametrize(
    "text,expected", [("v22.11.0\n", (22, 11, 0)), ("v20.19.1", (20, 19, 1)), ("garbage", None)]
)
def test_parse_node_version(text, expected):
    assert parse_node_version(text) == expected


def test_check_python_versions():
    assert check_python((3, 12, 1)).status == PASS
    assert check_python((3, 10, 6)).status == FAIL


def test_model_installed(fake_ollama):
    checks = by_name(check_ollama(Settings(ollama_host=fake_ollama, ollama_model="qwen2.5:7b-instruct")))
    assert checks["ollama host"].status == PASS
    assert checks["ollama server"].status == PASS
    assert checks["ollama model"].status == PASS


def test_untagged_model_matches_latest(fake_ollama):
    checks = by_name(check_ollama(Settings(ollama_host=fake_ollama, ollama_model="llama3.1")))
    assert checks["ollama model"].status == PASS


def test_model_missing_lists_installed_and_pull_hint(fake_ollama):
    checks = by_name(check_ollama(Settings(ollama_host=fake_ollama, ollama_model="mistral:7b")))
    model = checks["ollama model"]
    assert model.status == FAIL
    assert "qwen2.5:7b-instruct" in model.detail and "llama3.1:latest" in model.detail
    assert model.hint.startswith("ollama pull mistral:7b")


def test_server_down_fails():
    host = f"http://127.0.0.1:{closed_port()}"
    checks = by_name(check_ollama(Settings(ollama_host=host)))
    assert checks["ollama server"].status == FAIL
    assert "ollama model" not in checks


def test_non_loopback_host_warns(monkeypatch):
    monkeypatch.setattr("app.doctor.fetch_installed_models", lambda host: ["qwen2.5:7b-instruct"])
    checks = by_name(check_ollama(Settings(ollama_host="http://10.1.2.3:11434")))
    assert checks["ollama host"].status == WARN
