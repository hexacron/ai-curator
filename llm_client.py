"""Minimal Anthropic Messages API client: one structured-JSON call with retries and token accounting.

Uses `output_config.format` (JSON-schema constrained decoding) so the reply is valid JSON in the first `text`
block; any `thinking` blocks the model emits are ignored. The key is read from the environment only and is
never logged or included in an error message.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_RETRIES = 3
MAX_RETRY_WAIT_SECONDS = 60
RETRYABLE_STATUSES = {429, 500, 502, 503, 504, 529}  # 529: overloaded
FAILED_STOP_REASONS = {'refusal', 'max_tokens'}      # the output may not match the schema


class LLMError(RuntimeError):
    """A call failed in a way that leaves no usable answer for this repository."""


class LLMAuthError(LLMError):
    """The key was rejected (401/403). Retrying cannot help, so the caller stops using the API for the run."""


@dataclass
class LLMResponse:
    data: Dict
    input_tokens: int
    output_tokens: int


class AnthropicClient:
    def __init__(self, api_key: str, timeout: float = 90, session: Optional[requests.Session] = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._timeout = timeout
        self._sleep = sleep
        self._session = session or requests.Session()
        self._session.headers.update({
            'x-api-key': api_key,
            'anthropic-version': API_VERSION,
            'content-type': 'application/json',
        })

    def __repr__(self) -> str:  # the key must never show up in logs or tracebacks
        return 'AnthropicClient(...)'

    @classmethod
    def from_env(cls, timeout: float = 90) -> Optional['AnthropicClient']:
        """A client if ANTHROPIC_API_KEY is set, else None (the tracker then runs rules-only)."""
        key = (os.getenv('ANTHROPIC_API_KEY') or '').strip()
        return cls(key, timeout) if key else None

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get('retry-after', '')
        if retry_after.isdigit():
            return min(float(retry_after), MAX_RETRY_WAIT_SECONDS)
        return min(2.0 ** (attempt + 1), MAX_RETRY_WAIT_SECONDS)

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        try:
            return str(response.json().get('error', {}).get('message', ''))[:200]
        except ValueError:
            return response.text[:200]

    def create(self, model: str, system: str, user: str, schema: dict, max_tokens: int, effort: str) -> LLMResponse:
        """Send one single-turn request whose reply must match `schema`."""
        body = {
            'model': model,
            'max_tokens': max_tokens,
            'system': system,
            'messages': [{'role': 'user', 'content': user}],
            'output_config': {'effort': effort, 'format': {'type': 'json_schema', 'schema': schema}},
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._session.post(API_URL, json=body, timeout=self._timeout)
            except requests.exceptions.RequestException as e:
                error, delay = f"request failed: {type(e).__name__}", min(2.0 ** (attempt + 1), MAX_RETRY_WAIT_SECONDS)
            else:
                if response.status_code == 200:
                    return self._parse(response)
                if response.status_code in (401, 403):
                    raise LLMAuthError(f"Anthropic rejected the API key (status {response.status_code})")
                error = f"status {response.status_code}: {self._error_message(response)}"
                if response.status_code not in RETRYABLE_STATUSES:
                    raise LLMError(f"Anthropic request failed ({error})")
                delay = self._retry_delay(response, attempt)

            if attempt == MAX_RETRIES:
                raise LLMError(f"Anthropic request failed after {MAX_RETRIES + 1} attempts ({error})")
            logger.warning(f"Anthropic request failed ({error}); retrying in {delay:.0f}s ({attempt + 1}/{MAX_RETRIES})")
            self._sleep(delay)
        raise AssertionError('unreachable')

    @staticmethod
    def _parse(response: requests.Response) -> LLMResponse:
        try:
            body = response.json()
        except ValueError as e:
            raise LLMError(f"Anthropic returned a non-JSON body: {e}")

        stop_reason = body.get('stop_reason')
        if stop_reason in FAILED_STOP_REASONS:
            raise LLMError(f"Anthropic stopped with '{stop_reason}'; the output cannot be trusted to match the schema")

        text = next((block.get('text') for block in body.get('content') or [] if block.get('type') == 'text'), None)
        if text is None:
            raise LLMError("Anthropic returned no text block")
        try:
            data = json.loads(text)
        except ValueError as e:
            raise LLMError(f"Anthropic returned text that is not JSON: {e}")
        if not isinstance(data, dict):
            raise LLMError("Anthropic returned JSON that is not an object")

        usage = body.get('usage') or {}
        return LLMResponse(data, int(usage.get('input_tokens') or 0), int(usage.get('output_tokens') or 0))
