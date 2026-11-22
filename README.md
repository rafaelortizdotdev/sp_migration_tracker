# Snowflake Migration Tracker

Web application for planning and tracking the migration of Snowflake objects (stored procedures, views, tables) to Airflow/dbt.

Built with **FastAPI**, **SQLAlchemy**, **sqlglot** for AST-based SQL parsing, and **D3.js** for interactive call graph visualization.

## Quick Start

```bash
pip install -r requirements.txt
python import_data.py                          # loads data/lab_dataset.json
uvicorn backend.main:app --reload              # http://127.0.0.1:8000
```

## Architecture

```
import_data.py          → Parses JSON, runs sqlglot analyzer, loads SQLite
backend/
  main.py               → FastAPI app factory, Jinja2 templates
  database.py            → SQLAlchemy engine + session
  models.py              → SourceObject, MigrationState, VirtualNode, DAGGroup, ChangeLog
  services/
    analyzer.py          → SQLAnalyzer: sqlglot AST parsing + regex fallback
  routes/
    sps.py               → /api/objects, /api/stats, /api/edges, /api/clusters
    virtual_nodes.py     → /api/nodes/{name}/split, unsplit, preview
  frontend/
    templates/
      dashboard.html     → Single-page dashboard (D3, Highlight.js)
tests/
  test_analyzer.py       → 21 unit tests (classification, extraction, utilities)
  test_api.py            → 12 API integration tests (requires httpx2)
  test_phase2.py         → Virtual splitting tests
data/
  lab_dataset.json       → Fictitious dataset: 18 procedures, 8 views, 17 tables
```

## SQL Analyzer

Uses [sqlglot](https://github.com/tobymao/sqlglot) for AST-based parsing of Snowflake DDL with regex fallback for constructs sqlglot cannot handle (dynamic SQL, `$$` procedure bodies).

### Object Classification

**Procedures** (7 categories, priority order):

| Category | Criteria | Migration Target |
|----------|----------|------------------|
| ETL Control | BATCH_ID/JOB_ID refs + scheduler keywords | Airflow metadata |
| Scheduler | Root entry point + scheduling keywords | Airflow DAG root |
| Utility | Called by >= 2 others, calls none | Shared dbt macro |
| Orchestrator | Calls procedures, no direct SQL | Airflow DAG |
| Transformation | Pure SQL, no calls | dbt model |
| Hybrid | Both calls and SQL | Airflow + dbt |
| Legacy | DEV/BAK/JUNK references | Review/deprecate |

**Views** (6 categories): Simple, Join, Aggregation, Nested (CTE/subquery), Materialized, Legacy

**Tables**: Table or Legacy

### Extraction

- **Read tables**: FROM/JOIN via AST `exp.Table` traversal
- **Write tables**: INSERT/UPDATE/DELETE/MERGE/TRUNCATE targets
- **Calls**: CALL statement resolution against known inventory
- **View sources**: CREATE VIEW query source tables

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/objects` | List objects (filters: `object_type`, `category`, `status`, `search`) |
| GET | `/api/objects/{name}` | Object detail with DDL |
| PATCH | `/api/objects/{name}/status` | Update migration status |
| GET | `/api/stats` | Counts by type, category, status |
| GET | `/api/edges` | Call graph + dependency edges |
| GET | `/api/clusters` | Orphan objects grouped by target table |
| GET | `/api/sps` | Backward-compatible procedure list |
| POST | `/api/nodes/{name}/split` | Split hybrid into ORCH + LOGIC virtual nodes |
| POST | `/api/nodes/{name}/unsplit` | Reverse a split |
| GET | `/api/nodes/{name}/split-preview` | Preview split result |
| GET | `/api/virtual-nodes` | List virtual nodes |

## Dashboard

Three-tab single-page application:

1. **Full Inventory** — searchable/filterable table with inline status editing and code viewer modal
2. **DAG Pipelines** — orchestration chains and orphan table clusters
3. **Call Graph** — D3.js force-directed graph with zoom/pan/drag, category color-coding, and dependency toggles

## Testing

```bash
pip install pytest httpx2
python -m pytest tests/ -v
```

## Data Import

Accepts two JSON formats:
- **Multi-object**: `[{name, object_type, ddl, schema_name}, ...]` (recommended)
- **Legacy SP**: `[{sp_name, ddl, status}, ...]` (backward compatible)

```bash
python import_data.py path/to/data.json
```

## Tech Stack

- **Backend**: FastAPI, SQLAlchemy, SQLite (PostgreSQL-ready), Pydantic
- **Analysis**: sqlglot (Snowflake dialect AST parsing)
- **Frontend**: Jinja2, D3.js v7.9.0, Highlight.js v11.9.0
- **Testing**: pytest, FastAPI TestClient
