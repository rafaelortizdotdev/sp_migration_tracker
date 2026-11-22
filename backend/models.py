"""
SQLAlchemy database models — generalized for procedures, views, and tables.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Float
from sqlalchemy.sql import func
from backend.database import Base


class SourceObject(Base):
    """Immutable — original DDL/metadata extracted from Snowflake."""
    __tablename__ = "source_objects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    object_type = Column(String(30), nullable=False, index=True)  # procedure, view, table
    schema_name = Column(String(255))
    ddl = Column(Text)
    category = Column(String(50))
    reason = Column(Text)
    loc = Column(Integer, default=0)
    calls = Column(Text)        # JSON array — procedures called
    called_by = Column(Integer, default=0)
    target_tables = Column(Text) # JSON array — tables written to
    dependencies = Column(Text)  # JSON array — tables/views read from
    source_hash = Column(String(64))
    language = Column(String(30))
    has_dynamic_sql = Column(Boolean, default=False)
    parse_method = Column(String(30))
    is_legacy = Column(Boolean, default=False)
    legacy_reason = Column(Text)
    references_legacy = Column(Boolean, default=False)
    imported_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        # unique per (schema, name, type)
        {"sqlite_autoincrement": True},
    )


class MigrationState(Base):
    """Mutable — user edits for each source object."""
    __tablename__ = "migration_state"

    id = Column(Integer, primary_key=True, index=True)
    source_object_id = Column(Integer, ForeignKey("source_objects.id"), nullable=False)
    status = Column(String(50), default="Not Started")
    notes = Column(Text)
    assigned_dag = Column(String(255))
    is_hidden = Column(Boolean, default=False)
    updated_by = Column(String(100))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class VirtualNode(Base):
    """User-created nodes that don't exist in Snowflake."""
    __tablename__ = "virtual_nodes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    node_type = Column(String(50))
    parent_object_id = Column(Integer, ForeignKey("source_objects.id"))
    inherited_calls = Column(Text)
    inherited_tables = Column(Text)
    assigned_dag = Column(String(255))
    position_x = Column(Float)
    position_y = Column(Float)
    created_by = Column(String(100))
    created_at = Column(DateTime, server_default=func.now())


class DAGGroup(Base):
    """User-defined DAG containers."""
    __tablename__ = "dag_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    color = Column(String(7))
    description = Column(Text)
    execution_order = Column(Text)
    created_by = Column(String(100))
    created_at = Column(DateTime, server_default=func.now())


class ChangeLog(Base):
    """Audit trail for all user actions."""
    __tablename__ = "change_log"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(50))
    entity_type = Column(String(50))
    entity_id = Column(Integer)
    old_state = Column(Text)
    new_state = Column(Text)
    user = Column(String(100))
    timestamp = Column(DateTime, server_default=func.now())
