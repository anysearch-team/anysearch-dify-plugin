import httpx

from anysearch.catalog import Catalog
from anysearch.client import AnySearchClient


def _catalog_response(request: httpx.Request) -> httpx.Response:
    row = {
        "domain": "code",
        "description": "Code",
        "sub_domain_count": 1,
        "sub_domains": [{
            "sub_domain": "code.doc",
            "description": "Documentation",
            "params": {"library": {"description": "Library", "required": True}},
        }],
    }
    if request.url.path == "/v1/domains":
        row = {key: value for key, value in row.items() if key != "sub_domains"}
    return httpx.Response(200, json={"code": 0, "request_id": "catalog", "data": {"domains": [row]}})


def test_credentials_are_scoped_to_search_vertical_and_extract_posts():
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return _catalog_response(request)
        data = {"content": "content"} if request.url.path == "/v1/extract" else {"results": []}
        return httpx.Response(200, json={"code": 0, "request_id": "result", "data": data})

    transport = httpx.MockTransport(upstream)
    client = AnySearchClient({"api_key": "synthetic-valid-key"}, transport=transport)
    client.search({"query": "search"})
    client.vertical_search(
        {"query": "docs", "tag": "code.doc", "params_json": '{"library":"react"}'},
        catalog=Catalog(transport=transport),
    )
    client.extract({"url": "https://example.com/"})

    posts = [request for request in requests if request.method == "POST"]
    assert [request.url.path for request in posts] == ["/v1/search", "/v1/search", "/v1/extract"]
    assert all(request.headers["authorization"] == "Bearer synthetic-valid-key" for request in posts)
    gets = [request for request in requests if request.method == "GET"]
    assert gets and all("authorization" not in request.headers for request in gets)


def test_empty_credentials_never_fall_back_to_environment(monkeypatch):
    monkeypatch.setenv("ANYSEARCH_API_KEY", "environment-key-must-be-ignored")
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "code": 0,
            "request_id": "result",
            "data": {"results": []},
        })

    AnySearchClient({}, transport=httpx.MockTransport(upstream)).search({"query": "anonymous"})
    assert len(requests) == 1
    assert "authorization" not in requests[0].headers
