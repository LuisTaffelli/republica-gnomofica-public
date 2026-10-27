from typing import Any, Protocol, runtime_checkable


class TextProvider(Protocol):
    """Contrato mínimo que consume el núcleo conversacional actual."""

    def ask(self, prompt: str) -> str: ...


@runtime_checkable
class StructuredTextProvider(TextProvider, Protocol):
    """Salida con esquema por llamada; el consumidor sigue validando el texto."""

    def ask_structured(self, prompt: str, schema: dict[str, Any]) -> str: ...
