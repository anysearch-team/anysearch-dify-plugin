import gzip
import json
import time

import gevent
import httpx
import pytest

from anysearch.catalog import Catalog
from anysearch.errors import AnySearchError


@pytest.mark.parametrize("status,attempts,cause", [(502,2,"UPSTREAM_UNAVAILABLE"), (503,2,"UPSTREAM_UNAVAILABLE"),
    (504,2,"UPSTREAM_UNAVAILABLE"), (500,1,"UPSTREAM_UNAVAILABLE"), (401,1,"AUTHENTICATION_FAILED"),
    (403,1,"PERMISSION_DENIED"), (429,1,"RATE_LIMITED"), (400,1,"UPSTREAM_REQUEST_REJECTED"),
    (422,1,"UPSTREAM_REQUEST_REJECTED"), (302,1,"UPSTREAM_HTTP_ERROR")])
def test_get_http_retry_matrix_and_redaction(status, attempts, cause, caplog):
    calls = []
    def upstream(request):
        calls.append(time.monotonic())
        assert "authorization" not in request.headers
        return httpx.Response(status, json={"code": 9, "request_id": "safe-id", "message": "private-sentinel",
            "auto_registered": {"api_key": "secret-sentinel"}}, headers={"Retry-After": "7"})
    with pytest.raises(AnySearchError) as caught:
        Catalog(transport=httpx.MockTransport(upstream)).list_domains()
    assert caught.value.fields["cause_code"] == cause
    assert caught.value.fields["http_status"] == status
    assert caught.value.fields["request_id"] == "safe-id"
    if status == 429: assert caught.value.fields["retry_after_seconds"] == 7
    assert len(calls) == attempts
    if attempts == 2: assert calls[1] - calls[0] >= 0.49
    assert "sentinel" not in str(caught.value) + caplog.text


@pytest.mark.parametrize("error,attempts,cause", [(httpx.ConnectError,2,"NETWORK_ERROR"),
    (httpx.ConnectTimeout,2,"TIMEOUT"), (httpx.ReadTimeout,2,"TIMEOUT"), (httpx.WriteTimeout,2,"TIMEOUT"),
    (httpx.PoolTimeout,2,"TIMEOUT"), (httpx.ReadError,1,"NETWORK_ERROR"), (httpx.RemoteProtocolError,1,"NETWORK_ERROR")])
def test_network_retry_matrix(error, attempts, cause):
    calls = []
    def upstream(request):
        calls.append(request)
        raise error("secret-sentinel")
    with pytest.raises(AnySearchError) as caught:
        Catalog(transport=httpx.MockTransport(upstream)).list_domains()
    assert caught.value.fields["cause_code"] == cause
    assert len(calls) == attempts
    assert "sentinel" not in str(caught.value)


@pytest.mark.parametrize("body,cause", [(b'not-json',"INVALID_RESPONSE"), (b'[]',"INVALID_RESPONSE"),
    (b'{"code":false}',"INVALID_RESPONSE"), (b'{"code":"0"}',"INVALID_RESPONSE"),
    (b'{"code":0}',"INVALID_RESPONSE"), (b'{"code":7}',"UPSTREAM_BUSINESS_ERROR"),
    (b'{"code":0,"code":1,"data":{"domains":[]}}',"INVALID_RESPONSE"),
    (b'{"code":0,"data":{"domains":[],"topology_version":NaN}}',"INVALID_RESPONSE"),
    (b'{"code":0,"data":{"domains":null}}',"INVALID_RESPONSE")])
def test_protocol_failures_never_retry(body, cause):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=body)
    with pytest.raises(AnySearchError) as caught:
        Catalog(transport=httpx.MockTransport(upstream)).list_domains()
    assert caught.value.fields["cause_code"] == cause
    assert len(calls) == 1


def test_retry_success_and_deadline_prevents_second_attempt():
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json={"code": 0, "data": {"domains": []}})
    assert Catalog(transport=httpx.MockTransport(upstream)).list_domains()["domains"] == []
    assert len(calls) == 2
    calls.clear()
    with pytest.raises(AnySearchError, match="TIMEOUT"):
        Catalog(transport=httpx.MockTransport(upstream), budget_seconds=0.05).list_domains()
    assert len(calls) == 1


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_catalog_response_limit_and_close(compressed, delta):
    prefix, suffix = b'{"code":0,"data":{"domains":[]},"padding":"', b'"}'
    body = prefix + b'x' * (2*1024*1024 + delta - len(prefix) - len(suffix)) + suffix
    class Stream(httpx.SyncByteStream):
        closed = False
        def __iter__(self):
            yield gzip.compress(body) if compressed else body
        def close(self):
            self.closed = True
    stream = Stream()
    catalog = Catalog(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream,
        headers={"Content-Encoding": "gzip"} if compressed else {})))
    if delta > 0:
        with pytest.raises(AnySearchError, match="RESPONSE_TOO_LARGE"):
            catalog.list_domains()
    else:
        assert catalog.list_domains()["status"] == "ok"
    assert stream.closed


def test_oversize_catalog_fails_without_truncating_descriptions():
    body = {"code": 0, "data": {"domains": [{"domain": "code", "description": "中" * 350000, "sub_domain_count": 0}]}}
    with pytest.raises(AnySearchError, match="OUTPUT_TOO_LARGE"):
        Catalog(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))).list_domains()


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_catalog_exact_utf8_output_boundary(delta):
    from anysearch.output import catalog_payload
    record = {"domains": [{"domain": "code", "description": "中", "sub_domain_count": 0}], "capabilities": [],
              "source": {"scope": "domains", "fetched_at": "2026-09-22T00:00:00+00:00", "cache_hit": False,
                         "source_request_id": "r", "topology_generation": "", "topology_version": ""}}
    base = catalog_payload([record], False, "r")
    size = len(json.dumps(base, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    record["domains"][0]["description"] += "x" * (1024*1024 + delta - size)
    if delta > 0:
        with pytest.raises(AnySearchError, match="OUTPUT_TOO_LARGE"):
            catalog_payload([record], False, "r")
    else:
        result = catalog_payload([record], False, "r")
        assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) == 1024*1024+delta


@pytest.mark.parametrize("mode", ["idle", "slow"])
def test_catalog_real_socket_deadline_closes_connection(mode):
    import subprocess
    import sys
    server = subprocess.Popen([sys.executable, "tests/http_fixture.py", mode], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8")
    with gevent.Timeout(5):
        port = int(server.stdout.readline())
    class LocalTransport(httpx.HTTPTransport):
        def handle_request(self, request):
            request.url = httpx.URL(f"http://127.0.0.1:{port}/catalog")
            return super().handle_request(request)
    try:
        catalog = Catalog(transport=LocalTransport(), budget_seconds=1)
        started = time.monotonic()
        with pytest.raises(AnySearchError, match="TIMEOUT"):
            catalog.list_domains()
        assert time.monotonic() - started < 1.8
        output, error = server.communicate(timeout=2)
        assert server.returncode == 0, error
        assert json.loads(output) == {"accepted": True, "peer_closed": True}
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=2)
