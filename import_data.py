"""
Import script - Load SP data from JSON snapshot into database
"""
import json
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import SessionLocal, engine, Base
from backend.models import SourceSP, MigrationState
from backend.services.analyzer import SPAnalyzer


def import_from_json(json_path: str):
    """Import SPs from JSON file into database"""
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    # Load JSON
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"Loaded {len(data)} SPs from {json_path}")
    
    # Extract all SP names for call resolution
    all_sp_names = [sp.get('sp_name', '') for sp in data if sp.get('status') == 'SUCCESS']
    
    # Build call graph
    sp_calls = {}
    for sp in data:
        if sp.get('status') != 'SUCCESS':
            continue
        name = sp.get('sp_name', '')
        ddl = sp.get('ddl', '') or ''
        sp_calls[name] = SPAnalyzer.extract_calls(ddl, all_sp_names)
    
    # Calculate called_by counts
    called_by = {name: 0 for name in all_sp_names}
    for caller, callees in sp_calls.items():
        for callee in callees:
            if callee in called_by:
                called_by[callee] += 1
    
    # Import into database
    db = SessionLocal()
    imported = 0
    skipped = 0
    
    try:
        for sp in data:
            name = sp.get('sp_name', 'Unknown')
            status = sp.get('status', 'Unknown')
            
            # Check if already exists
            existing = db.query(SourceSP).filter(SourceSP.name == name).first()
            if existing:
                print(f"Skipping {name} (already exists)")
                skipped += 1
                continue
            
            if status != 'SUCCESS':
                print(f"Skipping {name} (status: {status})")
                skipped += 1
                continue
            
            ddl = sp.get('ddl', '') or ''
            
            # Analyze
            analysis = SPAnalyzer.analyze_sp(
                name, 
                ddl, 
                all_sp_names, 
                called_by.get(name, 0)
            )
            
            # Create SourceSP
            source_sp = SourceSP(
                name=analysis['name'],
                ddl=analysis['ddl'],
                category=analysis['category'],
                reason=analysis['reason'],
                loc=analysis['loc'],
                calls=json.dumps(analysis['calls']),
                called_by=analysis['called_by'],
                target_tables=json.dumps(analysis['target_tables']),
                dependencies=json.dumps(analysis['dependencies']),
                source_hash=analysis['source_hash']
            )
            db.add(source_sp)
            db.flush()  # Get ID
            
            # Create default MigrationState
            migration_state = MigrationState(
                source_sp_id=source_sp.id,
                status="Not Started"
            )
            db.add(migration_state)
            
            imported += 1
            if imported % 10 == 0:
                print(f"Imported {imported} SPs...")
        
        db.commit()
        print(f"\n[SUCCESS] Import complete!")
        print(f"   Imported: {imported}")
        print(f"   Skipped: {skipped}")
        
    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] Import failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    json_file = os.path.join(
        os.path.dirname(__file__),
        'data',
        'sp_output.json'
    )
    
    if not os.path.exists(json_file):
        print(f"[ERROR] File not found: {json_file}")
        print("Please place your SP output.json in the data/ folder")
        sys.exit(1)
    
    import_from_json(json_file)
