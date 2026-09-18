from .base import CardEffect, CardEffectRegistry
from .sha import ShaEffect
from .tricks import TRICK_EFFECTS

def create_default_registry():
    registry = CardEffectRegistry()
    registry.register(ShaEffect())
    for effect_type in TRICK_EFFECTS:
        registry.register(effect_type())
    return registry


__all__ = ["CardEffect", "CardEffectRegistry", "ShaEffect", "create_default_registry"]
