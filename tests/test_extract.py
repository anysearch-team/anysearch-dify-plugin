import json

import httpx
import pytest
import jsonschema
import yaml
from pathlib import Path

from anysearch.client import AnySearchClient
from anysearch.errors import AnySearchError


def test_extract_sends_only_normalized_url_and_returns_unicode_content():
    requests = []

    def upstream(request):
        requests.append(request)
        assert str(request.url) == 'https://api.anysearch.com/v1/extract'
        assert request.method == 'POST'
        assert json.loads(request.content) == {'url': 'https://example.com/page?lang=zh'}
        assert 'authorization' not in request.headers
        assert request.extensions['timeout']['read'] == 45
        return httpx.Response(200, json={'code': 0, 'request_id': 'extract-fixture', 'data': {
            'title': '正文', 'url': 'https://example.com/final', 'content': '甲😀乙丙'}})

    result = AnySearchClient({}, transport=httpx.MockTransport(upstream)).extract({
        'url': ' https://example.com/page?lang=zh#part ', 'max_chars': 3})
    assert result == {'schema_version': '1.0', 'status': 'ok', 'request_id': 'extract-fixture',
                      'warnings': ['Content truncated locally to 3 characters.'],
                      'input_url': 'https://example.com/page?lang=zh', 'url': 'https://example.com/final',
                      'title': '正文', 'content': '甲😀乙', 'content_length': 3, 'received_length': 4,
                      'truncated': True, 'upstream_truncation': 'unknown'}
    assert len(requests) == 1


@pytest.mark.parametrize('url', [
    None, '', ' ', 1, 'ftp://example.com/a', '//example.com/a', 'http:///a',
    'https://user:password@example.com/', 'https://@example.com/', 'https://example.com/\npath',
    'http://localhost/', 'http://api.localhost./', 'http://printer.local/',
    'http://127.0.0.1/', 'http://10.1.2.3/', 'http://169.254.169.254/', 'http://0.0.0.0/',
    'http://192.168.1.1/', 'http://100.64.0.1/', 'http://224.0.0.1/', 'http://255.255.255.255/',
    'http://[::1]/', 'http://[fc00::1]/', 'http://[fe80::1]/', 'http://[ff02::1]/',
    'http://[::ffff:127.0.0.1]/', 'http://[fe80::1%25eth0]/',
    'http://2130706433/', 'http://127.1/', 'http://0177.0.0.1/', 'http://0x7f000001/',
    'http://0x7f.0.0.1/', 'http://127.0.0.1./', 'http://example.com:bad/', 'http://example.com:65536/',
    'http://example.com\\@127.0.0.1/', 'http://example.com /', 'http://%31%32%37.0.0.1/',
    'https://example.com/' + 'a' * 8192,
])
def test_unsafe_url_fails_before_any_http(url):
    requests = []
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda request: requests.append(request)))
    with pytest.raises(AnySearchError, match='INVALID_INPUT'):
        client.extract({'url': url})
    assert requests == []


def test_extract_json_body_limit_is_checked_before_http():
    prefix = 'https://example.com/'
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={'code': 0, 'data': {'content': 'ok'}})

    client = AnySearchClient({}, transport=httpx.MockTransport(upstream))
    for count in (5451, 5452):
        url = prefix + '中' * count
        size = len(httpx.Request('POST', 'https://api.anysearch.com/v1/extract', json={'url': url}).content)
        assert (size <= 16384) == (count == 5451)
        if count == 5451:
            client.extract({'url': url})
            assert len(calls) == 1
            assert len(calls[0].content) == size
        else:
            with pytest.raises(AnySearchError, match='INVALID_INPUT'):
                client.extract({'url': url})
            assert len(calls) == 1


def test_extract_sdk_registration_and_outputs(monkeypatch):
    from tools.extract import ExtractTool
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda self, request:
                        httpx.Response(200, json={'code': 0, 'data': {'content': '甲😀乙丙'}}))
    messages = [m.model_dump(mode='json') for m in ExtractTool.from_credentials({}).invoke(
        {'url': 'https://example.com/', 'max_chars': 3})]
    payload = messages[0]['message']['json_object']
    schema = yaml.safe_load(Path('tools/extract.yaml').read_text(encoding='utf-8'))['output_schema']
    jsonschema.validate(payload, schema)
    assert 'tools/extract.yaml' in yaml.safe_load(Path('provider/anysearch.yaml').read_text(encoding='utf-8'))['tools']
    assert {m['message']['variable_name']: m['message']['variable_value'] for m in messages if m['type'] == 'variable'} == payload
    summary = messages[-1]['message']['text']
    assert 'untrusted' in summary and '甲😀乙' in summary and 'unknown' in summary
    assert all(warning in summary for warning in payload['warnings'])


@pytest.mark.parametrize('field', ['title', 'url', 'content'])
@pytest.mark.parametrize('value', [None, '', 'text', 0, False, [], {}])
def test_extract_field_contract(field, value):
    data = {'content': 'body', 'title': 'title', 'url': 'https://example.com/final'}
    data[field] = value
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'code': 0, 'data': data})))
    if (value is not None and not isinstance(value, str)) or (field == 'content' and value is None):
        with pytest.raises(AnySearchError, match='INVALID_RESPONSE'):
            client.extract({'url': 'https://example.com/'})
    else:
        result = client.extract({'url': 'https://example.com/'})
        assert result[field] == ('https://example.com/' if field == 'url' and not value else value or '')
        assert bool(result['warnings']) == (field in ('url', 'content') and not value)


@pytest.mark.parametrize('field', ['title', 'url', 'content'])
def test_extract_missing_fields(field):
    data = {'content': 'body', 'title': 'title', 'url': 'https://example.com/final'}
    del data[field]
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'code': 0, 'data': data})))
    if field == 'content':
        with pytest.raises(AnySearchError, match='INVALID_RESPONSE'):
            client.extract({'url': 'https://example.com/'})
    else:
        result = client.extract({'url': 'https://example.com/'})
        assert result[field] == ('' if field == 'title' else 'https://example.com/')


@pytest.mark.parametrize('count', [None, True, False, 0, -1, 50001, 1.5, '1.5', '', ' 1', [], {}, float('inf'), float('nan'), '9' * 5000])
def test_invalid_max_chars_never_calls_upstream(count):
    def forbidden(request):
        pytest.fail('Invalid input must fail before HTTP')
    with pytest.raises(AnySearchError, match='INVALID_INPUT'):
        AnySearchClient({}, transport=httpx.MockTransport(forbidden)).extract({'url': 'https://example.com/', 'max_chars': count})


@pytest.mark.parametrize('count', [1, 50000, 3.0, '3'])
def test_max_chars_and_unknown_upstream_truncation(count):
    client = AnySearchClient({}, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        'code': 0, 'data': {'content': '甲' * 50000, 'url': 'https://example.com/'}})))
    result = client.extract({'url': 'https://example.com/', 'max_chars': count})
    assert result['received_length'] == 50000
    assert result['content_length'] == int(count)
    assert result['truncated'] == (int(count) < 50000)
    assert result['upstream_truncation'] == 'unknown'


@pytest.mark.parametrize('url', ['https://example.com/a.pdf?x=1#part', 'http://8.8.8.8/', 'https://[2606:4700:4700::1111]/', 'https://例子.测试/正文'])
def test_public_urls_pass_without_local_fetch_or_extension_guess(url):
    calls = []
    def upstream(request):
        calls.append(request)
        assert request.url.host == 'api.anysearch.com'
        assert json.loads(request.content)['url'] == url.split('#')[0]
        return httpx.Response(200, json={'code': 0, 'data': {'content': ''}})
    AnySearchClient({}, transport=httpx.MockTransport(upstream)).extract({'url': url})
    assert len(calls) == 1


def test_default_budget_is_55_seconds():
    ticks = [0.0]
    def upstream(request):
        ticks[0] = 55.0
        return httpx.Response(200, json={'code': 0, 'data': {'content': 'body'}})
    with pytest.raises(AnySearchError, match='TIMEOUT'):
        AnySearchClient({}, clock=lambda: ticks[0], transport=httpx.MockTransport(upstream)).extract({'url': 'https://example.com/'})


def test_failed_extract_never_emits_success(monkeypatch):
    from tools.extract import ExtractTool
    from anysearch.errors import AnySearchToolError
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda self, request:
                        httpx.Response(200, json={'code': 0, 'data': {'content': None}}))
    observed = []
    with pytest.raises(AnySearchToolError, match='INVALID_RESPONSE'):
        for message in ExtractTool.from_credentials({}).invoke({'url': 'https://example.com/'}):
            observed.append(message)
    assert observed == []
