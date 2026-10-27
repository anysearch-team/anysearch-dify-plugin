from pathlib import Path

import httpx
import jsonschema
import pytest
import yaml

from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from anysearch.errors import AnySearchToolError
from provider.anysearch import AnysearchProvider
from tools.search import SearchTool


@pytest.mark.parametrize("value", [None, "", "  ", "synthetic-key"])
def test_provider_save_does_not_call_network(value, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Saving credentials must not perform network requests")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    AnysearchProvider().validate_credentials({"api_key": value})


@pytest.mark.parametrize("value", [False, 3, {}, "key\n", "key\tvalue", "key\x00", "key\x7f", "key\x85"])
def test_provider_rejects_unsafe_format(value):
    with pytest.raises(ToolProviderCredentialValidationError):
        AnysearchProvider().validate_credentials({"api_key": value})


@pytest.mark.parametrize("results", [[], [{}], [{"title": "T", "url": "U", "content": "C"}]])
def test_sdk_outputs_match_schema_and_one_another(results, monkeypatch):
    def upstream(self, request):
        return httpx.Response(200, json={"code": 0, "data": {"results": results}})
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", upstream)
    messages = [m.model_dump(mode="json") for m in SearchTool.from_credentials({}).invoke({"query": "x"})]
    payload = messages[0]["message"]["json_object"]
    schema = yaml.safe_load(Path("tools/search.yaml").read_text(encoding="utf-8"))["output_schema"]
    jsonschema.validate(payload, schema)
    variables = {m["message"]["variable_name"]: m["message"]["variable_value"] for m in messages if m["type"] == "variable"}
    assert variables == payload
    assert len(messages) == 11
    text = messages[-1]["message"]["text"]
    if not results:
        assert "No results found" in text
    for result in payload["results"]:
        assert all(value in text for value in result.values())
    assert all(warning in text for warning in payload["warnings"])


@pytest.mark.parametrize("response", [httpx.Response(401, text="private-body"), httpx.Response(200, json={"code": 17}), httpx.Response(200, json={"code": 0, "data": {}})])
def test_tool_error_emits_no_success_message(response, monkeypatch):
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: response)
    observed = []
    with pytest.raises(AnySearchToolError, match="ANYSEARCH_ERROR") as error:
        for message in SearchTool.from_credentials({}).invoke({"query": "private-query"}):
            observed.append(message)
    assert observed == []
    assert "private" not in str(error.value)


def test_unexpected_transport_failure_has_no_context_or_success(monkeypatch):
    import traceback
    def broken(self,request):
        raise RuntimeError("private-sentinel")
    monkeypatch.setattr(httpx.HTTPTransport,"handle_request",broken)
    with pytest.raises(AnySearchToolError,match="INTERNAL_ERROR") as error:
        list(SearchTool.from_credentials({}).invoke({"query":"x"}))
    assert "private-sentinel" not in "".join(traceback.format_exception(error.value))
