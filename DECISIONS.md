# Decisions

Choices the spec leaves open, with the reason for each. Newest at the bottom.

## M0 — Scaffold

**Repo location.** `~/Downloads/CHI_2027__LIFT/Graveyard/permit-guardian`, next to
`vaa-audit`. Because it is under `~/Downloads`, Claude Code also loads the VAA project's
`~/Downloads/CLAUDE.md`. A local `CLAUDE.md` says that file doesn't apply here.

**Python environment: pip + venv, uv if present.** uv isn't installed on the dev
machine. `make setup` uses uv when it finds it and falls back to `python -m venv` + pip.
The system `python3` is 3.10, so the Makefile picks `python3.12` (then 3.13, 3.11)
first. Override with `make setup PYTHON=...`.

**Dependencies use lower bounds, not a lockfile.** This is enough for a portfolio
project. If exact reproducibility matters later (for example, freezing the
environment before the study runs), add a `pip freeze` constraints file.

**Default model: `qwen2.5:7b-instruct`.** A 7B instruct model with reliable JSON-schema
structured output in Ollama. Change it with `OLLAMA_MODEL`. `make doctor` lists the
installed models and gives the `ollama pull` command if this one is missing.

**`make doctor` uses only the standard library.** It has to work before `make setup`,
so it queries Ollama's `/api/tags` (the endpoint behind `ollama list`) with `urllib`,
not the `ollama` package. When the app starts in M4, it does the same check with the
`ollama` package. Configuration (`app/config.py`) is a plain dataclass, not
pydantic-settings, for the same reason.

**A non-loopback `OLLAMA_HOST` is a warning, not an error.** A real deployment might
point at an on-prem GPU server inside the site boundary. The doctor warns, and the
network-guard test in M7 enforces whatever host is configured.

**FastAPI's `/docs` and `/redoc` are disabled.** Swagger UI and ReDoc load JS and CSS
from a CDN, which breaks the no-external-requests rule. `/openapi.json` still works.

**The API binds to 127.0.0.1, not 0.0.0.0.** This keeps the dev server off the LAN.

**Frontend scaffold deferred to M5.** Node isn't installed on the dev machine, so a
hand-written Vite/React scaffold couldn't be installed or built to check it. `make setup`
and `make dev` already handle `frontend/` once it exists.

**Unimplemented make targets fail loudly.** `seed`, `verify-audit`, `precompute` and
`study-export` exit 1 with the milestone that delivers them. They don't print a
success message for work that didn't happen.

**The spec is saved in `docs/spec.md`.** It is the reference for later milestones and
later sessions.
