"""Alias transitorio: una sola implementación de construcción de contexto.

La API retorna ContextProjection, no el antiguo prompt plano.
"""

from core.context.context_manager import ContextManager as ContextBuilder


__all__ = ["ContextBuilder"]
