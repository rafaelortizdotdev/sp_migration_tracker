"""
Evaluate a dataset of Snowflake objects — run the analyzer and print summary stats.

Usage:
    python evaluate_corpus.py                      # uses data/lab_dataset.json
    python evaluate_corpus.py path/to/data.json
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.services.analyzer import SQLAnalyzer


def evaluate(json_path: str):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    procedures = [d for d in data if d.get("object_type") == "procedure"]
    views = [d for d in data if d.get("object_type") == "view"]
    tables = [d for d in data if d.get("object_type") == "table"]

    all_proc_names = [p["name"] for p in procedures]
    proc_calls = {}
    for p in procedures:
        ddl = p.get("ddl", "") or ""
        proc_calls[p["name"]] = SQLAnalyzer.extract_calls(ddl, all_proc_names)

    called_by = {n: 0 for n in all_proc_names}
    for caller, callees in proc_calls.items():
        for callee in callees:
            if callee in called_by:
                called_by[callee] += 1

    cat_counts = Counter()
    lang_counts = Counter()
    parse_counts = Counter()
    legacy_count = 0
    refs_legacy_count = 0
    dynamic_sql_count = 0
    total_loc = 0
    all_results = []

    for p in procedures:
        analysis = SQLAnalyzer.analyze_procedure(
            p["name"], p.get("ddl", ""), all_proc_names,
            called_by.get(p["name"], 0), p.get("schema_name", ""),
        )
        cat_counts[analysis["category"]] += 1
        lang_counts[analysis.get("language", "unknown")] += 1
        parse_counts[analysis.get("parse_method", "unknown")] += 1
        if analysis.get("is_legacy"):
            legacy_count += 1
        if analysis.get("has_dynamic_sql"):
            dynamic_sql_count += 1
        total_loc += analysis["loc"]
        all_results.append(analysis)

    for v in views:
        analysis = SQLAnalyzer.analyze_view(v["name"], v.get("ddl", ""), v.get("schema_name", ""))
        cat_counts[analysis["category"]] += 1
        if analysis.get("is_legacy"):
            legacy_count += 1
        total_loc += analysis["loc"]
        all_results.append(analysis)

    for t in tables:
        analysis = SQLAnalyzer.analyze_table(t["name"], t.get("ddl", ""), t.get("schema_name", ""))
        cat_counts[analysis["category"]] += 1
        if analysis.get("is_legacy"):
            legacy_count += 1
        all_results.append(analysis)

    legacy_names = {r["name"].upper() for r in all_results if r.get("is_legacy")}
    for r in all_results:
        if r.get("is_legacy"):
            continue
        deps = r.get("calls", []) + r.get("target_tables", []) + r.get("dependencies", [])
        for ref in deps:
            if ref.split(".")[-1].strip('"').upper() in legacy_names:
                refs_legacy_count += 1
                break

    print(f"Dataset: {json_path}")
    print(f"Total objects: {len(data)} ({len(procedures)} procedures, {len(views)} views, {len(tables)} tables)")
    print(f"Total LOC: {total_loc}")
    print()

    print("Category distribution:")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:25s} {count:3d}")
    print()

    print("Language distribution (procedures):")
    for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
        print(f"  {lang:25s} {count:3d}")
    print()

    print("Parse method distribution (procedures):")
    for method, count in sorted(parse_counts.items(), key=lambda x: -x[1]):
        print(f"  {method:25s} {count:3d}")
    print()

    print(f"Legacy objects:               {legacy_count}")
    print(f"References legacy:            {refs_legacy_count}")
    print(f"Dynamic SQL:                  {dynamic_sql_count}")

    call_edges = sum(len(v) for v in proc_calls.values())
    print(f"Call graph edges:             {call_edges}")

    roots = [n for n in all_proc_names if called_by[n] == 0 and proc_calls.get(n)]
    print(f"Root orchestrators:           {len(roots)}")
    if roots:
        for r in sorted(roots):
            print(f"  - {r}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "data", "lab_dataset.json")
    if not os.path.exists(path):
        print(f"[ERROR] File not found: {path}")
        sys.exit(1)
    evaluate(path)
