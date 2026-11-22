"""
Tests for Phase 4 — migration waves, change history, language/legacy in API responses.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from backend.database import Base
from backend.main import app
from backend.models import SourceObject, MigrationState, ChangeLog
from tests.conftest import engine, TestSession


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestSession()
    db.query(MigrationState).delete()
    db.query(ChangeLog).delete()
    db.query(SourceObject).delete()
    db.commit()

    table = SourceObject(
        name="FACT_SALES",
        object_type="table",
        schema_name="DW",
        category="Table",
        reason="Data table",
        loc=0,
        calls="[]",
        called_by=0,
        target_tables="[]",
        dependencies="[]",
        source_hash="",
    )
    loader = SourceObject(
        name="SP_LOAD_SALES",
        object_type="procedure",
        schema_name="DW",
        ddl="INSERT INTO FACT_SALES SELECT * FROM RAW;",
        category="Transformation",
        reason="Pure SQL",
        loc=1,
        calls="[]",
        called_by=1,
        target_tables='["DW.FACT_SALES"]',
        dependencies='["RAW"]',
        source_hash="a1",
        language="sql",
        has_dynamic_sql=False,
        parse_method="ast",
    )
    orchestrator = SourceObject(
        name="SP_MASTER",
        object_type="procedure",
        schema_name="DW",
        ddl="CALL SP_LOAD_SALES(); CALL SP_JS_PROC();",
        category="Orchestrator",
        reason="Calls others",
        loc=2,
        calls='["SP_LOAD_SALES", "SP_JS_PROC"]',
        called_by=0,
        target_tables="[]",
        dependencies="[]",
        source_hash="a2",
        language="sql",
    )
    js_proc = SourceObject(
        name="SP_JS_PROC",
        object_type="procedure",
        schema_name="DW",
        ddl="var stmt = snowflake.createStatement({sqlText: 'INSERT INTO T1 VALUES(1)'});",
        category="Transformation",
        reason="JS body",
        loc=3,
        calls="[]",
        called_by=1,
        target_tables='["T1"]',
        dependencies="[]",
        source_hash="a3",
        language="javascript",
        has_dynamic_sql=True,
        parse_method="js_tokenizer",
    )
    legacy_obj = SourceObject(
        name="SP_JUNK_OLD",
        object_type="procedure",
        schema_name="DW_DEV",
        ddl="SELECT 1;",
        category="Utility",
        reason="Simple utility",
        loc=1,
        calls="[]",
        called_by=0,
        target_tables="[]",
        dependencies="[]",
        source_hash="a4",
        language="sql",
        is_legacy=True,
        legacy_reason="JUNK schema reference",
    )
    refs_legacy = SourceObject(
        name="SP_CHECKER",
        object_type="procedure",
        schema_name="DW",
        ddl="SELECT * FROM SP_JUNK_OLD;",
        category="Utility",
        reason="Utility",
        loc=1,
        calls='["SP_JUNK_OLD"]',
        called_by=0,
        target_tables="[]",
        dependencies="[]",
        source_hash="a5",
        language="sql",
        references_legacy=True,
    )

    db.add_all([table, loader, orchestrator, js_proc, legacy_obj, refs_legacy])
    db.flush()
    for obj in [table, loader, orchestrator, js_proc, legacy_obj, refs_legacy]:
        db.add(MigrationState(source_object_id=obj.id, status="Not Started"))
    db.commit()
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)


class TestMigrationWaves:

    def test_waves_endpoint_returns_200(self):
        r = client.get("/api/waves")
        assert r.status_code == 200
        data = r.json()
        assert "waves" in data
        assert "cycles" in data

    def test_waves_contain_all_objects(self):
        r = client.get("/api/waves")
        data = r.json()
        all_names = set()
        for wave in data["waves"]:
            for obj in wave["objects"]:
                all_names.add(obj["name"])
        for name in data["cycles"]:
            all_names.add(name)
        assert len(all_names) == 6

    def test_wave_zero_has_no_deps(self):
        r = client.get("/api/waves")
        data = r.json()
        wave0_names = {o["name"] for o in data["waves"][0]["objects"]}
        assert "FACT_SALES" in wave0_names

    def test_orchestrator_in_later_wave(self):
        r = client.get("/api/waves")
        data = r.json()
        wave_map = {}
        for wave in data["waves"]:
            for obj in wave["objects"]:
                wave_map[obj["name"]] = wave["wave"]
        assert wave_map.get("SP_MASTER", -1) > wave_map.get("SP_LOAD_SALES", 999)
        assert wave_map.get("SP_MASTER", -1) > wave_map.get("SP_JS_PROC", 999)

    def test_wave_objects_have_expected_fields(self):
        r = client.get("/api/waves")
        data = r.json()
        for wave in data["waves"]:
            for obj in wave["objects"]:
                assert "name" in obj
                assert "object_type" in obj
                assert "category" in obj
                assert "migration_status" in obj

    def test_no_cycles_in_simple_dag(self):
        r = client.get("/api/waves")
        data = r.json()
        assert len(data["cycles"]) == 0

    def test_loader_before_orchestrator(self):
        r = client.get("/api/waves")
        data = r.json()
        wave_map = {}
        for wave in data["waves"]:
            for obj in wave["objects"]:
                wave_map[obj["name"]] = wave["wave"]
        loader_wave = wave_map.get("SP_LOAD_SALES", -1)
        master_wave = wave_map.get("SP_MASTER", -1)
        assert loader_wave < master_wave


class TestMigrationWavesCycles:

    def test_cycle_detection(self):
        db = TestSession()
        db.query(MigrationState).delete()
        db.query(SourceObject).delete()
        db.commit()

        a = SourceObject(
            name="A", object_type="procedure", category="Transformation",
            reason="test", loc=1, calls='["B"]', called_by=1,
            target_tables="[]", dependencies="[]", source_hash="c1",
        )
        b = SourceObject(
            name="B", object_type="procedure", category="Transformation",
            reason="test", loc=1, calls='["A"]', called_by=1,
            target_tables="[]", dependencies="[]", source_hash="c2",
        )
        db.add_all([a, b])
        db.flush()
        for obj in [a, b]:
            db.add(MigrationState(source_object_id=obj.id, status="Not Started"))
        db.commit()
        db.close()

        r = client.get("/api/waves")
        data = r.json()
        assert set(data["cycles"]) == {"A", "B"}


class TestObjectHistory:

    def test_history_empty(self):
        r = client.get("/api/objects/SP_LOAD_SALES/history")
        assert r.status_code == 200
        assert r.json() == []

    def test_history_after_status_change(self):
        client.patch(
            "/api/objects/SP_LOAD_SALES/status",
            json={"status": "In Progress", "user": "rafael"},
        )
        r = client.get("/api/objects/SP_LOAD_SALES/history")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["action"] == "status_change"
        assert data[0]["new_state"] == "In Progress"
        assert data[0]["user"] == "rafael"

    def test_history_multiple_changes(self):
        client.patch("/api/objects/SP_LOAD_SALES/status", json={"status": "In Progress"})
        client.patch("/api/objects/SP_LOAD_SALES/status", json={"status": "Migrated"})
        r = client.get("/api/objects/SP_LOAD_SALES/history")
        data = r.json()
        assert len(data) == 2
        assert data[0]["new_state"] == "Migrated"
        assert data[1]["new_state"] == "In Progress"

    def test_history_404(self):
        r = client.get("/api/objects/NONEXISTENT/history")
        assert r.status_code == 404

    def test_history_has_timestamp(self):
        client.patch("/api/objects/SP_LOAD_SALES/status", json={"status": "Blocked"})
        r = client.get("/api/objects/SP_LOAD_SALES/history")
        data = r.json()
        assert data[0]["timestamp"] is not None


class TestLanguageLegacyInAPI:

    def test_list_includes_language(self):
        r = client.get("/api/objects")
        data = r.json()
        js = next(o for o in data if o["name"] == "SP_JS_PROC")
        assert js["language"] == "javascript"
        assert js["has_dynamic_sql"] is True

    def test_list_includes_legacy_flags(self):
        r = client.get("/api/objects")
        data = r.json()
        legacy = next(o for o in data if o["name"] == "SP_JUNK_OLD")
        assert legacy["is_legacy"] is True
        assert "JUNK" in legacy["legacy_reason"]

    def test_list_includes_references_legacy(self):
        r = client.get("/api/objects")
        data = r.json()
        checker = next(o for o in data if o["name"] == "SP_CHECKER")
        assert checker["references_legacy"] is True
        assert checker["is_legacy"] is False

    def test_detail_includes_parse_method(self):
        r = client.get("/api/objects/SP_JS_PROC")
        data = r.json()
        assert data["parse_method"] == "js_tokenizer"
        assert data["language"] == "javascript"

    def test_sql_proc_language(self):
        r = client.get("/api/objects/SP_LOAD_SALES")
        data = r.json()
        assert data["language"] == "sql"
        assert data["parse_method"] == "ast"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
