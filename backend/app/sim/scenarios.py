"""Scenario library: injected situations with a ground-truth label.

``true_violation`` scenarios really break a clause of the site procedure. ``no_violation``
decoys only look like they do (tracker jitter, a late record, occlusion, a misread
activity). Some decoys are meant to trip the rules, so the system is sometimes wrong
and the study can measure whether people notice.

Each injector rewrites the plan for one permit and returns where and when the scenario
happened, or ``None`` if it can't be placed on that permit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from app.models import Activity, GroundTruthLabel, GroundTruthLabelValue, PermitType, Role
from app.sim import site
from app.sim.generator import (
    BOOK_BEFORE,
    COLD_MIX,
    MIN,
    SEC,
    GenerationError,
    Mix,
    Segment,
    Timeline,
    World,
    add_lift,
    default_gas_interval,
    gas_schedule,
)
from app.sim.site import AREA_BY_ID, Rect

TRUE = GroundTruthLabelValue.true_violation
DECOY = GroundTruthLabelValue.no_violation

HOT = (PermitType.hot_work,)
COLD = (PermitType.cold_work,)
TASK = (PermitType.hot_work, PermitType.cold_work, PermitType.line_break)

HIGH_CONF = (0.85, 0.97)
OCCLUDED_CONF = (0.35, 0.6)
MISREAD_CONF = (0.4, 0.58)


@dataclass
class Placement:
    permit_ids: list[str]
    subject_id: str | None
    start: datetime
    end: datetime
    notes: str


Injector = Callable[[World, Timeline], "Placement | None"]


@dataclass(frozen=True)
class ScenarioSpec:
    kind: str
    label: GroundTruthLabelValue
    rule_id: str
    permit_types: tuple[PermitType, ...]
    family: str  # at most one scenario per family per permit
    count: int
    inject: Injector


def _fmt(t: datetime) -> str:
    return t.strftime("%H:%M:%S")


def _absent(subject: str, start: datetime, end: datetime, anchor: tuple[float, float], area_id: str,
            conf: tuple[float, float], cadence_s: int = 60) -> Segment:
    return Segment(subject, start, end, ((Activity.absent, 1.0),), anchor, area_id,
                   cadence_s=cadence_s, jitter_m=0.0, conf=conf)


def _edge_point(rect: Rect, anchor: tuple[float, float], offset: float) -> tuple[float, float]:
    """Point level with ``anchor`` on the nearest edge of ``rect``, moved ``offset`` metres
    outward (positive) or inward (negative)."""
    x, y = anchor
    dists = {"left": x - rect.x0, "right": rect.x1 - x, "bottom": y - rect.y0, "top": rect.y1 - y}
    side = min(dists, key=dists.__getitem__)
    if side == "left":
        return rect.x0 - offset, y
    if side == "right":
        return rect.x1 + offset, y
    if side == "bottom":
        return x, rect.y0 - offset
    return x, rect.y1 + offset


# --- Area -----------------------------------------------------------------------


def area_breach(world: World, tl: Timeline) -> Placement | None:
    subject = world.rng.choice(tl.working_subjects)
    dur = world.minutes(4, 12)
    t = world.pick_window(subject, tl.work_start + 20 * MIN, tl.work_end - 20 * MIN, dur)
    if t is None:
        return None
    offset = world.rng.uniform(3, 8)
    pos = _edge_point(tl.rect, tl.anchors[subject], offset)
    world.carve(subject, t, t + dur)
    world.add(Segment(subject, t, t + dur, tl.work_mix, pos, tl.permit.area_id))
    return Placement([tl.permit.id], subject, t, t + dur,
                     f"{subject} worked {offset:.1f} m outside the permitted work area")


def decoy_area_jitter(world: World, tl: Timeline) -> Placement | None:
    subject = world.rng.choice(tl.working_subjects)
    t = world.pick_window(subject, tl.work_start + 20 * MIN, tl.work_end - 20 * MIN, 4 * SEC)
    if t is None:
        return None
    anchor = tl.anchors[subject]
    inside, outside = _edge_point(tl.rect, anchor, -0.4), _edge_point(tl.rect, anchor, 0.5)
    activity = tl.work_mix[0][0]
    world.carve(subject, t, t + 4 * SEC)
    for k, pos in enumerate((inside, outside, outside, inside)):
        world.add(Segment(subject, t + k * SEC, t + (k + 1) * SEC, ((activity, 1.0),), pos,
                          tl.permit.area_id, jitter_m=0.0, once=True))
    return Placement([tl.permit.id], subject, t + SEC, t + 3 * SEC,
                     "Tracker jitter: position read 0.5 m outside the work area for 2 s; the worker never left")


# --- Gas tests ------------------------------------------------------------------


def _gas_base(world: World, tl: Timeline, slack: timedelta) -> tuple[list[datetime], int] | None:
    """A normal test schedule and a re-test index whose due time falls inside the work."""
    base = gas_schedule(world, tl.gas_first, tl.work_end, default_gas_interval(tl.permit))
    max_age = timedelta(minutes=tl.permit.gas_test_max_age_min)
    ks = [k for k in range(1, len(base)) if base[k - 1] + max_age + slack < tl.work_end]
    return (base, world.rng.choice(ks)) if ks else None


def gas_stale(world: World, tl: Timeline) -> Placement | None:
    picked = _gas_base(world, tl, 15 * MIN)
    if picked is None:
        return None
    base, k = picked
    due = base[k - 1] + timedelta(minutes=tl.permit.gas_test_max_age_min)
    retest = due + world.minutes(15, 35)
    times = base[:k]
    if retest < tl.work_end:
        times += gas_schedule(world, retest, tl.work_end, default_gas_interval(tl.permit))
    tl.gas_times = [(t, t + world.seconds(0, 60)) for t in times]
    end = min(retest, tl.work_end)
    return Placement([tl.permit.id], tl.roles[Role.welder][0], due, end,
                     f"Re-test due {_fmt(due)} was not taken until {_fmt(retest)}; hot work continued")


def decoy_gas_late_record(world: World, tl: Timeline) -> Placement | None:
    picked = _gas_base(world, tl, 5 * MIN)
    if picked is None:
        return None
    base, k = picked
    due = base[k - 1] + timedelta(minutes=tl.permit.gas_test_max_age_min)
    taken = due - world.minutes(1, 3)
    recorded = taken + 4 * MIN
    rest = gas_schedule(world, taken, tl.work_end, default_gas_interval(tl.permit))[1:]
    tl.gas_times = ([(t, t + world.seconds(0, 60)) for t in base[:k]] + [(taken, recorded)]
                    + [(t, t + world.seconds(0, 60)) for t in rest])
    return Placement([tl.permit.id], None, due, recorded,
                     f"Re-test was taken in time at {_fmt(taken)} but its record arrived 4 min late at {_fmt(recorded)}")


# --- Fire watch -----------------------------------------------------------------


def fire_watch_absent(world: World, tl: Timeline) -> Placement | None:
    fw = tl.roles[Role.fire_watch][0]
    dur = world.minutes(8, 20)
    t = world.pick_window(fw, tl.work_start + 20 * MIN, tl.work_end - 20 * MIN, dur)
    if t is None:
        return None
    world.carve(fw, t, t + dur)
    world.add(_absent(fw, t, t + dur, tl.anchors[fw], tl.permit.area_id, HIGH_CONF))
    return Placement([tl.permit.id], fw, t, t + dur, f"Fire watch {fw} left the site while hot work continued")


def fire_watch_early_leave(world: World, tl: Timeline) -> Placement | None:
    fw = tl.roles[Role.fire_watch][0]
    leave = tl.work_end + world.minutes(15, 35)
    watch_until = tl.work_end + timedelta(minutes=tl.permit.fire_watch_post_work_min)
    if not world.reserve(fw, leave, watch_until):
        return None
    world.carve(fw, leave, tl.fire_watch_end)
    world.add(_absent(fw, leave, watch_until + 2 * MIN, tl.anchors[fw], tl.permit.area_id, HIGH_CONF))
    return Placement([tl.permit.id], fw, leave, watch_until,
                     f"Fire watch {fw} left {(leave - tl.work_end).seconds // 60} min after hot work ended (60 min required)")


def decoy_fire_watch_occluded(world: World, tl: Timeline) -> Placement | None:
    fw = tl.roles[Role.fire_watch][0]
    dur = world.seconds(30, 90)
    t = world.pick_window(fw, tl.work_start + 20 * MIN, tl.work_end - 20 * MIN, dur)
    if t is None:
        return None
    world.carve(fw, t, t + dur)
    world.add(_absent(fw, t, t + dur, tl.anchors[fw], tl.permit.area_id, OCCLUDED_CONF, cadence_s=10))
    return Placement([tl.permit.id], fw, t, t + dur,
                     "Fire watch was briefly hidden behind scaffolding; they never left their post")


# --- Validity -------------------------------------------------------------------


def work_after_expiry(world: World, tl: Timeline) -> Placement | None:
    vt = tl.permit.valid_to
    end = vt + world.minutes(15, 30)
    if end > site.SHIFT_END or not all(world.reserve(w, tl.work_end, end) for w in tl.working_subjects):
        return None
    for w in tl.working_subjects:
        world.add(Segment(w, tl.work_end, end, COLD_MIX, tl.anchors[w], tl.permit.area_id, bounds=tl.rect))
    return Placement([tl.permit.id], None, vt, end,
                     f"Crew kept working {(end - vt).seconds // 60} min after the permit expired at {_fmt(vt)}")


# --- Activity -------------------------------------------------------------------


def _grinding(world: World, tl: Timeline, dur: timedelta, mix: Mix,
              conf: tuple[float, float]) -> tuple[str, datetime] | None:
    subject = world.rng.choice(tl.working_subjects)
    t = world.pick_window(subject, tl.work_start + 15 * MIN, tl.work_end - 15 * MIN, dur)
    if t is None:
        return None
    world.carve(subject, t, t + dur)
    world.add(Segment(subject, t, t + dur, mix, tl.anchors[subject], tl.permit.area_id, bounds=tl.rect, conf=conf))
    return subject, t


def activity_mismatch(world: World, tl: Timeline) -> Placement | None:
    dur = world.minutes(6, 15)
    placed = _grinding(world, tl, dur, ((Activity.grinding, 0.85), (Activity.cutting, 0.15)), HIGH_CONF)
    if placed is None:
        return None
    subject, t = placed
    return Placement([tl.permit.id], subject, t, t + dur,
                     f"{subject} used a powered grinder under a cold-work permit")


def decoy_grinding_misread(world: World, tl: Timeline) -> Placement | None:
    dur = world.minutes(2, 5)
    placed = _grinding(world, tl, dur, ((Activity.grinding, 1.0),), MISREAD_CONF)
    if placed is None:
        return None
    subject, t = placed
    return Placement([tl.permit.id], subject, t, t + dur,
                     "Detector read hand wire-brushing as grinding; wire brushes are allowed on cold work (PTW-8.2)")


# --- Confined space -------------------------------------------------------------


def cs_over_count(world: World, tl: Timeline) -> Placement | None:
    standby = tl.roles[Role.fitter][-1]
    cycle = world.rng.choice(tl.cs_cycles)
    all_in = max(enter for enter, _ in cycle.values())
    first_out = min(leave for _, leave in cycle.values())
    dur = world.minutes(8, 18)
    t = world.pick_window(standby, all_in + 5 * MIN, first_out - 5 * MIN, dur)
    if t is None:
        return None
    world.carve(standby, t, t + dur + SEC)
    for when, act in ((t, Activity.entering_cs), (t + dur, Activity.exiting_cs)):
        world.add(Segment(standby, when, when + SEC, ((act, 1.0),), tl.manway, tl.permit.area_id,
                          jitter_m=0.1, once=True))
    limit = tl.permit.max_entrants
    return Placement([tl.permit.id], standby, t, t + dur,
                     f"{standby} entered as person {limit + 1}; the permit allows {limit}")


def cs_no_attendant(world: World, tl: Timeline) -> Placement | None:
    attendant = tl.roles[Role.attendant][0]
    cycle = world.rng.choice(tl.cs_cycles)
    first_in = min(enter for enter, _ in cycle.values())
    last_out = max(leave for _, leave in cycle.values())
    dur = world.minutes(8, 15)
    t = world.pick_window(attendant, first_in + 5 * MIN, last_out - 5 * MIN, dur)
    if t is None:
        return None
    world.carve(attendant, t, t + dur)
    world.add(_absent(attendant, t, t + dur, tl.anchors[attendant], tl.permit.area_id, HIGH_CONF))
    return Placement([tl.permit.id], attendant, t, t + dur,
                     f"Attendant {attendant} left the entry point with entrants inside")


# --- SIMOPS ---------------------------------------------------------------------


def simops_overlap(world: World, tl: Timeline) -> Placement | None:
    """Add a lifting permit whose lift footprint overlaps this hot-work area mid-job."""
    rng = world.rng
    area = AREA_BY_ID[tl.permit.area_id]
    for _ in range(20):
        dur = world.minutes(25, 45)
        lo, hi = tl.work_start + 30 * MIN, tl.work_end - 20 * MIN
        if hi - dur <= lo:
            return None
        start = world.time_between(lo, hi - dur)
        end = start + dur
        w, h = rng.uniform(6, 9), rng.uniform(6, 9)
        # Centre the footprint on a random point of the hot-work boundary.
        if rng.random() < 0.5:
            cx, cy = rng.choice([tl.rect.x0, tl.rect.x1]), rng.uniform(tl.rect.y0, tl.rect.y1)
        else:
            cx, cy = rng.uniform(tl.rect.x0, tl.rect.x1), rng.choice([tl.rect.y0, tl.rect.y1])
        fp = Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2).intersection(area.rect.expand(-1))
        if fp is None:
            continue
        fp = Rect(round(fp.x0, 1), round(fp.y0, 1), round(fp.x1, 1), round(fp.y1, 1))
        overlap = fp.intersection(tl.rect)
        if overlap is None or overlap.width * overlap.height < 4:
            continue
        # Only this hot-work permit may be affected, so the label is unambiguous.
        clash = any(
            other is not tl and other.permit.type == PermitType.hot_work and other.permit.area_id == area.id
            and other.permit.valid_from < end and start < other.permit.valid_to
            and other.rect.intersection(fp) is not None
            for other in world.timelines.values()
        )
        if clash:
            continue
        vf, vt = start - world.minutes(10, 20), end + world.minutes(15, 30)
        crew = world.book({Role.crane_op: 1, Role.banksman: 1}, vf - BOOK_BEFORE, vt + 10 * MIN)
        if crew is None:
            continue
        rect = fp.expand(2).intersection(area.rect) or fp
        item = rng.choice(["a replacement valve", "pipe spools", "a scaffold bundle"])
        lift_permit = world.new_permit(
            PermitType.lifting, area_id=area.id, description=f"Lift {item} into {area.name}",
            work_polygon=rect.polygon(), valid_from=vf, valid_to=vt,
            issuer=rng.choice(site.AREA_AUTHORITIES), crew=[w for ids in crew.values() for w in ids],
        )
        lift_tl = Timeline(lift_permit, rect, crew, start, end, {})
        world.timelines[lift_permit.id] = lift_tl
        lift_tl.families.append("simops")
        lift = add_lift(world, lift_tl, fp, start, end, f"Lift {item}")
        return Placement([tl.permit.id, lift_permit.id], lift.vehicle_id, start, end,
                         f"Lift {lift.id} footprint overlaps the work area of hot-work permit {tl.permit.id}")
    return None


SCENARIOS: tuple[ScenarioSpec, ...] = (
    # SIMOPS first: it needs free cranes and crews.
    ScenarioSpec("simops_overlap", TRUE, "R-SIMOPS", HOT, "simops", 3, simops_overlap),
    ScenarioSpec("gas_stale", TRUE, "R-GAS-AGE", HOT, "gas", 4, gas_stale),
    ScenarioSpec("decoy_gas_late_record", DECOY, "R-GAS-AGE", HOT, "gas", 3, decoy_gas_late_record),
    ScenarioSpec("fire_watch_absent", TRUE, "R-FIRE-WATCH", HOT, "fire_watch", 3, fire_watch_absent),
    ScenarioSpec("fire_watch_early_leave", TRUE, "R-FIRE-WATCH", HOT, "fire_watch", 2, fire_watch_early_leave),
    ScenarioSpec("decoy_fire_watch_occluded", DECOY, "R-FIRE-WATCH", HOT, "fire_watch", 3, decoy_fire_watch_occluded),
    ScenarioSpec("cs_over_count", TRUE, "R-CS-COUNT", (PermitType.confined_space,), "cs_count", 2, cs_over_count),
    ScenarioSpec("cs_no_attendant", TRUE, "R-CS-ATTENDANT", (PermitType.confined_space,), "cs_attendant", 3,
                 cs_no_attendant),
    ScenarioSpec("work_after_expiry", TRUE, "R-EXPIRED", (PermitType.cold_work, PermitType.line_break), "expiry", 5,
                 work_after_expiry),
    ScenarioSpec("activity_mismatch", TRUE, "R-ACTIVITY-MISMATCH", COLD, "activity", 3, activity_mismatch),
    ScenarioSpec("decoy_grinding_misread", DECOY, "R-ACTIVITY-MISMATCH", COLD, "activity", 3, decoy_grinding_misread),
    ScenarioSpec("area_breach", TRUE, "R-AREA", TASK, "area", 7, area_breach),
    ScenarioSpec("decoy_area_jitter", DECOY, "R-AREA", TASK, "area", 4, decoy_area_jitter),
)


def inject_scenarios(world: World) -> None:
    """Place every scenario in ``SCENARIOS``, spreading them over the least-used permits."""
    for spec in SCENARIOS:
        hosts = [tl for tl in world.timelines.values()
                 if tl.permit.type in spec.permit_types and spec.family not in tl.families]
        world.rng.shuffle(hosts)
        hosts.sort(key=lambda tl: len(tl.families))
        placed = 0
        for tl in hosts:
            if placed == spec.count:
                break
            p = spec.inject(world, tl)
            if p is None:
                continue
            tl.families.append(spec.family)
            placed += 1
            world.labels.append(GroundTruthLabel(
                scenario_id="", kind=spec.kind, label=spec.label, expected_rule_id=spec.rule_id,
                permit_ids=p.permit_ids, subject_id=p.subject_id, start=p.start, end=p.end, notes=p.notes,
            ))
        if placed < spec.count:
            raise GenerationError(f"placed only {placed}/{spec.count} {spec.kind} scenarios")
