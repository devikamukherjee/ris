"""The seeded shift: structure, privacy, and that every scenario really is what its
ground-truth label says, checked from the raw events rather than the generator's plan."""

import json
import re
from collections import Counter, defaultdict
from datetime import timedelta

import pytest

from app.fixtures import export_fixtures
from app.models import HOT_ACTIVITIES, Activity, GroundTruthLabelValue, PermitType
from app.sim import site
from app.sim.generator import generate
from app.sim.scenarios import SCENARIOS
from app.sim.site import Rect

TRUE = GroundTruthLabelValue.true_violation
DECOY = GroundTruthLabelValue.no_violation


@pytest.fixture(scope="module")
def ds():
    return generate(42)


@pytest.fixture(scope="module")
def permits(ds):
    return {p.id: p for p in ds.site.permits}


@pytest.fixture(scope="module")
def by_subject(ds):
    out = defaultdict(list)
    for e in ds.site.events:
        out[e.worker_id or e.vehicle_id].append(e)
    return out


def labels_of(ds, kind):
    return [lb for lb in ds.ground_truth if lb.kind == kind]


def events_in(events, start, end):
    return [e for e in events if start <= e.time <= end]


def rect(permit):
    return Rect.from_polygon(permit.work_polygon)


def occupancy(ds, permit):
    """(time, people inside) after each entry/exit of this permit's crew, from events."""
    count, out = 0, []
    for e in ds.site.events:
        if e.worker_id in permit.crew and permit.valid_from <= e.time <= permit.valid_to + timedelta(hours=1):
            if e.activity == Activity.entering_cs:
                count += 1
            elif e.activity == Activity.exiting_cs:
                count -= 1
            else:
                continue
            out.append((e.time, count))
    return out


# --- Determinism and size -------------------------------------------------------


def dump(dataset, tmp_path):
    export_fixtures(dataset, tmp_path)
    return {p.name: p.read_bytes() for p in sorted(tmp_path.iterdir())}


def test_same_seed_same_bytes_and_other_seed_differs(ds, tmp_path):
    a = dump(ds, tmp_path / "a")
    assert a == dump(generate(42), tmp_path / "b")
    assert a != dump(generate(7), tmp_path / "c")


def test_committed_fixtures_match_seed_42(ds, tmp_path):
    from app.fixtures import FIXTURE_DIR

    fresh = dump(ds, tmp_path)
    committed = {name: (FIXTURE_DIR / name).read_bytes() for name in fresh}
    assert committed == fresh, "data/fixtures is stale: run make seed"


@pytest.mark.parametrize("seed", [1, 2, 3, 99, 2026])
def test_other_seeds_generate(seed):
    assert len(generate(seed).ground_truth) == sum(s.count for s in SCENARIOS)


def test_sizes_match_spec(ds):
    c = ds.manifest["counts"]
    assert 6 <= c["areas"] <= 8
    assert 25 <= c["permits"] <= 35
    assert 22 <= c["workers"] <= 30
    assert {p.type for p in ds.site.permits} == set(PermitType)
    assert c["events"] > 5000 and c["gas_tests"] > 20


def test_scenario_counts(ds):
    counts = Counter(lb.kind for lb in ds.ground_truth)
    assert counts == {s.kind: s.count for s in SCENARIOS}
    labels = Counter(lb.label for lb in ds.ground_truth)
    assert labels[DECOY] >= 7  # enough wrong flags for the study (~15% of 40)
    assert labels[TRUE] + labels[DECOY] >= 43  # 40 study trials + 3 practice


# --- Structure ------------------------------------------------------------------


def test_polygons_nest(ds, permits):
    areas = {a.id: Rect.from_polygon(a.polygon) for a in ds.site.areas}
    for p in permits.values():
        assert areas[p.area_id].contains_rect(rect(p)), p.id
        assert p.valid_from < p.valid_to
        assert site.SHIFT_START <= p.valid_from and p.valid_to <= site.SHIFT_END
    for lp in ds.site.lift_plans:
        assert areas[lp.area_id].contains_rect(Rect.from_polygon(lp.footprint)), lp.id
        assert permits[lp.permit_id].valid_from <= lp.start < lp.end <= permits[lp.permit_id].valid_to


def test_no_hot_work_in_laydown(permits):
    assert all(p.area_id != site.LAYDOWN_AREA for p in permits.values() if p.type == PermitType.hot_work)


def test_nobody_on_two_permits_at_once(permits):
    windows = defaultdict(list)
    for p in permits.values():
        for w in p.crew:
            windows[w].append((p.valid_from, p.valid_to, p.id))
    for w, ws in windows.items():
        ws.sort()
        for (_, end1, p1), (start2, _, p2) in zip(ws, ws[1:]):
            assert end1 <= start2, f"{w} on {p1} and {p2} at once"


def test_conditions_by_type(permits):
    for p in permits.values():
        if p.type == PermitType.hot_work:
            assert p.gas_test_max_age_min == 120 and p.fire_watch_post_work_min == 60
        elif p.type == PermitType.confined_space:
            assert p.gas_test_max_age_min == 60 and p.attendant_required and p.max_entrants in (2, 3)
        else:
            assert not (p.gas_test_required or p.fire_watch_required or p.attendant_required)


def test_events_are_sorted_within_shift_and_well_formed(ds):
    events = ds.site.events
    assert [e.id for e in events] == list(range(1, len(events) + 1))
    assert all(a.time <= b.time for a, b in zip(events, events[1:]))
    for e in events:
        assert site.SHIFT_START <= e.time <= site.SHIFT_END
        assert 0 <= e.confidence <= 1
        assert (e.worker_id is None) != (e.vehicle_id is None)


def test_no_identities_or_biometrics(ds):
    assert all(re.fullmatch(r"W-\d{3}", w.id) for w in ds.site.workers)
    allowed = {"id", "time", "camera_id", "area_id", "worker_id", "vehicle_id", "x", "y", "activity", "ppe",
               "confidence"}
    for e in ds.site.events[:50]:
        assert set(e.model_dump()) == allowed
    assert {frozenset(e.ppe) for e in ds.site.events if e.ppe} == {frozenset(site.PPE_OK)}
    text = json.dumps([p.model_dump(mode="json") for p in ds.site.permits]).lower()
    assert not re.search(r"\b(face|name|photo|image)\b", text)


# --- Baseline behaviour is clean outside scenario windows -----------------------


def scenario_windows(ds, kinds, slack=timedelta(seconds=1)):
    out = defaultdict(list)
    for lb in ds.ground_truth:
        if lb.kind in kinds:
            out[lb.subject_id].append((lb.start - slack, lb.end + slack))
    return out


def crew_windows(permits):
    """(worker, permit, start, end): from valid_from to 40 min after valid_to (room for
    overrun scenarios), cut short when the worker's next permit starts."""
    starts = defaultdict(list)
    for p in permits.values():
        for w in p.crew:
            starts[w].append(p.valid_from)
    for p in permits.values():
        for w in p.crew:
            nxt = min((t for t in starts[w] if t > p.valid_from), default=site.SHIFT_END)
            yield w, p, p.valid_from, min(p.valid_to + timedelta(minutes=40), nxt - timedelta(seconds=1))


def test_crew_stay_in_work_area_outside_area_scenarios(ds, permits, by_subject):
    skip = scenario_windows(ds, {"area_breach", "decoy_area_jitter"}, slack=timedelta(seconds=61))
    for w, p, start, end in crew_windows(permits):
        r = rect(p)
        for e in events_in(by_subject[w], start, end):
            if e.activity == Activity.absent or any(s <= e.time <= t for s, t in skip[w]):
                continue
            assert r.contains(e.x, e.y), (p.id, w, e.time)


def test_hot_activity_only_on_hot_work_outside_activity_scenarios(ds, permits, by_subject):
    skip = scenario_windows(ds, {"activity_mismatch", "decoy_grinding_misread"})
    for w, p, start, end in crew_windows(permits):
        if p.type == PermitType.hot_work:
            continue
        for e in events_in(by_subject[w], start, end):
            if e.activity in HOT_ACTIVITIES:
                assert any(s <= e.time <= t for s, t in skip[w]), (p.id, w, e.time)


def test_gas_tests_fresh_except_scenarios(ds, permits):
    scen = {lb.permit_ids[0] for lb in ds.ground_truth if lb.kind in ("gas_stale", "decoy_gas_late_record")}
    tests = defaultdict(list)
    for g in ds.site.gas_tests:
        tests[g.permit_id].append(g)
    for p in permits.values():
        if not p.gas_test_required:
            assert p.id not in tests
            continue
        ts = sorted(tests[p.id], key=lambda g: g.time)
        assert ts and ts[0].time < p.valid_from + timedelta(minutes=15)
        if p.id in scen:
            continue
        max_age = timedelta(minutes=p.gas_test_max_age_min)
        assert all(b.time - a.time < max_age for a, b in zip(ts, ts[1:])), p.id
        assert all(g.recorded_at - g.time <= timedelta(seconds=60) for g in ts), p.id


# --- Each scenario is what its label says ---------------------------------------


def test_area_breach(ds, permits, by_subject):
    for lb in labels_of(ds, "area_breach"):
        evs = events_in(by_subject[lb.subject_id], lb.start, lb.end - timedelta(seconds=1))
        assert len(evs) >= 3
        assert not any(rect(permits[lb.permit_ids[0]]).expand(2.5).contains(e.x, e.y) for e in evs)


def test_decoy_area_jitter_is_half_a_metre_for_two_seconds(ds, permits, by_subject):
    for lb in labels_of(ds, "decoy_area_jitter"):
        r = rect(permits[lb.permit_ids[0]])
        near = events_in(by_subject[lb.subject_id], lb.start - timedelta(seconds=1), lb.end + timedelta(seconds=1))
        outside = [e for e in near if not r.contains(e.x, e.y)]
        assert [e.time for e in outside] == [lb.start, lb.start + timedelta(seconds=1)]
        assert all(r.expand(0.51).contains(e.x, e.y) for e in outside)
        assert r.contains(near[0].x, near[0].y) and r.contains(near[-1].x, near[-1].y)


def test_gas_stale_and_decoy_late_record(ds, permits, by_subject):
    tests = defaultdict(list)
    for g in ds.site.gas_tests:
        tests[g.permit_id].append(g)
    for lb in labels_of(ds, "gas_stale"):
        p = permits[lb.permit_ids[0]]
        ts = sorted(tests[p.id], key=lambda g: g.time)
        last_before = max(g.time for g in ts if g.time < lb.start)
        assert lb.start - last_before == timedelta(minutes=p.gas_test_max_age_min)
        assert not any(lb.start <= g.time < lb.end for g in ts)
        welder_hot = [e for e in events_in(by_subject[lb.subject_id], lb.start, lb.end) if e.activity in HOT_ACTIVITIES]
        assert welder_hot, "hot work must continue while the test is stale"
    for lb in labels_of(ds, "decoy_gas_late_record"):
        p = permits[lb.permit_ids[0]]
        late = [g for g in tests[p.id] if g.recorded_at == lb.end]
        assert len(late) == 1 and late[0].recorded_at - late[0].time == timedelta(minutes=4)
        prev = max(g.time for g in tests[p.id] if g.time < late[0].time)
        assert late[0].time - prev < timedelta(minutes=p.gas_test_max_age_min)  # taken in time


def test_fire_watch_scenarios(ds, permits, by_subject):
    for lb in labels_of(ds, "fire_watch_absent"):
        evs = events_in(by_subject[lb.subject_id], lb.start, lb.end - timedelta(seconds=1))
        assert evs and all(e.activity == Activity.absent and e.confidence >= 0.85 for e in evs)
        welder = next(w for w in permits[lb.permit_ids[0]].crew if w != lb.subject_id)
        assert any(e.activity in HOT_ACTIVITIES for e in events_in(by_subject[welder], lb.start, lb.end))
    for lb in labels_of(ds, "fire_watch_early_leave"):
        evs = events_in(by_subject[lb.subject_id], lb.start, lb.end)
        assert evs and all(e.activity == Activity.absent for e in evs)
    for lb in labels_of(ds, "decoy_fire_watch_occluded"):
        evs = events_in(by_subject[lb.subject_id], lb.start, lb.end - timedelta(seconds=1))
        assert lb.end - lb.start <= timedelta(seconds=90)
        assert evs and all(e.activity == Activity.absent and e.confidence < 0.6 for e in evs)
        after = events_in(by_subject[lb.subject_id], lb.end, lb.end + timedelta(minutes=2))
        assert after and after[0].activity == Activity.watching


def test_work_after_expiry(ds, permits, by_subject):
    for lb in labels_of(ds, "work_after_expiry"):
        p = permits[lb.permit_ids[0]]
        assert lb.start == p.valid_to and lb.end - lb.start >= timedelta(minutes=15)
        late = [e for w in p.crew for e in by_subject[w] if p.valid_to < e.time <= lb.end]
        assert len(late) >= 10


def test_simops_overlap(ds, permits, by_subject):
    for lb in labels_of(ds, "simops_overlap"):
        hot, lift_permit = (permits[i] for i in lb.permit_ids)
        assert hot.type == PermitType.hot_work and lift_permit.type == PermitType.lifting
        lifts = [lp for lp in ds.site.lift_plans if lp.permit_id == lift_permit.id]
        assert len(lifts) == 1
        assert Rect.from_polygon(lifts[0].footprint).intersection(rect(hot)) is not None
        assert hot.valid_from < lb.start < lb.end < hot.valid_to
        crane = events_in(by_subject[lb.subject_id], lb.start, lb.end)
        assert crane and all(e.activity == Activity.lift_in_progress for e in crane)
    # Routine lifts never touch an active hot-work area.
    simops_lifts = {lb.permit_ids[1] for lb in labels_of(ds, "simops_overlap")}
    for lp in ds.site.lift_plans:
        if lp.permit_id in simops_lifts:
            continue
        for hot in permits.values():
            if hot.type == PermitType.hot_work and hot.valid_from < lp.end and lp.start < hot.valid_to:
                assert Rect.from_polygon(lp.footprint).intersection(rect(hot)) is None


def test_confined_space_scenarios(ds, permits, by_subject):
    over = defaultdict(list)
    for lb in labels_of(ds, "cs_over_count"):
        over[lb.permit_ids[0]].append(lb)
    for p in permits.values():
        if p.type != PermitType.confined_space:
            continue
        for t, n in occupancy(ds, p):
            in_scenario = any(lb.start <= t < lb.end for lb in over[p.id])
            assert n == p.max_entrants + 1 if in_scenario else 0 <= n <= p.max_entrants, (p.id, t, n)
        assert occupancy(ds, p)[-1][1] == 0
    for lb in labels_of(ds, "cs_no_attendant"):
        p = permits[lb.permit_ids[0]]
        evs = events_in(by_subject[lb.subject_id], lb.start, lb.end - timedelta(seconds=1))
        assert evs and all(e.activity == Activity.absent for e in evs)
        inside = [n for t, n in occupancy(ds, p) if t <= lb.start]
        assert inside and inside[-1] > 0


def test_activity_scenarios(ds, permits, by_subject):
    for kind, check in (("activity_mismatch", lambda e: e.confidence >= 0.8),
                        ("decoy_grinding_misread", lambda e: e.confidence < 0.6)):
        for lb in labels_of(ds, kind):
            assert permits[lb.permit_ids[0]].type == PermitType.cold_work
            evs = events_in(by_subject[lb.subject_id], lb.start, lb.end - timedelta(seconds=1))
            assert evs and all(e.activity in HOT_ACTIVITIES and check(e) for e in evs)
