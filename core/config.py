from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from providers import OllamaProvider, TextProvider


DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "hf.co/Qwen/Qwen3-8B-GGUF:Q5_K_M"
DEFAULT_ENDPOINT = "http://localhost:11434"
DEFAULT_TIMEOUT = 180.0
DEFAULT_OLLAMA_THINKING = False


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    endpoint: str = DEFAULT_ENDPOINT
    timeout: float = DEFAULT_TIMEOUT
    ollama_thinking: bool = DEFAULT_OLLAMA_THINKING

    @classmethod
    def from_env(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "ProviderConfig":
        values = os.environ if environment is None else environment
        raw_timeout = values.get(
            "REPUBLICA_PROVIDER_TIMEOUT",
            str(DEFAULT_TIMEOUT),
        )

        try:
            timeout = float(raw_timeout)
        except ValueError as error:
            raise ValueError(
                "REPUBLICA_PROVIDER_TIMEOUT debe ser un número."
            ) from error

        if timeout <= 0:
            raise ValueError(
                "REPUBLICA_PROVIDER_TIMEOUT debe ser mayor que cero."
            )

        raw_thinking = values.get(
            "REPUBLICA_OLLAMA_THINKING",
            str(DEFAULT_OLLAMA_THINKING),
        ).strip().casefold()
        if raw_thinking not in {"true", "false"}:
            raise ValueError(
                "REPUBLICA_OLLAMA_THINKING debe ser true o false."
            )

        return cls(
            provider=values.get(
                "REPUBLICA_PROVIDER",
                DEFAULT_PROVIDER,
            ).strip().lower(),
            model=values.get(
                "REPUBLICA_MODEL",
                DEFAULT_MODEL,
            ).strip(),
            endpoint=values.get(
                "REPUBLICA_PROVIDER_ENDPOINT",
                DEFAULT_ENDPOINT,
            ).strip(),
            timeout=timeout,
            ollama_thinking=raw_thinking == "true",
        )


def create_provider(
    config: ProviderConfig | None = None,
) -> TextProvider:
    settings = config or ProviderConfig.from_env()

    if settings.provider != "ollama":
        raise ValueError(
            "Proveedor no soportado: "
            f"{settings.provider or '<vacío>'}. "
            "Actualmente sólo está disponible 'ollama'."
        )

    if not settings.model:
        raise ValueError("REPUBLICA_MODEL no puede estar vacío.")

    if not settings.endpoint:
        raise ValueError(
            "REPUBLICA_PROVIDER_ENDPOINT no puede estar vacío."
        )

    return OllamaProvider(
        model=settings.model,
        host=settings.endpoint,
        timeout=settings.timeout,
        thinking=settings.ollama_thinking,
    )
