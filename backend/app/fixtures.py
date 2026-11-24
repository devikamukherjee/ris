"""JSON fixtures in ``data/fixtures/``, so tests and the replay don't need the database.

Site data (what the rule engine may see) and ground truth (what really happened) are in
separate files and load through separate functions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from sqlmodel import SQLModel

from app.config import REPO_ROOT
from app.models import Area, DetectionEvent, GasTest, GroundTruthLabel, LiftPlan, Permit, Worker

FIXTURE_DIR = REPO_ROOT / "data" / "fixtures"
GROUND_TRUTH_FILE = "ground_truth.json"
MANIFEST_FILE = "manifest.json"


@dataclass
class SiteData:
    """Everything the rule engine is allowed to read."""

    areas: list[Area]
    workers: list[Worker]
    permits: list[Permit]
    gas_tests: list[GasTest]
    lift_plans: list[LiftPlan]
    events: list[DetectionEvent]


SITE_MODELS: dict[str, type[SQLModel]] = {
    "areas": Area,
    "workers": Worker,
    "permits": Permit,
    "gas_tests": GasTest,
    "lift_plans": LiftPlan,
    "events": DetectionEvent,
}


@dataclass
class Dataset:
    site: SiteData
    ground_truth: list[GroundTruthLabel]
    manifest: dict[str, Any]


def _write_rows(path: Path, rows: list[SQLModel]) -> None:
    """One JSON object per line inside an array: valid JSON, and diffs stay readable."""
    lines = [json.dumps(r.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) for r in rows]
    path.write_text("[\n" + ",\n".join(lines) + "\n]\n")


def _read_rows(path: Path, model: type[SQLModel]) -> list[Any]:
    return [model.model_validate(row) for row in json.loads(path.read_text())]


def export_fixtures(dataset: Dataset, directory: Path = FIXTURE_DIR) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for f in fields(SiteData):
        _write_rows(directory / f"{f.name}.json", getattr(dataset.site, f.name))
    _write_rows(directory / GROUND_TRUTH_FILE, dataset.ground_truth)
    (directory / MANIFEST_FILE).write_text(json.dumps(dataset.manifest, indent=2, sort_keys=True) + "\n")


def load_site(directory: Path = FIXTURE_DIR) -> SiteData:
    return SiteData(**{name: _read_rows(directory / f"{name}.json", model) for name, model in SITE_MODELS.items()})


def load_ground_truth(directory: Path = FIXTURE_DIR) -> list[GroundTruthLabel]:
    return _read_rows(directory / GROUND_TRUTH_FILE, GroundTruthLabel)


def load_manifest(directory: Path = FIXTURE_DIR) -> dict[str, Any]:
    return json.loads((directory / MANIFEST_FILE).read_text())
