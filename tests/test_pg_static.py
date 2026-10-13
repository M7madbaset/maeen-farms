"""Static checks on the SQL migrations. No database required (needs `pglast`)."""
import re
import pytest
from maeen.pgmigrate import MIGRATIONS_DIR, SEEDS_DIR, migration_files

pglast = pytest.importorskip("pglast")


def test_migration_numbering_and_names():
    files = migration_files()
    assert len(files) >= 7 and files[0].name.startswith("001_")


@pytest.mark.parametrize("path", sorted(MIGRATIONS_DIR.glob("*.sql")) + [SEEDS_DIR / "dev_seed.sql"],
                         ids=lambda p: p.name)
def test_sql_parses(path):
    stmts = pglast.parse_sql(path.read_text(encoding="utf-8"))
    assert len(stmts) > 0


def test_migrations_are_non_destructive_and_free_of_dev_data():
    for f in MIGRATIONS_DIR.glob("*.sql"):
        txt = re.sub(r"--.*", "", f.read_text(encoding="utf-8")).upper()
        assert not re.search(r"\bDROP\s+(TABLE|COLUMN|SCHEMA|DATABASE)\b", txt), f.name
        assert not re.search(r"\bDELETE\s+FROM\b|\bTRUNCATE\s+TABLE\b", txt), f.name
        assert "PASSWORD '" not in txt and "SECRET" not in txt, f.name


def test_only_reference_data_is_in_migrations():
    txt = "".join(f.read_text(encoding="utf-8") for f in MIGRATIONS_DIR.glob("*.sql")).upper()
    assert "INSERT INTO FARM" not in txt and "INSERT INTO FARM_BLOCK" not in txt
