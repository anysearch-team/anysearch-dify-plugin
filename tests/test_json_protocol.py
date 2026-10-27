"""异常上游 JSON 的失败、缓存恢复及真实工具消息边界。"""
import json

import httpx
import pytest

from anysearch.catalog import Catalog
from anysearch.errors import AnySearchError, AnySearchToolError
from tools.extract import ExtractTool
from tools.search import SearchTool
from tools.vertical_search import VerticalSearchTool


BAD_VALUES = ['NaN', 'Infinity', '-Infinity', '1e999', '-1e999', '"\\ud800"', '"\\udfff"']


def catalog_response(request, marker="null"):
    rows = [{"domain": name, "description": name, "sub_domain_count": 1}
            for name in (request.url.params.get_list("domain") or ["code", "custom"])]
    if request.url.path == "/v1/sub-domains":
        for row in rows:
            row["sub_domains"] = [{"sub_domain": row["domain"] + ".custom", "description": "Custom",
                                   "params": {"value": {"description": "Value", "required": False,
                                                        "enum": ["marker"]}}}]
    body = json.dumps({"code": 0, "data": {"domains": rows}})
    # 只损坏批次最后一个参数；同时检测此前正常记录没有部分发布。
    before, separator, after = body.rpartition('"marker"')
    if separator:
        body = before + marker + after
    return httpx.Response(200, content=body.encode())


@pytest.mark.parametrize("tool,parameters,data", [
    (SearchTool, {"query": "topic"}, '{"results":[]}'),
    (VerticalSearchTool, {"query": "topic", "tag": "code.custom"}, '{"results":[]}'),
    (ExtractTool, {"url": "https://example.org/"}, '{"content":"text"}'),
])
@pytest.mark.parametrize("fragment", [*(f'"padding":{value},' for value in BAD_VALUES),
                                      '"code":9,', '"padding":{"key":0,"key":1},',
                                      '"\\ud800":0,'])
def test_post_rejects_invalid_json_without_success_messages(monkeypatch, tool, parameters, data, fragment):
    calls = []

    def upstream(self, request):
        calls.append(request)
        if request.method == "GET":
            return catalog_response(request)
        return httpx.Response(200, content=('{'+fragment+'"code":0,"data":'+data+'}').encode())

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", upstream)
    monkeypatch.setattr("anysearch.catalog.catalog", Catalog())
    observed = []
    with pytest.raises(AnySearchToolError, match="INVALID_RESPONSE"):
        for message in tool.from_credentials({}).invoke(parameters):
            observed.append(message)
    assert observed == []
    assert sum(request.method == "POST" for request in calls) == 1


@pytest.mark.parametrize("marker", BAD_VALUES)
@pytest.mark.parametrize("warm", [False, True])
def test_bad_catalog_batch_never_publishes_and_recovers(marker, warm):
    calls = []
    bad = [False]

    def upstream(request):
        calls.append(request)
        return catalog_response(request, marker if bad[0] else "null")

    catalog = Catalog(transport=httpx.MockTransport(upstream))
    if warm:
        catalog.get_capabilities(["code", "custom"])
    original = dict(catalog._records)
    bad[0] = True
    with pytest.raises(AnySearchError) as caught:
        catalog.get_capabilities(["code", "custom"], refresh=warm)
    assert caught.value.error_code == "CATALOG_UNAVAILABLE"
    assert caught.value.fields["cause_code"] == "INVALID_RESPONSE"
    assert {key: value for key, value in catalog._records.items() if key} == {
        key: value for key, value in original.items() if key}
    bad[0] = False
    calls.clear()
    recovered = catalog.get_capabilities(["code", "custom"])
    assert recovered["status"] == "ok"
    assert [request.url.path for request in calls] == ([] if warm else ["/v1/sub-domains"])


@pytest.mark.parametrize("marker", ['"\\ud800"', '1e999'])
def test_bad_discover_then_vertical_never_posts_and_recovery_fetches(monkeypatch, marker):
    from tools.discover import DiscoverTool

    calls = []
    bad = [True]

    def upstream(self, request):
        calls.append(request)
        assert request.method == "GET"
        return catalog_response(request, marker if bad[0] else "null")

    catalog = Catalog()
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", upstream)
    monkeypatch.setattr("tools.discover.catalog", catalog)
    monkeypatch.setattr("anysearch.catalog.catalog", catalog)
    for tool, parameters in [(DiscoverTool, {"domains": "code"}),
                             (VerticalSearchTool, {"query": "topic", "tag": "code.custom"})]:
        observed = []
        with pytest.raises(AnySearchToolError, match="CATALOG_UNAVAILABLE"):
            for message in tool.from_credentials({}).invoke(parameters):
                observed.append(message)
        assert observed == []
    assert [request.url.path for request in calls] == ["/v1/domains", "/v1/sub-domains", "/v1/sub-domains"]
    bad[0] = False
    calls.clear()
    assert catalog.get_capabilities(["code"])["status"] == "ok"
    assert [request.url.path for request in calls] == ["/v1/sub-domains"]


@pytest.mark.parametrize("status,code", [(401, "AUTHENTICATION_FAILED"), (429, "RATE_LIMITED"),
                                        (503, "UPSTREAM_UNAVAILABLE")])
def test_malformed_error_json_preserves_http_classification(status, code):
    from anysearch.client import AnySearchClient

    client = AnySearchClient({}, transport=httpx.MockTransport(lambda request: httpx.Response(
        status, content=b'{"code":0,"padding":1e999}', headers={"Retry-After": "7"})))
    with pytest.raises(AnySearchError) as caught:
        client.search({"query": "topic"})
    assert caught.value.error_code == code
    assert caught.value.fields["http_status"] == status
    if status == 429:
        assert caught.value.fields["retry_after_seconds"] == 7


def test_valid_unicode_and_finite_numbers_remain_accepted():
    from anysearch.client import AnySearchClient

    client = AnySearchClient({}, transport=httpx.MockTransport(lambda request: httpx.Response(
        200, content=b'{"code":0,"padding":1e308,"data":{"results":[{"title":"\\ud83d\\ude00","content":"text"}]}}')))
    assert client.search({"query": "topic"})["results"][0]["title"] == "😀"
