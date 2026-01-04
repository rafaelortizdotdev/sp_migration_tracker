"""
Virtual Node API Routes - Split/Unsplit functionality
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
import json

from backend.database import get_db
from backend.models import SourceSP, MigrationState, VirtualNode, ChangeLog
from backend.services.analyzer import SPAnalyzer


router = APIRouter()


# Pydantic schemas
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
    parent_sp_name: str
    inherited_calls: List[str]
    inherited_tables: List[str]
    assigned_dag: Optional[str]
    
    class Config:
        from_attributes = True


@router.post("/nodes/{name}/split", response_model=SplitResponse)
async def split_node(name: str, request: SplitRequest, db: Session = Depends(get_db)):
    """
    Split a Hybrid SP into two virtual nodes:
    - {name}_ORCH (orchestrator) - inherits CALL statements
    - {name}_LOGIC (transformation) - inherits SQL operations
    """
    # Find source SP
    sp = db.query(SourceSP).filter(SourceSP.name == name).first()
    if not sp:
        raise HTTPException(status_code=404, detail="SP not found")
    
    # Check if already split
    existing_virtual = db.query(VirtualNode).filter(VirtualNode.parent_sp_id == sp.id).first()
    if existing_virtual:
        raise HTTPException(status_code=400, detail="SP is already split")
    
    # Get migration state
    state = db.query(MigrationState).filter(MigrationState.source_sp_id == sp.id).first()
    if not state:
        state = MigrationState(source_sp_id=sp.id)
        db.add(state)
    
    # Parse stored data
    calls = json.loads(sp.calls) if sp.calls else []
    target_tables = json.loads(sp.target_tables) if sp.target_tables else []
    
    # Validation warnings
    warnings = []
    
    # Check if it's actually a Hybrid
    if sp.category != 'Hybrid':
        warnings.append(f"Warning: SP is classified as '{sp.category}', not 'Hybrid'. Split may not be appropriate.")
    
    # Create ORCH node
    orch_node = VirtualNode(
        name=f"{name}_ORCH",
        node_type="orchestrator",
        parent_sp_id=sp.id,
        inherited_calls=json.dumps(calls),
        inherited_tables=json.dumps([]),
        created_by=request.user
    )
    db.add(orch_node)
    
    # Create LOGIC node
    logic_node = VirtualNode(
        name=f"{name}_LOGIC",
        node_type="transformation",
        parent_sp_id=sp.id,
        inherited_calls=json.dumps([]),
        inherited_tables=json.dumps(target_tables),
        created_by=request.user
    )
    db.add(logic_node)
    
    # Hide original
    state.is_hidden = True
    
    # Validation: Check if LOGIC still has calls (shouldn't)
    if calls and len(calls) > 0:
        # Check DDL for CALL statements in non-orchestration sections
        ddl_lower = sp.ddl.lower() if sp.ddl else ""
        if 'call' in ddl_lower:
            warnings.append("Warning: Logic node may still contain CALL statements. Manual review recommended.")
    
    # Validation: Check if ORCH has SQL ops (shouldn't)
    sql_ops = ['insert', 'update', 'delete', 'merge', 'truncate']
    ddl_lower = sp.ddl.lower() if sp.ddl else ""
    has_sql = any(op in ddl_lower for op in sql_ops)
    if has_sql and len(target_tables) > 0:
        warnings.append("Warning: Orchestrator node may contain SQL operations. Consider further splitting.")
    
    # Audit log
    log = ChangeLog(
        action="split",
        entity_type="source_sp",
        entity_id=sp.id,
        old_state=json.dumps({"is_hidden": False}),
        new_state=json.dumps({
            "is_hidden": True,
            "virtual_nodes": [orch_node.name, logic_node.name]
        }),
        user=request.user
    )
    db.add(log)
    
    db.commit()
    db.refresh(orch_node)
    db.refresh(logic_node)
    
    return {
        "original_hidden": True,
        "created_nodes": [
            {
                "id": orch_node.id,
                "name": orch_node.name,
                "type": "orchestrator",
                "inherited_calls": calls,
                "inherited_tables": []
            },
            {
                "id": logic_node.id,
                "name": logic_node.name,
                "type": "transformation",
                "inherited_calls": [],
                "inherited_tables": target_tables
            }
        ],
        "warnings": warnings
    }


@router.post("/nodes/{name}/unsplit")
async def unsplit_node(name: str, request: SplitRequest, db: Session = Depends(get_db)):
    """
    Revert a split - delete virtual nodes and unhide original
    """
    # Find source SP
    sp = db.query(SourceSP).filter(SourceSP.name == name).first()
    if not sp:
        raise HTTPException(status_code=404, detail="SP not found")
    
    # Find virtual nodes
    virtual_nodes = db.query(VirtualNode).filter(VirtualNode.parent_sp_id == sp.id).all()
    if not virtual_nodes:
        raise HTTPException(status_code=400, detail="SP is not split")
    
    # Get migration state
    state = db.query(MigrationState).filter(MigrationState.source_sp_id == sp.id).first()
    
    # Delete virtual nodes
    for vn in virtual_nodes:
        db.delete(vn)
    
    # Unhide original
    if state:
        state.is_hidden = False
    
    # Audit log
    log = ChangeLog(
        action="unsplit",
        entity_type="source_sp",
        entity_id=sp.id,
        old_state=json.dumps({
            "is_hidden": True,
            "virtual_nodes": [vn.name for vn in virtual_nodes]
        }),
        new_state=json.dumps({"is_hidden": False}),
        user=request.user
    )
    db.add(log)
    
    db.commit()
    
    return {"success": True, "message": f"Unsplit {name}, deleted {len(virtual_nodes)} virtual nodes"}


@router.get("/virtual-nodes", response_model=List[VirtualNodeResponse])
async def list_virtual_nodes(db: Session = Depends(get_db)):
    """List all virtual nodes"""
    nodes = db.query(VirtualNode, SourceSP).join(
        SourceSP, VirtualNode.parent_sp_id == SourceSP.id
    ).all()
    
    result = []
    for vn, parent in nodes:
        result.append({
            "id": vn.id,
            "name": vn.name,
            "node_type": vn.node_type,
            "parent_sp_name": parent.name,
            "inherited_calls": json.loads(vn.inherited_calls) if vn.inherited_calls else [],
            "inherited_tables": json.loads(vn.inherited_tables) if vn.inherited_tables else [],
            "assigned_dag": vn.assigned_dag
        })
    
    return result


@router.get("/nodes/{name}/split-preview")
async def get_split_preview(name: str, db: Session = Depends(get_db)):
    """
    Preview what a split would look like without actually splitting
    """
    sp = db.query(SourceSP).filter(SourceSP.name == name).first()
    if not sp:
        raise HTTPException(status_code=404, detail="SP not found")
    
    calls = json.loads(sp.calls) if sp.calls else []
    target_tables = json.loads(sp.target_tables) if sp.target_tables else []
    
    # Extract sections from DDL
    ddl = sp.ddl or ""
    
    # Simple heuristic: lines with CALL go to ORCH, lines with INSERT/UPDATE/etc go to LOGIC
    orch_lines = []
    logic_lines = []
    
    for line in ddl.split('\n'):
        line_upper = line.upper()
        if 'CALL' in line_upper:
            orch_lines.append(line)
        elif any(op in line_upper for op in ['INSERT', 'UPDATE', 'DELETE', 'MERGE', 'TRUNCATE']):
            logic_lines.append(line)
    
    return {
        "name": name,
        "category": sp.category,
        "can_split": sp.category == 'Hybrid',
        "orch_preview": {
            "name": f"{name}_ORCH",
            "type": "orchestrator",
            "calls": calls,
            "sample_code": '\n'.join(orch_lines[:10]) if orch_lines else "No CALL statements found"
        },
        "logic_preview": {
            "name": f"{name}_LOGIC",
            "type": "transformation",
            "target_tables": target_tables,
            "sample_code": '\n'.join(logic_lines[:10]) if logic_lines else "No SQL operations found"
        }
    }
