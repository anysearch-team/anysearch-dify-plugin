import gzip
import json
import time

import gevent
import httpx
import pytest

from anysearch.client import AnySearchClient
from anysearch.errors import AnySearchError


class Stream(httpx.SyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks = chunks
        self.delay = delay
        self.closed = False
        self.reads = 0

    def __iter__(self):
        for chunk in self.chunks:
            gevent.sleep(self.delay)
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


def test_hard_deadline_interrupts_slow_stream_and_closes_it():
    stream = Stream([b" "] * 200, 0.02)
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream)), budget_seconds=0.08)
    started = time.monotonic()
    with pytest.raises(AnySearchError, match="TIMEOUT"):
        client.search({"query": "x"})
    assert time.monotonic() - started < 0.5
    assert stream.closed and stream.reads < 20
    reads = stream.reads
    gevent.sleep(0.05)
    assert stream.reads == reads


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_actual_decompressed_response_limit(compressed, delta):
    prefix = b'{"code":0,"data":{"results":[]},"padding":"'
    suffix = b'"}'
    body = prefix + b"x" * (2 * 1024 * 1024 + delta - len(prefix) - len(suffix)) + suffix
    raw = gzip.compress(body) if compressed else body
    stream = Stream([raw[i:i+4096] for i in range(0, len(raw), 4096)])
    headers = {"Content-Encoding": "gzip"} if compressed else {}
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream, headers=headers)))
    if delta > 0:
        with pytest.raises(AnySearchError, match="RESPONSE_TOO_LARGE"):
            client.search({"query": "x"})
    else:
        assert client.search({"query": "x"})["result_count"] == 0
    assert stream.closed


def test_payload_limit_does_not_return_partial_results():
    body = {"code": 0, "data": {"results": [{"title": "x" * (1024 * 1024), "url": "u", "content": "c"}]}}
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(AnySearchError, match="OUTPUT_TOO_LARGE"):
        client.search({"query": "x"})


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_exact_utf8_payload_boundary(delta):
    expected = {"schema_version":"1.0","status":"ok","request_id":"","warnings":[],"query":"x","tag":"",
                "results":[{"title":"t","url":"u","content":"c"}],"result_count":1,"catalog":{}}
    base = len(json.dumps(expected,ensure_ascii=False,separators=(",", ":")).encode("utf-8"))
    title = "t" * (1 + 1024 * 1024 + delta - base)
    body = {"code":0,"data":{"results":[{"title":title,"url":"u","content":"c"}]}}
    client = AnySearchClient({},transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body)))
    if delta > 0:
        with pytest.raises(AnySearchError, match="OUTPUT_TOO_LARGE"):
            client.search({"query":"x"})
    else:
        result = client.search({"query":"x"})
        assert len(json.dumps(result,ensure_ascii=False,separators=(",", ":")).encode("utf-8")) == 1024*1024+delta


def test_monotonic_deadline_also_covers_non_yielding_transport():
    ticks = [0.0]
    def upstream(request):
        ticks[0] = 41.0
        return httpx.Response(200,json={"code":0,"data":{"results":[]}})
    with pytest.raises(AnySearchError,match="TIMEOUT"):
        AnySearchClient({},clock=lambda:ticks[0],transport=httpx.MockTransport(upstream)).search({"query":"x"})


@pytest.mark.parametrize("mode", ["idle", "slow"])
@pytest.mark.parametrize("tool", ["search", "extract"])
def test_real_http_socket_is_closed_at_hard_deadline(mode, tool):
    import subprocess
    import sys

    server = subprocess.Popen([sys.executable,'tests/http_fixture.py',mode],stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,text=True,encoding='utf-8')
    with gevent.Timeout(5):
        port = int(server.stdout.readline())
    class LocalTransport(httpx.HTTPTransport):
        def handle_request(self, request):
            request.url = httpx.URL(f"http://127.0.0.1:{port}/search")
            return super().handle_request(request)
    try:
        client = AnySearchClient({}, transport=LocalTransport(), budget_seconds=1)
        started = time.monotonic()
        with pytest.raises(AnySearchError, match="TIMEOUT"):
            getattr(client, tool)({"query": "x"} if tool == "search" else {"url": "https://example.com/"})
        assert time.monotonic() - started < 1.8
        output, error = server.communicate(timeout=2)
        assert server.returncode == 0, error
        assert json.loads(output) == {"accepted":True,"peer_closed":True}
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=2)
