"""Compatibility import; shared v3 workflow owns the target DDL."""

from v3_workflow.persistence.schema import DDL, initialize_schema

__all__ = ("DDL", "initialize_schema")
