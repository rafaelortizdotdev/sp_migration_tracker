"""
SQLAlchemy database models
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Float
from sqlalchemy.sql import func
from backend.database import Base


class SourceSP(Base):
    """Immutable - Original data from Snowflake"""
    __tablename__ = "source_sps"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    ddl = Column(Text)
    category = Column(String(50))  # Auto-classified
    reason = Column(Text)
    loc = Column(Integer, default=0)
    calls = Column(Text)  # JSON array as string
    called_by = Column(Integer, default=0)
    target_tables = Column(Text)  # JSON array as string
    dependencies = Column(Text)  # JSON array as string
    source_hash = Column(String(64))  # SHA256 of DDL for change detection
    imported_at = Column(DateTime, server_default=func.now())


class MigrationState(Base):
    """Mutable - User edits for each source SP"""
    __tablename__ = "migration_state"

    id = Column(Integer, primary_key=True, index=True)
    source_sp_id = Column(Integer, ForeignKey("source_sps.id"), nullable=False)
    status = Column(String(50), default="Not Started")
    notes = Column(Text)
    assigned_dag = Column(String(255))  # FK to dag_groups.name
    is_hidden = Column(Boolean, default=False)  # For split nodes
    updated_by = Column(String(100))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class VirtualNode(Base):
    """User-created nodes that don't exist in Snowflake"""
    __tablename__ = "virtual_nodes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    node_type = Column(String(50))  # 'orchestrator', 'transformation'
    parent_sp_id = Column(Integer, ForeignKey("source_sps.id"))  # Original SP
    inherited_calls = Column(Text)  # JSON array - for _ORCH nodes
    inherited_tables = Column(Text)  # JSON array - for _LOGIC nodes
    assigned_dag = Column(String(255))
    position_x = Column(Float)  # Graph position
    position_y = Column(Float)
    created_by = Column(String(100))
    created_at = Column(DateTime, server_default=func.now())


class DAGGroup(Base):
    """User-defined DAG containers"""
    __tablename__ = "dag_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    color = Column(String(7))  # Hex color
    description = Column(Text)
    execution_order = Column(Text)  # JSON array of node IDs in order
    created_by = Column(String(100))
    created_at = Column(DateTime, server_default=func.now())


class ChangeLog(Base):
    """Undo/history support"""
    __tablename__ = "change_log"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(50))  # 'split', 'group', 'status', 'hide'
    entity_type = Column(String(50))  # 'source_sp', 'virtual_node', 'dag_group'
    entity_id = Column(Integer)
    old_state = Column(Text)  # JSON snapshot
    new_state = Column(Text)  # JSON snapshot
    user = Column(String(100))
    timestamp = Column(DateTime, server_default=func.now())
