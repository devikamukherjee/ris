"""Parse the site PTW procedure (markdown) into a clause table.

Format: ``## N. Section`` headings group clauses; each clause starts with
``### PTW-N.M Title`` and its text runs until the next heading.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlmodel import Session

from app.config import REPO_ROOT
from app.models import Clause

PROCEDURE_PATH = REPO_ROOT / "data" / "procedure" / "PTW-procedure.md"

_SECTION = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
_CLAUSE = re.compile(r"^###\s+(PTW-\d+\.\d+)\s+(.+?)\s*$")
_HEADING = re.compile(r"^#{1,6}\s")


class ProcedureParseError(ValueError):
    pass


def parse_procedure(markdown: str) -> list[Clause]:
    """Return clauses in document order. Raises on a duplicate ID or an empty clause."""
    clauses: list[Clause] = []
    section = ""
    current: tuple[str, str] | None = None
    body: list[str] = []

    def close() -> None:
        if current is None:
            return
        text = " ".join(" ".join(body).split())
        if not text:
            raise ProcedureParseError(f"{current[0]} has no text")
        clauses.append(Clause(clause_id=current[0], section=section, title=current[1], text=text))

    for line in markdown.splitlines():
        if _HEADING.match(line):
            close()
            current, body = None, []
            if m := _CLAUSE.match(line):
                current = (m.group(1), m.group(2))
            elif m := _SECTION.match(line):
                section = f"{m.group(1)}. {m.group(2)}"
        elif current is not None:
            body.append(line.strip())
    close()

    seen: set[str] = set()
    for c in clauses:
        if c.clause_id in seen:
            raise ProcedureParseError(f"duplicate clause id {c.clause_id}")
        seen.add(c.clause_id)
    return clauses


def load_clauses(path: Path = PROCEDURE_PATH) -> list[Clause]:
    return parse_procedure(path.read_text())


def sync_clauses(session: Session, path: Path = PROCEDURE_PATH) -> int:
    """Upsert the parsed clauses into the clause table. Returns the clause count."""
    clauses = load_clauses(path)
    for clause in clauses:
        session.merge(clause)
    session.commit()
    return len(clauses)
