"""
SQL Object Analyzer — classifies and extracts metadata from Snowflake DDL
using sqlglot for AST-based parsing (procedures, views, tables).
"""
import hashlib
import re
from typing import Dict, List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp


DIALECT = "snowflake"


class SQLAnalyzer:
    """Analyzes Snowflake DDL using sqlglot AST parsing with regex fallback."""

    # -------------------------------------------------------------------
    # Parsing helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _safe_parse(ddl: str) -> List[exp.Expression]:
        """Parse DDL tolerantly — returns whatever sqlglot can handle."""
        try:
            return sqlglot.parse(ddl, dialect=DIALECT, error_level=sqlglot.ErrorLevel.IGNORE)
        except Exception:
            return []

    @staticmethod
    def _extract_body_sql(ddl: str) -> str:
        """For stored procedures, extract the SQL body from the AS $$ ... $$ block."""
        match = re.search(
            r"AS\s*\$\$(.+?)\$\$",
            ddl,
            re.DOTALL | re.IGNORECASE,
        )
        return match.group(1).strip() if match else ddl

    @staticmethod
    def _strip_comments(sql: str) -> str:
        """Remove SQL single-line and block comments."""
        sql = re.sub(r'--[^\n]*', '', sql)
        sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
        # Snowflake JS-style comments inside $$ bodies
        sql = re.sub(r'//[^\n]*', '', sql)
        return sql

    @staticmethod
    def _collect_cte_names(stmts: List[exp.Expression]) -> Set[str]:
        """Collect all CTE alias names so they can be excluded from table lists."""
        names: Set[str] = set()
        for stmt in stmts:
            if stmt is None:
                continue
            for cte in stmt.find_all(exp.CTE):
                if cte.alias:
                    names.add(cte.alias.upper())
        return names

    # -------------------------------------------------------------------
    # Table / view reference extraction (AST-based)
    # -------------------------------------------------------------------

    @classmethod
    def extract_read_tables(cls, ddl: str) -> List[str]:
        """Extract tables/views read (FROM/JOIN), excluding write targets and CTEs."""
        body = cls._strip_comments(cls._extract_body_sql(ddl))
        stmts = cls._safe_parse(body)

        all_tables: Set[str] = set()
        cte_names = cls._collect_cte_names(stmts)

        for stmt in stmts:
            if stmt is None:
                continue
            for table in stmt.find_all(exp.Table):
                name = cls._normalize_table(table)
                if name and name.split(".")[-1] not in cte_names:
                    all_tables.add(name)

        if not all_tables:
            all_tables = cls._regex_extract_dependencies(cls._strip_comments(ddl))

        write_targets = set(cls.extract_write_tables(ddl))
        read_only = all_tables - write_targets
        return sorted(read_only)

    @classmethod
    def extract_write_tables(cls, ddl: str) -> List[str]:
        """Extract tables written to (INSERT, UPDATE, DELETE, MERGE, TRUNCATE).
        Traverses the full AST tree so nested statements inside BEGIN/IF are found."""
        tables: Set[str] = set()
        body = cls._strip_comments(cls._extract_body_sql(ddl))
        stmts = cls._safe_parse(body)

        write_types = (exp.Insert, exp.Merge, exp.Update, exp.Delete, exp.Drop)

        for stmt in stmts:
            if stmt is None:
                continue
            for node in stmt.find_all(*write_types):
                tgt = node.find(exp.Table)
                if tgt:
                    name = cls._normalize_table(tgt)
                    if name:
                        tables.add(name)

        if not tables:
            tables = cls._regex_extract_targets(cls._strip_comments(ddl))
        return sorted(tables)

    @staticmethod
    def _normalize_table(table: exp.Table) -> Optional[str]:
        """Return SCHEMA.TABLE or just TABLE, uppercased."""
        parts = []
        if table.args.get("db"):
            parts.append(table.args["db"].name.upper())
        parts.append(table.name.upper())
        result = ".".join(parts)
        skip = {"DUAL", "VALUES", "RESULT_SCAN", "TABLE", "INFORMATION_SCHEMA", "SET", ""}
        if result in skip or result.startswith("$"):
            return None
        return result

    # -------------------------------------------------------------------
    # CALL extraction (procedures only)
    # -------------------------------------------------------------------

    @classmethod
    def extract_calls(cls, ddl: str, known_names: List[str]) -> List[str]:
        """Extract CALL references, resolved against the known inventory.
        Uses exact match only — no substring matching."""
        raw: Set[str] = set()
        body = cls._strip_comments(cls._extract_body_sql(ddl))
        for stmt in cls._safe_parse(body):
            if stmt is None:
                continue
            for cmd in stmt.find_all(exp.Command):
                if cmd.this and cmd.this.upper() == "CALL":
                    if cmd.expression:
                        raw.add(cmd.expression.name.upper() if hasattr(cmd.expression, 'name') else str(cmd.expression).split('(')[0].strip().upper())
        # Regex fallback on comment-stripped body
        for m in re.finditer(r'\bCALL\s+([A-Za-z0-9_."]+)', body, re.IGNORECASE):
            name = m.group(1).split(".")[-1].strip('"').upper()
            raw.add(name)

        known_upper = {n.upper(): n for n in known_names}
        resolved = []
        for name in raw:
            if name in known_upper:
                resolved.append(known_upper[name])
        return sorted(set(resolved))

    # -------------------------------------------------------------------
    # View-specific: extract source query dependencies
    # -------------------------------------------------------------------

    @classmethod
    def extract_view_sources(cls, ddl: str) -> List[str]:
        """For CREATE VIEW, extract all tables/views the view selects from."""
        tables: Set[str] = set()
        stmts = cls._safe_parse(ddl)
        cte_names = cls._collect_cte_names(stmts)

        for stmt in stmts:
            if stmt is None:
                continue
            if isinstance(stmt, exp.Create):
                query = stmt.expression
                if query:
                    for table in query.find_all(exp.Table):
                        name = cls._normalize_table(table)
                        if name and name.split(".")[-1] not in cte_names:
                            tables.add(name)
        if not tables:
            tables = cls._regex_extract_dependencies(ddl)
        return sorted(tables)

    # -------------------------------------------------------------------
    # Classification
    # -------------------------------------------------------------------

    CATEGORIES = [
        "Scheduler", "Orchestrator", "Transformation", "Hybrid",
        "Utility", "ETL Control", "Legacy", "Unknown",
    ]

    VIEW_CATEGORIES = [
        "Simple View", "Aggregation View", "Join View", "Nested View",
        "Materialized", "Legacy", "Unknown",
    ]

    @classmethod
    def classify_procedure(
        cls,
        name: str,
        ddl: str,
        call_count: int,
        called_by_count: int,
    ) -> Tuple[str, str]:
        """Classify a stored procedure into one of 7 categories."""
        body = cls._strip_comments(cls._extract_body_sql(ddl))
        sql_ops = cls._count_sql_ops(body)

        has_control = bool(re.search(r'\bCONTROL\b|\bETL_|\bBATCH_ID|\bJOB_ID', body, re.IGNORECASE))
        has_legacy = bool(re.search(r'(?:_DEV|_BAK|JUNK)', ddl, re.IGNORECASE))
        has_scheduler_kw = bool(re.search(r'REFRESH|BATCH|DAILY|SYNC|LOAD_ALL|SCHEDULER', name, re.IGNORECASE))

        is_root = call_count > 0 and called_by_count == 0
        is_utility = called_by_count >= 2 and call_count == 0

        if has_legacy and sql_ops == 0 and call_count == 0:
            return "Legacy", "Contains DEV/BAK/JUNK schema references"
        if has_control and (has_scheduler_kw or is_root):
            return "ETL Control", "Manages job state, batch IDs, or scheduling"
        if is_root and has_scheduler_kw:
            return "Scheduler", f"Entry point (calls {call_count}, not called by others)"
        if is_utility:
            return "Utility", f"Helper (called by {called_by_count} others)"
        if call_count > 0 and sql_ops == 0:
            return "Orchestrator", f"Calls {call_count} procedures with no direct SQL"
        if call_count == 0 and sql_ops > 0:
            return "Transformation", f"Pure SQL ({sql_ops} operations, dbt candidate)"
        if call_count > 0 and sql_ops > 0:
            ratio = sql_ops / (call_count + sql_ops)
            if ratio > 0.7:
                return "Hybrid", f"Mostly transformation ({sql_ops} SQL) but calls {call_count}"
            return "Hybrid", f"Mostly orchestration ({call_count} calls) with {sql_ops} SQL"
        if has_legacy:
            return "Legacy", "Contains DEV/BAK/JUNK schema references"
        return "Unknown", "Could not determine category"

    @classmethod
    def classify_view(cls, name: str, ddl: str, dep_count: int) -> Tuple[str, str]:
        """Classify a view based on its query structure."""
        is_mat = bool(re.search(r'\bMATERIALIZED\b', ddl, re.IGNORECASE))
        has_legacy = bool(re.search(r'(?:_DEV|_BAK|JUNK)', ddl, re.IGNORECASE))

        if has_legacy:
            return "Legacy", "References DEV/BAK/JUNK schemas"
        if is_mat:
            return "Materialized", "Materialized view"

        stmts = cls._safe_parse(ddl)
        create = next((s for s in stmts if s and isinstance(s, exp.Create)), None)
        if not create or not create.expression:
            return "Unknown", "Could not parse view definition"

        query = create.expression
        has_join = bool(query.find(exp.Join))
        has_agg = bool(query.find(exp.AggFunc))
        has_subquery = bool(query.find(exp.Subquery))
        has_cte = bool(query.find(exp.CTE))

        if has_subquery or has_cte:
            return "Nested View", f"Contains subqueries or CTEs ({dep_count} dependencies)"
        if has_join and has_agg:
            return "Aggregation View", f"Joins + aggregations ({dep_count} dependencies)"
        if has_join:
            return "Join View", f"Multi-table join ({dep_count} dependencies)"
        if has_agg:
            return "Aggregation View", f"Aggregation over {dep_count} source(s)"
        return "Simple View", f"Direct select ({dep_count} dependencies)"

    @classmethod
    def classify_table(cls, name: str, ddl: str) -> Tuple[str, str]:
        """Classify a table as a dependency node — always 'Table'."""
        has_legacy = bool(re.search(r'(?:_DEV|_BAK|JUNK)', ddl or name or "", re.IGNORECASE))
        if has_legacy:
            return "Legacy", "Table in DEV/BAK/JUNK schema"
        return "Table", "Data table (dependency node)"

    # -------------------------------------------------------------------
    # Full analysis entry points
    # -------------------------------------------------------------------

    @classmethod
    def analyze_procedure(
        cls,
        name: str,
        ddl: str,
        all_names: List[str],
        called_by_count: int = 0,
        schema_name: str = "",
    ) -> Dict:
        calls = cls.extract_calls(ddl, all_names)
        category, reason = cls.classify_procedure(name, ddl, len(calls), called_by_count)
        return {
            "name": name,
            "object_type": "procedure",
            "schema_name": schema_name,
            "ddl": ddl,
            "category": category,
            "reason": reason,
            "loc": cls._count_loc(ddl),
            "calls": calls,
            "called_by": called_by_count,
            "target_tables": cls.extract_write_tables(ddl),
            "dependencies": cls.extract_read_tables(ddl),
            "source_hash": cls._hash(ddl),
        }

    @classmethod
    def analyze_view(
        cls,
        name: str,
        ddl: str,
        schema_name: str = "",
    ) -> Dict:
        deps = cls.extract_view_sources(ddl)
        category, reason = cls.classify_view(name, ddl, len(deps))
        return {
            "name": name,
            "object_type": "view",
            "schema_name": schema_name,
            "ddl": ddl,
            "category": category,
            "reason": reason,
            "loc": cls._count_loc(ddl),
            "calls": [],
            "called_by": 0,
            "target_tables": [],
            "dependencies": deps,
            "source_hash": cls._hash(ddl),
        }

    @classmethod
    def analyze_table(
        cls,
        name: str,
        ddl: str = "",
        schema_name: str = "",
    ) -> Dict:
        category, reason = cls.classify_table(name, ddl)
        return {
            "name": name,
            "object_type": "table",
            "schema_name": schema_name,
            "ddl": ddl,
            "category": category,
            "reason": reason,
            "loc": cls._count_loc(ddl) if ddl else 0,
            "calls": [],
            "called_by": 0,
            "target_tables": [],
            "dependencies": [],
            "source_hash": cls._hash(ddl) if ddl else "",
        }

    # -------------------------------------------------------------------
    # Regex fallbacks (when AST parsing fails)
    # -------------------------------------------------------------------

    @staticmethod
    def _regex_extract_targets(ddl: str) -> Set[str]:
        patterns = [
            r'INSERT\s+INTO\s+([a-zA-Z0-9_."]+)',
            r'MERGE\s+INTO\s+([a-zA-Z0-9_."]+)',
            r'UPDATE\s+([a-zA-Z0-9_."]+)\s+SET\b',
            r'DELETE\s+FROM\s+([a-zA-Z0-9_."]+)',
            r'TRUNCATE\s+(?:TABLE\s+)?([a-zA-Z0-9_."]+)',
        ]
        targets: Set[str] = set()
        for p in patterns:
            for m in re.findall(p, ddl, re.IGNORECASE):
                clean = m.strip().strip('"').upper()
                if not clean.startswith("$") and "(" not in clean:
                    parts = clean.split(".")
                    targets.add(".".join(parts[-2:]) if len(parts) >= 2 else parts[-1])
        return targets

    @staticmethod
    def _regex_extract_dependencies(ddl: str) -> Set[str]:
        skip = {"SELECT", "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT",
                "TABLE", "VALUES", "SET", "LATERAL", ""}
        tables = re.findall(r'(?:FROM|JOIN)\s+([a-zA-Z0-9_."]+)', ddl, re.IGNORECASE)
        return {t.strip().upper() for t in tables if t.strip().upper() not in skip}

    # -------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------

    @staticmethod
    def _count_loc(ddl: str) -> int:
        return len([line for line in ddl.split("\n") if line.strip()])

    @staticmethod
    def _hash(ddl: str) -> str:
        return hashlib.sha256(ddl.encode("utf-8")).hexdigest()

    @staticmethod
    def _count_sql_ops(body: str) -> int:
        """Count distinct SQL write operations. Each statement type counted once per occurrence."""
        count = 0
        for op in ["INSERT\\s+INTO", "UPDATE\\s+\\w", "DELETE\\s+FROM",
                    "MERGE\\s+INTO", "TRUNCATE", "CREATE\\s+TABLE"]:
            count += len(re.findall(op, body, re.IGNORECASE))
        return count
