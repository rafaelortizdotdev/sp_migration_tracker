"""
API integration tests — exercises endpoints against a test database.
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


# Use in-memory SQLite for tests
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
    # Seed test data
    proc = SourceObject(
        name="LOAD_SALES",
        object_type="procedure",
        schema_name="DWINV",
        ddl="INSERT INTO FACT_SALES SELECT * FROM RAW_SALES;",
        category="Transformation",
        reason="Pure SQL",
        loc=1,
        calls="[]",
        called_by=0,
        target_tables='["DWINV.FACT_SALES"]',
        dependencies='["RAW_SALES"]',
        source_hash="abc123",
    )
    view = SourceObject(
        name="V_ACTIVE_PRODUCTS",
        object_type="view",
        schema_name="DWINV",
        ddl="CREATE VIEW V_ACTIVE_PRODUCTS AS SELECT * FROM PRODUCTS WHERE active=1;",
        category="Simple View",
        reason="Direct select",
        loc=1,
        calls="[]",
        called_by=0,
        target_tables="[]",
        dependencies='["PRODUCTS"]',
        source_hash="def456",
    )
    table = SourceObject(
        name="FACT_SALES",
        object_type="table",
        schema_name="DWINV",
        category="Table",
        reason="Data table",
        loc=0,
        calls="[]",
        called_by=0,
        target_tables="[]",
        dependencies="[]",
        source_hash="",
    )
    db.add_all([proc, view, table])
    db.flush()
    for obj in [proc, view, table]:
        db.add(MigrationState(source_object_id=obj.id, status="Not Started"))
    db.commit()
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)


class TestListObjects:

    def test_list_all(self):
        r = client.get("/api/objects")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 3

    def test_filter_by_type(self):
        r = client.get("/api/objects?object_type=procedure")
        assert r.status_code == 200
        data = r.json()
        assert all(o["object_type"] == "procedure" for o in data)
        assert len(data) == 1

    def test_filter_by_category(self):
        r = client.get("/api/objects?category=Table")
        data = r.json()
        assert len(data) == 1
        assert data[0]["name"] == "FACT_SALES"

    def test_search(self):
        r = client.get("/api/objects?search=ACTIVE")
        data = r.json()
        assert len(data) == 1
        assert data[0]["name"] == "V_ACTIVE_PRODUCTS"


class TestGetObject:

    def test_get_existing(self):
        r = client.get("/api/objects/LOAD_SALES")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "LOAD_SALES"
        assert data["object_type"] == "procedure"
        assert "ddl" in data

    def test_get_missing(self):
        r = client.get("/api/objects/NONEXISTENT")
        assert r.status_code == 404


class TestStatusUpdate:

    def test_update_status(self):
        r = client.patch(
            "/api/objects/LOAD_SALES/status",
            json={"status": "In Progress", "user": "test"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "In Progress"

        # Verify persisted
        r2 = client.get("/api/objects/LOAD_SALES")
        assert r2.json()["migration_status"] == "In Progress"

    def test_update_missing(self):
        r = client.patch(
            "/api/objects/NONEXISTENT/status",
            json={"status": "Migrated"},
        )
        assert r.status_code == 404


class TestStats:

    def test_stats(self):
        r = client.get("/api/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert data["by_type"]["procedure"] == 1
        assert data["by_type"]["view"] == 1
        assert data["by_type"]["table"] == 1


class TestEdges:

    def test_edges(self):
        r = client.get("/api/edges")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)


class TestBackwardCompat:

    def test_sps_endpoint(self):
        r = client.get("/api/sps")
        assert r.status_code == 200
        data = r.json()
        assert all(o["object_type"] == "procedure" for o in data)


class TestHealth:

    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
