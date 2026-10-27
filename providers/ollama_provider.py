from __future__ import annotations

from typing import Any

import requests


class OllamaProviderError(RuntimeError):
    pass


class OllamaProvider:
    def __init__(
        self,
        model: str = "hf.co/Qwen/Qwen3-8B-GGUF:Q5_K_M",
        host: str = "http://localhost:11434",
        timeout: float = 180,
        thinking: bool = False,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.thinking = thinking

    def ask(self, prompt: str) -> str:
        return self._generate(prompt)

    def ask_structured(self, prompt: str, schema: dict[str, Any]) -> str:
        return self._generate(prompt, schema)

    def _generate(self, prompt: str, schema: dict[str, Any] | None = None) -> str:
        request: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "30s",
            "think": self.thinking,
        }
        if schema is not None:
            request["format"] = schema
        try:
            response = requests.post(
                f"{self.host}/api/generate",
                json=request,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise OllamaProviderError(
                "No fue posible consultar Ollama."
            ) from error

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as error:
            raise OllamaProviderError(
                "Ollama devolvió una respuesta JSON inválida."
            ) from error

        generated = payload.get("response")
        if not isinstance(generated, str):
            raise OllamaProviderError(
                "La respuesta de Ollama no contiene texto generado."
            )

        generated = generated.strip()
        if not generated:
            raise OllamaProviderError(
                "Ollama devolvió una respuesta vacía."
            )

        return generated
