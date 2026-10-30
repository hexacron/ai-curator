import json

import pytest

import llm_client as llm
from llm_client import AnthropicClient, LLMAuthError, LLMError

KEY = 'sk-ant-test-secret-value'
SCHEMA = {'type': 'object', 'properties': {'a': {'type': 'integer'}}, 'required': ['a'], 'additionalProperties': False}


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError('not json')
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.headers = {}
        self.responses = list(responses)
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append({'url': url, 'json': json, 'timeout': timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ok(text='{"a": 1}', stop='end_turn', content=None):
    return FakeResponse(200, {'stop_reason': stop, 'usage': {'input_tokens': 120, 'output_tokens': 30},
                              'content': content or [{'type': 'text', 'text': text}]})


def client(*responses, sleeps=None):
    session = FakeSession(*responses)
    return AnthropicClient(KEY, timeout=45, session=session, sleep=(sleeps.append if sleeps is not None else lambda s: None)), session


def call(c):
    return c.create(model='claude-haiku-5-5', system='sys', user='usr', schema=SCHEMA, max_tokens=4000, effort='low')


def test_the_request_carries_the_key_version_schema_and_effort():
    c, session = client(ok())

    call(c)

    post = session.posts[0]
    assert post['url'] == 'https://api.anthropic.com/v1/messages' and post['timeout'] == 45
    assert session.headers['x-api-key'] == KEY and session.headers['anthropic-version'] == '2023-06-01'
    assert post['json'] == {
        'model': 'claude-haiku-5-5', 'max_tokens': 4000, 'system': 'sys',
        'messages': [{'role': 'user', 'content': 'usr'}],
        'output_config': {'effort': 'low', 'format': {'type': 'json_schema', 'schema': SCHEMA}},
    }


def test_the_json_comes_from_the_text_block_and_thinking_blocks_are_ignored():
    content = [{'type': 'thinking', 'thinking': 'hmm {"a": 99}'}, {'type': 'text', 'text': '{"a": 7}'}]
    c, _ = client(ok(content=content))

    response = call(c)

    assert response.data == {'a': 7}
    assert (response.input_tokens, response.output_tokens) == (120, 30)


@pytest.mark.parametrize('stop', ['refusal', 'max_tokens'])
def test_refused_or_truncated_output_is_a_failure_without_retry(stop):
    c, session = client(ok(stop=stop))

    with pytest.raises(LLMError):
        call(c)
    assert len(session.posts) == 1


def test_a_rejected_key_is_an_auth_error_and_is_not_retried():
    c, session = client(FakeResponse(401, {'error': {'message': 'invalid x-api-key'}}))

    with pytest.raises(LLMAuthError):
        call(c)
    assert len(session.posts) == 1


def test_rate_limits_wait_for_retry_after_then_succeed():
    sleeps = []
    c, session = client(FakeResponse(429, {'error': {'message': 'slow'}}, {'retry-after': '7'}), ok(), sleeps=sleeps)

    assert call(c).data == {'a': 1}
    assert sleeps == [7.0] and len(session.posts) == 2


def test_persistent_server_errors_give_up_after_the_retry_budget():
    c, session = client(*[FakeResponse(529, {'error': {'message': 'overloaded'}}) for _ in range(llm.MAX_RETRIES + 1)])

    with pytest.raises(LLMError):
        call(c)
    assert len(session.posts) == llm.MAX_RETRIES + 1


def test_other_client_errors_fail_at_once_and_never_leak_the_key():
    c, session = client(FakeResponse(400, {'error': {'message': 'schema not supported'}}))

    with pytest.raises(LLMError) as raised:
        call(c)

    assert len(session.posts) == 1
    assert KEY not in str(raised.value)


@pytest.mark.parametrize('text', ['not json', '[1, 2]'])
def test_text_that_is_not_a_json_object_is_a_failure(text):
    c, _ = client(ok(text=text))

    with pytest.raises(LLMError):
        call(c)


def test_the_key_comes_from_the_environment_and_is_hidden_from_repr(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    assert AnthropicClient.from_env() is None

    monkeypatch.setenv('ANTHROPIC_API_KEY', f'  {KEY}\n')
    built = AnthropicClient.from_env()

    assert built is not None and KEY not in repr(built)
    assert built._session.headers['x-api-key'] == KEY
