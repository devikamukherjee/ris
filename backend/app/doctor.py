"""Environment check for ``make doctor``.

Checks the Python interpreter, Node and npm, that Ollama answers at
OLLAMA_HOST, and that OLLAMA_MODEL is installed. Standard library only, so it
runs before ``make setup``. The only network call is to OLLAMA_HOST.

Exit code: 0 if no check failed, 1 otherwise. Warnings do not fail.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.config import REPO_ROOT, Settings, get_settings, is_loopback

MIN_PYTHON = (3, 11)
MIN_NODE = (20, 19)  # Vite 7 minimum

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""


def normalise_model(name: str) -> str:
    """Ollama treats an untagged name as ``:latest``; make that explicit for comparison."""
    return name if ":" in name else f"{name}:latest"


def parse_node_version(text: str) -> tuple[int, ...] | None:
    """Parse ``node --version`` output such as ``v22.11.0`` into ``(22, 11, 0)``."""
    try:
        return tuple(int(p) for p in text.strip().lstrip("v").split("."))
    except ValueError:
        return None


def check_python(version: tuple[int, ...] = tuple(sys.version_info[:3])) -> Check:
    shown = ".".join(map(str, version))
    if version[:2] >= MIN_PYTHON:
        return Check("python", PASS, f"{sys.executable} ({shown})")
    return Check(
        "python",
        FAIL,
        f"{sys.executable} is {shown}; need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
        "install python@3.12 (brew install python@3.12) or run make with PYTHON=/path/to/python3.12",
    )


def check_venv() -> Check:
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return Check("venv", PASS, str(venv_python))
    return Check("venv", WARN, ".venv not created yet", "run make setup")


def check_node() -> list[Check]:
    node = shutil.which("node")
    if node is None:
        return [
            Check("node", FAIL, "node not found on PATH", "brew install node  (needed for the frontend)")
        ]
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout
    version = parse_node_version(out)
    if version is None or version[:2] < MIN_NODE:
        node_check = Check(
            "node",
            FAIL,
            f"{node} reports {out.strip() or 'no version'}; need >= {MIN_NODE[0]}.{MIN_NODE[1]}",
            "brew upgrade node",
        )
    else:
        node_check = Check("node", PASS, f"{node} ({out.strip()})")
    npm = shutil.which("npm")
    npm_check = (
        Check("npm", PASS, npm)
        if npm
        else Check("npm", FAIL, "npm not found on PATH", "reinstall node: brew reinstall node")
    )
    return [node_check, npm_check]


def fetch_installed_models(host: str, timeout_s: float = 3.0) -> list[str]:
    """Return model names from Ollama's ``/api/tags`` (what ``ollama list`` shows).

    Raises ``OSError`` (including ``urllib.error.URLError``) if the server is unreachable.
    """
    with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=timeout_s) as resp:
        payload = json.load(resp)
    return [m["name"] for m in payload.get("models", [])]


def check_ollama(settings: Settings) -> list[Check]:
    checks = []
    if is_loopback(settings.ollama_host):
        checks.append(Check("ollama host", PASS, f"{settings.ollama_host} (loopback)"))
    else:
        checks.append(
            Check(
                "ollama host",
                WARN,
                f"{settings.ollama_host} is not loopback; data would leave this machine",
                "set OLLAMA_HOST=http://localhost:11434 unless this is a deliberate on-prem server",
            )
        )

    try:
        installed = fetch_installed_models(settings.ollama_host)
    except (OSError, ValueError) as exc:
        hint = "start Ollama (open the app, or run: ollama serve)"
        if shutil.which("ollama") is None:
            hint = "install Ollama from https://ollama.com/download, then start it"
        checks.append(Check("ollama server", FAIL, f"no answer at {settings.ollama_host}: {exc}", hint))
        return checks
    checks.append(Check("ollama server", PASS, f"{len(installed)} model(s) installed"))

    wanted = normalise_model(settings.ollama_model)
    if wanted in {normalise_model(m) for m in installed}:
        checks.append(Check("ollama model", PASS, wanted))
    else:
        listing = ", ".join(sorted(installed)) or "none"
        checks.append(
            Check(
                "ollama model",
                FAIL,
                f"OLLAMA_MODEL={settings.ollama_model} is not installed. Installed: {listing}",
                f"ollama pull {settings.ollama_model}   (or set OLLAMA_MODEL in .env to an installed 7-8B instruct model)",
            )
        )
    return checks


def run_checks(settings: Settings) -> list[Check]:
    return [check_python(), check_venv(), *check_node(), *check_ollama(settings)]


def main() -> int:
    checks = run_checks(get_settings())
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{c.status}] {c.name:<{width}}  {c.detail}")
        if c.hint and c.status != PASS:
            print(f"       {'':<{width}}  -> {c.hint}")
    failed = [c for c in checks if c.status == FAIL]
    print()
    print(f"{len(failed)} check(s) failed." if failed else "All required checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
