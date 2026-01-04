"""
SP API Routes
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
import json

from backend.database import get_db
from backend.models import SourceSP, MigrationState, VirtualNode, ChangeLog


router = APIRouter()


# Pydantic schemas
class SPResponse(BaseModel):
    name: str
    category: str
    reason: str
    loc: int
    calls: List[str]
    called_by: int
    target_tables: List[str]
    migration_status: str
    is_virtual: bool = False
    parent_sp_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class SPDetailResponse(SPResponse):
    ddl: str
    dependencies: List[str]


class StatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None
    user: Optional[str] = None


class StatsResponse(BaseModel):
    total: int
    by_category: dict
    by_status: dict


@router.get("/sps", response_model=List[SPResponse])
async def list_sps(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    include_hidden: bool = Query(False),
    db: Session = Depends(get_db)
):
    """
    List all stored procedures with optional filters
    Merges source SPs (non-hidden) with virtual nodes
    """
    # Get source SPs
    query = db.query(SourceSP, MigrationState).outerjoin(
        MigrationState, SourceSP.id == MigrationState.source_sp_id
    )
    
    if category:
        query = query.filter(SourceSP.category == category)
    if search:
        query = query.filter(SourceSP.name.ilike(f"%{search}%"))
    
    results = query.all()
    
    sps = []
    
    # Add non-hidden source SPs
    for sp, state in results:
        is_hidden = state.is_hidden if state else False
        if is_hidden and not include_hidden:
            continue
            
        status_val = state.status if state else "Not Started"
        if status and status_val != status:
            continue
            
        sps.append({
            "name": sp.name,
            "category": sp.category or "Unknown",
            "reason": sp.reason or "",
            "loc": sp.loc or 0,
            "calls": json.loads(sp.calls) if sp.calls else [],
            "called_by": sp.called_by or 0,
            "target_tables": json.loads(sp.target_tables) if sp.target_tables else [],
            "migration_status": status_val,
            "is_virtual": False,
            "parent_sp_name": None
        })
    
    # Add virtual nodes
    virtual_nodes = db.query(VirtualNode, SourceSP).join(
        SourceSP, VirtualNode.parent_sp_id == SourceSP.id
    ).all()
    
    for vn, parent in virtual_nodes:
        sps.append({
            "name": vn.name,
            "category": vn.node_type.capitalize(),
            "reason": f"Virtual node split from {parent.name}",
            "loc": 0,
            "calls": json.loads(vn.inherited_calls) if vn.inherited_calls else [],
            "called_by": 0,
            "target_tables": json.loads(vn.inherited_tables) if vn.inherited_tables else [],
            "migration_status": "Not Started",
            "is_virtual": True,
            "parent_sp_name": parent.name
        })
    
    return sps


@router.get("/sps/{name}", response_model=SPDetailResponse)
async def get_sp(name: str, db: Session = Depends(get_db)):
    """Get a single SP with full details including DDL"""
    sp = db.query(SourceSP).filter(SourceSP.name == name).first()
    if not sp:
        raise HTTPException(status_code=404, detail="SP not found")
    
    status = db.query(MigrationState).filter(MigrationState.source_sp_id == sp.id).first()
    
    return {
        "name": sp.name,
        "ddl": sp.ddl or "",
        "category": sp.category or "Unknown",
        "reason": sp.reason or "",
        "loc": sp.loc or 0,
        "calls": json.loads(sp.calls) if sp.calls else [],
        "called_by": sp.called_by or 0,
        "target_tables": json.loads(sp.target_tables) if sp.target_tables else [],
        "dependencies": json.loads(sp.dependencies) if sp.dependencies else [],
        "migration_status": status.status if status else "Not Started"
    }


@router.patch("/sps/{name}/status")
async def update_status(name: str, update: StatusUpdate, db: Session = Depends(get_db)):
    """Update migration status for an SP"""
    sp = db.query(SourceSP).filter(SourceSP.name == name).first()
    if not sp:
        raise HTTPException(status_code=404, detail="SP not found")
    
    status = db.query(MigrationState).filter(MigrationState.source_sp_id == sp.id).first()
    old_status = status.status if status else "Not Started"
    
    if status:
        status.status = update.status
        if update.notes:
            status.notes = update.notes
        if update.user:
            status.updated_by = update.user
    else:
        status = MigrationState(
            source_sp_id=sp.id,
            status=update.status,
            notes=update.notes,
            updated_by=update.user
        )
        db.add(status)
    
    # Audit log
    log = ChangeLog(
        entity_type="source_sp",
        entity_id=sp.id,
        action="status_change",
        old_state=old_status,
        new_state=update.status,
        user=update.user
    )
    db.add(log)
    db.commit()
    
    return {"success": True, "status": update.status}


@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: Session = Depends(get_db)):
    """Get category and status counts"""
    sps = db.query(SourceSP).all()
    statuses = {s.source_sp_id: s.status for s in db.query(MigrationState).all()}
    
    by_category = {}
    by_status = {}
    
    for sp in sps:
        cat = sp.category or "Unknown"
        by_category[cat] = by_category.get(cat, 0) + 1
        
        status = statuses.get(sp.id, "Not Started")
        by_status[status] = by_status.get(status, 0) + 1
    
    return {
        "total": len(sps),
        "by_category": by_category,
        "by_status": by_status
    }


@router.get("/edges")
async def get_edges(db: Session = Depends(get_db)):
    """Get call graph edges"""
    sps = db.query(SourceSP).all()
    sp_names = {sp.name.upper(): sp.name for sp in sps}
    
    edges = []
    for sp in sps:
        calls = json.loads(sp.calls) if sp.calls else []
        for callee in calls:
            clean = callee.split(".")[-1].strip('"').upper()
            if clean in sp_names:
                edges.append({"source": sp.name, "target": sp_names[clean]})
    
    return edges


@router.get("/clusters")
async def get_clusters(db: Session = Depends(get_db)):
    """Get orphan SPs grouped by target table"""
    sps = db.query(SourceSP).all()
    
    # Find connected SPs
    connected = set()
    for sp in sps:
        calls = json.loads(sp.calls) if sp.calls else []
        if calls:
            connected.add(sp.name)
            connected.update(calls)
    
    # Group orphans by target table
    clusters = {}
    for sp in sps:
        if sp.name not in connected:
            targets = json.loads(sp.target_tables) if sp.target_tables else []
            primary = targets[0] if targets else "STANDALONE"
            
            # Normalize
            if "FACT_INV" in primary:
                primary = "FACT_INV"
            elif "_DIM" in primary:
                primary = primary.split(".")[-1] if "." in primary else primary
            
            if primary not in clusters:
                clusters[primary] = []
            clusters[primary].append(sp.name)
    
    return clusters
