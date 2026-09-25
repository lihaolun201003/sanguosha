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
    # 响应窗口里打出了一张牌（【闪】/【杀】/【无懈可击】/【桃】）。
    # payload: actor / card / request / reason。与 CARD_USED 分开：
    # CARD_USED 是"使用"，响应是"打出"，两者的时机条件不同（银月枪一类）。
    CARD_RESPONDED = "card.responded"
    TARGET_SELECTED = "card.target.selected"
    BECOME_TARGET = "card.target.became"
    CARD_EFFECT_BEFORE = "card.effect.before"
    CARD_EFFECT_AFTER = "card.effect.after"
    # 一张【杀】被【闪】抵消（= 命中失败）。装备与武将技能都从这里接。
    SHA_DODGED = "card.sha.dodged"
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
    # 伤害完全结算完毕（含濒死与死亡处理）之后，供"受到伤害后"类技能使用。
    DAMAGE_SETTLED = "damage.settled"
    # 有角色回复了体力。payload: target / amount / source（施治者，可能为 None）。
    HP_RECOVERED = "hp.recovered"
    DYING_ENTERED = "dying.entered"
    DYING_EXITED = "dying.exited"
    DEATH = "death"
    JUDGE_STARTED = "judge.started"
    JUDGE_REVEALED = "judge.revealed"
    # 改判窗口里判定牌真的被替换了（鬼才一类）：UI 据此表现"原判定牌被改"。
    JUDGE_REPLACED = "judge.replaced"
    JUDGE_FINISHED = "judge.finished"
    EQUIPMENT_LOST = "equipment.lost"
    EQUIPMENT_EQUIPPED = "equipment.equipped"
    TURN_START = "turn.start"
    TURN_END = "turn.end"
    PHASE_START = "phase.start"
    PHASE_END = "phase.end"
    CHAIN_STATE_CHANGED = "chain.state.changed"
    # 有任何牌进入弃牌堆。payload: card / reason / owner / from。
    # 这是**唯一**的"弃牌"出口（由 MoveCardAtom 在目的地是弃牌堆时发出），
    # 因此琴音 / 落英 / 固政这类技能不需要各自去猜弃牌发生在哪条代码路径。
    CARD_DISCARDED = "card.discarded"
    # 有牌离开了某名角色的区域但**没有**进弃牌堆（被拿走 / 送出去 / 被装备）。
    # 「失去牌」类技能（屯田）读它；弃牌走 CARD_DISCARDED，两者不重复。
    CARD_LOST = "card.lost"
    # 濒死求桃全部失败、即将结算死亡之前。技能可以在这里把
    # payload["prevented"] 置为 True 来阻止这次死亡（不屈一类）。
    DYING_BEFORE_DEATH = "dying.before_death"
    # 武将牌翻面状态改变。payload: face_up / reason。
    FLIPPED = "player.flipped"
    SKILL_TRIGGERED = "skill.triggered"
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
