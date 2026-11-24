from datetime import datetime

import pytest
from sqlalchemy import func
from sqlmodel import Session, select

from app.db import make_engine
from app.fixtures import FIXTURE_DIR, export_fixtures, load_ground_truth, load_manifest, load_site
from app.models import Clause, DetectionEvent, Flag, GroundTruthLabel, Permit, Severity
from app.sim.generator import generate
from app.sim.seed import SeedRefused, write_db


@pytest.fixture(scope="module")
def ds():
    return generate(42)


def count(session, model):
    return session.exec(select(func.count()).select_from(model)).one()


def test_fixture_round_trip(ds, tmp_path):
    export_fixtures(ds, tmp_path)
    site = load_site(tmp_path)
    assert [p.model_dump() for p in site.permits] == [p.model_dump() for p in ds.site.permits]
    assert [e.model_dump() for e in site.events[:100]] == [e.model_dump() for e in ds.site.events[:100]]
    assert [g.model_dump() for g in load_ground_truth(tmp_path)] == [g.model_dump() for g in ds.ground_truth]
    assert load_manifest(tmp_path) == ds.manifest


def test_ground_truth_kept_out_of_site_files():
    for path in FIXTURE_DIR.glob("*.json"):
        if path.name != "ground_truth.json":
            text = path.read_text()
            assert "true_violation" not in text and "decoy" not in text, path.name


def test_write_db_counts_and_clauses(ds, tmp_path):
    engine = make_engine(tmp_path / "pg.db")
    write_db(ds, engine)
    with Session(engine) as s:
        assert count(s, Permit) == len(ds.site.permits)
        assert count(s, DetectionEvent) == len(ds.site.events)
        assert count(s, GroundTruthLabel) == len(ds.ground_truth)
        assert count(s, Clause) >= 25
        assert s.get(Permit, ds.site.permits[0].id).crew == ds.site.permits[0].crew


def test_reseed_refuses_to_wipe_flags_unless_forced(ds, tmp_path):
    engine = make_engine(tmp_path / "pg.db")
    write_db(ds, engine)
    with Session(engine) as s:
        s.add(Flag(id="F-0001", rule_id="R-AREA", permit_ids=["X"], severity=Severity.warning, evidence=[],
                   clause_ids=["PTW-3.2"], created_at=datetime(2026, 3, 10, 7)))
        s.commit()
    with pytest.raises(SeedRefused):
        write_db(ds, engine)
    write_db(ds, engine, force=True)
    with Session(engine) as s:
        assert count(s, Flag) == 0
