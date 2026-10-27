"""使用真实 Python 字符串占用验证目录缓存容量，不触发生产压力测试。"""
import sys

import gevent
from gevent.event import Event
import httpx

import anysearch.catalog as catalog_module

from anysearch.catalog import Catalog


def retained_size(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(retained_size(key, seen) + retained_size(item, seen) for key, item in value.items())
    elif isinstance(value, (tuple, list)):
        size += sum(retained_size(item, seen) for item in value)
    return size


def catalog_transport(description, count=24):
    def upstream(request):
        names = request.url.params.get_list("domain")
        rows = [{"domain": name, "description": description if names else "", "sub_domain_count": 0}
                for name in (names or [f"d{i}" for i in range(count)])]
        if names:
            for row in rows:
                row["sub_domains"] = []
        return httpx.Response(200, json={"code": 0, "data": {"domains": rows}})
    return httpx.MockTransport(upstream)


def test_large_unicode_record_is_returned_without_being_retained():
    catalog = Catalog(transport=catalog_transport("x" * (700 * 1024) + "😀"))
    first = catalog.get_capabilities(["d0"])
    assert first["status"] == "ok"
    assert first["domains"][0]["description"].endswith("😀")
    assert "d0" not in catalog._records
    second = catalog.get_capabilities(["d0"])
    assert second["catalog_sources"][1]["cache_hit"] is False


def test_total_memory_budget_evicts_old_scopes_before_record_count_limit():
    catalog = Catalog(transport=catalog_transport("x" * (256 * 1024) + "😀"))
    for i in range(20):
        assert catalog.get_capabilities([f"d{i}"])["status"] == "ok"
        assert retained_size((catalog._records, catalog._record_sizes)) <= catalog_module.MAX_CACHE_BYTES
    assert catalog.get_capabilities(["d19"])["catalog_sources"][1]["cache_hit"] is True
    assert catalog.get_capabilities(["d0"])["catalog_sources"][1]["cache_hit"] is False


def test_refresh_to_oversized_record_removes_previous_cached_version():
    large = [False]
    def upstream(request):
        description = "x" * (700 * 1024) + "😀" if large[0] else "small"
        return catalog_transport(description).handle_request(request)
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    assert catalog.get_capabilities(["d0"])["domains"][0]["description"] == "small"
    large[0] = True
    refreshed = catalog.get_capabilities(["d0"], refresh=True)
    assert refreshed["domains"][0]["description"].endswith("😀")
    assert "d0" not in catalog._records
    assert catalog.get_capabilities(["d0"])["catalog_sources"][1]["cache_hit"] is False


def test_byte_eviction_keeps_selected_records_for_current_batch(monkeypatch):
    monkeypatch.setattr(catalog_module, "MAX_CACHE_BYTES", 12 * 1024)
    catalog = Catalog(transport=catalog_transport("x" * 7000, count=2))
    catalog.get_capabilities(["d0"])
    result = catalog.get_capabilities(["d0", "d1"])
    assert [row["domain"] for row in result["domains"]] == ["d0", "d1"]
    assert [source["cache_hit"] for source in result["catalog_sources"]] == [True, True, False]
    assert "d0" not in catalog._records
    assert retained_size((catalog._records, catalog._record_sizes)) <= catalog_module.MAX_CACHE_BYTES


def test_repeated_refresh_and_ttl_expiry_do_not_accumulate_capacity():
    now = [0.0]
    catalog = Catalog(transport=catalog_transport("x" * 2000), clock=lambda: now[0])
    catalog.get_capabilities(["d0"])
    first_size = catalog._cache_bytes
    for i in range(5):
        now[0] += 300
        result = catalog.get_capabilities(["d0"], refresh=i % 2 == 0)
        assert result["catalog_sources"][1]["cache_hit"] is False
        assert catalog._cache_bytes == first_size
    assert len(catalog._records) == 2


def test_oversized_index_can_still_resolve_subdirectory_without_caching():
    def upstream(request):
        row = {"domain": "d0", "description": "x" * (700 * 1024) + "😀", "sub_domain_count": 0}
        if request.url.path == "/v1/sub-domains":
            row.update(description="small", sub_domains=[])
        return httpx.Response(200, json={"code": 0, "data": {"domains": [row]}})
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    payload = catalog.get_capabilities(["d0"])
    assert payload["status"] == "ok"
    assert "" not in catalog._records
    assert "d0" in catalog._records


def test_concurrent_disjoint_loads_stay_within_total_budget(monkeypatch):
    monkeypatch.setattr(catalog_module, "MAX_CACHE_BYTES", 24 * 1024)
    entered, release = Event(), Event()
    transport = catalog_transport("x" * 7000)
    def upstream(request):
        if request.url.params.get_list("domain") == ["d0"]:
            entered.set()
            release.wait(timeout=1)
        return transport.handle_request(request)
    catalog = Catalog(transport=httpx.MockTransport(upstream))
    catalog.list_domains()
    slow = gevent.spawn(catalog.get_capabilities, ["d0"])
    assert entered.wait(timeout=1)
    workers = [gevent.spawn(catalog.get_capabilities, [f"d{i}"]) for i in range(1, 5)]
    try:
        for worker in workers:
            assert worker.get(timeout=2)["status"] == "ok"
            assert retained_size((catalog._records, catalog._record_sizes)) <= catalog_module.MAX_CACHE_BYTES
    finally:
        release.set()
    assert slow.get(timeout=2)["domains"][0]["domain"] == "d0"
    assert retained_size((catalog._records, catalog._record_sizes)) <= catalog_module.MAX_CACHE_BYTES
    assert catalog._locks == {}
