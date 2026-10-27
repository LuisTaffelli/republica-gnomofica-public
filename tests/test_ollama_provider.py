from __future__ import annotations

import pytest
import requests

from providers import OllamaProvider


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


@pytest.mark.parametrize("thinking", [False, True])
def test_structured_thinking_setting_is_sent_without_modifying_prompt(
    monkeypatch, thinking,
):
    captured = {}

    def fake_post(url, *, json, timeout):
        captured.update(url=url, json=json, timeout=timeout)
        return FakeResponse({"response": "respuesta final"})

    monkeypatch.setattr("providers.ollama_provider.requests.post", fake_post)
    prompt = "prompt exacto sin workaround textual"
    provider = OllamaProvider(
        model="modelo-sin-acoplamiento", host="http://ollama.test/",
        timeout=12, thinking=thinking,
    )

    assert provider.ask(prompt) == "respuesta final"
    assert captured["json"]["think"] is thinking
    assert captured["json"]["prompt"] == prompt
    assert "/no_think" not in captured["json"]["prompt"]


def test_model_without_thinking_response_still_works_and_trace_is_not_returned(
    monkeypatch,
):
    responses = iter([
        FakeResponse({"response": "modelo común"}),
        FakeResponse({
            "response": "respuesta final normal",
            "thinking": "razonamiento interno que no es output",
        }),
    ])
    monkeypatch.setattr(
        "providers.ollama_provider.requests.post",
        lambda *args, **kwargs: next(responses),
    )
    provider = OllamaProvider(model="cualquiera", thinking=False)

    assert provider.ask("uno") == "modelo común"
    final = provider.ask("dos")
    assert final == "respuesta final normal"
    assert "razonamiento interno" not in final


@pytest.mark.parametrize("thinking", [False, True])
def test_structured_format_is_per_call_and_plain_calls_remain_unchanged(monkeypatch, thinking):
    calls = []
    schema = {"type": "object", "properties": {"final_answer": {"type": "string"}}}

    def fake_post(url, *, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse({"response": '{"final_answer":"hola"}', "thinking": "omitido"})

    monkeypatch.setattr("providers.ollama_provider.requests.post", fake_post)
    provider = OllamaProvider(model="modelo", host="http://ollama.test", timeout=12, thinking=thinking)
    provider.ask("antes")
    assert provider.ask_structured("decisión", schema) == '{"final_answer":"hola"}'
    provider.ask("después")

    assert all(url == "http://ollama.test/api/generate" and timeout == 12 for url, _, timeout in calls)
    assert [payload["prompt"] for _, payload, _ in calls] == ["antes", "decisión", "después"]
    assert all(payload["think"] is thinking for _, payload, _ in calls)
    assert calls[1][1]["format"] == schema
    assert "format" not in calls[0][1] and "format" not in calls[2][1]


def test_structured_response_is_not_repaired_or_retried(monkeypatch):
    raw = 'prefijo inválido </think>\n{"final_answer":"hola"}'
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse({"response": raw})

    monkeypatch.setattr("providers.ollama_provider.requests.post", fake_post)
    assert OllamaProvider().ask_structured("decisión", {"type": "object"}) == raw
    assert len(calls) == 1


def test_structured_transport_failure_does_not_fallback_to_plain_ask(monkeypatch):
    from providers import OllamaProviderError

    calls = []

    def failed_post(*args, **kwargs):
        calls.append(kwargs["json"])
        raise requests.ConnectionError("servicio ausente")

    monkeypatch.setattr("providers.ollama_provider.requests.post", failed_post)
    provider = OllamaProvider()
    monkeypatch.setattr(provider, "ask", lambda *args: pytest.fail("No debe usar ask como fallback"))
    with pytest.raises(OllamaProviderError, match="No fue posible consultar Ollama"):
        provider.ask_structured("decisión", {"type": "object"})
    assert len(calls) == 1 and "format" in calls[0]
