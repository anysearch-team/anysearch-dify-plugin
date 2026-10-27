"""合成公开目录；网络边界与时间可控，不代表真实服务验收。"""
import httpx

from anysearch.catalog import Catalog
from anysearch.errors import AnySearchError
import pytest


def fixture(request):
    names = request.url.params.get_list("domain")
    rows = [{"domain": name, "description": name, "sub_domain_count": 1} for name in (names or ["code", "academic", "social_media"])]
    if names:
        for row in rows:
            row["sub_domains"] = [{"sub_domain": row["domain"] + ".z", "description": "Z", "params": {
                "late": {"description": "Late", "required": False},
                "first": {"description": "Do not guess type", "required": True, "sort_order": 0},
                "typed": {"description": "Explicit", "required": False, "sort_order": 1, "type": "integer", "minimum": 1}}},
                {"sub_domain": row["domain"] + ".a", "description": "A", "params": {}}]
    return httpx.Response(200, json={"code": 0, "request_id": "sub" if names else "total", "data": {"domains": rows}})


def test_subdirectories_deduplicate_preserve_order_and_machine_constraints():
    calls = []
    def upstream(request):
        calls.append(request)
        return fixture(request)
    result = Catalog(transport=httpx.MockTransport(upstream)).discover({"domains": " code, academic,code "})
    assert calls[-1].url.params.multi_items() == [("domain", "code"), ("domain", "academic")]
    assert [d["domain"] for d in result["domains"]] == ["code", "academic"]
    assert [d["sub_domain_count"] for d in result["domains"]] == [2, 2]
    assert [c["tag"] for c in result["capabilities"]] == ["code.a", "code.z", "academic.a", "academic.z"]
    assert result["capabilities"][1]["parameters"] == [
        {"name": "first", "description": "Do not guess type", "required": True, "constraints": {}},
        {"name": "typed", "description": "Explicit", "required": False, "constraints": {"type": "integer", "minimum": 1}},
        {"name": "late", "description": "Late", "required": False, "constraints": {}}]


def test_structural_machine_constraints_are_preserved_without_unknown_metadata():
    constraints = {"type": "object", "patternProperties": {"^x": {"type": "string"}},
                   "propertyNames": {"maxLength": 12}, "dependentRequired": {"x": ["y"]},
                   "if": {"required": ["x"]}, "then": {"required": ["y"]},
                   "properties": {"items": {"type": "array", "contains": {"const": 1}, "minContains": 1,
                                             "maxContains": 2, "prefixItems": [{"type": "integer"}]}},
                   "unevaluatedProperties": False}
    def upstream(request):
        body = fixture(request).json()
        if request.url.path == "/v1/sub-domains":
            body["data"]["domains"][0]["sub_domains"][0]["params"]["first"].update(
                constraints, auto_registered={"api_key": "private-sentinel"})
        return httpx.Response(200, json=body)
    payload = Catalog(transport=httpx.MockTransport(upstream)).get_capabilities(["code"])
    assert payload["capabilities"][1]["parameters"][0]["constraints"] == constraints
    assert "private-sentinel" not in str(payload)


def test_total_catalog_is_anonymous_and_reuses_source_until_ttl():
    calls = []
    now = [0.0]

    def upstream(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/domains"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"code": 0, "request_id": f"r-{len(calls)}", "data": {
            "domains": [{"domain": "code", "description": "Code", "sub_domain_count": 2}],
            "topology_version": 16}})

    catalog = Catalog(transport=httpx.MockTransport(upstream), clock=lambda: now[0])
    first = catalog.list_domains()
    assert first["domains"] == [{"domain": "code", "description": "Code", "sub_domain_count": 2}]
    assert first["capabilities"] == []
    assert first["catalog_sources"][0]["topology_version"] == "16"
    now[0] = 299
    cached = catalog.list_domains()
    assert cached["request_id"] == ""
    assert cached["catalog_sources"][0]["source_request_id"] == "r-1"
    assert cached["catalog_sources"][0]["cache_hit"] is True
    now[0] = 300
    assert catalog.list_domains()["request_id"] == "r-2"
    assert len(calls) == 2


@pytest.mark.parametrize("parameters", [{"domains": "code,"}, {"domains": ",code"}, {"domains": "a,b,c,d,e,f"},
    {"domains": ["code"]}, {"refresh": "false"}, {"refresh": 1}, {"extra": "sentinel"}])
def test_invalid_inputs_fail_before_http(parameters):
    def forbidden(request):
        pytest.fail("Invalid inputs must not send HTTP")
    with pytest.raises(AnySearchError, match="INVALID_INPUT"):
        Catalog(transport=httpx.MockTransport(forbidden)).discover(parameters)


def test_unknown_domain_refreshes_index_once_then_fails():
    calls = []
    def upstream(request):
        calls.append(request)
        return fixture(request)
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    with pytest.raises(AnySearchError, match="UNKNOWN_DOMAIN"):
        catalog.discover({"domains": "unknown"})
    assert [r.url.path for r in calls] == ["/v1/domains", "/v1/domains"]
    calls.clear()
    with pytest.raises(AnySearchError, match="UNKNOWN_DOMAIN"):
        catalog.discover({"domains": "unknown", "refresh": True})
    assert len(calls) == 1


def test_mixed_sources_and_returned_copy_survive_refresh():
    calls = []
    def upstream(request):
        calls.append(request)
        result = fixture(request).json()
        result["request_id"] = f"r-{len(calls)}"
        result["data"]["topology_version"] = len(calls)
        return httpx.Response(200, json=result)
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    first = catalog.get_capabilities(["code"])
    mixed = catalog.get_capabilities(["code", "academic"])
    assert calls[-1].url.params.get_list("domain") == ["academic"]
    assert [(s["scope"], s["cache_hit"], s["source_request_id"]) for s in mixed["catalog_sources"]] == [
        ("domains", True, "r-1"), ("code", True, "r-2"), ("academic", False, "r-3")]
    assert mixed["request_id"] == "r-3"
    catalog.get_capabilities(["code"], refresh=True)
    assert first["catalog_sources"][1]["source_request_id"] == "r-2"
    mixed["capabilities"][1]["parameters"][0]["description"] = "mutated"
    assert catalog.get_capabilities(["academic"])["capabilities"][1]["parameters"][0]["description"] == "Do not guess type"


@pytest.mark.parametrize("damage", ["missing-domain", "extra-domain", "duplicate", "params", "required", "description", "sort", "tags"])
def test_bad_batch_is_not_partially_cached(damage):
    bad = [True]
    calls = []
    def upstream(request):
        calls.append(request)
        result = fixture(request).json()
        rows = result["data"]["domains"]
        if request.url.path == "/v1/sub-domains" and bad[0]:
            if damage == "missing-domain": rows.pop()
            elif damage == "extra-domain": rows.append({"domain": "extra", "description": "x", "sub_domains": []})
            elif damage == "duplicate": rows.append(rows[0])
            elif damage == "params": rows[-1]["sub_domains"][0]["params"] = []
            elif damage == "tags": rows[-1]["sub_domains"][0]["sub_domain"] = "other.tag"
            else: rows[-1]["sub_domains"][0]["params"]["first"][{"sort": "sort_order"}.get(damage, damage)] = None
        return httpx.Response(200, json=result)
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    with pytest.raises(AnySearchError, match="INVALID_RESPONSE"):
        catalog.get_capabilities(["code", "academic"])
    bad[0] = False
    catalog.get_capabilities(["code", "academic"])
    assert calls[-1].url.params.get_list("domain") == ["code", "academic"]


@pytest.mark.parametrize("constraints", [
    {"type": [{}]}, {"type": {}}, {"type": 17}, {"type": None},
    {"type": True}, {"type": ""}, {"type": "unknown"}, {"type": []},
    {"type": ["string", 17]}, {"type": ["string", "unknown"]},
    {"type": ["string", "string"]},
    {"enum": 17}, {"enum": None}, {"enum": {}}, {"enum": "value"},
    {"nullable": 1}, {"nullable": None}, {"nullable": "true"},
], ids=[f"invalid-constraint-{n}" for n in range(18)])
def test_invalid_constraints_reject_entire_batch_and_recover(constraints):
    bad = [True]
    calls = []

    def upstream(request):
        calls.append(request)
        body = fixture(request).json()
        if bad[0] and request.url.path == "/v1/sub-domains":
            body["data"]["domains"][-1]["sub_domains"][0]["params"]["first"].update(constraints)
        return httpx.Response(200, json=body)

    catalog = Catalog(transport=httpx.MockTransport(upstream))
    with pytest.raises(AnySearchError) as caught:
        catalog.get_capabilities(["code", "academic"])
    assert caught.value.error_code == "CATALOG_UNAVAILABLE"
    assert caught.value.fields["cause_code"] == "INVALID_RESPONSE"
    bad[0] = False
    result = catalog.get_capabilities(["code", "academic"])
    assert result["status"] == "ok"
    assert calls[-1].url.params.get_list("domain") == ["code", "academic"]
    assert all(not source["cache_hit"] for source in result["catalog_sources"][1:])


def test_invalid_constraint_refresh_preserves_previous_valid_subdirectories():
    bad = [False]
    calls = []

    def upstream(request):
        calls.append(request)
        body = fixture(request).json()
        if bad[0] and request.url.path == "/v1/sub-domains":
            body["data"]["domains"][-1]["sub_domains"][0]["params"]["first"]["type"] = [{}]
        return httpx.Response(200, json=body)

    catalog = Catalog(transport=httpx.MockTransport(upstream))
    original = catalog.get_capabilities(["code", "academic"])
    bad[0] = True
    with pytest.raises(AnySearchError, match="INVALID_RESPONSE"):
        catalog.get_capabilities(["code", "academic"], refresh=True)
    count = len(calls)
    cached = catalog.get_capabilities(["code", "academic"])
    assert len(calls) == count
    assert cached["capabilities"] == original["capabilities"]
    assert all(source["cache_hit"] for source in cached["catalog_sources"])


@pytest.mark.parametrize("refresh", [True, False])
def test_failed_refresh_never_returns_expired_or_old_data(refresh):
    now = [0.0]
    failure = [False]
    def upstream(request):
        return httpx.Response(403) if failure[0] else fixture(request)
    catalog = Catalog(transport=httpx.MockTransport(upstream), clock=lambda: now[0])
    catalog.list_domains()
    failure[0] = True
    now[0] = 0 if refresh else 300
    with pytest.raises(AnySearchError, match="PERMISSION_DENIED"):
        catalog.list_domains(refresh=refresh)


def test_lru_is_bounded_and_recently_used_scope_survives():
    calls = []
    def upstream(request):
        calls.append(request)
        if request.url.path == "/v1/domains":
            return httpx.Response(200, json={"code": 0, "data": {"domains": [
                {"domain": f"d{i}", "description": "", "sub_domain_count": 0} for i in range(130)]}})
        return fixture(request)
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    for i in range(127):
        catalog.get_capabilities([f"d{i}"])
    catalog.get_capabilities(["d0"])
    catalog.get_capabilities(["d127"])
    count = len(calls)
    assert catalog.get_capabilities(["d0"])["request_id"] == ""
    assert len(calls) == count
    assert catalog.get_capabilities(["d1"])["catalog_sources"][1]["cache_hit"] is False


def test_concurrent_cold_reads_coalesce_and_explicit_refresh_waits_then_loads():
    import gevent
    from gevent.event import Event
    entered, release = Event(), Event()
    calls = []
    def upstream(request):
        calls.append(request)
        if len(calls) == 1:
            entered.set()
            release.wait()
        result = fixture(request).json()
        result["request_id"] = f"r-{len(calls)}"
        return httpx.Response(200, json=result)
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    first = gevent.spawn(catalog.list_domains)
    entered.wait(timeout=1)
    ordinary = gevent.spawn(catalog.list_domains)
    explicit = gevent.spawn(catalog.list_domains, True)
    gevent.sleep(0.01)
    assert len(calls) == 1
    release.set()
    assert first.get(timeout=2)["request_id"] == "r-1"
    assert ordinary.get(timeout=2)["catalog_sources"][0]["source_request_id"] == "r-1"
    assert explicit.get(timeout=2)["request_id"] == "r-2"


def test_overlapping_batches_do_not_deadlock_or_duplicate_loads():
    import gevent
    calls = []
    def upstream(request):
        calls.append(request)
        gevent.sleep(0.01)
        return fixture(request)
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    workers = [gevent.spawn(catalog.get_capabilities, names) for names in
               (["code", "academic"], ["academic", "code"], ["academic", "social_media"])]
    for worker in workers:
        assert worker.get(timeout=2)["status"] == "ok"
    names = [d for r in calls for d in r.url.params.get_list("domain")]
    assert sorted(names) == ["academic", "code", "social_media"]


def test_lock_wait_is_bounded_by_tool_deadline():
    import gevent
    from gevent.event import Event
    entered, release = Event(), Event()
    def upstream(request):
        entered.set()
        release.wait()
        return fixture(request)
    catalog = Catalog(transport=httpx.MockTransport(upstream), budget_seconds=0.1)
    def run():
        try:
            catalog.list_domains()
        except AnySearchError as error:
            return error.fields["cause_code"]
    first = gevent.spawn(run)
    entered.wait(timeout=1)
    second = gevent.spawn(run)
    assert first.get(timeout=1) == "TIMEOUT"
    assert second.get(timeout=1) == "TIMEOUT"
    release.set()
    assert catalog.list_domains()["status"] == "ok"


def test_batch_keeps_selected_cached_record_during_lru_eviction():
    def upstream(request):
        if request.url.path == "/v1/domains":
            return httpx.Response(200, json={"code": 0, "data": {"domains": [
                {"domain": f"d{i}", "description": "", "sub_domain_count": 0} for i in range(130)]}})
        return fixture(request)
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    for i in range(127):
        catalog.get_capabilities([f"d{i}"])
    result = catalog.get_capabilities(["d0", "d127"])
    assert [r["domain"] for r in result["domains"]] == ["d0", "d127"]
    assert [s["cache_hit"] for s in result["catalog_sources"]] == [True, True, False]


def test_different_scopes_finish_out_of_order_without_invalidating_each_other():
    import gevent
    from gevent.event import Event
    entered, release = Event(), Event()
    def upstream(request):
        names = request.url.params.get_list("domain")
        if names == ["code"]:
            entered.set()
            release.wait()
        body = fixture(request).json()
        body["request_id"] = names[0] if names else "index"
        body["data"]["topology_version"] = 1 if names == ["code"] else 99
        return httpx.Response(200, json=body)
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    catalog.list_domains()
    slow = gevent.spawn(catalog.get_capabilities, ["code"])
    assert entered.wait(timeout=1)
    fast = gevent.spawn(catalog.get_capabilities, ["academic"])
    assert fast.get(timeout=1)["request_id"] == "academic"
    release.set()
    assert slow.get(timeout=1)["request_id"] == "code"
    mixed = catalog.get_capabilities(["code", "academic"])
    assert mixed["request_id"] == ""
    assert [s["topology_version"] for s in mixed["catalog_sources"]] == ["99", "1", "99"]
