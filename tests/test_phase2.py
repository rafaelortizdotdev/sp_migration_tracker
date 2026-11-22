"""
Tests for Phase 2 — virtual splitting (split/unsplit/preview).
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from backend.database import Base
from backend.main import app
from backend.models import SourceObject, MigrationState
from tests.conftest import engine, TestSession


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestSession()
    db.query(MigrationState).delete()
    db.query(SourceObject).delete()
    db.commit()

    hybrid = SourceObject(
        name="REFRESH_INVENTORY",
        object_type="procedure",
        schema_name="DW",
        ddl="TRUNCATE TABLE DW.FACT_INV;\nINSERT INTO DW.FACT_INV SELECT * FROM STAGING.INV;\nCALL UPDATE_AGGREGATES();",
        category="Hybrid",
        reason="Mix of SQL and calls",
        loc=3,
        calls='["UPDATE_AGGREGATES"]',
        called_by=1,
        target_tables='["DW.FACT_INV"]',
        dependencies='["STAGING.INV"]',
        source_hash="aaa",
    )
    callee = SourceObject(
        name="UPDATE_AGGREGATES",
        object_type="procedure",
        schema_name="DW",
        ddl="INSERT INTO DW.AGG_DAILY SELECT * FROM DW.FACT_SALES GROUP BY day;",
        category="Transformation",
        reason="Pure SQL",
        loc=1,
        calls="[]",
        called_by=1,
        target_tables='["DW.AGG_DAILY"]',
        dependencies='["DW.FACT_SALES"]',
        source_hash="bbb",
    )
    db.add_all([hybrid, callee])
    db.flush()
    for obj in [hybrid, callee]:
        db.add(MigrationState(source_object_id=obj.id, status="Not Started"))
    db.commit()
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)


class TestSplitPreview:

    def test_preview_hybrid(self):
        r = client.get("/api/nodes/REFRESH_INVENTORY/split-preview")
        assert r.status_code == 200
        data = r.json()
        assert data["can_split"] is True
        assert "orch_preview" in data
        assert "logic_preview" in data

    def test_preview_non_hybrid(self):
        r = client.get("/api/nodes/UPDATE_AGGREGATES/split-preview")
        assert r.status_code == 200
        data = r.json()
        assert data["can_split"] is False


class TestSplitUnsplit:

    def test_split_and_unsplit(self):
        r = client.post(
            "/api/nodes/REFRESH_INVENTORY/split",
            json={"user": "test"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["original_hidden"] is True
        assert len(data["created_nodes"]) == 2

        vn = client.get("/api/virtual-nodes")
        assert vn.status_code == 200
        nodes = vn.json()
        assert len(nodes) == 2

        r2 = client.post(
            "/api/nodes/REFRESH_INVENTORY/unsplit",
            json={"user": "test"},
        )
        assert r2.status_code == 200

        vn2 = client.get("/api/virtual-nodes")
        assert len(vn2.json()) == 0

    def test_split_creates_correct_names(self):
        r = client.post(
            "/api/nodes/REFRESH_INVENTORY/split",
            json={"user": "test"},
        )
        data = r.json()
        names = [n["name"] for n in data["created_nodes"]]
        assert "REFRESH_INVENTORY_ORCH" in names
        assert "REFRESH_INVENTORY_LOGIC" in names

    def test_split_orch_inherits_calls(self):
        r = client.post(
            "/api/nodes/REFRESH_INVENTORY/split",
            json={"user": "test"},
        )
        data = r.json()
        orch = next(n for n in data["created_nodes"] if n["type"] == "orchestrator")
        assert "UPDATE_AGGREGATES" in orch["inherited_calls"]
        assert orch["inherited_tables"] == []

    def test_split_logic_inherits_tables(self):
        r = client.post(
            "/api/nodes/REFRESH_INVENTORY/split",
            json={"user": "test"},
        )
        data = r.json()
        logic = next(n for n in data["created_nodes"] if n["type"] == "transformation")
        assert "DW.FACT_INV" in logic["inherited_tables"]
        assert logic["inherited_calls"] == []

    def test_double_split_rejected(self):
        client.post("/api/nodes/REFRESH_INVENTORY/split", json={"user": "test"})
        r2 = client.post("/api/nodes/REFRESH_INVENTORY/split", json={"user": "test"})
        assert r2.status_code == 400
        assert "Already split" in r2.json()["detail"]

    def test_unsplit_non_split_rejected(self):
        r = client.post(
            "/api/nodes/UPDATE_AGGREGATES/unsplit",
            json={"user": "test"},
        )
        assert r.status_code == 400

    def test_split_hides_from_default_listing(self):
        client.post("/api/nodes/REFRESH_INVENTORY/split", json={"user": "test"})
        r = client.get("/api/objects")
        names = [o["name"] for o in r.json()]
        assert "REFRESH_INVENTORY" not in names
        assert "REFRESH_INVENTORY_ORCH" in names or any("_ORCH" in n for n in names)

    def test_split_visible_with_include_hidden(self):
        client.post("/api/nodes/REFRESH_INVENTORY/split", json={"user": "test"})
        r = client.get("/api/objects?include_hidden=true")
        names = [o["name"] for o in r.json()]
        assert "REFRESH_INVENTORY" in names

    def test_split_non_hybrid_rejected(self):
        r = client.post(
            "/api/nodes/UPDATE_AGGREGATES/split",
            json={"user": "test"},
        )
        assert r.status_code == 400
        assert "Hybrid" in r.json()["detail"]

    def test_split_nonexistent(self):
        r = client.post(
            "/api/nodes/NONEXISTENT/split",
            json={"user": "test"},
        )
        assert r.status_code == 404

    def test_unsplit_nonexistent(self):
        r = client.post(
            "/api/nodes/NONEXISTENT/unsplit",
            json={"user": "test"},
        )
        assert r.status_code == 404

    def test_preview_nonexistent(self):
        r = client.get("/api/nodes/NONEXISTENT/split-preview")
        assert r.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
