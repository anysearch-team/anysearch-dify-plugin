import json
import re
from typing import Any


MESSAGES = {
    "UNKNOWN_TAG": "Unknown tag. Refresh discover and select an exact catalog tag.",
    "UNKNOWN_PARAMETER": "Unknown parameter. Refresh discover and use exact parameter names.",
    "UNKNOWN_DOMAIN": "Unknown domain. Use discover to list available domains.",
    "CATALOG_UNAVAILABLE": "Cannot load the public catalog. Try refreshing later; for size limits, discover one domain at a time.",
    "INVALID_INPUT": "Check tool inputs and credentials. Text must be valid UTF-8; API keys must be ASCII without control characters. Use discover for current tags and parameter rules; ordinary search requires a nonempty query; vertical query acceptance depends on the upstream capability.",
    "VERTICAL_QUERY_REQUIRED": "Academic and legal vertical searches require a nonempty query, including academic DOI lookup. Ask the user for search terms; the plugin does not invent them.",
    "AUTHENTICATION_FAILED": "Check the current API key; anonymous users may configure a key.",
    "PERMISSION_DENIED": "The service denied access. Check service permissions.",
    "QUOTA_EXHAUSTED": "The AnySearch quota is exhausted. Check your current quota on AnySearch.",
    "RATE_LIMITED": "The service rate limit was reached. Wait before trying again.",
    "UPSTREAM_REQUEST_REJECTED": "The service rejected the request. Check parameters or service limits.",
    "UPSTREAM_UNAVAILABLE": "The service is temporarily unavailable.",
    "UPSTREAM_HTTP_ERROR": "The service returned an unexpected HTTP status.",
    "UPSTREAM_BUSINESS_ERROR": "The service reported a business error. For vertical search, refresh discover before retrying; retrying may consume quota.",
    "TIMEOUT": "Search timed out. The service may already have executed it; retrying may consume quota.",
    "NETWORK_ERROR": "Cannot connect to the search service.",
    "INVALID_RESPONSE": "The service response does not match the search contract.",
    "RESPONSE_TOO_LARGE": "The service response exceeds the 2 MiB limit.",
    "OUTPUT_TOO_LARGE": "Output exceeds the 1 MiB limit. Request fewer results or discover one domain at a time.",
    "INTERNAL_ERROR": "Search could not complete because of an internal error.",
}


def safe_request_id(value: Any, secret: str = "") -> str:
    if not isinstance(value, str):
        return ""
    # 上游元数据不可信：仅接收短标识符，拒绝正文、URL 与凭据。
    value = "".join(c for c in value if ord(c) >= 32 and not 127 <= ord(c) <= 159)
    if secret and secret in value:
        return ""
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) else ""


class AnySearchError(Exception):
    def __init__(self, error_code: str, *, http_status: int | None = None,
                 upstream_code: int | None = None, request_id: str = "",
                 retry_after_seconds: int | None = None, cause_code: str | None = None,
                 parameter_names: list[str] | None = None):
        self.error_code = error_code
        self.fields: dict[str, Any] = {"error_code": error_code, "message": MESSAGES[error_code]}
        if parameter_names:
            names = [name for name in parameter_names[:32] if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)]
            if names:
                self.fields["message"] += " Check parameters: " + ", ".join(names) + "."
        for name, value in (("http_status", http_status), ("upstream_code", upstream_code),
                            ("request_id", safe_request_id(request_id) or None),
                            ("retry_after_seconds", retry_after_seconds), ("cause_code", cause_code)):
            if value is not None:
                self.fields[name] = value
        super().__init__("ANYSEARCH_ERROR " + json.dumps(self.fields, separators=(",", ":")))


class AnySearchToolError(Exception):
    """仅安全文本进入 SDK 异常通道；SDK 仍可能附加调用栈元数据。"""
