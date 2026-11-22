"""
Import script — load Snowflake objects (procedures, views, tables) into the database.

Supports two JSON formats:
  1. Legacy SP format: [{sp_name, ddl, status}, ...]
  2. Multi-object format: [{name, object_type, ddl, schema_name?}, ...]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.database import SessionLocal, engine, Base
from backend.models import SourceObject, MigrationState
from backend.services.analyzer import SQLAnalyzer


def import_from_json(json_path: str):
    Base.metadata.create_all(bind=engine)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"Loaded {len(data)} records from {json_path}")

    # Detect format
    is_legacy = any("sp_name" in item for item in data[:5])

    if is_legacy:
        _import_legacy_sps(data)
    else:
        _import_multi_object(data)


def _import_legacy_sps(data: list):
    """Import legacy SP-only JSON format."""
    all_names = [sp.get("sp_name", "") for sp in data if sp.get("status") == "SUCCESS"]

    # Build call graph for called_by counts
    sp_calls = {}
    for sp in data:
        if sp.get("status") != "SUCCESS":
            continue
        name = sp.get("sp_name", "")
        ddl = sp.get("ddl", "") or ""
        sp_calls[name] = SQLAnalyzer.extract_calls(ddl, all_names)

    called_by = {n: 0 for n in all_names}
    for caller, callees in sp_calls.items():
        for callee in callees:
            if callee in called_by:
                called_by[callee] += 1

    db = SessionLocal()
    imported = skipped = 0

    try:
        for sp in data:
            name = sp.get("sp_name", "Unknown")
            if sp.get("status") != "SUCCESS":
                skipped += 1
                continue
            if db.query(SourceObject).filter(SourceObject.name == name, SourceObject.object_type == "procedure").first():
                skipped += 1
                continue

            ddl = sp.get("ddl", "") or ""
            analysis = SQLAnalyzer.analyze_procedure(name, ddl, all_names, called_by.get(name, 0))

            obj = SourceObject(
                name=analysis["name"],
                object_type="procedure",
                schema_name=analysis.get("schema_name", ""),
                ddl=analysis["ddl"],
                category=analysis["category"],
                reason=analysis["reason"],
                loc=analysis["loc"],
                calls=json.dumps(analysis["calls"]),
                called_by=analysis["called_by"],
                target_tables=json.dumps(analysis["target_tables"]),
                dependencies=json.dumps(analysis["dependencies"]),
                source_hash=analysis["source_hash"],
                language=analysis.get("language"),
                has_dynamic_sql=analysis.get("has_dynamic_sql", False),
                parse_method=analysis.get("parse_method"),
                is_legacy=analysis.get("is_legacy", False),
                legacy_reason=analysis.get("legacy_reason"),
                references_legacy=analysis.get("references_legacy", False),
            )
            db.add(obj)
            db.flush()
            db.add(MigrationState(source_object_id=obj.id, status="Not Started"))
            imported += 1

        db.commit()
        print(f"\n[OK] Imported {imported} procedures, skipped {skipped}")
    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] {e}")
        raise
    finally:
        db.close()


def _import_multi_object(data: list):
    """Import multi-object format: procedures, views, tables."""
    # Separate by type
    procedures = [d for d in data if d.get("object_type") == "procedure"]
    views = [d for d in data if d.get("object_type") == "view"]
    tables = [d for d in data if d.get("object_type") == "table"]

    all_proc_names = [p["name"] for p in procedures]

    # Build call graph
    proc_calls = {}
    for p in procedures:
        ddl = p.get("ddl", "") or ""
        proc_calls[p["name"]] = SQLAnalyzer.extract_calls(ddl, all_proc_names)

    called_by = {n: 0 for n in all_proc_names}
    for caller, callees in proc_calls.items():
        for callee in callees:
            if callee in called_by:
                called_by[callee] += 1

    db = SessionLocal()
    counts = {"procedure": 0, "view": 0, "table": 0, "skipped": 0}

    try:
        for p in procedures:
            name = p["name"]
            if db.query(SourceObject).filter(SourceObject.name == name, SourceObject.object_type == "procedure").first():
                counts["skipped"] += 1
                continue
            analysis = SQLAnalyzer.analyze_procedure(
                name, p.get("ddl", ""), all_proc_names,
                called_by.get(name, 0), p.get("schema_name", ""),
            )
            _insert_object(db, analysis)
            counts["procedure"] += 1

        for v in views:
            name = v["name"]
            if db.query(SourceObject).filter(SourceObject.name == name, SourceObject.object_type == "view").first():
                counts["skipped"] += 1
                continue
            analysis = SQLAnalyzer.analyze_view(name, v.get("ddl", ""), v.get("schema_name", ""))
            _insert_object(db, analysis)
            counts["view"] += 1

        for t in tables:
            name = t["name"]
            if db.query(SourceObject).filter(SourceObject.name == name, SourceObject.object_type == "table").first():
                counts["skipped"] += 1
                continue
            analysis = SQLAnalyzer.analyze_table(name, t.get("ddl", ""), t.get("schema_name", ""))
            _insert_object(db, analysis)
            counts["table"] += 1

        # Second pass: flag objects that reference legacy objects
        legacy_names = set()
        for obj in db.query(SourceObject).filter(SourceObject.is_legacy == True).all():
            legacy_names.add(obj.name.upper())
        if legacy_names:
            for obj in db.query(SourceObject).filter(SourceObject.is_legacy == False).all():
                deps = json.loads(obj.dependencies) if obj.dependencies else []
                calls_list = json.loads(obj.calls) if obj.calls else []
                targets = json.loads(obj.target_tables) if obj.target_tables else []
                for ref in deps + calls_list + targets:
                    if ref.split(".")[-1].strip('"').upper() in legacy_names:
                        obj.references_legacy = True
                        break

        db.commit()
        print(f"\n[OK] Imported: {counts['procedure']} procedures, {counts['view']} views, {counts['table']} tables")
        print(f"     Skipped: {counts['skipped']}")
    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] {e}")
        raise
    finally:
        db.close()


def _insert_object(db, analysis: dict):
    obj = SourceObject(
        name=analysis["name"],
        object_type=analysis["object_type"],
        schema_name=analysis.get("schema_name", ""),
        ddl=analysis.get("ddl", ""),
        category=analysis["category"],
        reason=analysis["reason"],
        loc=analysis["loc"],
        calls=json.dumps(analysis.get("calls", [])),
        called_by=analysis.get("called_by", 0),
        target_tables=json.dumps(analysis.get("target_tables", [])),
        dependencies=json.dumps(analysis.get("dependencies", [])),
        source_hash=analysis.get("source_hash", ""),
        language=analysis.get("language"),
        has_dynamic_sql=analysis.get("has_dynamic_sql", False),
        parse_method=analysis.get("parse_method"),
        is_legacy=analysis.get("is_legacy", False),
        legacy_reason=analysis.get("legacy_reason"),
        references_legacy=analysis.get("references_legacy", False),
    )
    db.add(obj)
    db.flush()
    db.add(MigrationState(source_object_id=obj.id, status="Not Started"))


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "data", "lab_dataset.json")
    if not os.path.exists(path):
        print(f"[ERROR] File not found: {path}")
        sys.exit(1)
    import_from_json(path)
