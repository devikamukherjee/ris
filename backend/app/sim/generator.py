"""Seeded generator for one 12-hour day shift at Northbank Refinery, Unit 3.

Pipeline:
1. Schedule permits and book pseudonymous crews so nobody is in two places at once.
2. Plan each subject's (worker or crane's) behaviour as time segments.
3. Inject labelled scenarios (``app.sim.scenarios``) by carving and replacing segments.
4. Render segments into detection events, and build gas tests.

The same seed always gives byte-identical output.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from app.fixtures import Dataset, SiteData
from app.models import (
    Activity,
    Area,
    DetectionEvent,
    GasTest,
    GroundTruthLabel,
    LiftPlan,
    Permit,
    PermitType,
    Role,
    Worker,
)
from app.sim import site
from app.sim.site import AREA_BY_ID, Rect

GENERATOR_VERSION = "1"

MIN = timedelta(minutes=1)
SEC = timedelta(seconds=1)

# Permits that host scenarios, scheduled first (most constrained crews first).
PERMIT_PLAN: tuple[tuple[PermitType, int], ...] = (
    (PermitType.confined_space, 4),
    (PermitType.hot_work, 8),
    (PermitType.line_break, 4),
    (PermitType.cold_work, 7),
)
# Routine laydown lifts, scheduled after the SIMOPS scenarios have taken their cranes.
ROUTINE_LIFTS = 2
DURATION_H = {
    PermitType.hot_work: (4.0, 5.25),
    PermitType.cold_work: (2.0, 3.5),
    PermitType.confined_space: (2.5, 4.0),
    PermitType.lifting: (1.5, 2.5),
    PermitType.line_break: (2.0, 3.0),
}
PREFIX = {
    PermitType.hot_work: "HW",
    PermitType.cold_work: "CW",
    PermitType.confined_space: "CS",
    PermitType.lifting: "LF",
    PermitType.line_break: "LB",
}
EARLIEST_START = site.SHIFT_START + 15 * MIN
LATEST_END = site.SHIFT_END - 45 * MIN  # leaves room for work-after-expiry scenarios
BOOK_BEFORE = 10 * MIN
# Crew stay booked after valid_to; cold work and line breaks keep room for overrun scenarios.
BOOK_AFTER = {PermitType.cold_work: 40 * MIN, PermitType.line_break: 40 * MIN}
BOOK_AFTER_DEFAULT = 10 * MIN
PACKING_CHOICES = 6  # starts are drawn from the earliest or latest few feasible slots

HOT_GAS_INTERVAL_MIN = (80, 105)
CS_GAS_INTERVAL_MIN = (40, 52)

Mix = tuple[tuple[Activity, float], ...]
HOT_MIX: Mix = ((Activity.welding, 0.45), (Activity.grinding, 0.2), (Activity.cutting, 0.1), (Activity.idle, 0.25))
COLD_MIX: Mix = ((Activity.fitting, 0.7), (Activity.idle, 0.2), (Activity.walking, 0.1))
SURFACE_MIX: Mix = ((Activity.fitting, 0.6), (Activity.idle, 0.4))
WATCH: Mix = ((Activity.watching, 1.0),)
ATTEND: Mix = ((Activity.attending, 1.0),)
BANKSMAN_MIX: Mix = ((Activity.signalling, 0.8), (Activity.idle, 0.2))
NORMAL_CONF = (0.85, 0.98)


class GenerationError(RuntimeError):
    pass


@dataclass
class Segment:
    """A subject doing one kind of thing at one place, sampled every ``cadence_s`` seconds.

    ``once`` segments emit a single event at ``start`` (entries, exits, scripted bursts).
    Positions are jittered around ``anchor`` and, if ``bounds`` is set, kept 1 m inside it.
    """

    subject: str
    start: datetime
    end: datetime
    mix: Mix
    anchor: tuple[float, float]
    area_id: str
    cadence_s: int = 60
    jitter_m: float = 0.3
    bounds: Rect | None = None
    conf: tuple[float, float] = NORMAL_CONF
    once: bool = False


@dataclass
class Timeline:
    """Planned behaviour for one permit, before rendering."""

    permit: Permit
    rect: Rect
    roles: dict[Role, list[str]]
    work_start: datetime
    work_end: datetime
    anchors: dict[str, tuple[float, float]]
    gas_first: datetime | None = None
    gas_times: list[tuple[datetime, datetime]] | None = None  # (taken, recorded)
    fire_watch_end: datetime | None = None
    manway: tuple[float, float] | None = None
    cs_cycles: list[dict[str, tuple[datetime, datetime]]] = field(default_factory=list)
    families: list[str] = field(default_factory=list)

    @property
    def area(self) -> site.AreaSpec:
        return AREA_BY_ID[self.permit.area_id]

    @property
    def working_subjects(self) -> list[str]:
        """Crew members doing the task itself (not fire watch, attendant or standby)."""
        if self.permit.type == PermitType.hot_work:
            return self.roles[Role.welder]
        if self.permit.type in (PermitType.cold_work, PermitType.line_break):
            return self.roles[Role.fitter]
        return []

    @property
    def work_mix(self) -> Mix:
        return HOT_MIX if self.permit.type == PermitType.hot_work else COLD_MIX


@dataclass
class World:
    rng: random.Random
    workers: list[Worker]
    by_role: dict[Role, list[str]]
    crane_of: dict[str, str]
    busy: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)
    reserved: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)
    timelines: dict[str, Timeline] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    lift_plans: list[LiftPlan] = field(default_factory=list)
    labels: list[GroundTruthLabel] = field(default_factory=list)
    next_permit_no: int = 2041

    # -- random helpers --
    def minutes(self, lo: float, hi: float) -> timedelta:
        return timedelta(seconds=round(self.rng.uniform(lo, hi) * 60))

    def seconds(self, lo: float, hi: float) -> timedelta:
        return timedelta(seconds=round(self.rng.uniform(lo, hi)))

    def time_between(self, lo: datetime, hi: datetime) -> datetime:
        return lo + timedelta(seconds=round((hi - lo).total_seconds() * self.rng.random()))

    # -- crew booking --
    def free_workers(self, role: Role, start: datetime, end: datetime) -> list[str]:
        return [w for w in self.by_role[role] if _free(self.busy.get(w, []), start, end)]

    def can_book(self, needs: dict[Role, int], start: datetime, end: datetime) -> bool:
        return all(len(self.free_workers(role, start, end)) >= n for role, n in needs.items())

    def book(self, needs: dict[Role, int], start: datetime, end: datetime) -> dict[Role, list[str]] | None:
        picked: dict[Role, list[str]] = {}
        for role, n in needs.items():
            free = self.free_workers(role, start, end)
            if len(free) < n:
                return None
            picked[role] = self.rng.sample(free, n)
        for ids in picked.values():
            for w in ids:
                self.busy.setdefault(w, []).append((start, end))
        return picked

    # -- scenario windows, so two scenarios never rewrite the same subject at once --
    def reserve(self, subject: str, start: datetime, end: datetime, margin: timedelta = 5 * MIN) -> bool:
        if not _free(self.reserved.get(subject, []), start - margin, end + margin):
            return False
        self.reserved.setdefault(subject, []).append((start, end))
        return True

    def pick_window(self, subject: str, lo: datetime, hi: datetime, dur: timedelta, tries: int = 30) -> datetime | None:
        if hi - dur <= lo:
            return None
        for _ in range(tries):
            t = self.time_between(lo, hi - dur)
            if self.reserve(subject, t, t + dur):
                return t
        return None

    # -- segments --
    def add(self, seg: Segment) -> None:
        self.segments.append(seg)

    def carve(self, subject: str, start: datetime, end: datetime) -> None:
        """Remove ``subject``'s planned behaviour in ``[start, end)``, splitting segments."""
        kept: list[Segment] = []
        for seg in self.segments:
            if seg.subject != subject or seg.end <= start or seg.start >= end:
                kept.append(seg)
                continue
            if seg.once:
                continue
            if seg.start < start:
                kept.append(replace(seg, end=start))
            if seg.end > end:
                kept.append(replace(seg, start=end))
        self.segments = kept

    def new_permit(self, ptype: PermitType, **fields) -> Permit:
        permit = Permit(id=f"{PREFIX[ptype]}-{self.next_permit_no}", type=ptype, **fields)
        self.next_permit_no += 1
        return permit


def _free(intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
    return all(end <= s or start >= e for s, e in intervals)


def _size(rng: random.Random, w: tuple[float, float], h: tuple[float, float]) -> tuple[float, float]:
    return round(rng.uniform(*w), 1), round(rng.uniform(*h), 1)


def gas_schedule(world: World, first: datetime, end: datetime, interval_min: tuple[int, int]) -> list[datetime]:
    """Test times from ``first`` at random intervals, stopping before ``end``."""
    times = [first]
    while (nxt := times[-1] + world.minutes(*interval_min)) < end:
        times.append(nxt)
    return times


def default_gas_interval(permit: Permit) -> tuple[int, int]:
    return HOT_GAS_INTERVAL_MIN if permit.type == PermitType.hot_work else CS_GAS_INTERVAL_MIN


# --- Permit builders ----------------------------------------------------------


def _role_needs(world: World, ptype: PermitType) -> dict[Role, int]:
    rng = world.rng
    if ptype == PermitType.hot_work:
        return {Role.welder: 1, Role.fire_watch: 1}
    if ptype == PermitType.cold_work:
        return {Role.fitter: rng.choice([1, 1, 2])}
    if ptype == PermitType.line_break:
        return {Role.fitter: 2}
    if ptype == PermitType.confined_space:
        # max_entrants entrants plus one standby fitter at the surface
        return {Role.attendant: 1, Role.fitter: rng.choice([2, 2, 3]) + 1}
    return {Role.crane_op: 1, Role.banksman: 1}


def _schedule(world: World, ptype: PermitType) -> None:
    """Pick a duration and crew size, then a start with a free crew.

    Starts come from the earliest or latest few feasible slots, so each worker's permits
    pack back to back (as day-shift permits do) instead of fragmenting the day.
    """
    rng = world.rng
    after = BOOK_AFTER.get(ptype, BOOK_AFTER_DEFAULT)
    for _ in range(50):
        dur = timedelta(minutes=round(rng.uniform(*DURATION_H[ptype]) * 12) * 5)
        needs = _role_needs(world, ptype)
        n_slots = int((LATEST_END - dur - EARLIEST_START) / (5 * MIN))
        starts = [EARLIEST_START + 5 * MIN * i for i in range(n_slots + 1)]
        feasible = [s for s in starts if world.can_book(needs, s - BOOK_BEFORE, s + dur + after)]
        if feasible:
            end_of_day = rng.random() < 0.5
            start = rng.choice(feasible[-PACKING_CHOICES:] if end_of_day else feasible[:PACKING_CHOICES])
            crew = world.book(needs, start - BOOK_BEFORE, start + dur + after)
            BUILDERS[ptype](world, start, start + dur, crew)
            return
    raise GenerationError(f"could not find a crew for a {ptype.value} permit")


def _base_fields(world: World, area: site.AreaSpec, rect: Rect, vf: datetime, vt: datetime,
                 crew: dict[Role, list[str]], description: str) -> dict:
    return dict(
        area_id=area.id,
        description=description,
        work_polygon=rect.polygon(),
        valid_from=vf,
        valid_to=vt,
        issuer=world.rng.choice(site.AREA_AUTHORITIES),
        crew=[w for ids in crew.values() for w in ids],
    )


def _build_hot(world: World, vf: datetime, vt: datetime, crew: dict[Role, list[str]]) -> None:
    rng = world.rng
    area = AREA_BY_ID[rng.choice(site.WORK_AREAS)]
    rect = area.rect.random_subrect(rng, *_size(rng, (8, 16), (6, 12)), margin=2)
    eq = rng.choice(area.equipment)
    desc = f"Weld repair on {eq} {rng.choice(['suction flange', 'pipe support', 'drain line', 'nozzle reinforcement'])}"
    permit = world.new_permit(
        PermitType.hot_work,
        **_base_fields(world, area, rect, vf, vt, crew, desc),
        gas_test_required=True,
        gas_test_max_age_min=site.HOT_WORK_GAS_MAX_AGE_MIN,
        fire_watch_required=True,
        fire_watch_post_work_min=site.FIRE_WATCH_POST_WORK_MIN,
    )
    welder, fw = crew[Role.welder][0], crew[Role.fire_watch][0]
    w_anchor = rect.random_point(rng, margin=2)
    fw_anchor = rect.clamp(w_anchor[0] + rng.uniform(-3, 3), w_anchor[1] + rng.uniform(-3, 3), margin=1.5)
    ws, we = vf + world.minutes(15, 25), vt - world.minutes(70, 85)
    fw_start, fw_end = vf + world.minutes(5, 12), we + world.minutes(61, 66)
    tl = Timeline(permit, rect, crew, ws, we, {welder: w_anchor, fw: fw_anchor},
                  gas_first=vf + world.minutes(3, 10), fire_watch_end=fw_end)
    world.timelines[permit.id] = tl
    world.add(Segment(welder, ws, we, HOT_MIX, w_anchor, area.id, bounds=rect))
    world.add(Segment(fw, fw_start, fw_end, WATCH, fw_anchor, area.id, bounds=rect))


def _build_cold(world: World, vf: datetime, vt: datetime, crew: dict[Role, list[str]],
                ptype: PermitType = PermitType.cold_work) -> None:
    rng = world.rng
    area = AREA_BY_ID[rng.choice(site.WORK_AREAS)]
    rect = area.rect.random_subrect(rng, *_size(rng, (8, 16), (6, 12)), margin=2)
    eq = rng.choice(area.equipment)
    if ptype == PermitType.line_break:
        desc = f"Break containment on {eq} {rng.choice(['inlet spool', 'drain valve', 'control valve bypass'])}"
    else:
        desc = rng.choice([f"Replace gasket on {eq}", f"Scaffold erection at {eq}",
                           f"Valve maintenance on {eq}", f"Insulation repair on {eq}"])
    permit = world.new_permit(ptype, **_base_fields(world, area, rect, vf, vt, crew, desc))
    ws, we = vf + world.minutes(10, 20), vt - world.minutes(5, 20)
    anchors = {w: rect.random_point(rng, margin=2) for w in crew[Role.fitter]}
    world.timelines[permit.id] = Timeline(permit, rect, crew, ws, we, anchors)
    for w, anchor in anchors.items():
        world.add(Segment(w, ws, we, COLD_MIX, anchor, area.id, bounds=rect))


def _build_line_break(world: World, vf: datetime, vt: datetime, crew: dict[Role, list[str]]) -> None:
    _build_cold(world, vf, vt, crew, PermitType.line_break)


def _build_confined_space(world: World, vf: datetime, vt: datetime, crew: dict[Role, list[str]]) -> None:
    rng = world.rng
    area = AREA_BY_ID[rng.choice(site.CS_AREAS)]
    rect = area.rect.random_subrect(rng, *_size(rng, (8, 14), (6, 10)), margin=2)
    *entrants, standby = crew[Role.fitter]
    attendant = crew[Role.attendant][0]
    desc = f"Internal inspection of {site.CS_SPACES[area.id]}"
    permit = world.new_permit(
        PermitType.confined_space,
        **_base_fields(world, area, rect, vf, vt, crew, desc),
        gas_test_required=True,
        gas_test_max_age_min=site.CS_GAS_MAX_AGE_MIN,
        attendant_required=True,
        max_entrants=len(entrants),
    )
    manway = rect.random_point(rng, margin=1.5)
    att_anchor = rect.clamp(manway[0] + 1.5, manway[1], margin=1)
    sb_anchor = rect.random_point(rng, margin=2)
    ws, we = vf + world.minutes(15, 25), vt - world.minutes(15, 25)
    mid = ws + (we - ws) / 2
    cycles: list[dict[str, tuple[datetime, datetime]]] = []
    for c_start, c_end in ((ws, mid - 15 * MIN), (mid + 15 * MIN, we)):
        cycle = {}
        for i, e in enumerate(entrants):
            enter = c_start + i * world.minutes(1, 3)
            leave = c_end - i * world.minutes(1, 3)
            cycle[e] = (enter, leave)
            world.add(Segment(e, enter, enter + SEC, ((Activity.entering_cs, 1.0),), manway, area.id,
                              jitter_m=0.1, once=True))
            world.add(Segment(e, leave, leave + SEC, ((Activity.exiting_cs, 1.0),), manway, area.id,
                              jitter_m=0.1, once=True))
        cycles.append(cycle)
    world.timelines[permit.id] = Timeline(
        permit, rect, crew, ws, we,
        {attendant: att_anchor, standby: sb_anchor, **{e: manway for e in entrants}},
        gas_first=vf + world.minutes(3, 8), manway=manway, cs_cycles=cycles,
    )
    world.add(Segment(attendant, vf + world.minutes(5, 10), we + world.minutes(3, 8), ATTEND, att_anchor,
                      area.id, bounds=rect))
    world.add(Segment(standby, ws, we, SURFACE_MIX, sb_anchor, area.id, bounds=rect))


def add_lift(world: World, tl: Timeline, footprint: Rect, start: datetime, end: datetime, desc: str) -> LiftPlan:
    """Add a lift plan with its crane and banksman behaviour to a lifting permit."""
    crane = world.crane_of[tl.roles[Role.crane_op][0]]
    banksman = tl.roles[Role.banksman][0]
    lift = LiftPlan(
        id=f"LP-{len(world.lift_plans) + 1:02d}", permit_id=tl.permit.id, kind="lift", vehicle_id=crane,
        area_id=tl.permit.area_id, footprint=footprint.polygon(), start=start, end=end, description=desc,
    )
    world.lift_plans.append(lift)
    bm_anchor = tl.rect.clamp(footprint.x0 - 1.5, footprint.y0 - 1.5, margin=0.5)
    world.add(Segment(crane, start, end, ((Activity.lift_in_progress, 1.0),), footprint.centre,
                      tl.permit.area_id, jitter_m=0.2))
    world.add(Segment(banksman, start - 5 * MIN, end + 5 * MIN, BANKSMAN_MIX, bm_anchor, tl.permit.area_id,
                      bounds=tl.rect))
    return lift


def _build_lifting(world: World, vf: datetime, vt: datetime, crew: dict[Role, list[str]]) -> None:
    rng = world.rng
    area = AREA_BY_ID[site.LAYDOWN_AREA]
    rect = area.rect.random_subrect(rng, *_size(rng, (15, 30), (10, 20)), margin=2)
    item = rng.choice(["pipe spools", "scaffold bundles", "a pump skid", "heat exchanger bundle"])
    permit = world.new_permit(PermitType.lifting,
                              **_base_fields(world, area, rect, vf, vt, crew, f"Lift {item} at laydown"))
    tl = Timeline(permit, rect, crew, vf, vt, {})
    world.timelines[permit.id] = tl
    t = vf + world.minutes(15, 25)
    for _ in range(rng.randint(1, 3)):
        dur = world.minutes(20, 45)
        if t + dur > vt - 15 * MIN:
            break
        fp = rect.random_subrect(rng, *_size(rng, (6, 10), (6, 10)), margin=1)
        add_lift(world, tl, fp, t, t + dur, f"Lift {item}")
        t += dur + world.minutes(10, 30)


BUILDERS = {
    PermitType.hot_work: _build_hot,
    PermitType.cold_work: _build_cold,
    PermitType.line_break: _build_line_break,
    PermitType.confined_space: _build_confined_space,
    PermitType.lifting: _build_lifting,
}


# --- Rendering ------------------------------------------------------------------


def _render_events(world: World) -> list[DetectionEvent]:
    rng = world.rng
    events: list[DetectionEvent] = []
    for seg in world.segments:
        acts, weights = zip(*seg.mix)
        t = seg.start
        while t < seg.end:
            x = seg.anchor[0] + rng.gauss(0, seg.jitter_m) if seg.jitter_m else seg.anchor[0]
            y = seg.anchor[1] + rng.gauss(0, seg.jitter_m) if seg.jitter_m else seg.anchor[1]
            if seg.bounds is not None:
                x, y = seg.bounds.clamp(x, y, margin=1.0)
            activity = rng.choices(acts, weights)[0]
            area = site.area_at(x, y) or AREA_BY_ID[seg.area_id]
            is_vehicle = seg.subject in world.crane_of.values()
            events.append(DetectionEvent(
                id=0,
                time=t,
                camera_id=site.camera_for(area, x),
                area_id=area.id,
                worker_id=None if is_vehicle else seg.subject,
                vehicle_id=seg.subject if is_vehicle else None,
                x=round(x, 2),
                y=round(y, 2),
                activity=activity,
                ppe=None if is_vehicle or activity == Activity.absent else dict(site.PPE_OK),
                confidence=round(rng.uniform(*seg.conf), 2),
            ))
            if seg.once:
                break
            t += timedelta(seconds=seg.cadence_s)
    events.sort(key=lambda e: (e.time, e.worker_id or e.vehicle_id or ""))
    for i, e in enumerate(events, start=1):
        e.id = i
    return events


def _gas_tests(world: World) -> list[GasTest]:
    rng = world.rng
    tests: list[tuple[datetime, datetime, Timeline]] = []
    for tl in world.timelines.values():
        if not tl.permit.gas_test_required:
            continue
        if tl.gas_times is None:
            times = gas_schedule(world, tl.gas_first, tl.work_end, default_gas_interval(tl.permit))
            tl.gas_times = [(t, t + world.seconds(0, 60)) for t in times]
        tests.extend((taken, recorded, tl) for taken, recorded in tl.gas_times)
    tests.sort(key=lambda x: (x[0], x[2].permit.id))
    out = []
    for i, (taken, recorded, tl) in enumerate(tests, start=1):
        sour = "H2S" in tl.area.hazard_tags
        out.append(GasTest(
            id=f"GT-{i:04d}", permit_id=tl.permit.id, time=taken, recorded_at=recorded,
            lel_pct=round(rng.uniform(0, 0.4), 1), o2_pct=round(rng.uniform(20.6, 20.9), 1),
            h2s_ppm=round(rng.uniform(0, 1.5) if sour else rng.uniform(0, 0.3), 1),
            tester=rng.choice(site.GAS_TESTERS),
        ))
    return out


def generate(seed: int = 42) -> Dataset:
    """Generate the shift. Deterministic in ``seed``."""
    from app.sim.scenarios import inject_scenarios  # scenarios import this module

    rng = random.Random(seed)
    roles = [role for role, n in site.ROLE_COUNTS.items() for _ in range(n)]
    rng.shuffle(roles)
    workers = [Worker(id=f"W-{i:03d}", role=role) for i, role in enumerate(roles, start=1)]
    by_role = {role: [w.id for w in workers if w.role == role] for role in site.ROLE_COUNTS}
    world = World(rng, workers, by_role, dict(zip(by_role[Role.crane_op], site.CRANES)))

    for ptype, count in PERMIT_PLAN:
        for _ in range(count):
            _schedule(world, ptype)
    inject_scenarios(world)
    for _ in range(ROUTINE_LIFTS):
        _schedule(world, PermitType.lifting)

    gas_tests = _gas_tests(world)
    events = _render_events(world)
    labels = sorted(world.labels, key=lambda lb: (lb.start, lb.kind))
    for i, lb in enumerate(labels, start=1):
        lb.scenario_id = f"SC-{i:03d}"

    areas = [Area(id=a.id, name=a.name, polygon=a.rect.polygon(), hazard_tags=list(a.hazard_tags),
                  camera_ids=list(a.camera_ids)) for a in site.AREAS]
    permits = sorted((tl.permit for tl in world.timelines.values()), key=lambda p: p.id)
    manifest = {
        "site": "Northbank Refinery, Unit 3",
        "seed": seed,
        "generator_version": GENERATOR_VERSION,
        "shift_start": site.SHIFT_START.isoformat(),
        "shift_end": site.SHIFT_END.isoformat(),
        "counts": {
            "areas": len(areas),
            "workers": len(workers),
            "permits": len(permits),
            "gas_tests": len(gas_tests),
            "lift_plans": len(world.lift_plans),
            "events": len(events),
            "scenarios": len(labels),
        },
    }
    return Dataset(
        site=SiteData(areas=areas, workers=workers, permits=permits, gas_tests=gas_tests,
                      lift_plans=world.lift_plans, events=events),
        ground_truth=labels,
        manifest=manifest,
    )
