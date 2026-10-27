"""公开目录快照配合合成 HTTP；不冒充真实搜索验收。"""
import copy
import json
from pathlib import Path

import httpx
import pytest

from anysearch.catalog import Catalog
from anysearch.client import AnySearchClient
from anysearch.errors import AnySearchError, AnySearchToolError


CAPABILITIES = json.loads((Path(__file__).parent / 'fixtures/catalog.json').read_text(encoding='utf-8'))
ACADEMIC = ['academic.search', 'academic.preprint']


class Service:
    def __init__(self, tag):
        self.cap = copy.deepcopy(next(c for c in CAPABILITIES if c['tag'] == tag))
        self.calls = []
        self.status = 200
        self.code = 0

    def __call__(self, request):
        self.calls.append(request)
        if request.method == 'POST':
            return httpx.Response(self.status, json={'code': self.code, 'request_id': 'search', 'data': {'results': []}})
        row = {'domain': self.cap['domain'], 'description': 'Fixture', 'sub_domain_count': 1}
        if request.url.path == '/v1/sub-domains':
            row['sub_domains'] = [{'sub_domain': self.cap['tag'], 'description': self.cap['description'],
                'params': {p['name']: {'description': p['description'], 'required': p['required'], **p['constraints']}
                           for p in self.cap['parameters']}}]
        return httpx.Response(200, json={'code': 0, 'request_id': 'catalog', 'data': {'domains': [row]}})

    def invoke(self, params, query='paper', catalog=None):
        transport = httpx.MockTransport(self)
        return AnySearchClient({}, transport=transport).vertical_search(
            {'tag': self.cap['tag'], 'query': query, 'params_json': json.dumps(params)},
            catalog=catalog or Catalog(transport=transport))


@pytest.mark.parametrize('tag', ACADEMIC)
def test_doi_preserves_filters_without_claiming_they_are_ignored(tag):
    service = Service(tag)
    params = {'doi': '10.48550/arXiv.1706.03762', 'year_from': 'bad', 'year_to': '1900'}
    result = service.invoke(params, query='papers')
    assert result['query'] == 'papers' and result['status'] == 'ok'
    assert result['warnings'] == []
    assert json.loads(service.calls[-1].content)['params'] == params


@pytest.mark.parametrize('tag', ACADEMIC)
@pytest.mark.parametrize('params', [{'year_from': '20'}, {'year_to': '202x'},
    {'year_from': '2025', 'year_to': '2020'}, {'year_from': True}, {'year_to': '２０２０'}])
def test_year_descriptions_do_not_become_client_business_constraints(tag, params):
    service = Service(tag)
    result = service.invoke(params)
    assert result['status'] == 'ok' and result['warnings'] == []
    assert json.loads(service.calls[-1].content)['params'] == params


def test_social_preserves_region_and_keyword_without_semantic_promises():
    service = Service('social_media.social_media')
    params = {'type': 'zhihu', 'region': 'Beijing', 'keyword': 'Python'}
    result = service.invoke(params, query='大模型')
    assert result['warnings'] == []
    assert json.loads(service.calls[-1].content) == {
        'query': '大模型', 'tag': 'social_media.social_media', 'params': params, 'max_results': 5}


@pytest.mark.parametrize('tag', ACADEMIC)
@pytest.mark.parametrize('params', [{'year_from': '2020'}, {'year_to': 2024},
    {'year_from': '', 'year_to': ''}, {'year_from': '2020', 'year_to': '2020'},
    {'doi': '10.example/test', 'year_from': '2025', 'year_to': '1900'},
    {'doi': '10.example/test', 'year_from': False}])
def test_year_values_are_preserved_with_or_without_doi(tag, params):
    service = Service(tag)
    assert service.invoke(params)['status'] == 'ok'
    assert json.loads(service.calls[-1].content)['params'] == params


@pytest.mark.parametrize('tag', ACADEMIC)
@pytest.mark.parametrize('doi', ['', '  ', False, 123])
def test_doi_scalar_values_are_preserved_without_inferred_types(tag, doi):
    service = Service(tag)
    assert service.invoke({'doi': doi}, query='papers')['status'] == 'ok'
    assert json.loads(service.calls[-1].content)['params'] == {'doi': doi}


@pytest.mark.parametrize('tag', ACADEMIC)
@pytest.mark.parametrize('params,error', [({'unknown': 'x'}, 'UNKNOWN_PARAMETER'),
    ({'year_from': None}, 'INVALID_INPUT'), ({'year_to': []}, 'INVALID_INPUT')])
def test_doi_does_not_bypass_fields_or_structural_validation(tag, params, error):
    service = Service(tag)
    with pytest.raises(AnySearchError, match=error):
        service.invoke({'doi': '10.example/test', **params}, query='papers')
    assert all(r.method == 'GET' for r in service.calls)


@pytest.mark.parametrize('tag', ACADEMIC)
@pytest.mark.parametrize('change', ['description', 'required', 'constraints'])
def test_academic_description_drift_does_not_gate_nonempty_query(tag, change):
    service = Service(tag)
    year = next(p for p in service.cap['parameters'] if p['name'] == 'year_from')
    year[change] = {'description': 'Changed', 'required': True, 'constraints': {'type': 'string'}}[change]
    catalog = Catalog(transport=httpx.MockTransport(service))
    for _ in range(3):
        result = service.invoke({'doi': '10.example/test', 'year_from': 'bad'}, query='papers', catalog=catalog)
        assert result['status'] == 'ok' and result['warnings'] == []
    assert [r.method for r in service.calls] == ['GET', 'GET', 'POST', 'POST', 'POST']


@pytest.mark.parametrize('tag', ACADEMIC)
@pytest.mark.parametrize('constraint,value', [({'type': 'string'}, 2020), ({'enum': ['2020']}, 'bad')])
def test_doi_retains_explicit_directory_machine_constraints(tag, constraint, value):
    service = Service(tag)
    next(p for p in service.cap['parameters'] if p['name'] == 'year_from')['constraints'] = constraint
    with pytest.raises(AnySearchError, match='INVALID_INPUT'):
        service.invoke({'doi': '10.example/test', 'year_from': value})
    assert all(r.method == 'GET' for r in service.calls)


@pytest.mark.parametrize('params,warning_count', [({'region': 'Beijing'}, 0),
    ({'type': 'weibo_hot', 'region': 'Beijing'}, 0), ({'type': 'zhihu', 'region': ''}, 0),
    ({'keyword': ''}, 0), ({'keyword': 'Python'}, 0)])
def test_social_warning_conditions(params, warning_count):
    assert len(Service('social_media.social_media').invoke(params)['warnings']) == warning_count


@pytest.mark.parametrize('status,code,error', [(400, 0, 'UPSTREAM_REQUEST_REJECTED'),
    (200, 42, 'UPSTREAM_BUSINESS_ERROR'), (503, 0, 'UPSTREAM_UNAVAILABLE')])
def test_social_upstream_rejection_is_not_converted_to_warning(status, code, error):
    service = Service('social_media.social_media')
    service.status, service.code = status, code
    with pytest.raises(AnySearchError, match=error):
        service.invoke({'type': 'zhihu', 'region': 'Beijing', 'keyword': 'Python'})
    assert [r.method for r in service.calls] == ['GET', 'GET', 'POST']


def test_social_description_changes_do_not_add_semantic_warnings():
    service = Service('social_media.social_media')
    next(p for p in service.cap['parameters'] if p['name'] == 'region')['description'] = 'Now works for all types'
    assert service.invoke({'type': 'zhihu', 'region': 'Beijing'})['warnings'] == []


@pytest.mark.parametrize('tag', ACADEMIC)
def test_cached_machine_constraint_failure_sends_no_new_http(tag):
    service = Service(tag)
    next(p for p in service.cap['parameters'] if p['name'] == 'year_to')['constraints'] = {'enum': ['2020']}
    catalog = Catalog(transport=httpx.MockTransport(service))
    service.invoke({}, catalog=catalog)
    calls = len(service.calls)
    with pytest.raises(AnySearchError, match='INVALID_INPUT'):
        service.invoke({'year_to': 'bad'}, catalog=catalog)
    assert len(service.calls) == calls


def test_sdk_outputs_do_not_invent_semantic_warnings_or_log_inputs(monkeypatch, caplog):
    import logging
    from tools.vertical_search import VerticalSearchTool

    service = Service('social_media.social_media')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda self, request: service(request))
    caplog.set_level(logging.INFO, logger='anysearch')
    messages = [m.model_dump(mode='json') for m in VerticalSearchTool.from_credentials({}).invoke({
        'tag': 'social_media.social_media', 'query': 'private-query-sentinel',
        'params_json': '{"type":"zhihu","region":"private-region-sentinel","keyword":"private-keyword-sentinel"}'})]
    payload = messages[0]['message']['json_object']
    variables = {m['message']['variable_name']: m['message']['variable_value'] for m in messages if m['type'] == 'variable'}
    assert variables == payload
    assert payload['warnings'] == []
    assert 'Warning:' not in messages[-1]['message']['text']
    assert 'sentinel' not in caplog.text + json.dumps(payload['warnings'])


@pytest.mark.parametrize('tag', ACADEMIC)
def test_academic_empty_query_without_doi_fails_before_http(tag):
    service = Service(tag)
    with pytest.raises(AnySearchError, match='VERTICAL_QUERY_REQUIRED'):
        service.invoke({}, query='')
    assert service.calls == []


def test_other_tag_empty_query_remains_optional_locally():
    service = Service('social_media.social_media')
    result = service.invoke({}, query='')
    assert result['status'] == 'ok'
    assert json.loads(service.calls[-1].content)['query'] == ''


@pytest.mark.parametrize('tag', ACADEMIC)
def test_nonempty_query_upstream_rejection_is_preserved_without_retry(tag):
    service = Service(tag)
    service.status = 400
    with pytest.raises(AnySearchError, match='UPSTREAM_REQUEST_REJECTED'):
        service.invoke({'doi': '10.example/test'}, query='papers')
    assert [r.method for r in service.calls] == ['GET', 'GET', 'POST']
    assert json.loads(service.calls[-1].content)['query'] == 'papers'


@pytest.mark.parametrize('tag', [*ACADEMIC, 'legal.case', 'legal.legislation', 'legal.statute'])
@pytest.mark.parametrize('query', ['', '  '])
def test_academic_and_legal_empty_query_fails_before_http(tag, query):
    service = Service(tag if tag in ACADEMIC else 'academic.search')
    if tag.startswith('legal.'):
        service.cap.update(domain='legal', tag=tag, parameters=[])
    with pytest.raises(AnySearchError) as caught:
        service.invoke({'doi': '10.48550/arXiv.1706.03762'} if tag in ACADEMIC else {}, query=query)
    assert caught.value.error_code == 'VERTICAL_QUERY_REQUIRED'
    assert 'query' in caught.value.fields['message'].lower()
    assert 'academic' in caught.value.fields['message'].lower()
    assert 'legal' in caught.value.fields['message'].lower()
    assert service.calls == []


def test_sdk_doi_only_reports_query_requirement_without_http(monkeypatch):
    from tools.vertical_search import VerticalSearchTool

    def forbidden(self, request):
        pytest.fail('DOI-only request must fail before HTTP')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    with pytest.raises(AnySearchToolError) as caught:
        list(VerticalSearchTool.from_credentials({}).invoke({
            'tag': 'academic.search', 'params_json': '{"doi":"10.48550/arXiv.1706.03762"}'}))
    assert 'VERTICAL_QUERY_REQUIRED' in str(caught.value)
    assert 'query' in str(caught.value).lower()
    assert 'doi' in str(caught.value).lower()


@pytest.mark.parametrize('settings', [{}, {'query': ''}, {'query': '   '}])
def test_new_catalog_tag_accepts_optional_query_without_a_builtin_rule(settings):
    service = Service('academic.search')
    service.cap.update(domain='synthetic', tag='synthetic.lookup', parameters=[])
    transport = httpx.MockTransport(service)
    result = AnySearchClient({}, transport=transport).vertical_search(
        {'tag': 'synthetic.lookup', **settings}, catalog=Catalog(transport=transport))
    assert result['warnings'] == []
    assert json.loads(service.calls[-1].content) == {
        'tag': 'synthetic.lookup', 'query': '', 'max_results': 5}
