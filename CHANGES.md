# Changelog

## Phase 5 — Release hygiene

- Added `evaluate_corpus.py`: standalone analyzer that prints summary stats without importing to a database
- Updated `README.md` with accurate architecture, API endpoints, test counts, and feature descriptions
- Reviewed `.gitignore`: proprietary files excluded, vendor assets included
- Added `CHANGES.md` (this file)

## Phase 4 — Missing UI scope

### New API endpoints
- `GET /api/waves` — topological sort using Kahn's algorithm, returns migration waves and detected cycles
- `GET /api/objects/{name}/history` — change log entries for a named object

### Dashboard enhancements
- **Migration Order tab** — wave cards showing which objects can migrate in parallel, with cycle detection warnings
- **Language badge** — SQL, JS, SF Script badges in inventory table and detail modal
- **Legacy flags** — `Legacy`, `Refs Legacy`, `Dynamic SQL` flag badges in inventory table and detail modal
- **Split controls** — split/unsplit buttons in the detail modal for Hybrid procedures
- **Change history panel** — shows ChangeLog entries at the bottom of the detail modal
- **Syntax highlighting** — JavaScript procedures highlighted as JS instead of SQL

### Tests added
- `test_phase4.py`: 18 tests covering wave computation, cycle detection, history endpoint, and language/legacy API fields

## Phase 3 — Realistic fictitious dataset

- Expanded `lab_dataset.json` from 43 to 73 objects:
  - 31 procedures (20 SQL, 8 JavaScript, 3 Snowflake Scripting)
  - 11 views (including legacy-referencing and nested CTE views)
  - 31 tables (including 7 new tables for JS procedure targets)
- Added single-quote body procedure (`SP_COMPACT_UPSERT`)
- Added cross-language orchestrator (`SP_MASTER_PIPELINE` calling JS + scripting procs)
- Demonstrated `references_legacy` via `V_SALES_DRIFT_CHECK` referencing `FACT_SALES_BAK`

## Phase 2 — Legacy as a flag, not a category

### Analyzer changes
- Removed "Legacy" from `CATEGORIES` and `VIEW_CATEGORIES` lists
- Added `LEGACY_PATTERNS` class attribute: configurable list of `(regex, label)` tuples
- Added `check_legacy(name, schema_name)` — checks object's own name and schema, not DDL body
- `classify_procedure` and `classify_view` no longer return "Legacy" — they always return a real structural category
- All `analyze_*` methods return `is_legacy`, `legacy_reason`, `references_legacy` fields

### Model changes
- Added `is_legacy` (Boolean), `legacy_reason` (Text), `references_legacy` (Boolean) columns to `SourceObject`

### Import changes
- Second pass after import computes `references_legacy` by checking if any deps/calls/targets reference legacy-named objects

### Tests
- Updated 8 existing legacy tests to assert real categories + `is_legacy` flag
- Added `TestLegacyFlag` class with 7 tests for `check_legacy`, configurable patterns, false positive guards

## Phase 1 — JavaScript procedure support

### Analyzer changes
- Added language detection: `sql`, `javascript`, `snowflake_scripting`
- Added JS string tokenizer: extracts `sqlText:` values from `snowflake.createStatement()` patterns
- Added single-quote delimited body extraction (`AS '...'` with `''` escape handling)
- Added `EXECUTE IMMEDIATE` dynamic SQL detection
- Added MERGE double-counting fix: strip MERGE blocks before counting INSERT/UPDATE/DELETE
- `_strip_comments` preserves `//` comments (needed by JS tokenizer)

### Model changes
- Added `language` (String), `has_dynamic_sql` (Boolean), `parse_method` (String) columns to `SourceObject`

### API changes
- `ObjectResponse` includes `language`, `has_dynamic_sql`, `parse_method` fields

### Tests
- Added `TestLanguageDetection` (8 tests), `TestJavaScriptProcedures` (6 tests), `TestSingleQuoteBody` (3 tests), `TestExecuteImmediate` (2 tests), `TestMergeCount` (2 tests)
