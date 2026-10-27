from pathlib import Path

import httpx
import jsonschema
import pytest
import yaml

from anysearch.catalog import Catalog
from anysearch.errors import AnySearchToolError
from tools.discover import DiscoverTool
from tools.vertical_search import VerticalSearchTool


def test_discover_sdk_schema_variables_and_text_are_consistent(monkeypatch):
    def upstream(self, request):
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"code": 0, "request_id": "sdk-catalog", "data": {
            "domains": [{"domain": "code", "description": "Code description", "sub_domain_count": 2}]}})
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", upstream)
    messages = [m.model_dump(mode="json") for m in DiscoverTool.from_credentials({"api_key": "unused-secret"}).invoke({"refresh": True})]
    payload = messages[0]["message"]["json_object"]
    schema = yaml.safe_load(Path("tools/discover.yaml").read_text(encoding="utf-8"))["output_schema"]
    jsonschema.validate(payload, schema)
    assert {m["message"]["variable_name"]: m["message"]["variable_value"] for m in messages if m["type"] == "variable"} == payload
    text = messages[-1]["message"]["text"]
    assert "Code description" in text and "sdk-catalog" in text
    assert "unused-secret" not in str(messages)


def test_discover_failure_emits_no_partial_success(monkeypatch):
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, r: httpx.Response(403, text="private-sentinel"))
    observed = []
    with pytest.raises(AnySearchToolError, match="CATALOG_UNAVAILABLE") as caught:
        for message in DiscoverTool.from_credentials({}).invoke({"refresh": True}):
            observed.append(message)
    assert not observed
    assert "private-sentinel" not in str(caught.value)


@pytest.mark.parametrize("constraints", [{"type": [{}]}, {"enum": 17}, {"nullable": "true"}])
def test_malformed_discovery_then_vertical_fails_without_success_or_post(monkeypatch, constraints):
    calls = []
    bad = [True]

    def upstream(self, request):
        calls.append(request)
        assert request.method == "GET"
        row = {"domain": "code", "description": "Code", "sub_domain_count": 1}
        if request.url.path == "/v1/sub-domains":
            row["sub_domains"] = [{"sub_domain": "code.custom", "description": "Custom", "params": {
                "value": {"description": "Value", "required": False, **(constraints if bad[0] else {})}}}]
        return httpx.Response(200, json={"code": 0, "data": {"domains": [row]}})

    catalog = Catalog()
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", upstream)
    monkeypatch.setattr("tools.discover.catalog", catalog)
    monkeypatch.setattr("anysearch.catalog.catalog", catalog)
    for tool, parameters in [
        (DiscoverTool, {"domains": "code"}),
        (VerticalSearchTool, {"tag": "code.custom", "query": "topic", "params_json": '{"value":3}'}),
    ]:
        observed = []
        with pytest.raises(AnySearchToolError) as caught:
            for message in tool.from_credentials({}).invoke(parameters):
                observed.append(message)
        assert "CATALOG_UNAVAILABLE" in str(caught.value)
        assert "INVALID_RESPONSE" in str(caught.value)
        assert "INTERNAL_ERROR" not in str(caught.value)
        assert not observed
    assert [request.url.path for request in calls] == ["/v1/domains", "/v1/sub-domains", "/v1/sub-domains"]
    bad[0] = False
    recovered = catalog.get_capabilities(["code"])
    assert recovered["status"] == "ok"
    assert recovered["catalog_sources"][1]["cache_hit"] is False
