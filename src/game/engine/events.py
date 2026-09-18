"""Small synchronous event dispatcher for Engine V2.

Dispatch is deliberately synchronous.  A Pygame frame may start a flow and
later resume it with a response, but hooks themselves should finish quickly
and deterministically.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Hashable, List, Optional


class EventType(str, Enum):
    """Lifecycle events supplied by the V2 foundation.

    Card, damage, phase, and judgment events will be added with the first real
    vertical slices instead of guessing their payloads in the foundation step.
    """

    ATOM_BEFORE = "atom.before"
    ATOM_AFTER = "atom.after"
    ATOM_CANCELLED = "atom.cancelled"
    FLOW_STARTED = "flow.started"
    FLOW_WAITING = "flow.waiting"
    FLOW_RESUMED = "flow.resumed"
    FLOW_FINISHED = "flow.finished"
    FLOW_CANCELLED = "flow.cancelled"
    CARD_USE_BEFORE = "card.use.before"
    CARD_USED = "card.used"
    TARGET_SELECTED = "card.target.selected"
    BECOME_TARGET = "card.target.became"
    CARD_EFFECT_BEFORE = "card.effect.before"
    CARD_EFFECT_AFTER = "card.effect.after"
    CARD_USE_FINISHED = "card.use.finished"
    PENDING_CREATED = "pending.created"
    PENDING_RESOLVED = "pending.resolved"
    DAMAGE_CREATED = "damage.created"
    DAMAGE_SOURCE_BEFORE = "damage.source.before"
    DAMAGE_TARGET_BEFORE = "damage.target.before"
    DAMAGE_MODIFY = "damage.modify"
    DAMAGE_APPLIED = "damage.applied"
    DAMAGE_SOURCE_AFTER = "damage.source.after"
    DAMAGE_TARGET_AFTER = "damage.target.after"
    DYING_ENTERED = "dying.entered"
    DYING_EXITED = "dying.exited"
    DEATH = "death"
    JUDGE_STARTED = "judge.started"
    JUDGE_REVEALED = "judge.revealed"
    JUDGE_FINISHED = "judge.finished"
    EQUIPMENT_LOST = "equipment.lost"
    EQUIPMENT_EQUIPPED = "equipment.equipped"
    TURN_START = "turn.start"
    TURN_END = "turn.end"
    PHASE_START = "phase.start"
    PHASE_END = "phase.end"
    CHAIN_STATE_CHANGED = "chain.state.changed"
    JUDGE_BEFORE_RESULT = "judge.before_result"
    JUDGE_RESULT = "judge.result"


@dataclass
class Event:
    name: Hashable
    source: Any = None
    target: Any = None
    payload: Dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False
    propagation_stopped: bool = False

    def cancel(self) -> None:
        self.cancelled = True

    def stop_propagation(self) -> None:
        self.propagation_stopped = True


EventHandler = Callable[["GameContext", Event], None]


@dataclass(frozen=True)
class _Subscription:
    token: int
    handler: EventHandler
    priority: int
    order: int
    owner: Any = None


class EventDispatcher:
    """Priority-ordered dispatcher that is safe to modify during dispatch."""

    def __init__(self) -> None:
        self._subscriptions: Dict[Hashable, List[_Subscription]] = {}
        self._next_token = 1
        self._next_order = 1

    def subscribe(
        self,
        event_name: Hashable,
        handler: EventHandler,
        *,
        priority: int = 0,
        owner: Any = None,
    ) -> int:
        if not callable(handler):
            raise TypeError("event handler must be callable")

        token = self._next_token
        self._next_token += 1
        subscription = _Subscription(
            token=token,
            handler=handler,
            priority=int(priority),
            order=self._next_order,
            owner=owner,
        )
        self._next_order += 1

        listeners = self._subscriptions.setdefault(event_name, [])
        listeners.append(subscription)
        listeners.sort(key=lambda item: (-item.priority, item.order))
        return token

    def unsubscribe(self, token: int) -> bool:
        for event_name, listeners in list(self._subscriptions.items()):
            for listener in listeners:
                if listener.token != token:
                    continue
                listeners.remove(listener)
                if not listeners:
                    del self._subscriptions[event_name]
                return True
        return False

    def unsubscribe_owner(self, owner: Any) -> int:
        removed = 0
        for event_name, listeners in list(self._subscriptions.items()):
            kept = [item for item in listeners if item.owner is not owner]
            removed += len(listeners) - len(kept)
            if kept:
                self._subscriptions[event_name] = kept
            else:
                del self._subscriptions[event_name]
        return removed

    def dispatch(self, context: "GameContext", event: Event) -> Event:
        # Snapshotting avoids skipped or duplicated handlers when a hook
        # subscribes/unsubscribes while the current event is being dispatched.
        listeners = tuple(self._subscriptions.get(event.name, ()))
        for listener in listeners:
            listener.handler(context, event)
            if event.propagation_stopped:
                break
        return event

    def clear(self) -> None:
        self._subscriptions.clear()


# Imported only for static type checking; runtime imports stay acyclic.
try:
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from .context import GameContext
except ImportError:  # pragma: no cover - supported Python versions provide it
    pass
