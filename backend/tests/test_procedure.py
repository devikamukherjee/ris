import re

import pytest

from app.procedure import ProcedureParseError, load_clauses, parse_procedure
from app.sim import site

# Clauses the rule engine (M2) will cite.
RULE_CLAUSES = {"PTW-3.2", "PTW-3.3", "PTW-4.2", "PTW-4.3", "PTW-5.1", "PTW-5.2", "PTW-5.3",
                "PTW-6.1", "PTW-6.2", "PTW-7.1", "PTW-8.1"}


@pytest.fixture(scope="module")
def clauses():
    return {c.clause_id: c for c in load_clauses()}


def test_rule_clauses_present_with_text(clauses):
    assert RULE_CLAUSES <= clauses.keys()
    for cid in RULE_CLAUSES:
        assert len(clauses[cid].text.split()) >= 15, cid


def test_every_clause_has_section_and_title(clauses):
    assert len(clauses) >= 25
    for c in clauses.values():
        assert re.fullmatch(r"PTW-\d+\.\d+", c.clause_id)
        assert re.fullmatch(r"\d+\. .+", c.section)
        assert c.title


def test_procedure_numbers_match_generator(clauses):
    assert f"{site.HOT_WORK_GAS_MAX_AGE_MIN} minutes" in clauses["PTW-4.2"].text
    assert f"{site.CS_GAS_MAX_AGE_MIN} minutes" in clauses["PTW-4.3"].text
    assert f"{site.FIRE_WATCH_POST_WORK_MIN} minutes" in clauses["PTW-5.3"].text


def test_parser_groups_text_until_next_heading():
    md = "## 1. Intro\n\n### PTW-1.1 First\nLine one\nline two.\n\n### PTW-1.2 Second\nText.\n## 2. Next\n"
    out = parse_procedure(md)
    assert [(c.clause_id, c.title, c.text, c.section) for c in out] == [
        ("PTW-1.1", "First", "Line one line two.", "1. Intro"),
        ("PTW-1.2", "Second", "Text.", "1. Intro"),
    ]


def test_parser_rejects_duplicates_and_empty_clauses():
    with pytest.raises(ProcedureParseError, match="duplicate"):
        parse_procedure("## 1. A\n### PTW-1.1 X\ntext\n### PTW-1.1 Y\ntext\n")
    with pytest.raises(ProcedureParseError, match="no text"):
        parse_procedure("## 1. A\n### PTW-1.1 X\n\n### PTW-1.2 Y\ntext\n")
