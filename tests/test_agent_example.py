from pathlib import Path

import yaml


def test_agent_import_contract_keeps_user_settings_out_of_model_arguments():
    raw = Path('examples/discover-and-vertical-agent.yml').read_text(encoding='utf-8')
    dsl = yaml.safe_load(raw)
    assert dsl['app']['mode'] == 'agent-chat'
    config = dsl['model_config']
    assert config['agent_mode']['enabled'] is True
    assert config['agent_mode']['strategy'] == 'function_call'
    tools = {tool['tool_name']: tool for tool in config['agent_mode']['tools']}
    assert set(tools) == {'discover', 'vertical_search'}
    assert tools['discover']['tool_parameters'] == {'refresh': False}
    assert tools['vertical_search']['tool_parameters'] == {
        'max_results': 3, 'zone': 'auto', 'language': '',
    }
    assert config['model']['provider'] == 'langgenius/deepseek/deepseek'
    assert config['user_input_form'] == []
    assert 'untrusted DATA' in config['pre_prompt']
    assert 'Do not invent a query' in config['pre_prompt']
    assert 'Do not claim all sources are adapted or verified' in config['pre_prompt']
    for forbidden in ('workspace_id:', 'tenant_id:', 'credential_id:',
                      'api_key:', 'REMOTE_INSTALL_KEY', 'app_id:'):
        assert forbidden not in raw


def test_agent_query_guidance_matches_academic_legal_and_dynamic_tag_contract():
    dsl = yaml.safe_load(Path('examples/discover-and-vertical-agent.yml').read_text(encoding='utf-8'))
    prompt = ' '.join(dsl['model_config']['pre_prompt'].split())
    assert 'Academic and legal tags require a nonempty query, including DOI lookup' in prompt
    assert 'ask the user for search terms before calling vertical_search' in prompt
    assert 'VERTICAL_QUERY_REQUIRED' in prompt
    assert 'For other vertical tags, judge query requirements per source' in prompt
    assert 'actual call evidence' in prompt
    assert 'An intentionally empty query remains empty' not in prompt
    readme = Path('examples/README.md').read_text(encoding='utf-8')
    assert 'VERTICAL_QUERY_REQUIRED' in readme
    assert '学术、法律' in readme
    assert '非空 query' in readme
