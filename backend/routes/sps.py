"""
SP API Routes
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
import json

from backend.database import get_db
from backend.models import StoredProcedure, MigrationStatus, AuditLog


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
    db: Session = Depends(get_db)
):
    """List all stored procedures with optional filters"""
    query = db.query(StoredProcedure, MigrationStatus).outerjoin(
        MigrationStatus, StoredProcedure.id == MigrationStatus.sp_id
    )
    
    if category:
        query = query.filter(StoredProcedure.category == category)
    if search:
        query = query.filter(StoredProcedure.name.ilike(f"%{search}%"))
    
    results = query.all()
    
    sps = []
    for sp, status_obj in results:
        status_val = status_obj.status if status_obj else "Not Started"
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
            "migration_status": status_val
        })
    
    return sps


@router.get("/sps/{name}", response_model=SPDetailResponse)
async def get_sp(name: str, db: Session = Depends(get_db)):
    """Get a single SP with full details including DDL"""
    sp = db.query(StoredProcedure).filter(StoredProcedure.name == name).first()
    if not sp:
        raise HTTPException(status_code=404, detail="SP not found")
    
    status = db.query(MigrationStatus).filter(MigrationStatus.sp_id == sp.id).first()
    
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
    sp = db.query(StoredProcedure).filter(StoredProcedure.name == name).first()
    if not sp:
        raise HTTPException(status_code=404, detail="SP not found")
    
    status = db.query(MigrationStatus).filter(MigrationStatus.sp_id == sp.id).first()
    old_status = status.status if status else "Not Started"
    
    if status:
        status.status = update.status
        if update.notes:
            status.notes = update.notes
        if update.user:
            status.updated_by = update.user
    else:
        status = MigrationStatus(
            sp_id=sp.id,
            status=update.status,
            notes=update.notes,
            updated_by=update.user
        )
        db.add(status)
    
    # Audit log
    log = AuditLog(
        sp_id=sp.id,
        action="status_change",
        old_value=old_status,
        new_value=update.status,
        user=update.user
    )
    db.add(log)
    db.commit()
    
    return {"success": True, "status": update.status}


@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: Session = Depends(get_db)):
    """Get category and status counts"""
    sps = db.query(StoredProcedure).all()
    statuses = {s.sp_id: s.status for s in db.query(MigrationStatus).all()}
    
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
    sps = db.query(StoredProcedure).all()
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
    sps = db.query(StoredProcedure).all()
    
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
