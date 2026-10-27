"""Native structured generation for public parliament turns; no tools or retries."""
import http.client
import json

from providers.delegated_ollama import (
    DelegatedOllamaProvider, LocalResponseInvalid, LocalResponseLimit,
    MAX_ANSWER_CHARS, MAX_HTTP_BYTES, LocalProviderError, _unique, _constant,
)

TURN_SCHEMA = {
    'type': 'object',
    'properties': {
        'argumento': {'type': 'string', 'minLength': 1, 'maxLength': 160},
        'objecion': {'type': 'string', 'minLength': 1, 'maxLength': 160},
        'incertidumbre': {'type': 'string', 'minLength': 1, 'maxLength': 160},
        'postura': {'type': 'string', 'enum': ['a_favor', 'en_contra', 'abstencion']},
        'evidencia': {'type': 'array', 'items': {'type': 'string', 'enum': ['E1', 'E2']},
                      'maxItems': 2},
    },
    'required': ['argumento', 'objecion', 'incertidumbre', 'postura', 'evidencia'],
    'additionalProperties': False,
}


class LocalThinkingUnsupported(LocalProviderError):
    """The selected model explicitly cannot honor this adapter's think=false."""


class ParliamentOllamaProvider(DelegatedOllamaProvider):
    def _check_thinking_support(self):
        if getattr(self, '_thinking_support_checked', False):
            return
        connection = http.client.HTTPConnection('127.0.0.1', 11434, timeout=self.timeout)
        try:
            connection.request('POST', '/api/show', json.dumps({'model': self.model}),
                               {'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(MAX_HTTP_BYTES + 1)
            if response.status != 200:
                raise LocalProviderError('model_metadata_failed')
        except (OSError, http.client.HTTPException) as error:
            raise LocalProviderError('model_metadata_failed') from error
        finally:
            connection.close()
        if len(raw) > MAX_HTTP_BYTES:
            raise LocalResponseLimit('model_metadata_limit')
        try:
            metadata = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                                  parse_constant=_constant)
            if not isinstance(metadata, dict) or 'error' in metadata:
                raise ValueError('invalid model metadata')
            thinking = metadata.get('thinking')
            if thinking is not None:
                if (not isinstance(thinking, dict) or
                        not isinstance(thinking.get('values'), list) or
                        not thinking['values'] or
                        any(type(v) not in (bool, str) for v in thinking['values'])):
                    raise ValueError('invalid thinking metadata')
                if not any(v is False for v in thinking['values']):
                    raise LocalThinkingUnsupported('model_cannot_disable_thinking')
        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
            raise LocalResponseInvalid('invalid_model_metadata') from error
        # Older servers without metadata retain their previous behavior. Absence
        # is not a claim that think=false is supported; only explicit incompatibility blocks.
        self._thinking_support_checked = True

    def ask(self, prompt: str) -> str:
        self._check_thinking_support()
        text = self._generate(prompt, TURN_SCHEMA).strip()
        try:
            text.encode('utf-8')
        except UnicodeError as error:
            raise LocalResponseInvalid('invalid_local_response') from error
        if not text:
            raise LocalResponseInvalid('invalid_local_response')
        if len(text) > MAX_ANSWER_CHARS:
            raise LocalResponseLimit('answer_limit')
        # The unchanged turn validator remains authoritative: schema support is
        # partial (e.g. uniqueness, whitespace-only text and controls still need it).
        return text
