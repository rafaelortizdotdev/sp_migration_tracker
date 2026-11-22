"""
Virtual Node API Routes — split/unsplit for Hybrid procedures.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
import json

from backend.database import get_db
from backend.models import SourceObject, MigrationState, VirtualNode, ChangeLog


router = APIRouter()


class SplitRequest(BaseModel):
    user: Optional[str] = None


class SplitResponse(BaseModel):
    original_hidden: bool
    created_nodes: List[dict]
    warnings: List[str]


class VirtualNodeResponse(BaseModel):
    id: int
    name: str
    node_type: str
    parent_name: str
    inherited_calls: List[str]
    inherited_tables: List[str]
    assigned_dag: Optional[str]

    class Config:
        from_attributes = True


@router.post("/nodes/{name}/split", response_model=SplitResponse)
async def split_node(name: str, request: SplitRequest, db: Session = Depends(get_db)):
    obj = db.query(SourceObject).filter(SourceObject.name == name).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")

    existing = db.query(VirtualNode).filter(VirtualNode.parent_object_id == obj.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already split")

    state = db.query(MigrationState).filter(MigrationState.source_object_id == obj.id).first()
    if not state:
        state = MigrationState(source_object_id=obj.id)
        db.add(state)

    calls = json.loads(obj.calls) if obj.calls else []
    targets = json.loads(obj.target_tables) if obj.target_tables else []
    warnings = []

    if obj.category != "Hybrid":
        raise HTTPException(status_code=400, detail=f"Only Hybrid procedures can be split. This object is '{obj.category}'.")

    orch = VirtualNode(
        name=f"{name}_ORCH",
        node_type="orchestrator",
        parent_object_id=obj.id,
        inherited_calls=json.dumps(calls),
        inherited_tables=json.dumps([]),
        created_by=request.user,
    )
    logic = VirtualNode(
        name=f"{name}_LOGIC",
        node_type="transformation",
        parent_object_id=obj.id,
        inherited_calls=json.dumps([]),
        inherited_tables=json.dumps(targets),
        created_by=request.user,
    )
    db.add(orch)
    db.add(logic)
    state.is_hidden = True

    db.add(ChangeLog(
        action="split",
        entity_type="source_object",
        entity_id=obj.id,
        old_state=json.dumps({"is_hidden": False}),
        new_state=json.dumps({"is_hidden": True, "virtual_nodes": [orch.name, logic.name]}),
        user=request.user,
    ))
    db.commit()
    db.refresh(orch)
    db.refresh(logic)

    return {
        "original_hidden": True,
        "created_nodes": [
            {"id": orch.id, "name": orch.name, "type": "orchestrator", "inherited_calls": calls, "inherited_tables": []},
            {"id": logic.id, "name": logic.name, "type": "transformation", "inherited_calls": [], "inherited_tables": targets},
        ],
        "warnings": warnings,
    }


@router.post("/nodes/{name}/unsplit")
async def unsplit_node(name: str, request: SplitRequest, db: Session = Depends(get_db)):
    obj = db.query(SourceObject).filter(SourceObject.name == name).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")

    vns = db.query(VirtualNode).filter(VirtualNode.parent_object_id == obj.id).all()
    if not vns:
        raise HTTPException(status_code=400, detail="Object is not split")

    state = db.query(MigrationState).filter(MigrationState.source_object_id == obj.id).first()
    vn_names = [vn.name for vn in vns]
    for vn in vns:
        db.delete(vn)
    if state:
        state.is_hidden = False

    db.add(ChangeLog(
        action="unsplit",
        entity_type="source_object",
        entity_id=obj.id,
        old_state=json.dumps({"is_hidden": True, "virtual_nodes": vn_names}),
        new_state=json.dumps({"is_hidden": False}),
        user=request.user,
    ))
    db.commit()
    return {"success": True, "message": f"Unsplit {name}, deleted {len(vns)} virtual nodes"}


@router.get("/virtual-nodes", response_model=List[VirtualNodeResponse])
async def list_virtual_nodes(db: Session = Depends(get_db)):
    nodes = db.query(VirtualNode, SourceObject).join(
        SourceObject, VirtualNode.parent_object_id == SourceObject.id
    ).all()
    return [
        {
            "id": vn.id,
            "name": vn.name,
            "node_type": vn.node_type,
            "parent_name": parent.name,
            "inherited_calls": json.loads(vn.inherited_calls) if vn.inherited_calls else [],
            "inherited_tables": json.loads(vn.inherited_tables) if vn.inherited_tables else [],
            "assigned_dag": vn.assigned_dag,
        }
        for vn, parent in nodes
    ]


@router.get("/nodes/{name}/split-preview")
async def get_split_preview(name: str, db: Session = Depends(get_db)):
    obj = db.query(SourceObject).filter(SourceObject.name == name).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")

    calls = json.loads(obj.calls) if obj.calls else []
    targets = json.loads(obj.target_tables) if obj.target_tables else []
    ddl = obj.ddl or ""

    orch_lines = [l for l in ddl.split("\n") if "CALL" in l.upper()]
    logic_lines = [l for l in ddl.split("\n") if any(op in l.upper() for op in ["INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE"])]

    return {
        "name": name,
        "category": obj.category,
        "can_split": obj.category == "Hybrid",
        "orch_preview": {
            "name": f"{name}_ORCH",
            "type": "orchestrator",
            "calls": calls,
            "sample_code": "\n".join(orch_lines[:10]) or "No CALL statements found",
        },
        "logic_preview": {
            "name": f"{name}_LOGIC",
            "type": "transformation",
            "target_tables": targets,
            "sample_code": "\n".join(logic_lines[:10]) or "No SQL operations found",
        },
    }
