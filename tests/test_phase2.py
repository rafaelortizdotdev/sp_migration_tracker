"""
Tests for Phase 2 — virtual splitting (split/unsplit/preview).
"""
import pytest
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base, get_db
from backend.main import app
from backend.models import SourceObject, MigrationState


engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestSession = sessionmaker(bind=engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestSession()

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
        assert len(data["created_nodes"]) >= 2

        vn = client.get("/api/virtual-nodes")
        assert vn.status_code == 200
        nodes = vn.json()
        assert len(nodes) >= 2

        r2 = client.post(
            "/api/nodes/REFRESH_INVENTORY/unsplit",
            json={"user": "test"},
        )
        assert r2.status_code == 200

        vn2 = client.get("/api/virtual-nodes")
        assert len(vn2.json()) == 0

    def test_split_nonexistent(self):
        r = client.post(
            "/api/nodes/NONEXISTENT/split",
            json={"user": "test"},
        )
        assert r.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
