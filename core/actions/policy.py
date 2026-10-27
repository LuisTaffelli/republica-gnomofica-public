from __future__ import annotations

from enum import Enum

from .models import ActionIntent, EffectKind


class ActionDecision(str, Enum):
    ALLOWED = "allowed"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


class ActionPolicy:
    """Clasificación inicial de autoridad, independiente del modelo."""

    def decide(self, intent: ActionIntent) -> ActionDecision:
        if intent.effect_kind is EffectKind.FORBIDDEN:
            return ActionDecision.DENIED
        if intent.effect_kind is EffectKind.EFFECTFUL:
            return ActionDecision.APPROVAL_REQUIRED
        return ActionDecision.ALLOWED
