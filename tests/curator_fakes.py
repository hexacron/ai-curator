"""Shared fakes for the curator's LLM-review tests: GitHub GraphQL README answers, a per-model LLM, repos."""
from types import SimpleNamespace

from curator import LLM_DEFAULTS
from github_client import README_PATHS, GitHubAPIError
from llm_client import LLMResponse

HAIKU, SONNET = LLM_DEFAULTS['model'], LLM_DEFAULTS['confirm_model']


def reply(**overrides):
    base = {'relevance': 'core', 'relevance_reason': 'An AI agent that runs pentest tools.', 'category': 'ai-for-security',
            'summary': 'Agent that runs security tools for pentesters.', 'utility': 4,
            'utility_evidence': ['Runs 150 security tools'], 'confidence': 0.9}
    base.update(overrides)
    return base


def repo(repo_id, stars=100, pushed='2026-10-10', **overrides):
    values = dict(repo_id=repo_id, node_id=f'N{repo_id}', name=f'tool{repo_id}', full_name=f'o/tool{repo_id}',
                  description=f'Tool {repo_id}: runs security checks with an AI agent', topics=['ai', 'security'],
                  language='Python', last_updated=pushed, stars=stars, review=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def readme(repo_id):
    return f"# tool{repo_id}\n\nRuns 150 security tools for pentest.\n" + "More details about the tool. " * 10


class FakeGraphQL:
    """Answers the README query: {node id: text}; a missing id is a deleted repository, '' has no README."""

    def __init__(self, readmes, fail_on_call=None):
        self.readmes, self.calls, self.fail_on_call = dict(readmes), [], fail_on_call

    def graphql(self, query, variables=None):
        self.calls.append(list(variables['ids']))
        if self.fail_on_call == len(self.calls):
            raise GitHubAPIError('simulated GraphQL failure')
        nodes = []
        for node_id in variables['ids']:
            if node_id not in self.readmes:
                nodes.append(None)
                continue
            text = self.readmes[node_id]
            node = {f'readme{i}': None for i in range(len(README_PATHS))}
            if text:
                node['readme0'] = {'byteSize': len(text), 'text': text}
            nodes.append(node)
        return {'nodes': nodes}


class FakeLLM:
    """Replies per model (a dict or a callable(user_message)); `errors` is a per-call sequence (None = succeed)."""

    def __init__(self, by_model=None, errors=None):
        self.by_model = by_model or {HAIKU: reply(), SONNET: reply()}
        self.errors, self.calls = list(errors or []), []

    def create(self, model, system, user, schema, max_tokens, effort):
        self.calls.append({'model': model, 'user': user, 'system': system, 'schema': schema})
        failure = self.errors.pop(0) if self.errors else None
        if failure:
            raise failure
        chosen = self.by_model[model]
        return LLMResponse(chosen(user) if callable(chosen) else dict(chosen), 1000, 200)

    def models(self):
        return [call['model'] for call in self.calls]
