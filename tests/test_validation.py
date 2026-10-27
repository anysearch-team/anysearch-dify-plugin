import json

import httpx
import pytest

from anysearch.client import AnySearchClient


@pytest.mark.parametrize("parameters", [
    {}, {"query": None}, {"query": "  "}, {"query": 1}, {"query": "x" * 8001},
    *({"query": "x", "max_results": v} for v in [0, 11, 1.5, True, "1.0", "1e0", float("nan"), float("inf"), "9" * 5000, [], {}]),
    {"query": "x", "tag": "code.doc"}, {"query": "x", "zone": "us"},
    {"query": "x", "language": "en\n"}, {"query": "x", "language": "x" * 65},
    {"query": "x", "language": 42}, {"query": "x", "zone": False},
])
def test_bad_input_fails_before_http(parameters):
    calls = []
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda r: calls.append(r)))
    with pytest.raises(Exception, match='"error_code":"INVALID_INPUT"'):
        client.search(parameters)
    assert calls == []


@pytest.mark.parametrize("count", [1, 10, "1", "10", 5.0])
def test_configuration_mapping(count):
    def upstream(request):
        assert json.loads(request.content) == {"query": "a  b", "max_results": int(count), "zone": "intl", "language": "custom-language"}
        return httpx.Response(200, json={"code": 0, "data": {"results": []}})
    result = AnySearchClient({}, transport=httpx.MockTransport(upstream)).search(
        {"query": " a  b ", "max_results": count, "zone": "intl", "language": " custom-language "})
    assert result["query"] == "a  b"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_optional_strings_unset(value):
    def upstream(request):
        assert json.loads(request.content) == {"query":"x","max_results":5}
        return httpx.Response(200,json={"code":0,"data":{"results":[]}})
    AnySearchClient({},transport=httpx.MockTransport(upstream)).search({"query":"x","zone":value,"language":value})


def test_long_integer_string_with_leading_zeros_is_still_an_integer():
    def upstream(request):
        assert json.loads(request.content)["max_results"] == 1
        return httpx.Response(200,json={"code":0,"data":{"results":[]}})
    AnySearchClient({},transport=httpx.MockTransport(upstream)).search({"query":"x","max_results":"0"*5000+"1"})
