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
import_data.py          -> Parses JSON, runs sqlglot analyzer, loads SQLite
evaluate_corpus.py      -> Standalone analysis: runs analyzer, prints summary stats
backend/
  main.py               -> FastAPI app factory, Jinja2 templates
  database.py            -> SQLAlchemy engine + session
  models.py              -> SourceObject, MigrationState, VirtualNode, DAGGroup, ChangeLog
  services/
    analyzer.py          -> SQLAnalyzer: sqlglot AST parsing + regex fallback
  routes/
    sps.py               -> /api/objects, /api/stats, /api/edges, /api/clusters, /api/waves
    virtual_nodes.py     -> /api/nodes/{name}/split, unsplit, preview
  frontend/
    templates/
      dashboard.html     -> Single-page dashboard (D3, Highlight.js)
    static/vendor/       -> Vendored D3.js v7.9.0, Highlight.js v11.9.0 (no CDN)
tests/
  conftest.py            -> In-memory SQLite with StaticPool
  test_analyzer.py       -> 87 unit tests (classification, extraction, language, legacy)
  test_api.py            -> 28 API integration tests
  test_phase2.py         -> 13 virtual splitting tests
  test_phase4.py         -> 18 migration waves, history, language/legacy API tests
data/
  lab_dataset.json       -> Fictitious dataset: 31 procedures, 11 views, 31 tables (73 objects)
```

## SQL Analyzer

Uses [sqlglot](https://github.com/tobymao/sqlglot) for AST-based parsing of Snowflake DDL with regex fallback for constructs sqlglot cannot handle (dynamic SQL, `$$` procedure bodies, single-quote `AS '...'` bodies).

### Language Detection

Procedures are classified by language:

| Language | Detection |
|----------|-----------|
| `javascript` | `LANGUAGE JAVASCRIPT` clause in DDL |
| `snowflake_scripting` | DECLARE/RESULTSET/LET/EXECUTE IMMEDIATE markers |
| `sql` | Default when no other language markers found |

JavaScript procedures use a dedicated tokenizer that extracts SQL from `snowflake.createStatement({sqlText: ...})` patterns.

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
| Unknown | No classifiable operations | Review |

**Views** (6 categories): Simple View, Join View, Aggregation View, Nested View, Materialized, Unknown

**Tables**: Table

### Legacy Flag

Legacy is tracked as a boolean flag (`is_legacy`) with a reason string, not as an overriding category. This preserves the real structural category while marking objects in DEV/BAK/JUNK schemas. `references_legacy` flags objects that depend on legacy-named objects.

Legacy patterns are configurable via `SQLAnalyzer.LEGACY_PATTERNS`.

### Extraction

- **Read tables**: FROM/JOIN via AST `exp.Table` traversal
- **Write tables**: INSERT/UPDATE/DELETE/MERGE/TRUNCATE targets (MERGE counted once)
- **Calls**: CALL statement resolution against known inventory
- **View sources**: CREATE VIEW query source tables
- **Dynamic SQL**: EXECUTE IMMEDIATE detection

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/objects` | List objects (filters: `object_type`, `category`, `status`, `search`) |
| GET | `/api/objects/{name}` | Object detail with DDL, language, legacy flags |
| PATCH | `/api/objects/{name}/status` | Update migration status |
| GET | `/api/objects/{name}/history` | Change log entries for an object |
| GET | `/api/stats` | Counts by type, category, status |
| GET | `/api/edges` | Call graph + dependency edges |
| GET | `/api/clusters` | Orphan objects grouped by target table |
| GET | `/api/waves` | Migration order waves (topological sort with cycle detection) |
| GET | `/api/sps` | Backward-compatible procedure list |
| POST | `/api/nodes/{name}/split` | Split hybrid into ORCH + LOGIC virtual nodes |
| POST | `/api/nodes/{name}/unsplit` | Reverse a split |
| GET | `/api/nodes/{name}/split-preview` | Preview split result |
| GET | `/api/virtual-nodes` | List virtual nodes |

## Dashboard

Four-tab single-page application:

1. **Full Inventory** — searchable/filterable table with language badges, legacy/dynamic SQL flags, inline status editing, and code viewer modal with split controls and change history
2. **Migration Order** — topological wave visualization showing which objects can migrate in parallel, with cycle detection
3. **DAG Pipelines** — orchestration chains and orphan table clusters
4. **Call Graph** — D3.js force-directed graph with zoom/pan/drag, category color-coding, and dependency toggles

## Corpus Evaluation

Run the analyzer on any dataset without importing to a database:

```bash
python evaluate_corpus.py                      # uses data/lab_dataset.json
python evaluate_corpus.py path/to/data.json    # custom dataset
```

Prints category distribution, language distribution, parse methods, legacy counts, call graph edges, and root orchestrators.

## Testing

```bash
pip install pytest httpx2
python -m pytest tests/ -v
```

146 tests across 4 test files covering analyzer classification, API endpoints, virtual splitting, migration waves, change history, and language/legacy fields.

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
- **Frontend**: Jinja2, D3.js v7.9.0, Highlight.js v11.9.0 (vendored, no CDN)
- **Testing**: pytest, FastAPI TestClient
