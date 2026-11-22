"""
Tests for the sqlglot-based SQL analyzer.
Covers procedure, view, and table classification + dependency extraction.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.analyzer import SQLAnalyzer


# ---------------------------------------------------------------------------
# Edge cases: empty / malformed input
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_ddl_procedure(self):
        result = SQLAnalyzer.analyze_procedure("EMPTY_SP", "", [])
        assert result["category"] == "Unknown"
        assert result["calls"] == []
        assert result["target_tables"] == []
        assert result["dependencies"] == []

    def test_empty_ddl_view(self):
        result = SQLAnalyzer.analyze_view("EMPTY_VIEW", "")
        assert result["category"] == "Unknown"
        assert result["dependencies"] == []

    def test_unparseable_sql_falls_back_to_regex(self):
        ddl = "GOBBLEDYGOOK INSERT INTO DW.TBL1 SELECT * FROM DW.SRC1; END JIBBERISH"
        writes = SQLAnalyzer.extract_write_tables(ddl)
        assert "DW.TBL1" in [w.upper() for w in writes]

    def test_no_dollar_body_uses_full_ddl(self):
        ddl = "INSERT INTO DW.TARGET SELECT * FROM DW.SOURCE;"
        result = SQLAnalyzer.analyze_procedure("NO_BODY", ddl, [])
        assert "DW.TARGET" in result["target_tables"]

    def test_normalize_skips_dollar_variables(self):
        ddl = "AS $$ SELECT * FROM $1; $$"
        tables = SQLAnalyzer.extract_read_tables(ddl)
        assert not any(t.startswith("$") for t in tables)

    def test_analyze_procedure_returns_all_keys(self):
        ddl = "AS $$ SELECT 1; $$"
        result = SQLAnalyzer.analyze_procedure("TEST", ddl, [])
        expected_keys = {"name", "object_type", "schema_name", "ddl", "category",
                         "reason", "loc", "calls", "called_by", "target_tables",
                         "dependencies", "source_hash"}
        assert set(result.keys()) == expected_keys

    def test_analyze_view_returns_all_keys(self):
        ddl = "CREATE VIEW V1 AS SELECT 1;"
        result = SQLAnalyzer.analyze_view("V1", ddl)
        expected_keys = {"name", "object_type", "schema_name", "ddl", "category",
                         "reason", "loc", "calls", "called_by", "target_tables",
                         "dependencies", "source_hash"}
        assert set(result.keys()) == expected_keys

    def test_analyze_table_returns_all_keys(self):
        result = SQLAnalyzer.analyze_table("T1", "CREATE TABLE T1 (id INT);")
        expected_keys = {"name", "object_type", "schema_name", "ddl", "category",
                         "reason", "loc", "calls", "called_by", "target_tables",
                         "dependencies", "source_hash"}
        assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Procedure analysis
# ---------------------------------------------------------------------------

class TestProcedureClassification:

    def test_transformation_pure_sql(self):
        ddl = """
        CREATE OR REPLACE PROCEDURE LOAD_FACT_SALES()
        RETURNS STRING
        LANGUAGE SQL
        AS $$
        INSERT INTO DW.FACT_SALES (product_id, qty, sale_date)
        SELECT p.id, s.quantity, s.sale_date
        FROM STAGING.RAW_SALES s
        JOIN DW.DIM_PRODUCT p ON s.sku = p.sku;
        $$;
        """
        result = SQLAnalyzer.analyze_procedure("LOAD_FACT_SALES", ddl, [])
        assert result["category"] == "Transformation"
        assert "dbt candidate" in result["reason"]

    def test_orchestrator_calls_only(self):
        ddl = """
        CREATE OR REPLACE PROCEDURE RUN_ALL()
        RETURNS STRING
        LANGUAGE SQL
        AS $$
        CALL LOAD_DIM_PRODUCT();
        CALL LOAD_FACT_SALES();
        CALL LOAD_FACT_INVENTORY();
        $$;
        """
        known = ["LOAD_DIM_PRODUCT", "LOAD_FACT_SALES", "LOAD_FACT_INVENTORY"]
        result = SQLAnalyzer.analyze_procedure("RUN_ALL", ddl, known, called_by_count=0)
        assert result["category"] in ("Orchestrator", "Scheduler")

    def test_hybrid_calls_and_sql(self):
        ddl = """
        CREATE OR REPLACE PROCEDURE REFRESH_INVENTORY()
        RETURNS STRING
        LANGUAGE SQL
        AS $$
        TRUNCATE TABLE DW.FACT_INV;
        INSERT INTO DW.FACT_INV SELECT * FROM STAGING.INV_SNAPSHOT;
        CALL UPDATE_AGGREGATES();
        $$;
        """
        known = ["UPDATE_AGGREGATES"]
        result = SQLAnalyzer.analyze_procedure("REFRESH_INVENTORY", ddl, known, called_by_count=1)
        assert result["category"] == "Hybrid"

    def test_utility_called_by_many(self):
        ddl = """
        CREATE OR REPLACE PROCEDURE LOG_ETL_STATUS(msg STRING)
        RETURNS STRING
        LANGUAGE SQL
        AS $$
        INSERT INTO CONTROL.ETL_LOG (message, ts) VALUES (:msg, CURRENT_TIMESTAMP());
        $$;
        """
        result = SQLAnalyzer.analyze_procedure("LOG_ETL_STATUS", ddl, [], called_by_count=5)
        assert result["category"] == "Utility"

    def test_etl_control(self):
        ddl = """
        CREATE OR REPLACE PROCEDURE BATCH_SCHEDULER()
        RETURNS STRING
        LANGUAGE SQL
        AS $$
        UPDATE CONTROL.ETL_BATCH SET STATUS = 'RUNNING' WHERE BATCH_ID = 100;
        CALL LOAD_ALL_DIMS();
        $$;
        """
        known = ["LOAD_ALL_DIMS"]
        result = SQLAnalyzer.analyze_procedure("BATCH_SCHEDULER", ddl, known, called_by_count=0)
        assert result["category"] == "ETL Control"

    def test_legacy_detection(self):
        ddl = """
        CREATE OR REPLACE PROCEDURE OLD_JUNK_LOADER()
        RETURNS STRING
        LANGUAGE SQL
        AS $$
        SELECT * FROM JUNK.TEMP_TABLE;
        $$;
        """
        result = SQLAnalyzer.analyze_procedure("OLD_JUNK_LOADER", ddl, [])
        assert result["category"] == "Legacy"

    def test_legacy_trumps_transformation(self):
        """Legacy with SQL ops still classifies as Legacy because legacy check comes first."""
        ddl = """
        CREATE OR REPLACE PROCEDURE FIX_BAK_TABLE()
        RETURNS STRING LANGUAGE SQL
        AS $$
        UPDATE DW_BAK.OLD_TABLE SET status = 'FIXED';
        $$;
        """
        result = SQLAnalyzer.analyze_procedure("FIX_BAK_TABLE", ddl, [])
        assert result["category"] == "Legacy"

    def test_legacy_no_false_match_device(self):
        """DEVICE should not trigger Legacy — _DEV requires a non-word boundary after it."""
        ddl = """
        CREATE OR REPLACE PROCEDURE LOAD_DEVICE_DATA()
        RETURNS STRING LANGUAGE SQL
        AS $$
        INSERT INTO DW.DIM_DEVICE SELECT * FROM STAGING.RAW_DEVICES;
        $$;
        """
        result = SQLAnalyzer.analyze_procedure("LOAD_DEVICE_DATA", ddl, [])
        assert result["category"] != "Legacy"

    def test_legacy_no_false_match_bakery(self):
        """_BAKERY should not trigger Legacy — _BAK requires a non-word boundary after it."""
        ddl = """
        CREATE OR REPLACE PROCEDURE PROCESS_BAKERY()
        RETURNS STRING LANGUAGE SQL
        AS $$
        INSERT INTO DW.DIM_BAKERY SELECT * FROM STAGING.RAW_BAKERY;
        $$;
        """
        result = SQLAnalyzer.analyze_procedure("PROCESS_BAKERY", ddl, [])
        assert result["category"] != "Legacy"

    def test_unknown_no_ops_no_calls(self):
        ddl = """
        CREATE OR REPLACE PROCEDURE DO_NOTHING()
        RETURNS STRING LANGUAGE SQL
        AS $$
        SELECT 1;
        $$;
        """
        result = SQLAnalyzer.analyze_procedure("DO_NOTHING", ddl, [])
        assert result["category"] == "Unknown"

    def test_scheduler_root_with_keyword(self):
        ddl = """
        CREATE OR REPLACE PROCEDURE DAILY_REFRESH()
        RETURNS STRING LANGUAGE SQL
        AS $$
        CALL LOAD_A();
        CALL LOAD_B();
        $$;
        """
        known = ["LOAD_A", "LOAD_B"]
        result = SQLAnalyzer.analyze_procedure("DAILY_REFRESH", ddl, known, called_by_count=0)
        assert result["category"] in ("Scheduler", "ETL Control")

    def test_hybrid_mostly_sql(self):
        """High SQL-to-call ratio → Hybrid with 'Mostly transformation' reason."""
        ddl = """
        CREATE OR REPLACE PROCEDURE LOAD_AND_CALL()
        RETURNS STRING LANGUAGE SQL
        AS $$
        INSERT INTO T1 SELECT * FROM S1;
        INSERT INTO T2 SELECT * FROM S2;
        INSERT INTO T3 SELECT * FROM S3;
        INSERT INTO T4 SELECT * FROM S4;
        CALL NOTIFY_DONE();
        $$;
        """
        known = ["NOTIFY_DONE"]
        result = SQLAnalyzer.analyze_procedure("LOAD_AND_CALL", ddl, known, called_by_count=1)
        assert result["category"] == "Hybrid"
        assert "transformation" in result["reason"].lower()

    def test_hybrid_mostly_calls(self):
        """Low SQL-to-call ratio → Hybrid with 'Mostly orchestration' reason."""
        ddl = """
        CREATE OR REPLACE PROCEDURE ORCHESTRATE_PLUS()
        RETURNS STRING LANGUAGE SQL
        AS $$
        CALL STEP_A();
        CALL STEP_B();
        CALL STEP_C();
        INSERT INTO DW.LOG SELECT 'done';
        $$;
        """
        known = ["STEP_A", "STEP_B", "STEP_C"]
        result = SQLAnalyzer.analyze_procedure("ORCHESTRATE_PLUS", ddl, known, called_by_count=1)
        assert result["category"] == "Hybrid"
        assert "orchestration" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Dependency extraction
# ---------------------------------------------------------------------------

class TestDependencyExtraction:

    def test_extract_read_tables(self):
        ddl = """
        AS $$
        SELECT a.id, b.name
        FROM SCHEMA1.TABLE_A a
        JOIN SCHEMA1.TABLE_B b ON a.id = b.a_id
        WHERE a.active = 1;
        $$
        """
        tables = SQLAnalyzer.extract_read_tables(ddl)
        names = [t.upper() for t in tables]
        assert "SCHEMA1.TABLE_A" in names
        assert "SCHEMA1.TABLE_B" in names

    def test_extract_write_tables(self):
        ddl = """
        AS $$
        INSERT INTO DW.FACT_SALES (col1) SELECT col1 FROM STAGING.RAW;
        UPDATE DW.DIM_DATE SET is_current = 0;
        DELETE FROM STAGING.TEMP_TABLE WHERE processed = 1;
        $$
        """
        targets = SQLAnalyzer.extract_write_tables(ddl)
        names = [t.upper() for t in targets]
        assert "DW.FACT_SALES" in names
        assert "DW.DIM_DATE" in names
        assert "STAGING.TEMP_TABLE" in names

    def test_read_tables_excludes_write_targets(self):
        ddl = """
        AS $$
        INSERT INTO DW.FACT_SALES SELECT * FROM STAGING.RAW_SALES;
        $$
        """
        reads = SQLAnalyzer.extract_read_tables(ddl)
        writes = SQLAnalyzer.extract_write_tables(ddl)
        read_names = [t.upper() for t in reads]
        write_names = [t.upper() for t in writes]
        assert "DW.FACT_SALES" in write_names
        assert "DW.FACT_SALES" not in read_names
        assert "STAGING.RAW_SALES" in read_names

    def test_cte_names_excluded_from_reads(self):
        ddl = """
        AS $$
        WITH staging_cte AS (
            SELECT id, amount FROM STAGING.RAW_SALES
        )
        INSERT INTO DW.FACT_SALES SELECT * FROM staging_cte;
        $$
        """
        reads = SQLAnalyzer.extract_read_tables(ddl)
        read_names = [t.upper() for t in reads]
        assert "STAGING_CTE" not in read_names
        assert "STAGING.RAW_SALES" in read_names

    def test_comment_stripping(self):
        ddl = """
        AS $$
        -- CALL OLD_PROC();
        /* INSERT INTO JUNK.TABLE1 SELECT 1; */
        INSERT INTO DW.FACT_SALES SELECT * FROM STAGING.RAW;
        $$
        """
        writes = SQLAnalyzer.extract_write_tables(ddl)
        write_names = [t.upper() for t in writes]
        assert "DW.FACT_SALES" in write_names
        assert "JUNK.TABLE1" not in write_names

    def test_deep_nested_writes(self):
        ddl = """
        CREATE OR REPLACE PROCEDURE NESTED_PROC()
        RETURNS STRING LANGUAGE SQL
        AS $$
        BEGIN
            IF (TRUE) THEN
                INSERT INTO DW.INNER_TABLE SELECT * FROM STAGING.SRC;
            END IF;
        END;
        $$;
        """
        writes = SQLAnalyzer.extract_write_tables(ddl)
        write_names = [t.upper() for t in writes]
        assert "DW.INNER_TABLE" in write_names

    def test_extract_calls_exact_match(self):
        ddl = """
        AS $$
        CALL LOAD_DATA();
        $$
        """
        known = ["LOAD_DATA", "LOAD_DATA_FULL", "PRELOAD_DATA"]
        calls = SQLAnalyzer.extract_calls(ddl, known)
        assert "LOAD_DATA" in calls
        assert "LOAD_DATA_FULL" not in calls
        assert "PRELOAD_DATA" not in calls

    def test_extract_calls_resolves_known(self):
        ddl = """
        AS $$
        CALL SCHEMA1.LOAD_DIM_PRODUCT();
        CALL LOAD_FACT_SALES();
        CALL UNKNOWN_PROC();
        $$
        """
        known = ["LOAD_DIM_PRODUCT", "LOAD_FACT_SALES"]
        calls = SQLAnalyzer.extract_calls(ddl, known)
        assert "LOAD_DIM_PRODUCT" in calls
        assert "LOAD_FACT_SALES" in calls
        assert "UNKNOWN_PROC" not in calls

    def test_merge_extracts_write_target(self):
        ddl = """
        AS $$
        MERGE INTO DW.DIM_PRODUCT tgt
        USING STAGING.RAW_PRODUCTS src ON tgt.id = src.id
        WHEN MATCHED THEN UPDATE SET tgt.name = src.name
        WHEN NOT MATCHED THEN INSERT (id, name) VALUES (src.id, src.name);
        $$
        """
        writes = SQLAnalyzer.extract_write_tables(ddl)
        reads = SQLAnalyzer.extract_read_tables(ddl)
        write_names = [w.upper() for w in writes]
        read_names = [r.upper() for r in reads]
        assert "DW.DIM_PRODUCT" in write_names
        assert "DW.DIM_PRODUCT" not in read_names

    def test_truncate_detected_as_write(self):
        ddl = """
        AS $$
        TRUNCATE TABLE DW.STAGING_TMP;
        INSERT INTO DW.STAGING_TMP SELECT * FROM SRC.DATA;
        $$
        """
        writes = SQLAnalyzer.extract_write_tables(ddl)
        write_names = [w.upper() for w in writes]
        assert "DW.STAGING_TMP" in write_names

    def test_multiple_ctes_all_excluded(self):
        ddl = """
        AS $$
        WITH
            cte_a AS (SELECT id FROM DW.TABLE_A),
            cte_b AS (SELECT id FROM DW.TABLE_B)
        SELECT * FROM cte_a JOIN cte_b ON cte_a.id = cte_b.id;
        $$
        """
        reads = SQLAnalyzer.extract_read_tables(ddl)
        read_names = [r.upper() for r in reads]
        assert "CTE_A" not in read_names
        assert "CTE_B" not in read_names
        assert "DW.TABLE_A" in read_names
        assert "DW.TABLE_B" in read_names

    def test_js_comment_stripped(self):
        ddl = """
        AS $$
        // INSERT INTO JUNK.COMMENTED SELECT 1;
        INSERT INTO DW.REAL_TABLE SELECT * FROM DW.SOURCE;
        $$
        """
        writes = SQLAnalyzer.extract_write_tables(ddl)
        write_names = [w.upper() for w in writes]
        assert "DW.REAL_TABLE" in write_names
        assert "JUNK.COMMENTED" not in write_names

    def test_block_comment_spanning_lines(self):
        ddl = """
        AS $$
        /*
        INSERT INTO PHANTOM.TABLE1
        SELECT * FROM PHANTOM.TABLE2;
        */
        INSERT INTO DW.ACTUAL SELECT * FROM DW.SRC;
        $$
        """
        writes = SQLAnalyzer.extract_write_tables(ddl)
        reads = SQLAnalyzer.extract_read_tables(ddl)
        assert "PHANTOM.TABLE1" not in [w.upper() for w in writes]
        assert "DW.ACTUAL" in [w.upper() for w in writes]
        assert "DW.SRC" in [r.upper() for r in reads]

    def test_calls_in_comments_ignored(self):
        ddl = """
        AS $$
        -- CALL DEAD_PROC();
        /* CALL ALSO_DEAD(); */
        CALL ALIVE_PROC();
        $$
        """
        known = ["DEAD_PROC", "ALSO_DEAD", "ALIVE_PROC"]
        calls = SQLAnalyzer.extract_calls(ddl, known)
        assert "ALIVE_PROC" in calls
        assert "DEAD_PROC" not in calls
        assert "ALSO_DEAD" not in calls

    def test_extract_calls_empty_known(self):
        ddl = "AS $$ CALL SOME_PROC(); $$"
        calls = SQLAnalyzer.extract_calls(ddl, [])
        assert calls == []

    def test_sql_ops_count_precise(self):
        body = "INSERT INTO T1 SELECT 1; INSERT INTO T2 SELECT 2; UPDATE T3 SET x=1; DELETE FROM T4 WHERE 1=1;"
        count = SQLAnalyzer._count_sql_ops(body)
        assert count == 4

    def test_sql_ops_no_false_positives(self):
        body = "SELECT * FROM UPDATES WHERE inserted = 1 AND deleted = 0 AND merged IS NULL;"
        count = SQLAnalyzer._count_sql_ops(body)
        assert count == 0

    def test_sql_ops_no_false_match_last_update(self):
        body = "SELECT LAST_UPDATE FROM T WHERE is_truncated = 1;"
        count = SQLAnalyzer._count_sql_ops(body)
        assert count == 0

    def test_truncate_only_detected_as_write(self):
        ddl = "AS $$ TRUNCATE TABLE DW.STAGING_TMP; $$"
        writes = SQLAnalyzer.extract_write_tables(ddl)
        write_names = [w.upper() for w in writes]
        assert "DW.STAGING_TMP" in write_names


# ---------------------------------------------------------------------------
# View analysis
# ---------------------------------------------------------------------------

class TestViewClassification:

    def test_simple_view(self):
        ddl = "CREATE VIEW V_ACTIVE_PRODUCTS AS SELECT id, name FROM PRODUCTS WHERE active = 1;"
        result = SQLAnalyzer.analyze_view("V_ACTIVE_PRODUCTS", ddl)
        assert result["category"] == "Simple View"
        assert result["object_type"] == "view"

    def test_join_view(self):
        ddl = """
        CREATE VIEW V_ORDER_DETAILS AS
        SELECT o.id, c.name, p.title
        FROM ORDERS o
        JOIN CUSTOMERS c ON o.customer_id = c.id
        JOIN PRODUCTS p ON o.product_id = p.id;
        """
        result = SQLAnalyzer.analyze_view("V_ORDER_DETAILS", ddl)
        assert result["category"] == "Join View"
        deps = [d.upper() for d in result["dependencies"]]
        assert "ORDERS" in deps
        assert "CUSTOMERS" in deps
        assert "PRODUCTS" in deps

    def test_aggregation_view(self):
        ddl = """
        CREATE VIEW V_DAILY_SALES AS
        SELECT sale_date, SUM(amount) AS total, COUNT(*) AS cnt
        FROM SALES
        GROUP BY sale_date;
        """
        result = SQLAnalyzer.analyze_view("V_DAILY_SALES", ddl)
        assert result["category"] == "Aggregation View"

    def test_nested_view_with_cte(self):
        ddl = """
        CREATE VIEW V_TOP_CUSTOMERS AS
        WITH ranked AS (
            SELECT customer_id, SUM(amount) AS total
            FROM ORDERS
            GROUP BY customer_id
        )
        SELECT r.customer_id, c.name, r.total
        FROM ranked r
        JOIN CUSTOMERS c ON r.customer_id = c.id
        WHERE r.total > 1000;
        """
        result = SQLAnalyzer.analyze_view("V_TOP_CUSTOMERS", ddl)
        assert result["category"] == "Nested View"

    def test_materialized_view(self):
        ddl = "CREATE MATERIALIZED VIEW MV_DAILY AS SELECT day, SUM(amt) FROM SALES GROUP BY day;"
        result = SQLAnalyzer.analyze_view("MV_DAILY", ddl)
        assert result["category"] == "Materialized"

    def test_legacy_view(self):
        ddl = "CREATE VIEW V_OLD AS SELECT * FROM JUNK.TEMP_DATA;"
        result = SQLAnalyzer.analyze_view("V_OLD", ddl)
        assert result["category"] == "Legacy"

    def test_view_sources_extraction(self):
        ddl = """
        CREATE VIEW V_REPORT AS
        SELECT a.id, b.val
        FROM SCHEMA1.TABLE_A a
        JOIN SCHEMA2.TABLE_B b ON a.id = b.a_id;
        """
        sources = SQLAnalyzer.extract_view_sources(ddl)
        names = [s.upper() for s in sources]
        assert "SCHEMA1.TABLE_A" in names
        assert "SCHEMA2.TABLE_B" in names

    def test_view_with_subquery(self):
        ddl = """
        CREATE VIEW V_SUB AS
        SELECT * FROM (SELECT id, name FROM PRODUCTS WHERE active = 1) sub
        WHERE sub.id > 100;
        """
        result = SQLAnalyzer.analyze_view("V_SUB", ddl)
        assert result["category"] == "Nested View"

    def test_view_cte_names_excluded_from_deps(self):
        ddl = """
        CREATE VIEW V_CTE_TEST AS
        WITH helper AS (SELECT id FROM DW.SOURCE_TABLE)
        SELECT * FROM helper;
        """
        result = SQLAnalyzer.analyze_view("V_CTE_TEST", ddl)
        dep_names = [d.upper() for d in result["dependencies"]]
        assert "HELPER" not in dep_names
        assert "DW.SOURCE_TABLE" in dep_names

    def test_view_join_plus_agg_is_aggregation(self):
        ddl = """
        CREATE VIEW V_AGG_JOIN AS
        SELECT s.store_id, SUM(f.amount)
        FROM SALES f JOIN STORES s ON f.store_id = s.id
        GROUP BY s.store_id;
        """
        result = SQLAnalyzer.analyze_view("V_AGG_JOIN", ddl)
        assert result["category"] == "Aggregation View"

    def test_legacy_view_trumps_structure(self):
        ddl = """
        CREATE VIEW V_DEV_JOIN AS
        SELECT a.id, b.name
        FROM DW_DEV.TABLE_A a JOIN DW_DEV.TABLE_B b ON a.id = b.id;
        """
        result = SQLAnalyzer.analyze_view("V_DEV_JOIN", ddl)
        assert result["category"] == "Legacy"


# ---------------------------------------------------------------------------
# Table analysis
# ---------------------------------------------------------------------------

class TestTableClassification:

    def test_regular_table(self):
        result = SQLAnalyzer.analyze_table("FACT_SALES", "CREATE TABLE FACT_SALES (id INT, amount DECIMAL);")
        assert result["category"] == "Table"
        assert result["object_type"] == "table"

    def test_legacy_table(self):
        result = SQLAnalyzer.analyze_table("OLD_BAK_DATA", "CREATE TABLE OLD_BAK_DATA (id INT);")
        assert result["category"] == "Legacy"

    def test_table_no_ddl(self):
        result = SQLAnalyzer.analyze_table("DIM_DATE")
        assert result["category"] == "Table"
        assert result["loc"] == 0

    def test_legacy_table_by_name_only(self):
        result = SQLAnalyzer.analyze_table("JUNK_SCRATCH_DATA")
        assert result["category"] == "Legacy"

    def test_table_schema_propagated(self):
        result = SQLAnalyzer.analyze_table("MY_TABLE", "", schema_name="STAGING")
        assert result["schema_name"] == "STAGING"
        assert result["object_type"] == "table"


# ---------------------------------------------------------------------------
# Hash and LOC
# ---------------------------------------------------------------------------

class TestUtilities:

    def test_loc_counts_non_empty(self):
        ddl = "line1\n\nline3\n   \nline5"
        assert SQLAnalyzer._count_loc(ddl) == 3

    def test_hash_deterministic(self):
        ddl = "SELECT 1;"
        h1 = SQLAnalyzer._hash(ddl)
        h2 = SQLAnalyzer._hash(ddl)
        assert h1 == h2
        assert len(h1) == 64

    def test_hash_changes_on_edit(self):
        assert SQLAnalyzer._hash("SELECT 1;") != SQLAnalyzer._hash("SELECT 2;")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
