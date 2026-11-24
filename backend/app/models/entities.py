"""Domain entities, stored as SQLModel tables.

Units and conventions:
- Positions and polygons are site-plan coordinates in metres. A polygon is a list of
  ``[x, y]`` vertices in order, not repeated at the end.
- Times are naive datetimes in site local time (simulated shift clock).
- Confidences are detector confidences in [0, 1].

Workers are pseudonymous (``W-017``) with a role. Nothing here stores faces, names,
images or other biometric data.
"""

from __future__ import annotations

from pydantic import NaiveDatetime
from enum import Enum
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _json(nullable: bool = False) -> Any:
    return Field(sa_column=Column(JSON, nullable=nullable))


class PermitType(str, Enum):
    hot_work = "hot_work"
    cold_work = "cold_work"
    confined_space = "confined_space"
    lifting = "lifting"
    line_break = "line_break"


class PermitStatus(str, Enum):
    active = "active"
    suspended = "suspended"
    closed = "closed"


class Role(str, Enum):
    fitter = "fitter"
    welder = "welder"
    fire_watch = "fire_watch"
    attendant = "attendant"
    crane_op = "crane_op"
    banksman = "banksman"


class Activity(str, Enum):
    """What the edge detector reports a subject doing. ``absent`` means the detector
    could not see an assigned worker at their post (confidence = confidence they are gone)."""

    welding = "welding"
    grinding = "grinding"
    cutting = "cutting"
    fitting = "fitting"
    idle = "idle"
    walking = "walking"
    watching = "watching"
    attending = "attending"
    signalling = "signalling"
    entering_cs = "entering_cs"
    exiting_cs = "exiting_cs"
    lift_in_progress = "lift_in_progress"
    absent = "absent"


HOT_ACTIVITIES = frozenset({Activity.welding, Activity.grinding, Activity.cutting})


class Severity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class FlagStatus(str, Enum):
    open = "open"
    confirmed = "confirmed"
    dismissed = "dismissed"
    escalated = "escalated"


class DecisionAction(str, Enum):
    confirm = "confirm"
    dismiss = "dismiss"
    escalate = "escalate"
    request_retest = "request_retest"


class AuditRecordType(str, Enum):
    flag_raised = "flag_raised"
    explanation_generated = "explanation_generated"
    decision_made = "decision_made"
    handover_generated = "handover_generated"
    config_changed = "config_changed"


class GroundTruthLabelValue(str, Enum):
    true_violation = "true_violation"
    no_violation = "no_violation"


# --- Site and permits -------------------------------------------------------


class Area(SQLModel, table=True):
    id: str = Field(primary_key=True)  # "A03"
    name: str
    polygon: list[list[float]] = _json()
    hazard_tags: list[str] = _json()
    camera_ids: list[str] = _json()


class Worker(SQLModel, table=True):
    id: str = Field(primary_key=True)  # "W-017"
    role: Role


class Permit(SQLModel, table=True):
    id: str = Field(primary_key=True)  # "HW-2041"
    type: PermitType
    area_id: str = Field(foreign_key="area.id")
    description: str
    work_polygon: list[list[float]] = _json()
    valid_from: NaiveDatetime
    valid_to: NaiveDatetime
    issuer: str  # pseudonymous Area Authority, e.g. "AA-02"
    crew: list[str] = _json()  # worker IDs
    gas_test_required: bool = False
    gas_test_max_age_min: Optional[int] = None
    fire_watch_required: bool = False
    fire_watch_post_work_min: Optional[int] = None
    attendant_required: bool = False
    max_entrants: Optional[int] = None
    status: PermitStatus = PermitStatus.active


class GasTest(SQLModel, table=True):
    """A gas test. ``time`` is when it was taken; ``recorded_at`` is when the record
    reached the permit system (the rule engine can only see it from then on)."""

    id: str = Field(primary_key=True)  # "GT-0001"
    permit_id: str = Field(foreign_key="permit.id", index=True)
    time: NaiveDatetime
    recorded_at: NaiveDatetime
    lel_pct: float  # % of lower explosive limit
    o2_pct: float  # % by volume
    h2s_ppm: float
    tester: str  # pseudonymous authorised gas tester, e.g. "AGT-01"


class LiftPlan(SQLModel, table=True):
    """A planned crane lift (or vehicle move) and the ground footprint it occupies."""

    id: str = Field(primary_key=True)  # "LP-01"
    permit_id: str = Field(foreign_key="permit.id", index=True)
    kind: str = "lift"  # "lift" | "vehicle_move"
    vehicle_id: str  # "CR-01"
    area_id: str = Field(foreign_key="area.id")
    footprint: list[list[float]] = _json()
    start: NaiveDatetime
    end: NaiveDatetime
    description: str = ""


class DetectionEvent(SQLModel, table=True):
    """One output of the simulated camera/edge detector. Events only, never video."""

    id: int = Field(primary_key=True)
    time: NaiveDatetime = Field(index=True)
    camera_id: str
    area_id: str
    worker_id: Optional[str] = Field(default=None, index=True)
    vehicle_id: Optional[str] = None
    x: float
    y: float
    activity: Activity
    ppe: Optional[dict[str, bool]] = _json(nullable=True)
    confidence: float


class Clause(SQLModel, table=True):
    """One numbered clause of the site PTW procedure (data/procedure/PTW-procedure.md)."""

    clause_id: str = Field(primary_key=True)  # "PTW-4.2"
    section: str
    title: str
    text: str


# --- Flags, decisions, audit (filled by later milestones) --------------------


class Flag(SQLModel, table=True):
    id: str = Field(primary_key=True)  # "F-0001"
    rule_id: str = Field(index=True)
    permit_ids: list[str] = _json()
    severity: Severity
    detector_confidence: Optional[float] = None
    evidence: list[dict[str, Any]] = _json()
    clause_ids: list[str] = _json()
    status: FlagStatus = FlagStatus.open
    created_at: NaiveDatetime  # simulated time the rule fired


class Decision(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    flag_id: str = Field(foreign_key="flag.id", index=True)
    user_id: str
    action: DecisionAction
    reason: Optional[str] = None  # required for dismiss
    time_to_decision_ms: int
    ui_condition: str
    decided_at: NaiveDatetime


class AuditRecord(SQLModel, table=True):
    """Append-only, hash-chained log entry: ``hash = sha256(prev_hash + payload)``."""

    seq: Optional[int] = Field(default=None, primary_key=True)
    record_type: AuditRecordType
    payload: dict[str, Any] = _json()
    prev_hash: str
    hash: str
    created_at: NaiveDatetime


# --- Ground truth (never read by the rule engine) ----------------------------


class GroundTruthLabel(SQLModel, table=True):
    """What really happened in an injected scenario. Kept apart from everything the
    rule engine sees; used only to score the engine and to build study trials."""

    __tablename__ = "ground_truth"

    scenario_id: str = Field(primary_key=True)  # "SC-001"
    kind: str  # e.g. "area_breach", "decoy_area_jitter"
    label: GroundTruthLabelValue
    expected_rule_id: str  # rule that should (or, for a decoy, might wrongly) fire
    permit_ids: list[str] = _json()
    subject_id: Optional[str] = None
    start: NaiveDatetime
    end: NaiveDatetime
    notes: str = ""
