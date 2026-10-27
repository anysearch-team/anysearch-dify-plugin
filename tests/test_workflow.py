from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize('results,url', [([], ''), ([{'url': ''}, {'url': 'ftp://example.com'}], ''),
    ([{'url': 'https://[invalid'}, {'url': 'https://example.com/first?x=1#p'}, {'url': 'https://example.com/second'}], 'https://example.com/first?x=1#p')])
def test_workflow_selects_first_structured_http_url(results, url):
    workflow = yaml.safe_load(Path('examples/search_then_extract.yml').read_text(encoding='utf-8'))['workflow']
    select = next(n for n in workflow['graph']['nodes'] if n['id'] == 'select_url')['data']
    scope = {}
    exec(select['code'], scope)
    assert scope['main'](results) == {'url': url}
    assert select['variables'] == [{'variable': 'results', 'value_selector': ['search', 'results']}]


def test_portable_workflow_has_guarded_bindings_and_error_paths():
    dsl = yaml.safe_load(Path('examples/search_then_extract.yml').read_text(encoding='utf-8'))
    nodes = {n['id']: n['data'] for n in dsl['workflow']['graph']['nodes']}
    edges = {(e['source'], e['sourceHandle'], e['target']) for e in dsl['workflow']['graph']['edges']}
    assert nodes['search']['tool_parameters']['query']['value'] == ['start', 'query']
    assert nodes['extract']['tool_parameters']['url']['value'] == ['select_url', 'url']
    assert nodes['extract']['tool_configurations']['max_chars']['value'] == 50000
    assert ('has_results', 'true', 'select_url') in edges
    assert ('has_url', 'true', 'extract') in edges
    assert ('has_results', 'false', 'no_results_message') in edges
    assert ('has_url', 'false', 'no_url_message') in edges
    for tool in ['search', 'extract']:
        assert nodes[tool]['error_strategy'] == 'fail-branch'
        assert (tool, 'fail-branch', tool + '_failed_message') in edges
        assert 'credential_id' not in nodes[tool]
        assert 'plugin_unique_identifier' not in nodes[tool]
    raw = Path('examples/search_then_extract.yml').read_text(encoding='utf-8')
    assert all(key not in raw for key in ['workspace_id:', 'tenant_id:', 'api_key:', 'REMOTE_INSTALL_KEY'])
