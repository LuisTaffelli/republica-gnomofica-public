"""Single bounded loopback generation for the isolated synthetic turn adapter."""
from __future__ import annotations

import http.client
import json


MAX_HTTP_BYTES = 32768
MAX_PROMPT_BYTES = 16384
MAX_ANSWER_CHARS = 1024
MAX_GENERATION_TOKENS = 256
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class LocalProviderError(RuntimeError):
    pass


class LocalResponseInvalid(LocalProviderError):
    pass


class LocalResponseLimit(LocalProviderError):
    pass


class LocalGenerationLimit(LocalProviderError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("non-finite JSON")


class DelegatedOllamaProvider:
    """TextProvider; fixed IPv4 loopback, no proxy, redirects, retries or tools.

    Kept separate from interactive Ollama configuration: this launcher's bounds
    must not change existing chat/voice behavior or depend on persistent settings.
    The supervising CLI owns the total deadline, including a stalled HTTP body.
    """

    def __init__(self, model: str, timeout: float) -> None:
        self.model = model
        self.timeout = timeout

    def _generate(self, prompt: str, schema: dict) -> str:
        if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise LocalResponseLimit("prompt_limit")
        body = json.dumps({
            "model": self.model, "prompt": prompt, "stream": False,
            "think": False, "format": schema, "keep_alive": "30s",
            "options": {"num_predict": MAX_GENERATION_TOKENS, "num_ctx": 4096, "temperature": 0},
        }, ensure_ascii=True).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=self.timeout)
        try:
            connection.request("POST", "/api/generate", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise LocalProviderError("http_failure")
            raw = response.read(MAX_HTTP_BYTES + 1)
        except (OSError, http.client.HTTPException) as error:
            raise LocalProviderError("local_transport_failed") from error
        finally:
            connection.close()
        if len(raw) > MAX_HTTP_BYTES:
            raise LocalResponseLimit("http_response_limit")
        try:
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
            if not isinstance(payload, dict) or payload.get("done") is not True or "error" in payload:
                raise ValueError("incomplete generation")
            if payload.get("done_reason") == "length":
                raise LocalGenerationLimit("generation_limit")
            if payload.get("done_reason") != "stop":
                raise ValueError("unknown stop reason")
            generated = payload.get("response")
            if not isinstance(generated, str):
                raise ValueError("missing response")
        except (ValueError, TypeError, RecursionError) as error:
            raise LocalResponseInvalid("invalid_local_response") from error
        return generated

    def ask(self, prompt: str) -> str:
        generated = self._generate(prompt, ANSWER_SCHEMA)
        try:
            answer = json.loads(generated, object_pairs_hook=_unique, parse_constant=_constant)
            if (not isinstance(answer, dict) or set(answer) != {"answer"}
                    or not isinstance(answer["answer"], str) or not answer["answer"].strip()):
                raise ValueError("invalid answer")
            text = answer["answer"].strip()
            # Validate Unicode before the turn receives it (JSON permits surrogates).
            text.encode("utf-8")
        except (ValueError, TypeError, RecursionError) as error:
            raise LocalResponseInvalid("invalid_local_response") from error
        if len(text) > MAX_ANSWER_CHARS:
            raise LocalResponseLimit("answer_limit")
        return text
