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
