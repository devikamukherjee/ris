# Permit Guardian

A human-in-the-loop permit-to-work assistant for a fictional site, Northbank Refinery,
Unit 3. Deterministic rules flag drift between a work permit and what's happening on
site. A local LLM explains each flag, and a human permit issuer decides what to do.
Every flag, explanation and decision goes into a hash-chained audit log.

The only network calls the app makes go to a local Ollama server.

> **Status: 2 of 8 milestones done** (M0 scaffold and M1 data). The rule engine
> comes next. For the problem, the architecture and the roadmap, read
> [docs/overview.md](docs/overview.md). The full build plan is in
> [docs/spec.md](docs/spec.md).

## Quick start

Prerequisites: Python 3.11+, Node 20.19+, [Ollama](https://ollama.com) running locally.

```bash
make doctor     # checks Python, Node, Ollama and the configured model
make setup      # creates .venv, installs dependencies, creates .env
make test
make dev        # API on http://127.0.0.1:8000
```

Run `make help` to list all targets. Design choices are in [DECISIONS.md](DECISIONS.md).
