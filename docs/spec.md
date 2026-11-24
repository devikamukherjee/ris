# Build spec: Permit Guardian — a human-in-the-loop permit-to-work assistant

You are helping me build a portfolio project end to end. Read this whole file before writing any code. Work through the milestones in order. After each milestone: run the tests, make a git commit, and give me a 3–5 line summary of what works and what's next. If something in this spec is ambiguous, make a sensible choice, note it in DECISIONS.md, and keep going.

## 1. Who I am and why this exists

I'm an HCI researcher (mixed reality, wearable haptics, human–AI interaction). I'm building this to show a company that sells sovereign, on-premises AI and physical AI for process safety in chemical plants and refineries that I can design and build the human side of their AI systems.

Their pitch has three ideas this project must make real:

- **"Agents flag; people decide."** The AI never makes the safety decision. A human permit issuer confirms or dismisses every flag.
- **Sovereign by architecture.** Data and models stay inside the boundary. Here that means: the only network calls the app makes go to a local Ollama server on localhost. No cloud APIs, no telemetry, no CDN fonts or scripts at runtime.
- **Every action carries its record.** Every flag, AI explanation and human decision goes into an audit log a regulator could follow.

Keep the repo brand-neutral: don't name any real company, and use a fictional site ("Northbank Refinery, Unit 3").

## 2. The problem in one paragraph

In a refinery, risky jobs (hot work like welding, confined-space entry, crane lifts, breaking into process lines) need a permit to work. The permit sets the area, the valid hours, conditions such as a recent gas test and a fire watch, and the controls. Accidents often start when real work drifts from its permit: the job moves outside its area, the gas test goes stale, the fire watch walks away, the permit expires while work continues, or two jobs clash in the same place (SIMOPS, simultaneous operations). Cameras plus AI can spot drift. But a flood of AI alerts that people blindly accept, or blindly ignore, is its own hazard. This project builds the checker and the interface, and includes a study mode to measure whether the interface helps people catch the AI's mistakes.

## 3. Hard constraints (do not violate)

- Deterministic code decides whether a rule is broken. The LLM only explains and summarises. The LLM must never create, delete, change the severity of, or resolve a flag.
- The only outbound network calls go to Ollama at OLLAMA_HOST (default http://localhost:11434). Bundle all frontend assets locally.
- No faces, no identities. Workers are pseudonymous IDs (W-017) with roles. Simulated "camera" detections carry position, PPE and activity, never biometric data.
- Read-only toward "process systems". Simulated process and DCS data is input only. There is no code path that "acts on" plant equipment.
- Every LLM call is logged: model name, model digest if available, prompt hash, full prompt, response, latency, and whether the output passed validation.
- If Ollama is down or returns invalid output, the app still works, using templated explanations marked "AI explanation unavailable".
- Everything runs locally with one command after setup.

## 4. Tech stack

- **Backend:** Python 3.11+, FastAPI, SQLModel on SQLite, Pydantic v2, pytest. Use uv for dependency management if available, otherwise pip + venv.
- **LLM:** Ollama via the official ollama Python package. Use structured outputs (pass a JSON schema as format) and temperature ≤ 0.2.
  - Model is configurable with the OLLAMA_MODEL env var.
  - On first run, call ollama list. If OLLAMA_MODEL isn't installed, print the installed models and tell me what to pull. A 7–8B instruct model is enough.
- **Frontend:** React + TypeScript + Vite + Tailwind, served by Vite in dev. Keep it clean and calm: control-room style, high contrast, no gradients, nothing decorative. Severity is shown by text label + icon + colour, never colour alone.
- **Analysis:** a Jupyter notebook with pandas and matplotlib for study results.
- **Tooling:** a Makefile (or justfile) with setup, seed, dev, test, study-export.

## 5. Domain model

Create these entities (SQLModel tables or Pydantic models as appropriate):

- **Area:** id, name, polygon (site-plan coordinates in metres), hazard tags (e.g. hydrocarbon, confined_space, H2S).
- **Permit:** id (e.g. HW-2041), type (hot_work, cold_work, confined_space, lifting, line_break), area_id, work polygon (subset of the area), valid_from, valid_to, issuer, performing crew (worker IDs), conditions:
  - gas_test_required + gas_test_max_age_min
  - fire_watch_required + fire_watch_post_work_min
  - attendant_required (confined space)
  - max_entrants
  - status (active, suspended, closed)
- **GasTest:** permit_id, time, readings (LEL %, O2 %, H2S ppm), tester.
- **Worker:** pseudonymous id, role (fitter, welder, fire_watch, attendant, crane_op, banksman).
- **DetectionEvent** (simulated camera/edge output): time, camera_id, area_id, worker_id or vehicle_id, position (x, y), activity (welding, grinding, idle, entering_cs, exiting_cs, lift_in_progress, …), PPE flags, detector confidence (0–1). Events only — never video.
- **LiftPlan / VehicleMove:** for SIMOPS: time window, area, footprint polygon.
- **Flag:** id, rule_id, permit_id(s), severity (info, warning, critical), detector confidence, evidence (list of event, gas-test and permit references with the exact values that triggered it), clause_ids, status (open, confirmed, dismissed, escalated), created_at.
- **Decision:** flag_id, user_id, action (confirm, dismiss, escalate, request_retest), reason (required for dismiss), time_to_decision_ms, ui_condition.
- **AuditRecord:** append-only, hash-chained (each record stores the SHA-256 of the previous record plus its own payload). Record types: flag_raised, explanation_generated, decision_made, handover_generated, config_changed. Add make verify-audit to check the chain.

## 6. Synthetic data generator (seed)

Write a seeded, reproducible generator (--seed 42) that produces one 12-hour day shift for Northbank Refinery Unit 3:

- 6–8 areas, about 30 permits across all types, about 25 workers, gas tests and a detection-event stream.
- Scenario library: each injected scenario carries a ground-truth label (true_violation or no_violation) stored separately from what the rule engine sees:
  - Work outside the permitted polygon (true).
  - Gas test older than the permit allows while hot work is active (true).
  - Fire watch absent during hot work, or leaves before the post-work watch period ends (true).
  - Work continues after valid_to (true).
  - SIMOPS: a crane lift footprint overlaps an active hot-work area in time (true).
  - Confined space: entrant count exceeds max_entrants, or no attendant present while someone is inside (true).
- Decoys that should look like violations but aren't (no_violation), used to make the AI wrong some of the time:
  - Position jitter puts a worker 0.5 m outside the polygon for 2 seconds.
  - A valid gas re-test was done, but its record arrives 4 minutes late.
  - The fire watch is briefly occluded (low-confidence "absent" detection).
  - A misread activity: grinding detected while the permit is cold work, but the detector confidence is low and it's actually a wire brush.
- Write everything to SQLite, and also export JSON fixtures in data/fixtures/ so tests don't depend on the DB.

## 7. Mock site procedure (data/procedure/PTW-procedure.md)

Write an original mock permit-to-work procedure for the fictional site, about 2–3 pages. It should be realistic, in the style of common UK practice (e.g. HSE guidance HSG250), but don't copy any real document's text.

- Give every rule a numbered clause ID, e.g. PTW-4.2 Gas tests for hot work are valid for 120 minutes…, PTW-5.3 A fire watch shall remain for 60 minutes after hot work ends…, PTW-7.1 SIMOPS….
- Parse it into a clause table (clause_id, title, text) at startup.

## 8. Rule engine (deterministic)

- One module per rule: R-AREA, R-GAS-AGE, R-FIRE-WATCH, R-EXPIRED, R-SIMOPS, R-CS-COUNT, R-CS-ATTENDANT, R-ACTIVITY-MISMATCH.
- Each rule declares its rule_id, clause_ids, default severity, and a pure function (state at time t) -> list[Flag].
- Use debounce and persistence windows (e.g. outside the area for ≥ N seconds), configurable per rule in config/rules.yaml. Some decoys should still trip with default settings. That's intentional: the AI must sometimes be wrong for the study to mean anything.
- Replay the event stream in simulated time (configurable speed, e.g. 60×) and raise flags as they happen.
- Tests: unit tests per rule (positive, negative, edge cases) and an integration test that replays the seeded day and checks rule-level precision and recall against ground truth. Print a confusion matrix per rule.

## 9. LLM layer (Ollama)

Two jobs only:

**a) Flag explanation.** Input: the flag, its evidence, the permit, and the full text of only the clauses listed in flag.clause_ids. Output (JSON schema enforced):

```json
{
  "summary": "one sentence, plain English, ≤ 25 words",
  "what_was_seen": "facts from the evidence only",
  "why_it_matters": "the hazard, 1–2 sentences",
  "suggested_check": "what the issuer might verify before deciding",
  "cited_clauses": ["PTW-4.2"],
  "uncertainty_note": "mention low detector confidence or missing data if relevant, else empty"
}
```

Validation after every call:

- cited_clauses must be a non-empty subset of the clause IDs supplied.
- Any number or time in the text must appear in the evidence. Check with a simple regex extraction.
- No decision language ("you must dismiss", "this is safe").

If validation fails, retry once, then fall back to a template. Log everything.

**b) Shift handover brief.** Input: open permits, open and escalated flags, decisions made, and suspended or closed permits for the shift. Output: a structured brief (open risks first, then permits continuing into next shift, then notable decisions), with each item linking back to permit and flag IDs. It runs the same validation and fallback.

- Put the prompts in prompts/*.md as versioned files, and log the prompt version with each call.
- Cache explanations by (flag hash, model, prompt version) so study sessions are fast and identical for every participant.
- Add a make precompute command that generates and caches all explanations before a study session.

## 10. API (FastAPI)

These endpoints at minimum:

- GET /flags?status=&severity=
- GET /flags/{id} (with evidence, permit, clauses, explanation)
- POST /flags/{id}/decision
- GET /permits, GET /permits/{id}
- GET /handover
- GET /audit and GET /audit/verify
- POST /sim/start|pause|reset
- GET /stream (Server-Sent Events for new flags)

Study endpoints are covered in §12.

## 11. Issuer UI

Three screens:

**Flag queue (main screen).**

- Left: a live list sorted by severity then age, showing the rule, permit, area, time open and detector confidence.
- Right: the selected flag.
  - Evidence panel: a schematic site plan (SVG) showing the area polygon, permit work polygon, worker dots at the time of the event, and lift footprints. Add a small timeline scrubber for the 2 minutes around the event. This replaces the camera clip.
  - Rule & clause panel: the rule name and the quoted clause text with its ID.
  - AI explanation panel: clearly labelled as AI-generated, with model name and an "uncertainty" note.
  - Action bar: Confirm / Dismiss (reason required) / Escalate / Request re-test, with keyboard shortcuts.

**Shift handover:** the generated brief with links to flags and permits, plus a "regenerate" button that is logged.

**Audit log:** a filterable table and a "verify chain" button showing pass/fail.

Accessibility: keyboard-operable, WCAG AA contrast, colour never the only signal.

## 12. Study mode (the research part)

The research question: does showing evidence + clause citations + an AI explanation help permit issuers catch the AI's wrong flags (less over-reliance) without slowing them down too much, compared with a minimal alert?

- **Conditions:**
  - A_minimal: rule name, permit ID, severity, confidence.
  - B_explained: the full evidence, clause and AI explanation panels.
- **Trials:** a fixed set of about 40 flags drawn from the seeded day, about 15% of them decoys (wrong flags). Same flags for every participant, with order randomised per participant from a logged seed.
- **Design:** within-subjects, with condition order counterbalanced (AB/BA) and two matched flag sets so no one sees the same flag twice.
- **Session flow:** participant ID entry (pseudonymous) → short tutorial with 3 practice flags → block 1 → questionnaire → block 2 → questionnaire → done.
- **Questionnaires (in-app forms):** a trust-in-automation scale (Jian et al. 2000, 12 items), NASA-TLX (raw), and one open text box.
- **Logged per trial:** participant, condition, flag ID, ground truth, decision, correct (y/n), time to decision, which panels were expanded or hovered, and whether the explanation was read (visible ≥ 2 s).
- **Export:** make study-export writes tidy CSVs to study/exports/.
- **Analysis notebook (analysis/study_analysis.ipynb):**
  - Accuracy per condition.
  - Over-reliance (confirming decoys) and under-reliance (dismissing true violations) per condition.
  - Decision time.
  - Trust and TLX scores.
  - Plots plus a mixed-effects model (participant as random effect) using statsmodels.
- Include fake pilot data generated by a script, so the notebook runs before any real participants.
- Add a study/ETHICS_NOTE.md placeholder reminding me this needs university ethics approval and a participant information sheet before running with real people.

## 13. Repo layout

```
permit-guardian/
  README.md  DECISIONS.md  Makefile  .env.example
  backend/  app/ (api, models, rules, llm, audit, sim, study)  tests/
  frontend/ src/ (screens, components, lib)
  data/ procedure/  fixtures/
  prompts/  config/rules.yaml
  study/  analysis/
  docs/ architecture.md (mermaid diagram)  demo-script.md
```

## 14. Milestones

- **M0 — Scaffold:** repo, tooling, Makefile, .env.example, Ollama connectivity check (make doctor checks Python, Node, Ollama running, model installed).
- **M1 — Data:** domain models, seeded generator with the scenario library and ground truth, mock procedure and clause parser.
- **M2 — Rules:** rule engine, simulated-time replay, unit and integration tests, per-rule confusion matrix.
- **M3 — API + audit:** endpoints, hash-chained audit log, verify-audit.
- **M4 — LLM:** explanation and handover with structured output, validation, fallback, caching, logging, precompute. Tests mock Ollama; one optional live test is skipped if Ollama is down.
- **M5 — UI:** flag queue with evidence and action panels, live SSE updates, handover and audit screens.
- **M6 — Study mode:** conditions, sessions, questionnaires, logging, export, fake pilot data and analysis notebook.
- **M7 — Polish:** README (problem, architecture diagram, how sovereignty, human-in-the-loop and audit are enforced in code, how to run, screenshots placeholder), docs/demo-script.md (a 2-minute demo walkthrough), and a final check that the app makes no non-localhost requests. Add a test that patches the network layer and fails on any other host.

## 15. Definition of done

- make setup && make seed && make dev gives me a working app with live flags on a fresh machine with Ollama installed.
- All tests pass, and the rule-engine integration test prints per-rule precision and recall.
- I can run a full study session as a fake participant and get CSVs out, and the notebook runs end to end on the fake pilot data.
- The README explains, in plain language for a non-specialist hiring manager, what the system does, what the AI is and isn't allowed to do, and what the study measures.
