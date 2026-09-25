"""Concrete state-change atoms used by migrated combat flows."""

from dataclasses import dataclass

from .engine import Atom, AtomResult
from .engine.events import Event, EventType


def _remove_identity(items, value):
    for index, item in enumerate(items):
        if item is value:
            items.pop(index)
            return True
    return False


def _sync_granted_skills(game, player):
    """装备区的牌变了：重新收敛"装备赋予的技能"（丈八蛇矛一类）。

    延迟导入：``equipment_skills`` 包反过来要用本模块的原子。
    """

    from .equipment_skills.granted import sync_equipment_skills

    sync_equipment_skills(game, player)


@dataclass
class MoveCardAtom(Atom):
    card: object
    source: object = None
    destination: object = None
    #: 为什么会移动（"discard" / "judge" / "lose" / ""）。只用于事件 payload，
    #: 不参与任何判定——技能需要区分"弃置"与"判定进入弃牌堆"时读它。
    reason: str = ""

    def apply(self, context):
        if self.source is not None and not _remove_identity(self.source, self.card):
            raise ValueError("card is no longer in the expected source zone")
        if self.destination is not None:
            self.destination.append(self.card)
        result = AtomResult(
            data={
                "card": self.card,
                "source": self.source,
                "destination": self.destination,
            }
        )
        self._notify_discard(context)
        self._notify_lost(context)
        return result

    def _notify_discard(self, context):
        """目的地是弃牌堆时发一条统一的「有牌进弃牌堆」通知。

        这是全项目**唯一**的弃牌出口：手牌超限弃牌、拆顺弃牌、判定牌进弃牌堆、
        装备被弃……全部经过这里。订阅它的技能不必再分别适配每条丢弃路径。
        """

        game = context.state
        deck = getattr(game, "deck", None)
        if deck is None or self.destination is not deck.discard_pile:
            return
        from .engine.events import Event, EventType

        owner = None
        finder = getattr(game, "owner_of_zone", None)
        if callable(finder):
            owner = finder(self.source)
        context.emit(Event(
            EventType.CARD_DISCARDED,
            source=owner,
            target=owner,
            payload={
                "card": self.card,
                "reason": self.reason or "discard",
                "owner": owner,
                "from": self.source,
            },
        ))

    def _notify_lost(self, context):
        """牌离开了某名角色的区域、但没有进弃牌堆（给出去了 / 被拿走了）。

        「失去牌」是【屯田】一类能力的时机，它比「弃牌」更宽：被【顺手牵羊】
        拿走、被【仁德】送出去都算。弃牌已经由 CARD_DISCARDED 单独通知，
        这里不重复发，避免同一个动作触发两次。
        """

        game = context.state
        deck = getattr(game, "deck", None)
        if deck is None or self.destination is deck.discard_pile:
            return
        finder = getattr(game, "owner_of_zone", None)
        owner = finder(self.source) if callable(finder) else None
        if owner is None:
            return
        from .engine.events import Event, EventType

        context.emit(Event(
            EventType.CARD_LOST,
            source=owner,
            target=owner,
            payload={
                "card": self.card,
                "reason": self.reason or "lose",
                "owner": owner,
                "from": self.source,
                "to": self.destination,
            },
        ))


@dataclass
class LoseHpAtom(Atom):
    target: object
    amount: int

    def apply(self, context):
        amount = max(0, int(self.amount))
        self.target.hp -= amount
        return AtomResult(data={"target": self.target, "amount": amount})


@dataclass
class RecoverHpAtom(Atom):
    #: ``source`` = 谁让这次回复发生（施治者）。规则上不参与任何判定，
    #: 只用于 HP_RECOVERED 事件——【恩怨】这类"别人给你回血"的能力要它。
    target: object
    amount: int
    source: object = None

    def apply(self, context):
        amount = max(0, int(self.amount))
        before = self.target.hp
        self.target.hp = min(self.target.max_hp, self.target.hp + amount)
        healed = self.target.hp - before
        if healed:
            context.emit(Event(
                EventType.HP_RECOVERED,
                source=self.source,
                target=self.target,
                payload={"target": self.target, "amount": healed, "source": self.source},
            ))
        return AtomResult(
            data={
                "target": self.target,
                "amount": healed,
            }
        )


@dataclass
class DrawCardsAtom(Atom):
    target: object
    amount: int

    def apply(self, context):
        game = context.state
        drawn = []
        for _ in range(max(0, int(self.amount))):
            card = game.deck.draw()
            if card is None:
                break
            self.target.hand.append(card)
            drawn.append(card)
        return AtomResult(data={"target": self.target, "cards": drawn, "amount": len(drawn)})


@dataclass
class EquipCardAtom(Atom):
    target: object
    card: object

    def apply(self, context):
        old = self.target.get_equipment(self.card.subtype)
        self.target.set_equipment(self.card)
        _sync_granted_skills(context.state, self.target)
        return AtomResult(data={"target": self.target, "card": self.card, "old": old})


@dataclass
class UnequipAtom(Atom):
    """把一张装备牌取下装备区，并发出「失去装备」事件。

    这是装备区离开的统一出口：主动换装、被拆、被顺、被弃、死亡清理都走它，
    订阅 ``EQUIPMENT_LOST`` 的技能（枭姬一类）不必关心是谁把牌拿走的。
    ``destination`` 给定时顺手把牌放进目标区域。
    """

    player: object
    slot: str
    destination: object = None

    def apply(self, context):
        from .engine.events import Event, EventType

        card = self.player.remove_equipment(self.slot)
        if card is None:
            raise ValueError("equipment slot is empty: " + str(self.slot))
        if self.destination is not None:
            self.destination.append(card)
        event = context.emit(Event(
            EventType.EQUIPMENT_LOST,
            source=card,
            target=self.player,
            payload={
                "card": card,
                "slot": self.slot,
                "healed": False,
                "destination": self.destination,
            },
        ))
        # 装备已经离开装备区：由它赋予的技能（丈八蛇矛一类）同时失效。
        _sync_granted_skills(context.state, self.player)
        return AtomResult(data={
            "card": card,
            "player": self.player,
            "slot": self.slot,
            "destination": self.destination,
            "healed": bool(event.payload.get("healed")),
        })


@dataclass
class TransferEquipmentAtom(Atom):
    source_player: object
    slot: str
    destination: object

    def apply(self, context):
        # 借刀杀人一类把装备转给别人：同样按「失去装备」结算。
        result = context.apply(UnequipAtom(self.source_player, self.slot, self.destination))
        return AtomResult(data={
            "card": result.data["card"],
            "source": self.source_player,
            "slot": self.slot,
            "destination": self.destination,
        })


@dataclass
class SetChainedAtom(Atom):
    target: object
    chained: bool

    def apply(self, context):
        before = bool(self.target.chained)
        self.target.chained = bool(self.chained)
        return AtomResult(data={"target": self.target, "before": before, "chained": self.target.chained})
