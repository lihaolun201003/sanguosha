"""Domain actions submitted by human and AI input adapters."""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence


class GameAction:
    """Marker base class for game intent, not animation work."""


@dataclass
class UseCardAction(GameAction):
    actor: Any
    card: Any
    targets: Sequence[Any]
    source_rect: Any = None
    ignore_usage_limit: bool = False
    on_complete: Optional[Callable[[Any], None]] = None
    metadata: dict = field(default_factory=dict)

    @property
    def actor_id(self):
        return self.actor.player_id

    @property
    def target_ids(self):
        return tuple(target.player_id for target in self.targets)


@dataclass
class RespondCardAction(GameAction):
    actor: Any
    request_id: int
    card: Any
    source_rect: Any = None

    @property
    def actor_id(self):
        return self.actor.player_id


@dataclass
class PassPendingAction(GameAction):
    actor: Any
    request_id: int


@dataclass
class ConfirmPendingAction(GameAction):
    actor: Any
    request_id: int
    confirmed: bool


@dataclass
class SelectCardsAction(GameAction):
    actor: Any
    request_id: int
    cards: Sequence[Any]


@dataclass
class ChooseOptionAction(GameAction):
    actor: Any
    request_id: int
    option: Any
