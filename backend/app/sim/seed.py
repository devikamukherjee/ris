"""``make seed``: generate the shift, write JSON fixtures and (re)build the SQLite database.

    python -m app.sim.seed --seed 42 [--db PATH] [--fixtures DIR] [--force]

Reseeding replaces all site data. It refuses to run if the database already holds flags,
decisions or audit records, unless ``--force`` is given, so it can't silently wipe a
session's record.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import fields
from pathlib import Path

from sqlalchemy import Engine, func
from sqlmodel import Session, SQLModel, select

from app.config import get_settings
from app.db import init_db, make_engine
from app.fixtures import FIXTURE_DIR, Dataset, SiteData, export_fixtures
from app.models import AuditRecord, Decision, Flag
from app.procedure import sync_clauses
from app.sim.generator import generate

PROTECTED_TABLES = (Flag, Decision, AuditRecord)


class SeedRefused(RuntimeError):
    pass


def protected_row_counts(engine: Engine) -> dict[str, int]:
    init_db(engine)
    with Session(engine) as s:
        return {m.__tablename__: s.exec(select(func.count()).select_from(m)).one() for m in PROTECTED_TABLES}


def write_db(dataset: Dataset, engine: Engine, force: bool = False) -> None:
    counts = protected_row_counts(engine)
    if any(counts.values()) and not force:
        raise SeedRefused(f"database already holds {counts}; rerun with --force to replace it")
    SQLModel.metadata.drop_all(engine)
    init_db(engine)
    rows = [r for f in fields(SiteData) for r in getattr(dataset.site, f.name)] + dataset.ground_truth
    with Session(engine) as s:
        # Copies, so the session never takes ownership of (and expires) the dataset's objects.
        s.add_all(type(r).model_validate(r.model_dump()) for r in rows)
        s.commit()
        sync_clauses(s)


def summary(dataset: Dataset) -> str:
    c = dataset.manifest["counts"]
    types = Counter(p.type.value for p in dataset.site.permits)
    labels = Counter(lb.label.value for lb in dataset.ground_truth)
    return (
        f"seed {dataset.manifest['seed']}: {c['areas']} areas, {c['workers']} workers, "
        f"{c['permits']} permits ({', '.join(f'{k} {v}' for k, v in sorted(types.items()))}), "
        f"{c['gas_tests']} gas tests, {c['lift_plans']} lift plans, {c['events']} detection events\n"
        f"scenarios: {labels['true_violation']} true violations, {labels['no_violation']} decoys"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--db", type=Path, default=None, help="SQLite file (default: PG_DB_PATH)")
    parser.add_argument("--fixtures", type=Path, default=FIXTURE_DIR)
    parser.add_argument("--force", action="store_true", help="replace a database that holds flags or decisions")
    args = parser.parse_args(argv)

    db_path = args.db or get_settings().db_path
    dataset = generate(args.seed)
    try:
        write_db(dataset, make_engine(db_path), force=args.force)
    except SeedRefused as exc:
        print(f"Refusing to reseed: {exc}", file=sys.stderr)
        return 1
    export_fixtures(dataset, args.fixtures)
    print(summary(dataset))
    print(f"database: {db_path}\nfixtures: {args.fixtures}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
