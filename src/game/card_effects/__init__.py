from .base import CardEffect, CardEffectRegistry
from .basic import BASIC_EFFECTS
from .equipment import EquipEffect
from .sha import ShaEffect
from .tricks import TRICK_EFFECTS

def create_default_registry():
    registry = CardEffectRegistry()
    registry.register(ShaEffect())
    registry.register(EquipEffect())
    for effect_type in BASIC_EFFECTS:
        registry.register(effect_type())
    for effect_type in TRICK_EFFECTS:
        registry.register(effect_type())
    return registry


__all__ = ["CardEffect", "CardEffectRegistry", "EquipEffect", "ShaEffect", "create_default_registry"]
