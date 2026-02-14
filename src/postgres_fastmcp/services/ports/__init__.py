"""Application ports (Protocols) for SQL execution, templating, param replacement, extensions."""

from postgres_fastmcp.services.ports.executor import QueryExecutorPort, QueryTemplatePort
from postgres_fastmcp.services.ports.extensions import ExtensionInspectorPort, ExtensionStatus
from postgres_fastmcp.services.ports.param_replacer import ParamReplacerPort


__all__ = [
    "ExtensionInspectorPort",
    "ExtensionStatus",
    "ParamReplacerPort",
    "QueryExecutorPort",
    "QueryTemplatePort",
]
