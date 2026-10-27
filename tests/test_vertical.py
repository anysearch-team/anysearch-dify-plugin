"""合成 HTTP 边界验证；真实服务证据单独记录。"""
import json

import httpx
import pytest

from anysearch.catalog import Catalog
from anysearch.client import AnySearchClient
from anysearch.errors import AnySearchError
from anysearch.semantic_rules import CodeDocRule


LIBRARY_DESCRIPTION = "Library or framework name to query docs for, e.g. react, express"


class Service:
    def __init__(self):
        self.calls = []
        self.description = LIBRARY_DESCRIPTION
        self.params = {"library": {"description": LIBRARY_DESCRIPTION, "required": True}}
        self.fail = False

    def __call__(self, request):
        self.calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "request_id": "search", "data": {"results": []}})
        if self.fail:
            return httpx.Response(403)
        row = {"domain": "code", "description": "Code", "sub_domain_count": 1}
        if request.url.path == "/v1/sub-domains":
            params = {k: dict(v) for k, v in self.params.items()}
            if "library" in params:
                params["library"]["description"] = self.description
            row["sub_domains"] = [{"sub_domain": "code.doc", "description": "Docs", "params": params}]
        return httpx.Response(200, json={"code": 0, "request_id": f"catalog-{len(self.calls)}", "data": {"domains": [row]}})

    @property
    def gets(self):
        return len([r for r in self.calls if r.method == "GET"])


def invoke(service, catalog=None, *, params='{"library":"react"}', **settings):
    transport = httpx.MockTransport(service)
    return AnySearchClient({}, transport=transport).vertical_search(
        {"query": "React useEffect cleanup", "tag": "code.doc", "params_json": params, **settings},
        catalog=catalog or Catalog(transport=transport))


@pytest.mark.parametrize("params", ['{}', '{"library":""}', '{"library":"  "}', '{"library":true}'])
def test_code_library_requires_nonempty_name_when_fingerprint_matches(params):
    service = Service()
    with pytest.raises(AnySearchError, match="INVALID_INPUT"):
        invoke(service, params=params)
    assert service.gets == 2
    assert all(r.method == "GET" for r in service.calls)


def test_unconfirmed_cached_mismatch_refreshes_once_across_calls():
    service = Service()
    service.description = "Changed semantics: empty library is permitted"
    catalog = Catalog(transport=httpx.MockTransport(service), rules={})
    catalog.get_capabilities(["code"])
    catalog.rules["code.doc"] = CodeDocRule()
    for _ in range(3):
        assert invoke(service, catalog, params='{"library":""}')["status"] == "ok"
    assert service.gets == 3


def test_cold_mismatch_is_confirmed_without_extra_get_and_does_not_extend_ttl():
    service = Service()
    service.description = "Changed"
    now = [0.0]
    catalog = Catalog(transport=httpx.MockTransport(service), clock=lambda: now[0])
    first = invoke(service, catalog, params='{"library":""}')["catalog"]
    now[0] = 299
    cached = invoke(service, catalog, params='{"library":""}')["catalog"]
    assert cached["fetched_at"] == first["fetched_at"]
    assert cached["cache_hit"] is True and service.gets == 2
    now[0] = 300
    invoke(service, catalog, params='{"library":""}')
    assert service.gets == 4
    catalog.get_capabilities(["code"], refresh=True)
    invoke(service, catalog, params='{"library":""}')
    assert service.gets == 6
    catalog.rules["code.doc"] = CodeDocRule(version="code.doc.library/2")
    invoke(service, catalog, params='{"library":""}')
    assert service.gets == 7


def test_failed_confirmation_can_retry_and_rematching_restores_rule():
    service = Service()
    service.description = "Changed"
    catalog = Catalog(transport=httpx.MockTransport(service), rules={})
    catalog.get_capabilities(["code"])
    catalog.rules["code.doc"] = CodeDocRule()
    service.fail = True
    for _ in range(2):
        with pytest.raises(AnySearchError, match="CATALOG_UNAVAILABLE"):
            invoke(service, catalog)
    assert service.gets == 4
    assert all(r.method == "GET" for r in service.calls)
    service.fail = False
    service.description = LIBRARY_DESCRIPTION
    with pytest.raises(AnySearchError, match="INVALID_INPUT"):
        invoke(service, catalog, params='{"library":""}')
    assert service.gets == 5


def test_concurrent_mismatch_confirmation_is_shared_after_lock_recheck():
    import gevent
    from gevent.event import Event

    service = Service()
    service.description = "Changed"
    entered, release = Event(), Event()
    block = [False]

    def upstream(request):
        if block[0] and request.url.path == "/v1/sub-domains":
            entered.set()
            assert release.wait(timeout=2)
        return service(request)

    catalog = Catalog(transport=httpx.MockTransport(upstream), rules={})
    catalog.get_capabilities(["code"])
    catalog.rules["code.doc"] = CodeDocRule()
    block[0] = True
    first = gevent.spawn(invoke, service, catalog, params='{"library":""}')
    try:
        assert entered.wait(timeout=1)
        others = [gevent.spawn(invoke, service, catalog, params='{"library":""}') for _ in range(3)]
        gevent.sleep(0)
        release.set()
        assert all(job.get(timeout=2)["status"] == "ok" for job in [first, *others])
        assert service.gets == 3
    finally:
        release.set()
        first.kill()


@pytest.mark.parametrize("raw", ['```json\n{}\n```', '[]', '1', 'null', 'true', '"x"',
    '{"library":"react","library":"x"}', '{"library":{"x":1,"x":2}}',
    '{"library":NaN}', '{"library":Infinity}', '{"library":-Infinity}', '{"library":1e999}',
    '{"library":"\\ud800"}', '{bad}', {"library": "react"}, '[', '{"library":"' + '中' * 5500 + '"}'],
    ids=[f"invalid-json-{n}" for n in range(17)])
def test_invalid_json_fails_without_any_http(raw):
    service = Service()
    with pytest.raises(AnySearchError, match="INVALID_INPUT"):
        invoke(service, params=raw)
    assert service.calls == []


@pytest.mark.parametrize("settings", [{"tag":"code"}, {"tag":"code..doc"}, {"tag":".doc"},
    {"tag":"code."}, {"tag":"code. doc"}, {"tag":"code.\ndoc"}, {"tag":"x"*129},
    {"tag":None}, {"query":False}, {"query":"x"*8001}, {"max_results":True}, {"zone":"other"}, {"unknown":1}])
def test_invalid_base_input_has_zero_http(settings):
    service = Service()
    with pytest.raises(AnySearchError, match="INVALID_INPUT"):
        invoke(service, **settings)
    assert service.calls == []


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_json_utf8_exact_16kib_boundary(delta):
    service = Service()
    raw = '{"library":"' + 'x' * (16384 + delta - len('{"library":""}')) + '"}'
    if delta > 0:
        with pytest.raises(AnySearchError, match="INVALID_INPUT"):
            invoke(service, params=raw)
        assert not service.calls
    else:
        assert invoke(service, params=raw)["status"] == "ok"


def test_oversize_whitespace_json_is_not_normalized_away():
    service = Service()
    with pytest.raises(AnySearchError, match="INVALID_INPUT"):
        invoke(service, params=" " * 16385)
    assert service.calls == []


@pytest.mark.parametrize("settings,error,gets", [({"tag":"other.doc"},"UNKNOWN_DOMAIN",2),
    ({"tag":"code.Doc"},"UNKNOWN_TAG",3), ({"params_json":'{"Library":"react"}'},"UNKNOWN_PARAMETER",3)])
def test_unknown_items_refresh_at_most_once_then_fail_without_post(settings, error, gets):
    service = Service()
    with pytest.raises(AnySearchError, match=error):
        invoke(service, **settings)
    assert service.gets == gets and len(service.calls) == gets


@pytest.mark.parametrize("value,constraints,valid", [(None, {}, False), ({}, {}, False), ([], {}, False),
    ("", {}, True), (True, {}, True), (3.5, {}, True),
    (3, {"type":"integer"}, True), (True, {"type":"integer"}, False),
    ("3", {"type":"number"}, False), (None, {"type":["null","string"]}, True),
    (None, {"type":"string","nullable":True}, True), ([], {"type":"array"}, True),
    ({}, {"type":"object"}, True), ("bad", {"enum":["good"]}, False),
    (True, {"enum":[1]}, False), (1, {"enum":[1]}, True),
    (3, {"type":["integer", "string"], "enum":[3, "3"]}, True),
    (None, {"nullable":True}, True), (None, {"type":"string", "nullable":False}, False),
    ({"k":[True, 1]}, {"type":"object", "enum":[{"k":[True, 1]}]}, True),
    (False, {"enum":[0, False]}, True), (1, {"enum":[]}, False)])
def test_catalog_types_and_enums_preserve_legal_json_values(value, constraints, valid):
    service = Service()
    service.params = {"value":{"description":"Synthetic", "required":True, **constraints}}
    raw = json.dumps({"value":value})
    if valid:
        assert invoke(service, params=raw)["status"] == "ok"
        assert json.loads(service.calls[-1].content)["params"] == {"value":value}
    else:
        with pytest.raises(AnySearchError, match="INVALID_INPUT"):
            invoke(service, params=raw)
        assert all(r.method == "GET" for r in service.calls)


def test_drift_does_not_block_optional_vertical_query():
    service = Service()
    service.description = "Changed, empty queries maybe accepted upstream"
    assert invoke(service, query="")["status"] == "ok"
    assert json.loads(service.calls[-1].content)["query"] == ""
    assert service.gets == 2 and len(service.calls) == 3


def test_unknown_cached_parameter_refreshes_and_accepts_new_catalog():
    service = Service()
    catalog = Catalog(transport=httpx.MockTransport(service))
    catalog.get_capabilities(["code"])
    service.params["new"] = {"description":"New", "required":False}
    result = invoke(service, catalog, params='{"library":"react","new":false}')
    assert result["status"] == "ok" and service.gets == 3
    assert json.loads(service.calls[-1].content)["params"]["new"] is False


def test_vertical_sdk_outputs_and_catalog_match_schema(monkeypatch):
    from pathlib import Path
    import jsonschema
    import yaml
    from tools.vertical_search import VerticalSearchTool

    service = Service()
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: service(request))
    messages = [m.model_dump(mode="json") for m in VerticalSearchTool.from_credentials({}).invoke(
        {"tag":"code.doc", "query":"React", "params_json":'{"library":"react"}'})]
    payload = messages[0]["message"]["json_object"]
    schema = yaml.safe_load(Path("tools/vertical_search.yaml").read_text(encoding="utf-8"))["output_schema"]
    jsonschema.validate(payload, schema)
    variables = {m["message"]["variable_name"]: m["message"]["variable_value"] for m in messages if m["type"] == "variable"}
    assert variables == payload and len(messages) == 11
    assert payload["tag"] in messages[-1]["message"]["text"]
    assert payload["catalog"]["source_request_id"] in messages[-1]["message"]["text"]


@pytest.mark.parametrize("status", [401, 429, 503])
def test_vertical_post_never_retries_or_falls_back(status, caplog):
    service = Service()
    posts = []
    def upstream(request):
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(status, text="private-response-sentinel")
        return service(request)
    with pytest.raises(AnySearchError) as error:
        AnySearchClient({"api_key":"private-key-sentinel"}, transport=httpx.MockTransport(upstream)).vertical_search(
            {"query":"private-input-sentinel", "tag":"code.doc", "params_json":'{"library":"react"}'},
            catalog=Catalog(transport=httpx.MockTransport(upstream)))
    assert len(posts) == 1 and json.loads(posts[0].content)["tag"] == "code.doc"
    assert all("authorization" not in r.headers for r in service.calls)
    assert posts[0].headers["authorization"] == "Bearer private-key-sentinel"
    assert "sentinel" not in str(error.value) + caplog.text


def test_deadline_is_shared_between_catalog_and_search():
    service = Service()
    now = [0.0]
    def upstream(request):
        now[0] += 26
        return service(request)
    transport = httpx.MockTransport(upstream)
    with pytest.raises(AnySearchError, match="TIMEOUT"):
        AnySearchClient({}, transport=transport, clock=lambda:now[0]).vertical_search(
            {"query":"React", "tag":"code.doc", "params_json":'{"library":"react"}'},
            catalog=Catalog(transport=transport, clock=lambda:now[0]))
    assert [r.method for r in service.calls] == ["GET", "GET", "POST"]


def test_catalog_retry_backoff_is_inside_vertical_deadline():
    import time
    service = Service()
    def unavailable(request):
        service.calls.append(request)
        return httpx.Response(503)
    transport = httpx.MockTransport(unavailable)
    started = time.monotonic()
    with pytest.raises(AnySearchError, match="CATALOG_UNAVAILABLE") as error:
        AnySearchClient({}, transport=transport, budget_seconds=0.05).vertical_search(
            {"query":"React", "tag":"code.doc"}, catalog=Catalog(transport=transport))
    assert error.value.fields["cause_code"] == "TIMEOUT"
    assert service.gets == 1 and time.monotonic() - started < 0.5


def test_vertical_waiter_deadline_does_not_cancel_other_call():
    import gevent
    from gevent.event import Event
    entered, release = Event(), Event()
    service = Service()
    def upstream(request):
        if request.url.path == "/v1/sub-domains":
            entered.set()
            assert release.wait(timeout=2)
        return service(request)
    transport = httpx.MockTransport(upstream)
    catalog = Catalog(transport=transport)
    first = gevent.spawn(invoke, service, catalog)
    try:
        assert entered.wait(timeout=1)
        with pytest.raises(AnySearchError, match="CATALOG_UNAVAILABLE"):
            AnySearchClient({}, transport=transport, budget_seconds=0.03).vertical_search(
                {"query":"React", "tag":"code.doc"}, catalog=catalog)
        release.set()
        assert first.get(timeout=2)["status"] == "ok"
        assert service.gets == 2
    finally:
        release.set()
        first.kill()


@pytest.mark.parametrize("value,constraints,valid", [(1.0, {"type":"integer"}, True),
    (1.0, {"enum":[1]}, True), ({"a":True}, {"type":"object","enum":[{"a":1}]}, False),
    ([True], {"type":"array","enum":[[1]]}, False),
    ([1.0], {"type":"array","enum":[[1]]}, True)])
def test_json_number_and_boolean_semantics(value, constraints, valid):
    test_catalog_types_and_enums_preserve_legal_json_values(value, constraints, valid)


def test_vertical_logs_account_for_directory_and_search_calls(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="anysearch")
    service = Service()
    catalog = Catalog(transport=httpx.MockTransport(service))
    invoke(service, catalog)
    records = [json.loads(record.message) for record in caplog.records if record.name == "anysearch"]
    assert sum(record["http_calls"] for record in records) == 3
    caplog.clear()
    invoke(service, catalog)
    records = [json.loads(record.message) for record in caplog.records if record.name == "anysearch"]
    assert sum(record["http_calls"] for record in records) == 1
    assert any(record["cache_hit"] for record in records)


@pytest.mark.parametrize("phase", ["GET", "POST"])
def test_vertical_hard_timer_closes_slow_stream_and_stops_work(phase):
    import gevent
    from test_budgets import Stream
    stream = Stream([b" "] * 200, delay=0.02)
    service = Service()
    calls = []
    def upstream(request):
        calls.append(request)
        if request.method == phase:
            return httpx.Response(200, stream=stream)
        return service(request)
    transport = httpx.MockTransport(upstream)
    with pytest.raises(AnySearchError, match="TIMEOUT"):
        AnySearchClient({}, transport=transport, budget_seconds=0.06).vertical_search(
            {"query":"React", "tag":"code.doc", "params_json":'{"library":"react"}'},
            catalog=Catalog(transport=transport))
    assert stream.closed and stream.reads < 10
    reads = stream.reads
    gevent.sleep(0.03)
    assert stream.reads == reads
    assert len([r for r in calls if r.method == "POST"]) == (1 if phase == "POST" else 0)


def test_missing_required_lists_names_and_optional_empty_object_omits_params():
    service = Service()
    with pytest.raises(AnySearchError, match="library"):
        invoke(service, params="{}")
    service.params = {}
    result = invoke(service, params="{}", language=" zh-CN ", zone="intl")
    assert result["status"] == "ok"
    assert json.loads(service.calls[-1].content) == {"query":"React useEffect cleanup", "tag":"code.doc",
        "max_results":5, "language":"zh-CN", "zone":"intl"}


def test_confirmation_does_not_survive_lru_eviction():
    service = Service()
    service.description = "Changed"
    names = ["code", *(f"extra{n}" for n in range(128))]
    def upstream(request):
        if request.method == "POST" or request.url.params.get("domain") == "code":
            return service(request)
        service.calls.append(request)
        requested = request.url.params.get_list("domain")
        rows = [{"domain":name, "description":name, "sub_domain_count":0,
                 **({"sub_domains":[]} if requested else {})} for name in (requested or names)]
        return httpx.Response(200, json={"code":0,"data":{"domains":rows}})
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    invoke(service, catalog, params='{"library":""}')
    for name in names[1:]:
        catalog.get_capabilities([name])
    before = service.gets
    invoke(service, catalog, params='{"library":""}')
    invoke(service, catalog, params='{"library":""}')
    assert service.gets == before + 1


def test_dynamic_tag_without_parameters_accepts_omitted_catalog_map():
    calls=[]
    def upstream(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200,json={"code":0,"data":{"results":[]}})
        row={"domain":"resource","description":"Resources","sub_domain_count":1}
        if request.url.path == "/v1/sub-domains":
            row["sub_domains"]=[{"sub_domain":"resource.image","description":"Images"}]
        return httpx.Response(200,json={"code":0,"data":{"domains":[row]}})
    transport=httpx.MockTransport(upstream)
    catalog=Catalog(transport=transport)
    discovered=catalog.get_capabilities(["resource"])
    assert discovered["capabilities"][0]["parameters"] == []
    result=AnySearchClient({},transport=transport).vertical_search(
        {"query":"landscape","tag":"resource.image"},catalog=catalog)
    assert result["status"] == "ok"
    assert "params" not in json.loads(calls[-1].content)


def test_dynamic_vertical_preserves_required_empty_and_original_types():
    calls = []

    def upstream(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "request_id": "search", "data": {"results": []}})
        row = {"domain": "finance", "description": "Finance", "sub_domain_count": 1}
        if request.url.path == "/v1/sub-domains":
            row["sub_domains"] = [{"sub_domain": "finance.fundamental", "description": "Synthetic", "params": {
                name: {"description": "Conditional value", "required": True}
                for name in ("empty", "number", "flag")}}]
        return httpx.Response(200, json={"code": 0, "request_id": "catalog", "data": {"domains": [row]}})

    transport = httpx.MockTransport(upstream)
    result = AnySearchClient({}, transport=transport).vertical_search(
        {"query": "report", "tag": "finance.fundamental",
         "params_json": '{"empty":"","number":2,"flag":true}'}, catalog=Catalog(transport=transport))
    assert [r.method for r in calls] == ["GET", "GET", "POST"]
    assert json.loads(calls[-1].content) == {"query": "report", "max_results": 5,
        "tag": "finance.fundamental", "params": {"empty": "", "number": 2, "flag": True}}
    assert result["tag"] == "finance.fundamental"
    assert result["catalog"]["source_request_id"] == "catalog"
    assert result["result_count"] == 0
