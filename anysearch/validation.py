import math
import json
import re
import ipaddress
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from anysearch.errors import AnySearchError


def has_control(value: str) -> bool:
    return any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)


def validate_utf8(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise AnySearchError("INVALID_INPUT") from None


def credential_key(credentials: dict[str, Any]) -> str:
    value = credentials.get("api_key")
    if value is None:
        return ""
    if not isinstance(value, str) or not value.isascii() or has_control(value):
        raise AnySearchError("INVALID_INPUT")
    return value.strip()


def extract_request(parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    if set(parameters) - {"url", "max_chars"}:
        raise AnySearchError("INVALID_INPUT")
    value = parameters.get("url")
    if not isinstance(value, str) or has_control(value) or not 1 <= len(value.strip()) <= 8192:
        raise AnySearchError("INVALID_INPUT")
    validate_utf8(value)
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None:
            raise ValueError()
        if parts.port is not None and parts.port == 0:
            raise ValueError()
        host = parts.hostname
        if '%' in host or '\\' in value or any(c.isspace() for c in value.strip()):
            raise ValueError()
        normalized = host.encode('idna').decode('ascii').lower().rstrip('.')
        if normalized == 'localhost' or normalized.endswith(('.localhost', '.local')) or normalized == 'local':
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # 数字末段可能被浏览器/上游按缩写、八进制或十六进制 IPv4 解释。
            last = normalized.rsplit('.', 1)[-1]
            if re.fullmatch(r'[0-9]+|0x[0-9a-f]+', last):
                raise ValueError()
            if len(normalized) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                            for label in normalized.split('.')):
                raise ValueError()
        else:
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if not address.is_global or address.is_multicast or address.is_reserved:
                raise ValueError()
        url = urlunsplit(parts._replace(fragment=""))
    except (ValueError, UnicodeError):
        raise AnySearchError("INVALID_INPUT") from None
    count = parameters.get("max_chars", 50000)
    if isinstance(count, str):
        if not re.fullmatch(r"[+-]?[0-9]+", count):
            raise AnySearchError("INVALID_INPUT")
        digits = count.lstrip("+-").lstrip("0") or "0"
        if len(digits) > 5:
            raise AnySearchError("INVALID_INPUT")
        count = int(digits) * (-1 if count.startswith("-") else 1)
    if isinstance(count, bool) or not isinstance(count, (int, float)) or not 1 <= count <= 50000 or count != int(count):
        raise AnySearchError("INVALID_INPUT")
    body = {"url": url}
    if len(json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 16384:
        raise AnySearchError("INVALID_INPUT")
    return body, int(count)


def search_request(parameters: dict[str, Any], *, allow_empty_query: bool = False) -> dict[str, Any]:
    if set(parameters) - {"query", "max_results", "zone", "language"}:
        raise AnySearchError("INVALID_INPUT")
    query = parameters.get("query")
    if allow_empty_query and query is None:
        query = ""
    if not isinstance(query, str) or not (0 if allow_empty_query else 1) <= len(query.strip()) <= 8000:
        raise AnySearchError("INVALID_INPUT")
    validate_utf8(query)
    count = parameters.get("max_results", 5)
    if isinstance(count, str):
        if not re.fullmatch(r"[+-]?[0-9]+", count):
            raise AnySearchError("INVALID_INPUT")
        negative = count.startswith("-")
        digits = count.lstrip("+-").lstrip("0") or "0"
        if len(digits) > 2:
            raise AnySearchError("INVALID_INPUT")
        count = int(digits) * (-1 if negative else 1)
    if isinstance(count, bool) or not isinstance(count, (int, float)):
        raise AnySearchError("INVALID_INPUT")
    if not 1 <= count <= 10 or not math.isfinite(count) or count != int(count):
        raise AnySearchError("INVALID_INPUT")
    body: dict[str, Any] = {"query": query.strip(), "max_results": int(count)}
    for name in ("zone", "language"):
        value = parameters.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or has_control(value):
            raise AnySearchError("INVALID_INPUT")
        validate_utf8(value)
        value = value.strip()
        if not value:
            continue
        if name == "zone":
            if value not in ("auto", "cn", "intl"):
                raise AnySearchError("INVALID_INPUT")
            if value == "auto":
                continue
        elif len(value) > 64:
            raise AnySearchError("INVALID_INPUT")
        body[name] = value
    return body


def discover_request(parameters: dict[str, Any]) -> tuple[list[str], bool]:
    if set(parameters) - {"domains", "refresh"}:
        raise AnySearchError("INVALID_INPUT")
    refresh = parameters.get("refresh", False)
    if type(refresh) is not bool:
        raise AnySearchError("INVALID_INPUT")
    raw = parameters.get("domains")
    if raw is None:
        return [], refresh
    if not isinstance(raw, str):
        raise AnySearchError("INVALID_INPUT")
    validate_utf8(raw)
    if not raw.strip():
        return [], refresh
    domains = list(dict.fromkeys(item.strip() for item in raw.split(",")))
    if not 1 <= len(domains) <= 5 or any(not item for item in domains):
        raise AnySearchError("INVALID_INPUT")
    return domains, refresh


def vertical_request(parameters: dict[str, Any]) -> dict[str, Any]:
    from anysearch.client import response_json

    if set(parameters) - {"query", "tag", "params_json", "max_results", "zone", "language"}:
        raise AnySearchError("INVALID_INPUT")
    tag = parameters.get("tag")
    if not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 128:
        raise AnySearchError("INVALID_INPUT")
    validate_utf8(tag)
    tag = tag.strip()
    if "." not in tag or any(not part or part.strip() != part for part in tag.split(".")) or has_control(tag):
        raise AnySearchError("INVALID_INPUT")
    raw = parameters.get("params_json")
    if raw is None:
        raw = "{}"
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16384:
            raise ValueError()
        if not raw.strip():
            raw = "{}"
        params = response_json(raw.encode("utf-8"))
        if not isinstance(params, dict):
            raise ValueError()
    except (ValueError, UnicodeError, RecursionError):
        raise AnySearchError("INVALID_INPUT") from None
    base = {name: value for name, value in parameters.items() if name not in ("tag", "params_json")}
    body = search_request(base, allow_empty_query=True)
    if tag.split(".", 1)[0] in ("academic", "legal") and not body["query"]:
        raise AnySearchError("VERTICAL_QUERY_REQUIRED")
    body["tag"] = tag
    if params:
        body["params"] = params
    return body


def json_equal(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def validate_capability(body: dict[str, Any], capability: dict[str, Any]) -> None:
    params = body.get("params", {})
    for parameter in capability["parameters"]:
        name = parameter["name"]
        if parameter["required"] and name not in params:
            raise AnySearchError("INVALID_INPUT", parameter_names=[name])
        if name not in params:
            continue
        value = params[name]
        constraints = parameter["constraints"]
        declared = constraints.get("type")
        kinds = declared if isinstance(declared, list) else [declared]
        if value is None and "null" not in kinds and constraints.get("nullable") is not True:
            raise AnySearchError("INVALID_INPUT")
        if isinstance(value, (dict, list)) and ("object" if isinstance(value, dict) else "array") not in kinds:
            raise AnySearchError("INVALID_INPUT")
        if declared:
            matches = {"string": isinstance(value, str), "integer": type(value) is int or isinstance(value, float) and value.is_integer(),
                       "number": type(value) in (int, float), "boolean": type(value) is bool,
                       "null": value is None, "object": isinstance(value, dict), "array": isinstance(value, list)}
            if not any(matches.get(kind, False) for kind in kinds) and not (value is None and constraints.get("nullable") is True):
                raise AnySearchError("INVALID_INPUT")
        if "enum" in constraints and not any(json_equal(value, item) for item in constraints["enum"]):
            raise AnySearchError("INVALID_INPUT")
