"""输入编码错误必须在网络请求前安全拒绝。"""
import httpx
import pytest

from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from anysearch.client import AnySearchClient
from anysearch.catalog import Catalog
from anysearch.errors import AnySearchError
from provider.anysearch import AnysearchProvider


@pytest.mark.parametrize("key", ["synthetic-全角-key", "synthetic-é-key", "synthetic-\ud800-key"])
def test_provider_rejects_non_ascii_key_without_network(key, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Credential validation must not access the network")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    with pytest.raises(ToolProviderCredentialValidationError, match="ASCII") as error:
        AnysearchProvider().validate_credentials({"api_key": key})
    assert "synthetic" not in str(error.value)


@pytest.mark.parametrize("key", ["synthetic-全角-key", "synthetic-é-key", "synthetic-\ud800-key"])
def test_runtime_rejects_non_ascii_key_before_http(key):
    calls = []
    client = AnySearchClient({"api_key": key}, transport=httpx.MockTransport(lambda r: calls.append(r)))
    with pytest.raises(AnySearchError) as error:
        client.search({"query": "topic"})
    assert error.value.error_code == "INVALID_INPUT"
    assert "ASCII" in str(error.value)
    assert "synthetic" not in str(error.value)
    assert calls == []


@pytest.mark.parametrize("method,parameters", [
    ("search", {"query": "\ud800"}),
    ("search", {"query": "topic", "language": "\udfff"}),
    ("search", {"query": "topic", "zone": "\ud800"}),
    ("extract", {"url": "https://example.org/\ud800"}),
    ("vertical_search", {"query": "\ud800", "tag": "code.doc"}),
    ("vertical_search", {"query": "topic", "tag": "code.\udfff"}),
])
def test_surrogate_input_is_invalid_before_any_http(method, parameters, monkeypatch, caplog):
    calls = []
    def forbidden(self, request):
        calls.append(request)
        pytest.fail("Invalid input must not access the network")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    with caplog.at_level("INFO", logger="anysearch"):
        with pytest.raises(AnySearchError) as error:
            getattr(AnySearchClient({}), method)(parameters)
    assert error.value.error_code == "INVALID_INPUT"
    assert "UTF-8" in str(error.value)
    assert calls == []
    assert all('"http_calls":0' in record.message for record in caplog.records)


def test_discover_rejects_surrogate_before_http():
    calls = []
    catalog = Catalog(transport=httpx.MockTransport(lambda r: calls.append(r)))
    with pytest.raises(AnySearchError) as error:
        catalog.discover({"domains": "\ud800"})
    assert error.value.error_code == "INVALID_INPUT"
    assert calls == []


def test_valid_unicode_text_and_ascii_key_are_preserved():
    def upstream(request):
        import json
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        assert json.loads(request.content) == {"query": "中文 😀", "language": "中文", "max_results": 5}
        return httpx.Response(200, json={"code": 0, "data": {"results": []}})
    AnysearchProvider().validate_credentials({"api_key": " synthetic-key "})
    payload = AnySearchClient({"api_key": " synthetic-key "}, transport=httpx.MockTransport(upstream)).search(
        {"query": "中文 😀", "language": "中文"})
    assert payload["status"] == "ok"


def test_valid_unicode_url_is_preserved():
    def upstream(request):
        import json
        assert json.loads(request.content) == {"url": "https://example.org/中文/😀"}
        return httpx.Response(200, json={"code": 0, "data": {"content": "正文"}})
    payload = AnySearchClient({}, transport=httpx.MockTransport(upstream)).extract(
        {"url": "https://example.org/中文/😀"})
    assert payload["status"] == "ok"
