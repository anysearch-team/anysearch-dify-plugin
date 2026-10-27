"""有出处的标签规则；目录指纹不匹配时不应用。

依据：https://api.anysearch.com/v1/sub-domains?domain=code
2026-09-22，request_id=68925a58-c4e4-4bec-8f17-5ad4ef853e58。
library 的非空名称要求按开发规格 §7.2/7.3 落地，不推广到其他 required 参数。
"""
from dataclasses import dataclass
from typing import Any, Protocol

from anysearch.errors import AnySearchError


class SemanticRule(Protocol):
    @property
    def version(self) -> str: ...
    def matches(self, capability: dict[str, Any]) -> bool: ...
    def validate(self, body: dict[str, Any]) -> list[str]: ...


@dataclass(frozen=True)
class CodeDocRule:
    version: str = "code.doc.library/1"

    def matches(self, capability: dict[str, Any]) -> bool:
        library = next((p for p in capability["parameters"] if p["name"] == "library"), None)
        return library == {"name": "library", "required": True, "constraints": {},
                           "description": "Library or framework name to query docs for, e.g. react, express"}

    def validate(self, body: dict[str, Any]) -> list[str]:
        library = body.get("params", {}).get("library")
        if not isinstance(library, str) or not library.strip():
            raise AnySearchError("INVALID_INPUT", parameter_names=["library"])
        return []


RULES: dict[str, SemanticRule] = {"code.doc": CodeDocRule()}
