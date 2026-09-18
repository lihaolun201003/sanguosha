"""Single authoritative pending request for resumable Engine V2 flows."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, FrozenSet, Optional, Sequence

from .events import Event, EventType


class PendingRequestType(str, Enum):
    RESPOND_CARD = "respond_card"
    CONFIRM = "confirm"
    SELECT_CARDS = "select_cards"
    CHOOSE_OPTION = "choose_option"


@dataclass
class PendingRequest:
    request_id: int
    request_type: PendingRequestType
    source: Any
    target: Any
    prompt: str
    allowed_cards: FrozenSet[str] = frozenset()
    min_cards: int = 0
    max_cards: int = 0
    options: Sequence[Any] = ()
    context: dict = field(default_factory=dict)
    owner_flow: Any = field(default=None, repr=False)
    status: str = "pending"

    @property
    def owner_id(self):
        return getattr(self.target, "player_id", None)

    @property
    def source_id(self):
        return getattr(self.source, "player_id", None)

    @property
    def target_ids(self):
        targets = self.context.get("targets", ())
        return tuple(getattr(target, "player_id", target) for target in targets)

    @property
    def allowed_card_ids(self):
        return tuple(getattr(card, "card_id", getattr(card, "id", None)) for card in self.context.get("candidates", ()))


@dataclass
class PendingResolution:
    request: PendingRequest
    actor: Any
    card: Any = None
    cards: Sequence[Any] = ()
    option: Any = None
    confirmed: Optional[bool] = None
    passed: bool = False
    source_rect: Any = None


class PendingManager:
    def __init__(self, context):
        self.context = context
        self._stack = []
        self._next_request_id = 1

    @property
    def active(self):
        return self.current is not None

    @property
    def current(self):
        return self._stack[-1] if self._stack else None

    @property
    def stack(self):
        return tuple(self._stack)

    def clear(self):
        for request in self._stack:
            request.status = "cancelled"
        self._stack.clear()

    def create(
        self,
        request_type,
        *,
        source,
        target,
        prompt,
        owner_flow,
        allowed_cards=(),
        min_cards=0,
        max_cards=0,
        options=(),
        request_context=None,
    ):
        request = PendingRequest(
            request_id=self._next_request_id,
            request_type=request_type,
            source=source,
            target=target,
            prompt=prompt,
            allowed_cards=frozenset(allowed_cards),
            min_cards=int(min_cards),
            max_cards=int(max_cards),
            options=tuple(options),
            context=dict(request_context or {}),
            owner_flow=owner_flow,
        )
        self._next_request_id += 1
        self._stack.append(request)
        self.context.emit(
            Event(
                EventType.PENDING_CREATED,
                source=source,
                target=target,
                payload={"request": request},
            )
        )
        return request

    def require(self, request_id):
        request = self.current
        if request is None or request.request_id != request_id:
            raise ValueError("PendingRequest is missing or no longer current")
        return request

    def take(self, request_id):
        request = self.require(request_id)
        self._stack.pop()
        request.status = "resolved"
        return request

    def emit_resolved(self, resolution):
        self.context.emit(
            Event(
                EventType.PENDING_RESOLVED,
                source=resolution.request.source,
                target=resolution.actor,
                payload={"resolution": resolution},
            )
        )
