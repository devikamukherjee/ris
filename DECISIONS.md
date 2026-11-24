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

## M1 — Data

**Simulated time has no timezone.** All times are naive datetimes on the site clock
(shift 2026-03-10, 06:00 to 18:00). SQLModel now rejects naive datetimes by default,
so the fields are annotated `NaiveDatetime`.

**28 workers and 28 permits** (the spec says "about 25" and "about 30"). Fewer workers
couldn't staff 8 hot-work permits and 4 confined-space crews without someone being in
two places at once. The permits are 8 hot work, 7 cold work, 4 confined space, 4 line
break and 5 lifting (3 of them SIMOPS). Each worker's permits run back to back, the
way day-shift permits cluster around the start and end of the shift.

**Extra fields beyond the spec:**
- `GasTest.recorded_at`: when the test record reached the permit system. The
  late-record decoy needs this, and the rule engine may only see a test from this time.
- `Permit.description`: a one-line job description for the UI and the LLM.
- `Area.camera_ids`.
- `LiftPlan.kind` (`lift` or `vehicle_move`): one table covers both. The generator
  only produces lifts.

**How detections work.**
- Each worker on a job gets a detection event every 60 s. Scenario bursts are denser:
  1 s for position jitter and 10 s for occlusion.
- The `absent` activity means the detector couldn't see an assigned worker (a fire
  watch or attendant) at their post. Its confidence is how sure the detector is that
  they're gone.
- Inside a confined space there are no events. Occupancy comes from `entering_cs` and
  `exiting_cs` events at the manway.
- Worker IDs on detections stand for a pseudonymous badge-tag association (PTW-9.2).
  No biometric fields exist anywhere.

**Every confined-space crew has a standby fitter** working at the surface. The
over-count scenario is that person climbing in.

**Scenario counts: 32 true violations, 13 decoys** (`app/sim/scenarios.py`). That is
45 labelled scenarios, enough for 40 study trials plus 3 practice flags. The generator
doesn't decide which decoys trip a rule; the M2 rule thresholds do. The decoys are
built so that thresholds can catch some of them:
- occlusion confidence 0.35 to 0.6
- misread grinding confidence 0.4 to 0.58
- late gas record visible as stale for 1 to 3 min
- position jitter 0.5 m outside for 2 s

**Ground truth is stored separately.** It has its own table (`ground_truth`) and its
own fixture file (`ground_truth.json`) with its own loader. `SiteData`, which the rule
engine reads, doesn't contain it, and a test checks that no site fixture mentions the
labels.

**Fixtures are committed (about 1.9 MB).** A test fails if `data/fixtures` stops
matching `generate(42)`, so they can't go stale silently.

**Reseeding refuses to wipe a used database.** `make seed` stops if the database holds
flags, decisions or audit records. `make seed FORCE=1` overrides this.

**Procedure limits are invented for the fictional site:** LEL ≤ 1 %, O2 19.5 to
23.5 %, H2S < 5 ppm, gas tests valid 120 min for hot work and 60 min for confined
spaces, and a 60 min fire watch after hot work. A test checks that the generator's
constants match the clause text.

**`httpx` is kept for now.** Starlette's test client warns that `httpx` is deprecated
in favour of `httpx2`. Switching means installing a new package; that is left for a
later change.
