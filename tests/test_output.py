import httpx
import pytest

from anysearch.client import AnySearchClient
from anysearch.errors import AnySearchError


def search(results, **parameters):
    return AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"code": 0, "data": {"results": results}}))).search({"query": "x", **parameters})


@pytest.mark.parametrize("field", ["title", "url", "content"])
@pytest.mark.parametrize("value", [None, "", " a ", False, 42, [], {}])
def test_result_field_contract(field, value):
    item = {"title": "T", "url": "https://example.org", "content": "C", field: value}
    if value is not None and not isinstance(value, str):
        with pytest.raises(AnySearchError, match="INVALID_RESPONSE"):
            search([item])
    else:
        result = search([item])
        assert result["results"][0][field] == (value or "")
        assert bool(result["warnings"]) == (not value)


@pytest.mark.parametrize("content", [None, ""])
@pytest.mark.parametrize("snippet", [None, "", "summary", False, 5, {}, []])
def test_snippet_is_validated_only_when_used(content, snippet):
    item = {"title": "T", "url": "U", "content": content, "snippet": snippet}
    if snippet is not None and not isinstance(snippet, str):
        with pytest.raises(AnySearchError, match="INVALID_RESPONSE"):
            search([item])
    else:
        result = search([item])
        assert result["results"][0]["content"] == (snippet or "")


def test_content_priority_missing_fields_truncation_and_order():
    result = search([{"title": "first", "content": "🙂" * 20001, "snippet": {}}, {}], max_results=1)
    assert result["results"] == [{"title": "first", "url": "", "content": "🙂" * 20000}]
    assert result["result_count"] == 1
    assert len(result["warnings"]) == 3
    assert search([{}])["results"] == [{"title": "", "url": "", "content": ""}]


@pytest.mark.parametrize("results", [None, {}, "", [None], [42]])
def test_invalid_results_do_not_become_empty_success(results):
    with pytest.raises(AnySearchError, match="INVALID_RESPONSE"):
        search(results)
