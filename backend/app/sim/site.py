"""Static layout of the fictional Northbank Refinery, Unit 3, plus shift constants.

Coordinates are site-plan metres. Areas are 40 x 30 m blocks separated by 10 m roads.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models import Role

SHIFT_START = datetime(2026, 3, 10, 6, 0)
SHIFT_END = SHIFT_START + timedelta(hours=12)

# Limits quoted in data/procedure/PTW-procedure.md (tests check they agree).
HOT_WORK_GAS_MAX_AGE_MIN = 120  # PTW-4.2
CS_GAS_MAX_AGE_MIN = 60  # PTW-4.3
FIRE_WATCH_POST_WORK_MIN = 60  # PTW-5.3


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in metres, ``x0 < x1`` and ``y0 < y1``."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def polygon(self) -> list[list[float]]:
        return [
            [round(self.x0, 2), round(self.y0, 2)],
            [round(self.x1, 2), round(self.y0, 2)],
            [round(self.x1, 2), round(self.y1, 2)],
            [round(self.x0, 2), round(self.y1, 2)],
        ]

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def contains_rect(self, other: "Rect") -> bool:
        return (
            self.x0 <= other.x0 and self.y0 <= other.y0 and other.x1 <= self.x1 and other.y1 <= self.y1
        )

    def intersection(self, other: "Rect") -> "Rect | None":
        """Overlap with positive area, or None."""
        x0, y0 = max(self.x0, other.x0), max(self.y0, other.y0)
        x1, y1 = min(self.x1, other.x1), min(self.y1, other.y1)
        return Rect(x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None

    def expand(self, d: float) -> "Rect":
        return Rect(self.x0 - d, self.y0 - d, self.x1 + d, self.y1 + d)

    def clamp(self, x: float, y: float, margin: float = 0.0) -> tuple[float, float]:
        return (
            min(max(x, self.x0 + margin), self.x1 - margin),
            min(max(y, self.y0 + margin), self.y1 - margin),
        )

    def random_point(self, rng: random.Random, margin: float = 0.0) -> tuple[float, float]:
        return (
            rng.uniform(self.x0 + margin, self.x1 - margin),
            rng.uniform(self.y0 + margin, self.y1 - margin),
        )

    def random_subrect(self, rng: random.Random, w: float, h: float, margin: float) -> "Rect":
        x0 = rng.uniform(self.x0 + margin, self.x1 - margin - w)
        y0 = rng.uniform(self.y0 + margin, self.y1 - margin - h)
        return Rect(round(x0, 1), round(y0, 1), round(x0 + w, 1), round(y0 + h, 1))

    @classmethod
    def from_polygon(cls, polygon: list[list[float]]) -> "Rect":
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        return cls(min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class AreaSpec:
    id: str
    name: str
    rect: Rect
    hazard_tags: tuple[str, ...]
    equipment: tuple[str, ...]
    camera_ids: tuple[str, ...]


AREAS: tuple[AreaSpec, ...] = (
    AreaSpec("A01", "Crude charge pump bay", Rect(0, 0, 40, 30), ("hydrocarbon",),
             ("P-301A", "P-301B", "P-302"), ("CAM-01",)),
    AreaSpec("A02", "Charge heater H-301", Rect(50, 0, 90, 30), ("hydrocarbon", "confined_space", "high_temperature"),
             ("H-301", "H-301 burner front"), ("CAM-02",)),
    AreaSpec("A03", "Fractionator C-301", Rect(100, 0, 140, 30), ("hydrocarbon", "confined_space", "work_at_height"),
             ("C-301", "E-305 reboiler"), ("CAM-03",)),
    AreaSpec("A04", "Sour water stripper", Rect(0, 40, 40, 70), ("hydrocarbon", "H2S"),
             ("C-310", "P-311A", "E-312"), ("CAM-04",)),
    AreaSpec("A05", "Drum V-305 bay", Rect(50, 40, 90, 70), ("hydrocarbon", "confined_space"),
             ("V-305", "P-306"), ("CAM-05",)),
    AreaSpec("A06", "Pipe rack north", Rect(100, 40, 140, 70), ("hydrocarbon",),
             ("rack bay 4", "rack bay 7"), ("CAM-06",)),
    AreaSpec("A07", "Compressor house K-302", Rect(0, 80, 40, 110), ("hydrocarbon", "H2S", "noise"),
             ("K-302", "K-302 lube oil skid"), ("CAM-07",)),
    AreaSpec("A08", "Crane pad and laydown", Rect(50, 80, 140, 110), ("lifting",),
             ("laydown", "crane pad"), ("CAM-08", "CAM-09")),
)
AREA_BY_ID = {a.id: a for a in AREAS}

# Areas where each permit type may be placed.
WORK_AREAS = ("A01", "A02", "A03", "A04", "A05", "A06", "A07")  # hot, cold, line break
CS_AREAS = tuple(a.id for a in AREAS if "confined_space" in a.hazard_tags)
LAYDOWN_AREA = "A08"  # routine lifts; no hot work is ever placed here

CS_SPACES = {"A02": "H-301 firebox", "A03": "C-301 bottom section", "A05": "drum V-305"}

ROLE_COUNTS: dict[Role, int] = {
    Role.welder: 5,
    Role.fire_watch: 5,
    Role.fitter: 12,
    Role.attendant: 2,
    Role.crane_op: 2,
    Role.banksman: 2,
}
CRANES = ("CR-01", "CR-02")  # one per crane operator
AREA_AUTHORITIES = ("AA-01", "AA-02", "AA-03")
GAS_TESTERS = ("AGT-01", "AGT-02", "AGT-03")

PPE_OK = {"hard_hat": True, "hi_vis": True, "fr_coverall": True, "gas_monitor": True}


def area_at(x: float, y: float) -> AreaSpec | None:
    for area in AREAS:
        if area.rect.contains(x, y):
            return area
    return None


def camera_for(area: AreaSpec, x: float) -> str:
    """Pick the camera covering ``x`` when an area has several (split left/right)."""
    if len(area.camera_ids) == 1:
        return area.camera_ids[0]
    span = area.rect.width / len(area.camera_ids)
    idx = int((x - area.rect.x0) // span)
    return area.camera_ids[min(max(idx, 0), len(area.camera_ids) - 1)]
