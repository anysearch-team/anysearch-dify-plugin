import json
import logging
import math
import time
from collections.abc import Callable
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import gevent
from gevent import Timeout

from anysearch.errors import AnySearchError, safe_request_id
from anysearch.output import search_payload, extract_payload
from anysearch.response import read_response
from anysearch.validation import credential_key, search_request, vertical_request, validate_capability, extract_request

logger = logging.getLogger("anysearch")


def response_json(raw: bytes) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError("Invalid JSON constant")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    result = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object)
    # 在任何数据使用或缓存发布前拒绝指数溢出和无法编码的孤立 surrogate。
    json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return result


class CatalogClient:
    """单次工具调用的匿名 GET 生命周期，不进入共享缓存。"""

    def __init__(self, deadline: float, *, clock: Callable[[], float] = time.monotonic,
                 transport: httpx.BaseTransport | None = None):
        self.deadline = deadline
        self.clock = clock
        self.last_request_id = ""
        self.http_calls = 0
        self.last_status: int | None = None
        self.last_path = ""
        self.client = httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                                  timeout=httpx.Timeout(connect=5, pool=5, write=10, read=10),
                                  headers={"Accept": "application/json", "Accept-Encoding": "gzip, deflate",
                                           "X-Anysearch-Client": "anysearch-dify-plugin/0.1.0"})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def remaining(self) -> float:
        self.check_deadline()
        return self.deadline - self.clock()

    def check_deadline(self) -> None:
        if self.clock() >= self.deadline:
            raise AnySearchError("TIMEOUT")

    def get_domains(self) -> tuple[dict[str, Any], str]:
        return self._get("/v1/domains", [])

    def get_sub_domains(self, domains: list[str]) -> tuple[dict[str, Any], str]:
        return self._get("/v1/sub-domains", [("domain", domain) for domain in domains])

    def _get(self, path: str, params: list[tuple[str, str]]) -> tuple[dict[str, Any], str]:
        for attempt in range(2):
            self.check_deadline()
            retry = False
            try:
                self.http_calls += 1
                self.last_path = path
                with self.client.stream("GET", "https://api.anysearch.com" + path, params=tuple(params)) as response:
                    status = response.status_code
                    self.last_status = status
                    failure = http_error(status) if not 200 <= status < 300 else None
                    envelope = None
                    try:
                        envelope = response_json(read_response(response, self.check_deadline))
                    except (AnySearchError, httpx.RequestError):
                        if failure is None:
                            raise
                    except (ValueError, UnicodeError, RecursionError):
                        pass
                    request_id = safe_request_id(envelope.get("request_id")) if isinstance(envelope, dict) else ""
                    self.last_request_id = request_id
                    if failure:
                        retry = status in (502, 503, 504)
                        code = envelope.get("code") if isinstance(envelope, dict) else None
                        raise AnySearchError(failure, http_status=status, request_id=request_id,
                                             upstream_code=code if type(code) is int else None,
                                             retry_after_seconds=retry_after(response.headers.get("retry-after"), time.time()) if status == 429 else None)
                    if not isinstance(envelope, dict) or type(envelope.get("code")) is not int:
                        raise AnySearchError("INVALID_RESPONSE", http_status=status, request_id=request_id)
                    if envelope["code"] != 0:
                        raise AnySearchError("UPSTREAM_BUSINESS_ERROR", http_status=status,
                                             upstream_code=envelope["code"], request_id=request_id)
                    if not isinstance(envelope.get("data"), dict):
                        raise AnySearchError("INVALID_RESPONSE", request_id=request_id)
                    self.check_deadline()
                    return envelope["data"], request_id
            except httpx.TimeoutException:
                error = AnySearchError("TIMEOUT")
                retry = True
            except httpx.ConnectError:
                error = AnySearchError("NETWORK_ERROR")
                retry = True
            except httpx.RequestError:
                error = AnySearchError("NETWORK_ERROR")
            except AnySearchError as caught:
                error = caught
            if not retry or attempt == 1:
                raise error from None
            if self.remaining() <= 0.5:
                raise AnySearchError("TIMEOUT") from None
            gevent.sleep(0.5)
            self.check_deadline()
        raise AnySearchError("INTERNAL_ERROR")


def retry_after(value: str | None, now: float) -> int | None:
    if not value or len(value) > 128:
        return None
    if value.isascii() and value.isdigit():
        return int(value) if len(value) <= 10 else None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        seconds = math.ceil(date.timestamp() - now)
        return seconds if seconds >= 0 else None
    except (ValueError, OverflowError, TypeError):
        return None


def http_error(status: int) -> str:
    if status in (401, 402, 403, 429):
        return {401: "AUTHENTICATION_FAILED", 402: "QUOTA_EXHAUSTED",
                403: "PERMISSION_DENIED", 429: "RATE_LIMITED"}[status]
    if 400 <= status < 500:
        return "UPSTREAM_REQUEST_REJECTED"
    if 500 <= status < 600:
        return "UPSTREAM_UNAVAILABLE"
    return "UPSTREAM_HTTP_ERROR"


class AnySearchClient:
    def __init__(self, credentials: dict[str, Any], *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic, wall_clock: Callable[[], float] = time.time,
                 budget_seconds: float | None = None):
        self.credentials = credentials
        self.transport = transport
        self.clock = clock
        self.wall_clock = wall_clock
        self.budget_seconds = budget_seconds

    def search(self, parameters: dict[str, Any]) -> dict[str, Any]:
        return self._post(parameters)

    def extract(self, parameters: dict[str, Any]) -> dict[str, Any]:
        return self._post(parameters, extracting=True)

    def vertical_search(self, parameters: dict[str, Any], *, catalog=None) -> dict[str, Any]:
        if catalog is None:
            from anysearch.catalog import catalog as shared_catalog
            catalog = shared_catalog
        return self._post(parameters, catalog=catalog)

    def _post(self, parameters: dict[str, Any], *, catalog=None, extracting: bool = False) -> dict[str, Any]:
        started = self.clock()
        tool = "extract" if extracting else "vertical_search" if catalog is not None else "search"
        path = "/v1/extract" if extracting else "/v1/search"
        budget = self.budget_seconds if self.budget_seconds is not None else {"extract": 55, "vertical_search": 75, "search": 40}[tool]
        deadline = started + budget

        def check_deadline() -> None:
            if self.clock() >= deadline:
                raise AnySearchError("TIMEOUT")

        timer = Timeout(budget, AnySearchError("TIMEOUT"))
        timer.start()
        calls = 0
        status = None
        request_id = ""
        outcome = "INTERNAL_ERROR"
        try:
            max_chars = 50000
            if extracting:
                body, max_chars = extract_request(parameters)
            else:
                body = vertical_request(parameters) if catalog is not None else search_request(parameters)
            key = credential_key(self.credentials)
            source = {}
            semantic_warnings: list[str] = []
            if catalog is not None:
                capability, source = catalog.resolve_tag(body, deadline)
                rule = catalog.rules.get(body["tag"])
                if rule is not None and not rule.matches(capability):
                    rule = None
                validate_capability(body, capability)
                if rule is not None:
                    semantic_warnings = rule.validate(body)
            headers = {"Accept": "application/json", "Accept-Encoding": "gzip, deflate", "X-Anysearch-Client": "anysearch-dify-plugin/0.1.0"}
            if key:
                headers["Authorization"] = "Bearer " + key
            with httpx.Client(transport=self.transport, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(connect=5, pool=5, write=10, read=45 if extracting else 30)) as client:
                check_deadline()
                calls += 1
                with client.stream("POST", "https://api.anysearch.com" + path, json=body, headers=headers) as response:
                    status = response.status_code
                    failure_kind = http_error(status) if not 200 <= status < 300 else None
                    envelope = None
                    try:
                        raw = read_response(response, check_deadline)
                        envelope = response_json(raw)
                        check_deadline()
                    except (AnySearchError, httpx.RequestError):
                        if failure_kind is None:
                            raise
                    except (ValueError, UnicodeError, RecursionError):
                        pass
                    if isinstance(envelope, dict):
                        request_id = safe_request_id(envelope.get("request_id"), key)
                    if failure_kind is not None:
                        upstream_code = envelope.get("code") if isinstance(envelope, dict) else None
                        raise AnySearchError(failure_kind, http_status=status, request_id=request_id,
                                             upstream_code=upstream_code if type(upstream_code) is int else None,
                                             retry_after_seconds=retry_after(response.headers.get("retry-after"), self.wall_clock()) if status == 429 else None)
            if not isinstance(envelope, dict) or type(envelope.get("code")) is not int:
                raise AnySearchError("INVALID_RESPONSE", http_status=status)
            request_id = safe_request_id(envelope.get("request_id"), key)
            if envelope["code"] != 0:
                raise AnySearchError("UPSTREAM_BUSINESS_ERROR", http_status=status,
                                     upstream_code=envelope["code"], request_id=request_id)
            data = envelope.get("data")
            if not isinstance(data, dict):
                raise AnySearchError("INVALID_RESPONSE", http_status=status, request_id=request_id)
            result = (extract_payload(body["url"], data, request_id, max_chars) if extracting else
                      search_payload(body, data, request_id, catalog=source, semantic_warnings=semantic_warnings))
            check_deadline()
            outcome = "ok"
            return result
        except AnySearchError as error:
            outcome = error.error_code
            raise
        except httpx.TimeoutException:
            outcome = "TIMEOUT"
            raise AnySearchError(outcome) from None
        except httpx.RequestError:
            outcome = "NETWORK_ERROR"
            raise AnySearchError(outcome) from None
        except Exception:
            raise AnySearchError("INTERNAL_ERROR") from None
        finally:
            timer.cancel()
            logger.info(json.dumps({"tool": tool, "path": path, "elapsed_ms": round((self.clock() - started) * 1000),
                                    "http_status": status, "error_code": outcome, "request_id": request_id,
                                    "http_calls": calls, "cache_hit": False}, separators=(",", ":")))
