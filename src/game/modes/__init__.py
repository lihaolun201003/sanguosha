"""Game modes: free-for-all, standard identity and the 1v1 test mode."""

from .base import (
    DeathResolution,
    GameMode,
    GameModeRegistry,
    create_default_mode_registry,
)
from .duel import DuelConfig, DuelTestMode, config_of
from .ffa import FreeForAllMode
from .identity import IdentityMode

__all__ = [
    "DeathResolution",
    "DuelConfig",
    "DuelTestMode",
    "FreeForAllMode",
    "GameMode",
    "GameModeRegistry",
    "IdentityMode",
    "config_of",
    "create_default_mode_registry",
]
