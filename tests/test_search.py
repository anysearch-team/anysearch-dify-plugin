import json

import httpx

from anysearch.client import AnySearchClient


def test_anonymous_general_search_defaults_and_no_credential_fallback(monkeypatch):
    monkeypatch.setenv("ANYSEARCH_API_KEY", "environment-secret-sentinel")
    requests = []

    def upstream(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://api.anysearch.com/v1/search"
        assert "authorization" not in request.headers
        assert json.loads(request.content) == {"query": "hello", "max_results": 5}
        return httpx.Response(200, json={"code": 0, "request_id": "synthetic-01", "data": {"results": []}})

    result = AnySearchClient({}, transport=httpx.MockTransport(upstream)).search({"query": " hello "})
    assert result == {"schema_version": "1.0", "status": "ok", "request_id": "synthetic-01",
                      "warnings": [], "query": "hello", "tag": "", "results": [], "result_count": 0, "catalog": {}}
    assert len(requests) == 1
