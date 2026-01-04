# SP Migration Tracker

A web-based tool for planning and tracking the migration of Snowflake stored procedures to Airflow/dbt.

---

## Current Implementation (Alpha)

This tool analyzes stored procedure DDLs extracted from Snowflake and provides:
- Automated categorization based on SP behavior
- Target table extraction for DAG clustering
- Call graph visualization
- Migration status tracking

---

## Classification System

### Categories

The analyzer classifies each SP into one of 7 categories based on behavior analysis:

| Category | Criteria | Migration Target |
|----------|----------|------------------|
| **Scheduler** | Entry point (calls other SPs, not called by others) + has scheduling keywords (REFRESH, BATCH, DAILY, SYNC) | Airflow DAG root |
| **Orchestrator** | Calls other SPs but has no direct SQL transformations | Airflow DAG |
| **Transformation** | Pure SQL operations (INSERT, UPDATE, DELETE, MERGE) with no SP calls | dbt model |
| **Hybrid** | Mix of SP calls AND SQL operations | Airflow + dbt |
| **Utility** | Called by 2+ other SPs, calls none itself | Shared dbt macro/model |
| **ETL Control** | References CONTROL schema, ETL_*, BATCH_ID, JOB_ID | Airflow metadata tasks |
| **Legacy** | Contains DEV/BAK/JUNK schema references with no clear purpose | Review/deprecate |
| **Unknown** | Could not determine category | Manual review |

### Classification Logic

Priority order (first match wins):
1. ETL Control (control refs + scheduler keywords or is root)
2. Scheduler (is root + scheduler keywords)
3. Utility (called by ≥2, calls 0)
4. Orchestrator (calls >0, SQL ops = 0)
5. Transformation (calls = 0, SQL ops > 0)
6. Hybrid (calls >0 AND SQL ops > 0)
7. Legacy (has legacy refs, nothing else matches)
8. Unknown (fallback)

### Metrics Extracted

For each SP, the analyzer extracts:
- **LOC**: Lines of code (non-empty lines)
- **Calls**: List of SPs this procedure calls (via `CALL` statements)
- **Called By**: Count of SPs that call this procedure
- **Target Tables**: Tables written to (INSERT INTO, MERGE INTO, UPDATE, DELETE FROM, TRUNCATE)
- **Dependencies**: All tables referenced (FROM, JOIN, etc.)

---

## Target Table Extraction

The analyzer identifies tables an SP writes to using these patterns:
- `INSERT INTO <table>`
- `MERGE INTO <table>`
- `UPDATE <table> SET`
- `DELETE FROM <table>`
- `TRUNCATE [TABLE] <table>`

Tables are normalized to `SCHEMA.TABLE` format for clustering.

---

## DAG Planning Features

### Orchestration Chains
SPs that call other SPs are identified as potential Airflow DAG roots. The dashboard shows:
- Source SP name
- Target SPs it calls
- Current migration status

### Table Pipelines (Clustering)
Orphan SPs (no call relationships) are grouped by their **primary target table**:
- All SPs writing to `DWINV.FACT_INV` → One future DAG
- All SPs writing to `DWINV.GEO_DIM` → Another DAG
- etc.

Common tables are normalized:
- `*FACT_INV*` → `FACT_INV`
- `*_DIM` tables → Extracted table name

### Migration Order
Topologically sorted list showing dependencies first. Color coding:
- **Green**: Already migrated
- **Yellow**: Has unmigrated dependencies (blocked)
- **Default**: Ready to migrate

---

## Call Graph Visualization

Interactive D3.js force-directed graph showing SP relationships:

### Node Types
- **Circle nodes**: Connected SPs (have call relationships)
- **Rectangle cluster nodes**: Grouped orphan SPs by target table

### Node Colors by Category
| Category | Color |
|----------|-------|
| Scheduler | Purple (#a78bfa) |
| Orchestrator | Green (#34d399) |
| Transformation | Blue (#60a5fa) |
| Hybrid | Orange (#fb923c) |
| Utility | Cyan (#22d3ee) |
| ETL Control | Indigo (#818cf8) |
| Legacy | Stone (#a8a29e) |
| Error | Red (#f87171) |

### Interactions
- **Scroll**: Zoom in/out
- **Drag background**: Pan
- **Drag node**: Reposition (temporarily fixes position)
- **Click node**: Open code viewer
- **Hover**: Show tooltip with details
- **Toggle "Show Clusters"**: Hide/show orphan cluster nodes

---

## Migration Status Tracking

### Status Values
| Status | Description |
|--------|-------------|
| **Not Started** | Default, not yet worked on |
| **In Progress** | Currently being migrated |
| **Migrated** | Successfully migrated to Airflow/dbt |
| **Blocked** | Cannot proceed (dependency issues, etc.) |
| **Deprecated** | Will not migrate, mark for removal |

### Persistence
- **Current**: Browser localStorage (per-user, survives refresh)
- **Planned**: Server-side database (team-shared)

### Progress Bar
Shows overall migration progress with counts by status.

---

## UI Components

### Tabs
1. **DAG Pipelines** (default): Orchestration chains, table clusters, migration order
2. **Full Inventory**: Searchable/filterable table of all SPs
3. **Call Graph**: Interactive visualization

### Inventory Table Columns
- Name (sortable)
- Category (filterable)
- Target Tables
- LOC (sortable)
- Status (filterable, editable dropdown)
- Action (View Code button)

### Code Viewer Modal
- Syntax highlighted SQL (Highlight.js)
- Metadata: Category, LOC, Target Tables
- Full DDL display

### Filters
- Category dropdown
- Status dropdown
- Text search (matches name, reason, target tables)

---

## Data Flow

```
SP output.json (DDL source)
        ↓
categorize_sps.py (Analysis)
        ↓
sp_categorization.json (Enriched data)
        ↓
generate_dashboard.py (HTML generation)
        ↓
sp_analysis_dashboard.html (Interactive UI)
```

---

## Current Files

| File | Purpose |
|------|---------|
| `categorize_sps.py` | Analyzes DDLs, classifies SPs, extracts metrics |
| `generate_dashboard.py` | Generates interactive HTML dashboard |
| `sp_categorization.json` | Analysis output (enriched SP data) |
| `sp_analysis_dashboard.html` | The interactive dashboard |
| `SP output.json` | Source DDL data from Snowflake |

---

## Known Issues / TODO

- [ ] Some CALL patterns not detected (dynamic SQL, EXECUTE IMMEDIATE)
- [ ] Hybrid category needs refinement
- [ ] Graph performance with large datasets
- [ ] Notes/comments per SP
- [ ] Team authentication
- [ ] Server-side persistence

---

## History

This tool was built iteratively through the following phases:

1. **DDL Export**: Extracted stored procedure DDLs from Snowflake
2. **Manual Fixes**: Corrected DDLs with DEV/BAK/JUNK schema references
3. **Basic Categorization**: Initial Airflow vs dbt classification
4. **Enhanced Categories**: Expanded to 7 categories based on behavior
5. **Dashboard v1**: Summary cards, charts, searchable table
6. **Code Viewer**: Modal with syntax highlighting
7. **Call Graph**: D3.js force-directed graph
8. **Target Table Extraction**: For DAG clustering
9. **DAG Planning View**: Orchestration chains, table pipelines, migration order
10. **Status Tracking**: Persistent migration progress

---

## Technologies

- **Python**: Analysis scripting
- **D3.js**: Call graph visualization
- **Highlight.js**: SQL syntax highlighting
- **Tailwind CSS**: Styling
- **Chart.js**: (Available for future charts)
