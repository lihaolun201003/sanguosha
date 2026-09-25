"""Engine V2 primitives used during the gradual migration."""

from .atoms import Atom, AtomResult, apply_atom
from .context import GameContext
from .domain_actions import (
    ActivateSkillAction,
    ChooseOptionAction,
    ConfirmPendingAction,
    GameAction,
    PassPendingAction,
    RespondCardAction,
    SelectCardsAction,
    SelectTargetsAction,
    UseCardAction,
)
from .events import Event, EventDispatcher, EventType
from .flows import Flow, FlowResult, FlowStatus
from .pending import (
    PendingManager,
    PendingRequest,
    PendingRequestType,
    PendingResolution,
)
from .runtime import GameEngine
from .skills import Skill, SkillBinding
from .state import GameOutcome, GameResult

__all__ = [
    "Atom",
    "AtomResult",
    "Event",
    "EventDispatcher",
    "EventType",
    "Flow",
    "FlowResult",
    "FlowStatus",
    "GameContext",
    "GameAction",
    "GameEngine",
    "GameOutcome",
    "GameResult",
    "UseCardAction",
    "RespondCardAction",
    "PassPendingAction",
    "ConfirmPendingAction",
    "SelectCardsAction",
    "SelectTargetsAction",
    "ChooseOptionAction",
    "PendingManager",
    "PendingRequest",
    "PendingRequestType",
    "PendingResolution",
    "Skill",
    "SkillBinding",
    "apply_atom",
]
