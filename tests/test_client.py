import json
import logging

import httpx
import pytest

from anysearch.client import AnySearchClient
from anysearch.errors import AnySearchError


@pytest.mark.parametrize("status,code", [(401, "AUTHENTICATION_FAILED"), (403, "PERMISSION_DENIED"),
    (402, "QUOTA_EXHAUSTED"), (429, "RATE_LIMITED"), (400, "UPSTREAM_REQUEST_REJECTED"),
    (422, "UPSTREAM_REQUEST_REJECTED"),
    (500, "UPSTREAM_UNAVAILABLE"), (502, "UPSTREAM_UNAVAILABLE"), (503, "UPSTREAM_UNAVAILABLE"),
    (504, "UPSTREAM_UNAVAILABLE"), (302, "UPSTREAM_HTTP_ERROR"), (304, "UPSTREAM_HTTP_ERROR")])
def test_http_errors_are_safe_and_never_retried(status, code, caplog):
    requests = []
    secret = "synthetic-current-key"
    def upstream(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer " + secret
        return httpx.Response(status, text="private-body-sentinel", headers={"Retry-After": "12", "Location": "https://other.invalid/?token=private-token"})
    with caplog.at_level(logging.INFO), pytest.raises(AnySearchError) as error:
        AnySearchClient({"api_key": secret}, transport=httpx.MockTransport(upstream)).search({"query": "private-query-sentinel"})
    assert error.value.error_code == code
    if status == 429:
        assert error.value.fields["retry_after_seconds"] == 12
    assert len(requests) == 1
    for value in (secret, "private-body-sentinel", "private-query-sentinel", "private-token"):
        assert value not in str(error.value) + caplog.text


def test_anonymous_quota_error_never_adopts_returned_credentials(caplog):
    requests = []
    returned_key = "synthetic-returned-key"
    returned_password = "synthetic-returned-password"

    def upstream(request):
        requests.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(402, json={"code": -1, "request_id": "safe-id",
            "message": f"username=generated\npassword={returned_password}\napi_key={returned_key}"})

    with caplog.at_level(logging.INFO), pytest.raises(AnySearchError) as error:
        AnySearchClient({}, transport=httpx.MockTransport(upstream)).search({"query": "test"})

    assert error.value.error_code == "QUOTA_EXHAUSTED"
    assert error.value.fields["http_status"] == 402
    assert len(requests) == 1
    assert returned_key not in str(error.value) + caplog.text
    assert returned_password not in str(error.value) + caplog.text


@pytest.mark.parametrize("envelope,code", [({}, "INVALID_RESPONSE"), ({"code": None}, "INVALID_RESPONSE"),
    ({"code": False}, "INVALID_RESPONSE"), ({"code": "0"}, "INVALID_RESPONSE"),
    ({"code": 1, "message": "quota key body sentinel", "auto_registered": {"api_key": "returned-key"}}, "UPSTREAM_BUSINESS_ERROR"),
    ({"code": 0, "data": None}, "INVALID_RESPONSE")])
def test_envelope_errors_are_not_empty_results(envelope, code):
    with pytest.raises(AnySearchError) as error:
        AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope))).search({"query": "x"})
    assert error.value.error_code == code
    assert "sentinel" not in str(error.value)
    assert "returned-key" not in str(error.value)


@pytest.mark.parametrize("key", [None, "", "   ", "synthetic-key"])
def test_current_credentials_only(key, monkeypatch):
    monkeypatch.setenv("ANYSEARCH_API_KEY", "environment-key")
    def upstream(request):
        assert request.headers.get("authorization") == ("Bearer synthetic-key" if key == "synthetic-key" else None)
        assert request.headers["x-anysearch-client"] == "anysearch-dify-plugin/0.1.0"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(200, json={"code": 0, "data": {"results": []}})
    AnySearchClient({"api_key": key}, transport=httpx.MockTransport(upstream)).search({"query": "x"})


@pytest.mark.parametrize("error_type,code", [(httpx.ConnectError, "NETWORK_ERROR"), (httpx.ReadError, "NETWORK_ERROR"),
    (httpx.ConnectTimeout, "TIMEOUT"), (httpx.ReadTimeout, "TIMEOUT"), (httpx.WriteTimeout, "TIMEOUT"), (httpx.PoolTimeout, "TIMEOUT")])
def test_network_and_timeout_errors_never_retry_or_leak(error_type, code):
    calls = []
    def upstream(request):
        calls.append(request)
        raise error_type("private-url?token=secret", request=request)
    with pytest.raises(AnySearchError) as error:
        AnySearchClient({}, transport=httpx.MockTransport(upstream)).search({"query": "x"})
    assert error.value.error_code == code
    assert "secret" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("header,expected", [("12",12), ("0",0), ("-1",None), ("1.5",None),
    ("invalid",None), ("Wed, 21 Oct 2015 07:28:00 GMT",10), ("Wed, 21 Oct 2015 07:27:00 GMT",None), ("9" * 200,None)])
def test_retry_after_seconds_and_http_date(header, expected):
    client = AnySearchClient({}, wall_clock=lambda: 1445412470,
        transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After":header})))
    with pytest.raises(AnySearchError) as error:
        client.search({"query": "x"})
    assert error.value.fields.get("retry_after_seconds") == expected


@pytest.mark.parametrize("body", [b"not JSON", b"[]", b"null", b'{"code":0}', b'{"code":0,"data":{}}', b'{"code":0,"data":{"results":null}}'])
def test_malformed_success_response_is_explicit_failure(body):
    with pytest.raises(AnySearchError, match="INVALID_RESPONSE"):
        AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))).search({"query": "x"})


@pytest.mark.parametrize("request_id,expected", [(None,""), (3,""), ("r\n-01","r-01"),
    ("x"*129,""), ("https://example.org/?token=secret",""), ("synthetic-key",""), ("safe-01","safe-01")])
def test_request_id_is_sanitized(request_id, expected):
    client = AnySearchClient({"api_key":"synthetic-key"}, transport=httpx.MockTransport(lambda r:
        httpx.Response(200, json={"code":0,"request_id":request_id,"auto_registered":{"api_key":"ignored"},"data":{"results":[]}})))
    assert client.search({"query":"x"})["request_id"] == expected


def test_http_error_preserves_safe_diagnostics_and_ignores_message():
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(403,
        json={"code": 19, "request_id": "safe-id", "message": "private body"})))
    with pytest.raises(AnySearchError) as error:
        client.search({"query":"x"})
    assert error.value.fields["http_status"] == 403
    assert error.value.fields["upstream_code"] == 19
    assert error.value.fields["request_id"] == "safe-id"
    assert "private body" not in str(error.value)


@pytest.mark.parametrize("status,expected", [(401,"AUTHENTICATION_FAILED"), (429,"RATE_LIMITED"), (503,"UPSTREAM_UNAVAILABLE")])
@pytest.mark.parametrize("kind", ["bad-gzip", "oversize", "slow-body"])
def test_bad_error_body_cannot_override_http_classification(status, expected, kind):
    import gevent
    class ErrorStream(httpx.SyncByteStream):
        closed = False
        def __iter__(self):
            if kind == "slow-body":
                gevent.sleep(1)
            yield b"not-gzip" if kind == "bad-gzip" else b"x" * (2 * 1024 * 1024 + 1)
        def close(self):
            self.closed = True
    stream = ErrorStream()
    headers = {"Retry-After":"7"}
    if kind == "bad-gzip":
        headers["Content-Encoding"] = "gzip"
    client = AnySearchClient({}, budget_seconds=0.05,
        transport=httpx.MockTransport(lambda r:httpx.Response(status,stream=stream,headers=headers)))
    with pytest.raises(AnySearchError) as error:
        client.search({"query":"x"})
    assert error.value.error_code == expected
    assert error.value.fields["http_status"] == status
    if status == 429:
        assert error.value.fields["retry_after_seconds"] == 7
    assert stream.closed
