"""Прикладные протоколы для выполнения SQL, шаблонизации, замены параметров, расширений."""

from postgres_fastmcp.services.protocols.executor import QueryExecutorPort, QueryTemplatePort
from postgres_fastmcp.services.protocols.extensions import ExtensionInspectorPort, ExtensionStatus
from postgres_fastmcp.services.protocols.param_replacer import ParamReplacerPort


__all__ = [
    "ExtensionInspectorPort",
    "ExtensionStatus",
    "ParamReplacerPort",
    "QueryExecutorPort",
    "QueryTemplatePort",
]
