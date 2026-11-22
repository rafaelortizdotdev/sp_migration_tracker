"""
Source Object API Routes — procedures, views, and tables.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
import json

from backend.database import get_db
from backend.models import SourceObject, MigrationState, VirtualNode, ChangeLog


router = APIRouter()


class ObjectResponse(BaseModel):
    name: str
    object_type: str
    schema_name: Optional[str] = None
    category: str
    reason: str
    loc: int
    calls: List[str]
    called_by: int
    target_tables: List[str]
    dependencies: List[str]
    migration_status: str
    is_virtual: bool = False
    parent_name: Optional[str] = None

    class Config:
        from_attributes = True


class ObjectDetailResponse(ObjectResponse):
    ddl: str


class StatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None
    user: Optional[str] = None


class StatsResponse(BaseModel):
    total: int
    by_type: dict
    by_category: dict
    by_status: dict


def _json_load(val: Optional[str]) -> list:
    return json.loads(val) if val else []


def _obj_to_dict(obj: SourceObject, status_val: str, include_deps: bool = False) -> dict:
    d = {
        "name": obj.name,
        "object_type": obj.object_type,
        "schema_name": obj.schema_name or "",
        "category": obj.category or "Unknown",
        "reason": obj.reason or "",
        "loc": obj.loc or 0,
        "calls": _json_load(obj.calls),
        "called_by": obj.called_by or 0,
        "target_tables": _json_load(obj.target_tables),
        "dependencies": _json_load(obj.dependencies) if include_deps else [],
        "migration_status": status_val,
        "is_virtual": False,
        "parent_name": None,
    }
    if include_deps:
        d["ddl"] = obj.ddl or ""
    return d


@router.get("/objects", response_model=List[ObjectResponse])
async def list_objects(
    object_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    include_hidden: bool = Query(False),
    db: Session = Depends(get_db),
):
    query = db.query(SourceObject, MigrationState).outerjoin(
        MigrationState, SourceObject.id == MigrationState.source_object_id
    )
    if object_type:
        query = query.filter(SourceObject.object_type == object_type)
    if category:
        query = query.filter(SourceObject.category == category)
    if search:
        query = query.filter(SourceObject.name.ilike(f"%{search}%"))

    results = query.all()
    items = []

    for obj, state in results:
        is_hidden = state.is_hidden if state else False
        if is_hidden and not include_hidden:
            continue
        status_val = state.status if state else "Not Started"
        if status and status_val != status:
            continue
        items.append(_obj_to_dict(obj, status_val))

    # Merge virtual nodes
    vns = db.query(VirtualNode, SourceObject).join(
        SourceObject, VirtualNode.parent_object_id == SourceObject.id
    ).all()
    for vn, parent in vns:
        items.append({
            "name": vn.name,
            "object_type": parent.object_type,
            "schema_name": parent.schema_name or "",
            "category": vn.node_type.capitalize() if vn.node_type else "Unknown",
            "reason": f"Virtual node split from {parent.name}",
            "loc": 0,
            "calls": _json_load(vn.inherited_calls),
            "called_by": 0,
            "target_tables": _json_load(vn.inherited_tables),
            "dependencies": [],
            "migration_status": "Not Started",
            "is_virtual": True,
            "parent_name": parent.name,
        })

    return items


@router.get("/objects/{name}", response_model=ObjectDetailResponse)
async def get_object(name: str, db: Session = Depends(get_db)):
    obj = db.query(SourceObject).filter(SourceObject.name == name).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")
    state = db.query(MigrationState).filter(MigrationState.source_object_id == obj.id).first()
    return _obj_to_dict(obj, state.status if state else "Not Started", include_deps=True)


@router.patch("/objects/{name}/status")
async def update_status(name: str, update: StatusUpdate, db: Session = Depends(get_db)):
    obj = db.query(SourceObject).filter(SourceObject.name == name).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")

    state = db.query(MigrationState).filter(MigrationState.source_object_id == obj.id).first()
    old_status = state.status if state else "Not Started"

    if state:
        state.status = update.status
        if update.notes is not None:
            state.notes = update.notes
        if update.user:
            state.updated_by = update.user
    else:
        state = MigrationState(
            source_object_id=obj.id,
            status=update.status,
            notes=update.notes,
            updated_by=update.user,
        )
        db.add(state)

    db.add(ChangeLog(
        entity_type="source_object",
        entity_id=obj.id,
        action="status_change",
        old_state=old_status,
        new_state=update.status,
        user=update.user,
    ))
    db.commit()
    return {"success": True, "status": update.status}


@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: Session = Depends(get_db)):
    objects = db.query(SourceObject).all()
    statuses = {s.source_object_id: s.status for s in db.query(MigrationState).all()}

    by_type: dict = {}
    by_category: dict = {}
    by_status: dict = {}

    for obj in objects:
        t = obj.object_type or "unknown"
        by_type[t] = by_type.get(t, 0) + 1

        cat = obj.category or "Unknown"
        by_category[cat] = by_category.get(cat, 0) + 1

        st = statuses.get(obj.id, "Not Started")
        by_status[st] = by_status.get(st, 0) + 1

    return {"total": len(objects), "by_type": by_type, "by_category": by_category, "by_status": by_status}


@router.get("/edges")
async def get_edges(db: Session = Depends(get_db)):
    """Call graph edges (procedure→procedure) + dependency edges (view/proc→table/view)."""
    objects = db.query(SourceObject).all()
    name_map = {o.name.upper(): o.name for o in objects}
    edges = []

    for obj in objects:
        # Procedure call edges
        calls = _json_load(obj.calls)
        for callee in calls:
            clean = callee.split(".")[-1].strip('"').upper()
            if clean in name_map:
                edges.append({"source": obj.name, "target": name_map[clean], "type": "calls"})

        # Dependency edges (reads from)
        deps = _json_load(obj.dependencies)
        for dep in deps:
            clean = dep.split(".")[-1].strip('"').upper()
            if clean in name_map:
                edges.append({"source": obj.name, "target": name_map[clean], "type": "depends_on"})

    return edges


@router.get("/clusters")
async def get_clusters(db: Session = Depends(get_db)):
    """Orphan objects grouped by primary target table."""
    objects = db.query(SourceObject).all()

    connected = set()
    for obj in objects:
        calls = _json_load(obj.calls)
        deps = _json_load(obj.dependencies)
        if calls:
            connected.add(obj.name)
            connected.update(calls)
        if deps and obj.object_type == "view":
            connected.add(obj.name)

    clusters: dict = {}
    for obj in objects:
        if obj.name in connected:
            continue
        targets = _json_load(obj.target_tables)
        primary = targets[0] if targets else "STANDALONE"
        if primary not in clusters:
            clusters[primary] = []
        clusters[primary].append(obj.name)

    return clusters


# Keep backward compat — /api/sps redirects to /api/objects?object_type=procedure
@router.get("/sps", response_model=List[ObjectResponse])
async def list_sps(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    include_hidden: bool = Query(False),
    db: Session = Depends(get_db),
):
    return await list_objects(
        object_type="procedure",
        category=category,
        status=status,
        search=search,
        include_hidden=include_hidden,
        db=db,
    )
