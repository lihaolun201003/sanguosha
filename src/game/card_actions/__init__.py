"""统一 Card Action / Conversion 查询层。

    game.card_actions.discover(...)   → 现在能做什么
    game.card_actions.source_candidates(...)

UI、AI、响应、救援共用同一份实现。
"""

from .context import (
    CONTEXTS,
    ActionKind,
    CardActionContext,
    card_uid,
    conversion_action_id,
    normal_action_id,
    source_uid,
)
from .discovery import CardActionDiscovery
from .option import CardActionOption

__all__ = [
    "CONTEXTS",
    "ActionKind",
    "CardActionContext",
    "CardActionDiscovery",
    "CardActionOption",
    "card_uid",
    "conversion_action_id",
    "normal_action_id",
    "source_uid",
]
