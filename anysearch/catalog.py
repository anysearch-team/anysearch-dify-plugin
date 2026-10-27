import copy
import json
import logging
import sys
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx
from gevent import Timeout
from gevent.lock import Semaphore

from anysearch.client import CatalogClient
from anysearch.errors import AnySearchError
from anysearch.semantic_rules import RULES, SemanticRule

logger = logging.getLogger("anysearch")

MAX_CACHE_RECORD_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024


def record_size(scope: str, record: tuple[float, dict[str, Any]]) -> int:
    # 按 Python 对象占用计数；同记录去重，跨记录共享对象重复计费以保守留余量。
    pending: list[Any] = [scope, record]
    seen: set[int] = set()
    size = 0
    while pending:
        value = pending.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        size += sys.getsizeof(value)
        if size > MAX_CACHE_RECORD_BYTES:
            return size
        if isinstance(value, dict):
            for key, item in value.items():
                pending.extend((key, item))
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    return size


class Catalog:
    """仅保存匿名目录；每次读取返回独立副本，来源与数据一同发布。"""

    def __init__(self, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic, budget_seconds: float = 35,
                 rules: dict[str, SemanticRule] | None = None):
        self.transport = transport
        self.clock = clock
        self.budget_seconds = budget_seconds
        self.rules = dict(RULES if rules is None else rules)
        self._records: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._record_sizes: dict[str, int] = {}
        self._cache_bytes = 0
        self._locks: dict[str, tuple[Semaphore, int]] = {}

    def _cache_record(self, scope: str, record: tuple[float, dict[str, Any]]) -> None:
        size = record_size(scope, record)
        # 刷新后的超大记录不保留，也不能让旧版本继续命中。
        self._records.pop(scope, None)
        self._cache_bytes -= self._record_sizes.pop(scope, 0)
        if size > MAX_CACHE_RECORD_BYTES:
            return
        self._records[scope] = record
        self._record_sizes[scope] = size
        self._cache_bytes += size
        while self._records and (len(self._records) > 128 or
                                 self._cache_bytes + sys.getsizeof(self._records) +
                                 sys.getsizeof(self._record_sizes) +
                                 sum(sys.getsizeof(value) for value in self._record_sizes.values()) > MAX_CACHE_BYTES):
            evicted, _ = self._records.popitem(last=False)
            self._cache_bytes -= self._record_sizes.pop(evicted)

    def _load(self, client: CatalogClient, scopes: list[str], refresh: bool,
              confirm_tag: str | None = None) -> list[dict[str, Any]]:
        acquired: list[Semaphore] = []
        reserved: list[str] = []
        try:
            # 引用计数包含等待者；最后一个使用者退出才删除，锁表不随历史领域无界增长。
            for scope in sorted(scopes):
                lock, users = self._locks.get(scope, (Semaphore(), 0))
                self._locks[scope] = (lock, users + 1)
                reserved.append(scope)
                if not lock.acquire(timeout=client.remaining()):
                    raise AnySearchError("TIMEOUT")
                acquired.append(lock)
            client.check_deadline()
            missing = [scope for scope in scopes if refresh or scope not in self._records
                       or self.clock() - self._records[scope][0] >= 300
                       or self._needs_confirmation(self._records[scope][1], confirm_tag)]
            selected = {}
            for scope in scopes:
                if scope not in missing:
                    self._records.move_to_end(scope)
                    selected[scope] = copy.deepcopy(self._records[scope][1])
            if missing:
                data, request_id = client.get_domains() if scopes == [""] else client.get_sub_domains(missing)
                parsed = parse_catalog(data, None if scopes == [""] else missing)
                source = {"fetched_at": datetime.now(timezone.utc).isoformat(),
                          "source_request_id": request_id,
                          "topology_generation": version(data.get("topology_generation")),
                          "topology_version": version(data.get("topology_version"))}
                client.check_deadline()
                for scope, value in parsed.items():
                    # 响应获取、指纹比较、确认发布共用 scope 锁；确认不续旧记录的 TTL。
                    confirmations = {item["tag"]: {"version": self.rules[item["tag"]].version,
                                                  "matches": self.rules[item["tag"]].matches(item)}
                                     for item in value["capabilities"] if item["tag"] in self.rules}
                    record = (self.clock(), {**value, "confirmations": confirmations,
                                            "source": {"scope": scope or "domains", **source}})
                    self._cache_record(scope, record)
                    selected[scope] = copy.deepcopy(record[1])
            for scope in scopes:
                selected[scope]["source"]["cache_hit"] = scope not in missing
            return [selected[scope] for scope in scopes]
        finally:
            for lock in reversed(acquired):
                lock.release()
            for scope in reserved:
                lock, users = self._locks[scope]
                if users == 1:
                    del self._locks[scope]
                else:
                    self._locks[scope] = (lock, users - 1)

    def _needs_confirmation(self, record: dict[str, Any], tag: str | None) -> bool:
        if tag is None or tag not in self.rules:
            return False
        rule = self.rules[tag]
        capability = next((item for item in record["capabilities"] if item["tag"] == tag), None)
        return capability is not None and not rule.matches(capability) and (
            record.get("confirmations", {}).get(tag, {}).get("version") != rule.version)

    def list_domains(self, refresh: bool = False) -> dict[str, Any]:
        return self.discover({"refresh": refresh})

    def resolve_tag(self, body: dict[str, Any], deadline: float) -> tuple[dict[str, Any], dict[str, Any]]:
        tag = body["tag"]
        domain = tag.split(".")[0]
        started = self.clock()
        client = None
        outcome = "INTERNAL_ERROR"
        try:
            with CatalogClient(deadline, clock=self.clock, transport=self.transport) as client:
                total = self._load(client, [""], False)[0]
                if domain not in {row["domain"] for row in total["domains"]}:
                    total = self._load(client, [""], True)[0]
                if domain not in {row["domain"] for row in total["domains"]}:
                    raise AnySearchError("UNKNOWN_DOMAIN")
                for refresh in (False, True):
                    record = self._load(client, [domain], refresh, confirm_tag=tag)[0]
                    capability = next((item for item in record["capabilities"] if item["tag"] == tag), None)
                    if capability is not None and not set(body.get("params", {})) - {p["name"] for p in capability["parameters"]}:
                        outcome = "ok"
                        return capability, {k: v for k, v in record["source"].items() if k != "scope"}
                raise AnySearchError("UNKNOWN_TAG" if capability is None else "UNKNOWN_PARAMETER")
        except AnySearchError as error:
            outcome = error.error_code
            if error.error_code in ("UNKNOWN_DOMAIN", "UNKNOWN_TAG", "UNKNOWN_PARAMETER"):
                raise
            raise AnySearchError("CATALOG_UNAVAILABLE", cause_code=error.error_code,
                                 **{k: v for k, v in error.fields.items() if k in
                                    ("http_status", "upstream_code", "request_id", "retry_after_seconds")}) from None
        except Exception:
            raise AnySearchError("CATALOG_UNAVAILABLE", cause_code="INTERNAL_ERROR") from None
        finally:
            logger.info(json.dumps({"tool": "vertical_search", "path": client.last_path if client else "",
                                    "elapsed_ms": round((self.clock() - started) * 1000),
                                    "http_status": client.last_status if client else None,
                                    "error_code": outcome, "request_id": client.last_request_id if client else "",
                                    "http_calls": client.http_calls if client else 0,
                                    "cache_hit": client is not None and client.http_calls == 0}, separators=(",", ":")))

    def get_capabilities(self, domains: list[str], refresh: bool = False) -> dict[str, Any]:
        return self.discover({"domains": ",".join(domains), "refresh": refresh})

    def discover(self, parameters: dict[str, Any]) -> dict[str, Any]:
        from anysearch.output import catalog_payload
        from anysearch.validation import discover_request

        domains, refresh = discover_request(parameters)
        started = self.clock()
        deadline = started + self.budget_seconds
        client = None
        outcome = "INTERNAL_ERROR"
        timer = Timeout(self.budget_seconds, AnySearchError("TIMEOUT"))
        timer.start()
        try:
            with CatalogClient(deadline, clock=self.clock, transport=self.transport) as client:
                total = self._load(client, [""], refresh)[0]
                records = [total]
                if domains:
                    known = {item["domain"] for item in total["domains"]}
                    if any(domain not in known for domain in domains) and not refresh:
                        total = self._load(client, [""], True)[0]
                        known = {item["domain"] for item in total["domains"]}
                    if any(domain not in known for domain in domains):
                        raise AnySearchError("UNKNOWN_DOMAIN")
                    records = [total, *self._load(client, domains, refresh)]
                payload = catalog_payload(records, bool(domains), client.last_request_id)
                client.check_deadline()
                outcome = "ok"
                return payload
        except AnySearchError as error:
            outcome = error.error_code
            if error.error_code in ("UNKNOWN_DOMAIN", "OUTPUT_TOO_LARGE"):
                raise
            raise AnySearchError("CATALOG_UNAVAILABLE", cause_code=error.error_code,
                                 **{k: v for k, v in error.fields.items() if k in
                                    ("http_status", "upstream_code", "request_id", "retry_after_seconds")}) from None
        except Exception:
            raise AnySearchError("CATALOG_UNAVAILABLE", cause_code="INTERNAL_ERROR") from None
        finally:
            timer.cancel()
            logger.info(json.dumps({"tool": "discover", "path": client.last_path if client else "",
                                    "elapsed_ms": round((self.clock() - started) * 1000),
                                    "http_status": client.last_status if client else None,
                                    "error_code": outcome, "request_id": client.last_request_id if client else "",
                                    "http_calls": client.http_calls if client else 0,
                                    "cache_hit": client is not None and client.http_calls == 0}, separators=(",", ":")))


def version(value: Any) -> str:
    if value is None:
        return ""
    if type(value) not in (str, int):
        raise AnySearchError("INVALID_RESPONSE")
    return str(value)


def validate_constraints(parameter: dict[str, Any]) -> None:
    if "type" in parameter:
        declared = parameter["type"]
        kinds = declared if isinstance(declared, list) else [declared]
        if not kinds or any(not isinstance(kind, str) or kind not in CONSTRAINT_TYPES for kind in kinds) \
                or len(set(kinds)) != len(kinds):
            raise AnySearchError("INVALID_RESPONSE")
    if "enum" in parameter and not isinstance(parameter["enum"], list):
        raise AnySearchError("INVALID_RESPONSE")
    if "nullable" in parameter and type(parameter["nullable"]) is not bool:
        raise AnySearchError("INVALID_RESPONSE")


def parse_catalog(data: dict[str, Any], requested: list[str] | None) -> dict[str, dict[str, Any]]:
    rows = data.get("domains")
    if not isinstance(rows, list):
        raise AnySearchError("INVALID_RESPONSE")
    domains = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("domain"), str) or not row["domain"] \
                or not isinstance(row.get("description"), str) or row["domain"] in seen:
            raise AnySearchError("INVALID_RESPONSE")
        seen.add(row["domain"])
        count = row.get("sub_domain_count")
        if requested is None and (type(count) is not int or count < 0):
            raise AnySearchError("INVALID_RESPONSE")
        domains.append({"domain": row["domain"], "description": row["description"], "sub_domain_count": count})
    if requested is None:
        return {"": {"domains": domains, "capabilities": []}}
    if seen != set(requested):
        raise AnySearchError("INVALID_RESPONSE")
    parsed = {}
    for row, domain in zip(rows, domains):
        raw = row.get("sub_domains")
        if not isinstance(raw, list):
            raise AnySearchError("INVALID_RESPONSE")
        capabilities = []
        tags = set()
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("sub_domain"), str) \
                    or not item["sub_domain"].startswith(row["domain"] + ".") \
                    or not item["sub_domain"].split(".")[-1] \
                    or item["sub_domain"] in tags or not isinstance(item.get("description"), str) \
                    or ("params" in item and not isinstance(item["params"], dict)):
                raise AnySearchError("INVALID_RESPONSE")
            tags.add(item["sub_domain"])
            # 实时公开目录省略无附加参数标签的 params（2026-09-22）。
            params = item.get("params", {})
            for name, param in params.items():
                if not name or not isinstance(param, dict) or not isinstance(param.get("description"), str) \
                        or type(param.get("required")) is not bool \
                        or ("sort_order" in param and type(param["sort_order"]) is not int):
                    raise AnySearchError("INVALID_RESPONSE")
                validate_constraints(param)
            parameters = [{"name": name, "description": params[name]["description"],
                           "required": params[name]["required"], "constraints": {
                               key: value for key, value in params[name].items() if key in CONSTRAINT_KEYS}}
                          for name in sorted(params, key=lambda name: ("sort_order" not in params[name],
                                                                      params[name].get("sort_order", 0), name))]
            capabilities.append({"domain": row["domain"], "tag": item["sub_domain"],
                                 "description": item["description"], "parameters": parameters})
        domain["sub_domain_count"] = len(raw)
        parsed[row["domain"]] = {"domains": [domain], "capabilities": sorted(capabilities, key=lambda c: c["tag"])}
    return parsed


CONSTRAINT_TYPES = frozenset({"string", "integer", "number", "boolean", "null", "object", "array"})

CONSTRAINT_KEYS = frozenset({"type", "enum", "const", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                             "multipleOf", "minLength", "maxLength", "pattern", "format", "items", "minItems", "maxItems",
                             "uniqueItems", "properties", "additionalProperties", "minProperties", "maxProperties",
                             "allOf", "anyOf", "oneOf", "not", "nullable", "if", "then", "else",
                             "patternProperties", "propertyNames", "dependentRequired", "dependentSchemas", "dependencies",
                             "contains", "minContains", "maxContains", "prefixItems", "additionalItems",
                             "unevaluatedItems", "unevaluatedProperties", "$ref", "$defs", "definitions",
                             "$id", "$schema", "$anchor", "$dynamicRef", "$dynamicAnchor",
                             "$recursiveRef", "$recursiveAnchor", "contentEncoding", "contentMediaType", "contentSchema"})


catalog = Catalog()
