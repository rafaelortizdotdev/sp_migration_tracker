"""
SP Analyzer Service - Core categorization logic
"""
import re
import hashlib
from typing import List, Tuple, Dict


class SPAnalyzer:
    """Analyzes stored procedure DDL and extracts metadata"""
    
    @staticmethod
    def classify_sp(sp_name: str, ddl: str, call_count: int, called_by_count: int) -> Tuple[str, str]:
        """
        Classify SP into one of 7 categories
        
        Returns:
            (category, reason)
        """
        # Extract indicators
        sql_ops = ['INSERT', 'UPDATE', 'DELETE', 'MERGE', 'TRUNCATE', 'CREATE TABLE']
        sql_count = sum(len(re.findall(r'\b' + op + r'\b', ddl, re.IGNORECASE)) for op in sql_ops)
        
        has_control_refs = bool(re.search(r'\bCONTROL\b|\bETL_|\bBATCH_ID|\bJOB_ID', ddl, re.IGNORECASE))
        has_legacy_refs = bool(re.search(r'\bDWINV_DEV\b|\bDWINV_BAK\b|\bJUNK\b|\bDWINV3\b', ddl, re.IGNORECASE))
        has_scheduler_keywords = bool(re.search(r'REFRESH|BATCH|DAILY|SYNC|LOAD_ALL|SCHEDULER', sp_name, re.IGNORECASE))
        
        is_root = call_count > 0 and called_by_count == 0
        is_utility = called_by_count >= 2 and call_count == 0
        
        # Classification logic (priority order)
        if has_control_refs and (has_scheduler_keywords or is_root):
            return 'ETL Control', 'Manages job state, batch IDs, or scheduling'
        
        if is_root and has_scheduler_keywords:
            return 'Scheduler', f'Entry point (calls {call_count} SPs, not called by others)'
        
        if is_utility:
            return 'Utility', f'Helper SP (called by {called_by_count} others)'
        
        if call_count > 0 and sql_count == 0:
            return 'Orchestrator', f'Calls {call_count} SPs with no direct SQL transformations'
        
        if call_count == 0 and sql_count > 0:
            return 'Transformation', f'Pure SQL ({sql_count} operations, dbt candidate)'
        
        if call_count > 0 and sql_count > 0:
            ratio = sql_count / (call_count + sql_count)
            if ratio > 0.7:
                return 'Hybrid', f'Mostly transformation ({sql_count} SQL ops) but calls {call_count} SPs'
            else:
                return 'Hybrid', f'Mostly orchestration ({call_count} calls) with {sql_count} SQL ops'
        
        # Legacy check is LAST
        if has_legacy_refs:
            return 'Legacy', 'Contains DEV/BAK/JUNK schema references and no clear purpose'
        
        return 'Unknown', 'Could not determine category'
    
    @staticmethod
    def calculate_loc(ddl: str) -> int:
        """Calculate lines of code (non-empty lines)"""
        lines = ddl.split('\n')
        return len([l for l in lines if l.strip()])
    
    @staticmethod
    def extract_target_tables(ddl: str) -> List[str]:
        """Extract tables that the SP writes to"""
        write_patterns = [
            r'INSERT\s+INTO\s+([a-zA-Z0-9_."]+)',
            r'MERGE\s+INTO\s+([a-zA-Z0-9_."]+)',
            r'UPDATE\s+([a-zA-Z0-9_."]+)\s+(?:SET|[A-Z])',
            r'DELETE\s+FROM\s+([a-zA-Z0-9_."]+)',
            r'TRUNCATE\s+(?:TABLE\s+)?([a-zA-Z0-9_."]+)',
        ]
        targets = set()
        for pattern in write_patterns:
            matches = re.findall(pattern, ddl, re.IGNORECASE)
            for m in matches:
                clean = m.strip().strip('"').upper()
                if not clean.startswith('$') and '(' not in clean:
                    parts = clean.split('.')
                    if len(parts) >= 2:
                        targets.add(f"{parts[-2]}.{parts[-1]}")
                    else:
                        targets.add(parts[-1])
        return list(targets)
    
    @staticmethod
    def extract_dependencies(ddl: str) -> List[str]:
        """Extract all table references (FROM, JOIN, etc.)"""
        tables = re.findall(r'(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([a-zA-Z0-9_."]+)', ddl, re.IGNORECASE)
        cleaned = set()
        skip = ['SELECT', 'WHERE', 'GROUP', 'ORDER', 'HAVING', 'LIMIT', 'TABLE', 'VALUES', 'SET', 'LATERAL']
        for t in tables:
            t = t.strip()
            if t.upper() not in skip:
                cleaned.add(t)
        return list(cleaned)
    
    @staticmethod
    def extract_calls(ddl: str, all_sp_names: List[str]) -> List[str]:
        """Extract normalized SP calls"""
        raw_calls = re.findall(r'CALL\s+([A-Za-z0-9_."]+)', ddl, re.IGNORECASE)
        normalized = []
        for call in raw_calls:
            name = call.split('.')[-1].strip('"').upper()
            for known in all_sp_names:
                if known.upper() == name or name in known.upper():
                    normalized.append(known)
                    break
        return normalized
    
    @staticmethod
    def calculate_hash(ddl: str) -> str:
        """Calculate SHA256 hash of DDL for change detection"""
        return hashlib.sha256(ddl.encode('utf-8')).hexdigest()
    
    @classmethod
    def analyze_sp(cls, sp_name: str, ddl: str, all_sp_names: List[str], called_by_count: int = 0) -> Dict:
        """
        Full analysis of a single SP
        
        Returns dict with all metadata
        """
        calls = cls.extract_calls(ddl, all_sp_names)
        call_count = len(calls)
        
        category, reason = cls.classify_sp(sp_name, ddl, call_count, called_by_count)
        
        return {
            'name': sp_name,
            'ddl': ddl,
            'category': category,
            'reason': reason,
            'loc': cls.calculate_loc(ddl),
            'calls': calls,
            'called_by': called_by_count,
            'target_tables': cls.extract_target_tables(ddl),
            'dependencies': cls.extract_dependencies(ddl),
            'source_hash': cls.calculate_hash(ddl)
        }
