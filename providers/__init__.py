from .base import StructuredTextProvider, TextProvider


def __getattr__(name):
    # Importing the cognitive protocol must not initialize an HTTP client.
    if name in {"OllamaProvider", "OllamaProviderError"}:
        from .ollama_provider import OllamaProvider, OllamaProviderError
        return {"OllamaProvider": OllamaProvider, "OllamaProviderError": OllamaProviderError}[name]
    raise AttributeError(name)


__all__ = [
    "OllamaProvider",
    "OllamaProviderError",
    "TextProvider",
    "StructuredTextProvider",
]
