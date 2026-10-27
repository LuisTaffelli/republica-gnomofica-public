"""Offline contract tests; HTTP is always replaced, no local service required."""
import json
from unittest.mock import patch
import pytest
from cli import parliament_cli as p
from providers import delegated_ollama as local
from providers.parliament_ollama import ParliamentOllamaProvider, TURN_SCHEMA, LocalThinkingUnsupported

VALID = dict(argumento='Propuesta breve.', objecion='Falta una medición.',
             incertidumbre='Resultado desconocido.', postura='abstencion', evidencia=['E1'])


def invoke(text, **changes):
    payload = dict(done=True, done_reason='stop', response=text, **changes)
    with patch.object(local.http.client, 'HTTPConnection') as connection:
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.side_effect = [json.dumps({'thinking': {'values': [True, False]}}).encode(), json.dumps(payload).encode()]
        result = ParliamentOllamaProvider('fake', 2).ask('synthetic public prompt')
        assert connection.return_value.close.call_count == 2
        return result, connection


def test_native_format_keeps_transport_and_generation_bounds():
    text = json.dumps(VALID)
    result, connection = invoke(text)
    assert result == text and p.validate(result) == VALID
    assert connection.call_count == 2
    connection.assert_called_with('127.0.0.1', 11434, timeout=2)
    method, path, raw, _headers = connection.return_value.request.call_args.args
    body = json.loads(raw)
    assert (method, path) == ('POST', '/api/generate')
    assert body['format'] == TURN_SCHEMA
    assert body['options'] == dict(num_predict=256, num_ctx=4096, temperature=0)
    assert body['think'] is False and body['stream'] is False
    assert body['keep_alive'] == '30s' and 'tools' not in body
    assert connection.return_value.request.call_count == 2
    assert connection.return_value.getresponse.return_value.read.call_count == 2
    connection.return_value.getresponse.return_value.read.assert_called_with(local.MAX_HTTP_BYTES + 1)


def test_schema_bounds_match_public_turn_contract():
    assert set(TURN_SCHEMA['required']) == p.FIELDS
    assert set(TURN_SCHEMA['properties']) == p.FIELDS
    assert TURN_SCHEMA['additionalProperties'] is False
    for key in ('argumento', 'objecion', 'incertidumbre'):
        assert TURN_SCHEMA['properties'][key] == dict(type='string', minLength=1, maxLength=160)
    assert TURN_SCHEMA['properties']['evidencia']['maxItems'] == 2


@pytest.mark.parametrize('changes', [
    dict(argumento='x' * 161), dict(argumento=' '), dict(objecion='a\nb'),
    dict(evidencia=['E1', 'E1']), dict(evidencia=['unknown']),
    dict(postura='execute'), dict(untrusted_extra='PRIVATE_SENTINEL'),
])
def test_native_schema_does_not_replace_or_relax_validator(changes):
    raw, _ = invoke(json.dumps({**VALID, **changes}))
    with pytest.raises(p.TurnValidationError):
        p.validate(raw)


@pytest.mark.parametrize('text,error', [('', local.LocalResponseInvalid),
                                        ('x' * 1025, local.LocalResponseLimit),
                                        ('\ud800', local.LocalResponseInvalid)])
def test_native_output_limits_remain_enforced(text, error):
    with pytest.raises(error):
        invoke(text)


def test_native_generation_limit_stays_failure():
    with patch.object(local.http.client, 'HTTPConnection') as connection:
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.side_effect = [json.dumps({'thinking': {'values': [False]}}).encode(),
                                     json.dumps(dict(done=True, done_reason='length', response=json.dumps(VALID))).encode()]
        with pytest.raises(local.LocalGenerationLimit):
            ParliamentOllamaProvider('fake', 2).ask('synthetic')
        assert connection.return_value.request.call_count == 2
        assert connection.return_value.close.call_count == 2


def test_thinking_only_blocks_before_generation_with_safe_diagnostic():
    with patch.object(local.http.client, 'HTTPConnection') as connection:
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"thinking":{"values":[true],"default":true}}'
        provider = ParliamentOllamaProvider('qwen3:4b', 2)
        with pytest.raises(LocalThinkingUnsupported) as caught:
            provider.ask('public fixture')
        assert connection.return_value.request.call_count == 1
        assert connection.return_value.request.call_args.args[1] == '/api/show'
        connection.return_value.close.assert_called_once()
        assert p.failure_diagnostic('provider', 'archivo', 1, caught.value) == dict(
            etapa='provider', rol='archivo', ronda=1, categoria='provider_thinking_unsupported')


@pytest.mark.parametrize('metadata', [{}, {'thinking': {'values': [False]}},
                                     {'thinking': {'values': [True, False]}}])
def test_metadata_check_cached_per_provider_without_changing_generation(metadata):
    with patch.object(local.http.client, 'HTTPConnection') as connection:
        response = connection.return_value.getresponse.return_value
        response.status = 200
        generated = json.dumps({'done': True, 'done_reason': 'stop', 'response': json.dumps(VALID)}).encode()
        response.read.side_effect = [json.dumps(metadata).encode(), generated, generated]
        provider = ParliamentOllamaProvider('fake', 2)
        assert p.validate(provider.ask('first')) == VALID
        assert p.validate(provider.ask('second')) == VALID
        assert [c.args[1] for c in connection.return_value.request.call_args_list] == [
            '/api/show', '/api/generate', '/api/generate']
        for c in connection.return_value.request.call_args_list[1:]:
            body = json.loads(c.args[2])
            assert body['think'] is False and body['format'] == TURN_SCHEMA
            assert body['options'] == dict(num_predict=256, num_ctx=4096, temperature=0)


@pytest.mark.parametrize('metadata', [[], {'thinking': {}}, {'thinking': {'values': []}},
                                     {'thinking': {'values': [0]}}, {'error': 'PRIVATE_SENTINEL'}])
def test_malformed_metadata_does_not_generate_or_expose_content(metadata):
    with patch.object(local.http.client, 'HTTPConnection') as connection:
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(metadata).encode()
        with pytest.raises(local.LocalResponseInvalid) as caught:
            ParliamentOllamaProvider('fake', 2).ask('public fixture')
        assert 'PRIVATE_SENTINEL' not in str(caught.value)
        assert connection.return_value.request.call_count == 1


def test_metadata_size_limit_precedes_generation():
    with patch.object(local.http.client, 'HTTPConnection') as connection:
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b'x' * (local.MAX_HTTP_BYTES + 1)
        with pytest.raises(local.LocalResponseLimit):
            ParliamentOllamaProvider('fake', 2).ask('public fixture')
        assert connection.return_value.request.call_count == 1
