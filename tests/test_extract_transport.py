import gzip
import json
import logging
import time

import gevent
import httpx
import pytest

from anysearch.client import AnySearchClient
from anysearch.errors import AnySearchError
from tests.test_budgets import Stream


@pytest.mark.parametrize('status,code', [(301, 'UPSTREAM_HTTP_ERROR'), (401, 'AUTHENTICATION_FAILED'),
    (402, 'QUOTA_EXHAUSTED'), (403, 'PERMISSION_DENIED'), (429, 'RATE_LIMITED'),
    (500, 'UPSTREAM_UNAVAILABLE'),
    (503, 'UPSTREAM_UNAVAILABLE'), (400, 'UPSTREAM_REQUEST_REJECTED')])
def test_extract_http_errors_do_not_retry_follow_redirects_or_leak(status, code, caplog):
    calls = []
    def upstream(request):
        calls.append(request)
        assert request.headers['authorization'] == 'Bearer synthetic-key'
        return httpx.Response(status, json={'code': 17, 'request_id': 'safe-id',
            'message': 'private-body', 'auto_registered': {'api_key': 'returned-key'}},
            headers={'Location': 'https://example.com/?token=private-token', 'Retry-After': '12'})
    with caplog.at_level(logging.INFO), pytest.raises(AnySearchError) as caught:
        AnySearchClient({'api_key': 'synthetic-key'}, transport=httpx.MockTransport(upstream)).extract(
            {'url': 'https://example.com/private-path?token=private-query'})
    assert caught.value.error_code == code
    assert len(calls) == 1
    assert caught.value.fields['request_id'] == 'safe-id'
    if status == 429:
        assert caught.value.fields['retry_after_seconds'] == 12
    assert all(s not in str(caught.value) + caplog.text for s in
               ['synthetic-key', 'private-body', 'returned-key', 'private-token', 'private-path', 'private-query'])


@pytest.mark.parametrize('body,code', [(b'not-json', 'INVALID_RESPONSE'), (b'[]', 'INVALID_RESPONSE'),
    (b'{"data":{}}', 'INVALID_RESPONSE'), (b'{"code":false}', 'INVALID_RESPONSE'),
    (b'{"code":0}', 'INVALID_RESPONSE'), (b'{"code":17}', 'UPSTREAM_BUSINESS_ERROR')])
def test_extract_protocol_failures(body, code):
    with pytest.raises(AnySearchError, match=code):
        AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))).extract({'url': 'https://example.com/'})


@pytest.mark.parametrize('error,code', [(httpx.ConnectError, 'NETWORK_ERROR'), (httpx.ReadTimeout, 'TIMEOUT')])
def test_extract_network_errors_are_not_retried(error, code):
    calls = []
    def upstream(request):
        calls.append(request)
        raise error('private URL', request=request)
    with pytest.raises(AnySearchError, match=code) as caught:
        AnySearchClient({}, transport=httpx.MockTransport(upstream)).extract({'url': 'https://example.com/'})
    assert len(calls) == 1 and 'private URL' not in str(caught.value)


def test_extract_hard_deadline_closes_stream_without_background_work():
    stream = Stream([b' '] * 100, 0.02)
    started = time.monotonic()
    with pytest.raises(AnySearchError, match='TIMEOUT'):
        AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream)),
                        budget_seconds=0.08).extract({'url': 'https://example.com/'})
    assert time.monotonic() - started < 0.5
    assert stream.closed
    reads = stream.reads
    gevent.sleep(0.05)
    assert stream.reads == reads


@pytest.mark.parametrize('compressed', [True, False])
@pytest.mark.parametrize('delta', [-1, 0, 1])
def test_extract_decompressed_response_boundary(compressed, delta):
    prefix = b'{"code":0,"data":{"content":"ok"},"padding":"'
    raw = prefix + b'x' * (2 * 1024 * 1024 + delta - len(prefix) - 2) + b'"}'
    raw = gzip.compress(raw) if compressed else raw
    stream = Stream([raw[i:i+4096] for i in range(0, len(raw), 4096)])
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream,
        headers={'Content-Encoding': 'gzip'} if compressed else {})))
    if delta > 0:
        with pytest.raises(AnySearchError, match='RESPONSE_TOO_LARGE'):
            client.extract({'url': 'https://example.com/'})
    else:
        assert client.extract({'url': 'https://example.com/'})['content'] == 'ok'
    assert stream.closed


@pytest.mark.parametrize('delta', [-1, 0, 1])
def test_extract_serialized_payload_boundary(delta):
    expected = {'schema_version': '1.0', 'status': 'ok', 'request_id': '', 'warnings': [],
                'input_url': 'https://example.com/', 'url': 'https://example.com/', 'title': '',
                'content': '甲', 'content_length': 1, 'received_length': 1, 'truncated': False,
                'upstream_truncation': 'unknown'}
    size = len(json.dumps(expected, ensure_ascii=False, separators=(',', ':')).encode())
    data = {'content': '甲', 'url': 'https://example.com/', 'title': 'x' * (1024 * 1024 + delta - size)}
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'code': 0, 'data': data})))
    if delta > 0:
        with pytest.raises(AnySearchError, match='OUTPUT_TOO_LARGE'):
            client.extract({'url': 'https://example.com/'})
    else:
        result = client.extract({'url': 'https://example.com/'})
        assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) == 1024 * 1024 + delta
