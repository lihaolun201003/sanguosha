"""Concrete state-change atoms used by migrated combat flows."""

from dataclasses import dataclass

from src.game.conversion import EQUIPMENT_ZONE

from .engine import Atom, AtomResult
from .engine.events import Event, EventType

#: 牌移动的原因词汇表里，"弃置"这一个。**进弃牌堆不等于弃置**：使用后置入、
#: 判定后置入、替换装备置入、死亡清理置入都不是弃置，而【落英】这类技能只认
#: 后者。规则的差异全部由这个字符串承载，不由目的地承载。
DISCARD_REASON = "discard"


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


def emit_card_moved(context, card, *, source, destination, reason, owner,
                    extra=None):
    """牌移动之后的规则通知——全项目唯一的出口。

    手牌移动与装备区移动读的是同一份 ``reason`` 语义，订阅者（【落英】
    【屯田】…）不必关心牌是从哪个区域离开的：

    * 目的地是弃牌堆 → ``CARD_DISCARDED``，``reason`` 如实带上，**算不算由
      订阅者判断**（落英只认 discard / judge）；
    * 其它目的地 → 牌离开了某名角色的区域 → ``CARD_LOST``；
    * ``reason`` 为空 = 调用方**没有声明**这次移动的规则语义 → 不发任何通知。
      这是兼容承诺：装备区的"未归类移动"（换装、死亡清理…）不会因为这次
      收口而被当成弃置。
    """

    if not reason:
        return
    game = context.state
    deck = getattr(game, "deck", None)
    if deck is None:
        return
    payload = {
        "card": card,
        "reason": reason,
        "owner": owner,
        "from": source,
    }
    if extra:
        payload.update(extra)
    if destination is deck.discard_pile:
        context.emit(Event(
            EventType.CARD_DISCARDED,
            source=owner,
            target=owner,
            payload=payload,
        ))
        return
    if owner is None:
        return
    payload["to"] = destination
    context.emit(Event(
        EventType.CARD_LOST,
        source=owner,
        target=owner,
        payload=payload,
    ))


@dataclass
class MoveCardAtom(Atom):
    card: object
    source: object = None
    destination: object = None
    #: 为什么会移动（"discard" 弃置 / "judge" 判定 / "use" 使用后置入 /
    #: "respond" 打出后置入 / "pindian" 拼点 / "lose"）。它进 CARD_DISCARDED /
    #: CARD_LOST 的 payload，**规则的差异确实看它**——【落英】只认"因弃置或
    #: 判定进入弃牌堆"，使用 / 打出 / 重铸 / 拼点都不算（官方 FAQ）。
    reason: str = ""
    #: 这张牌原本属于谁。只有在区域查不出归属时才需要它：处理区
    #: （``game.processing_zone``）不属于任何角色，而**使用后的牌**与
    #: **判定牌**都要经过处理区才进弃牌堆——不传就等于告诉订阅者"这牌没人
    #: 要"（实测：【落英】据此把自己刚用掉的牌收回手里，那张牌因此永远用不完）。
    owner: object = None

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
        emit_card_moved(
            context,
            self.card,
            source=self.source,
            destination=self.destination,
            reason=self.reason or DISCARD_REASON,
            owner=self._discard_owner(context.state),
        )
        return result

    def _discard_owner(self, game):
        """这次移动的"牌原本属于谁"：显式声明优先，其次按来源区域反查。"""

        finder = getattr(game, "owner_of_zone", None)
        owner = self.owner
        if owner is None and callable(finder):
            owner = finder(self.source)
        return owner


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
        if old is not None:
            # 换装：旧装备必须**离开**装备区并按规则进弃牌堆。
            # 直接 set_equipment 覆盖会让那张牌从所有区域里凭空消失——
            # 既丢牌（长局里牌堆会慢慢被掏空），也让"失去装备"类技能
            # （枭姬 / 白银狮子）收不到事件。
            context.apply(UnequipAtom(
                self.target, self.card.subtype,
                context.state.deck.discard_pile))
        self.target.set_equipment(self.card)
        _sync_granted_skills(context.state, self.target)
        return AtomResult(data={"target": self.target, "card": self.card, "old": old})


@dataclass
class UnequipAtom(Atom):
    """把一张装备牌取下装备区，并发出「失去装备」事件。

    这是装备区离开的统一出口：主动换装、被拆、被顺、被弃、死亡清理都走它，
    订阅 ``EQUIPMENT_LOST`` 的技能（枭姬一类）不必关心是谁把牌拿走的。
    ``destination`` 给定时顺手把牌放进目标区域。

    ``reason`` 是这次离开装备区的**规则原因**（与 ``MoveCardAtom.reason``
    同一套词汇）。它决定要不要再发一条牌移动通知：

    * ``reason="discard"`` → 牌进弃牌堆时额外发 ``CARD_DISCARDED``，
      所以【落英】这类"因弃置进入弃牌堆"的技能看得到装备被弃；
    * 留空 → 只发 ``EQUIPMENT_LOST``。**进弃牌堆不等于弃置**——换装、死亡
      清理、以及尚未归类的路径都靠这一条保持原来的语义，不会被误当成弃置。
    """

    player: object
    slot: str
    destination: object = None
    reason: str = ""

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
        # 再按规则原因补一条牌移动通知（弃置 → CARD_DISCARDED）。原拥有者
        # 就是装备区的主人，不必靠区域反查——牌现在已经不在装备槽里了。
        emit_card_moved(
            context,
            card,
            source=None,
            destination=self.destination,
            reason=self.reason,
            owner=self.player,
            extra={"from_zone": EQUIPMENT_ZONE, "slot": self.slot},
        )
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
    #: 与 ``UnequipAtom.reason`` 同一套词汇；借刀杀人一类"转给别人"不是弃置，
    #: 所以默认留空（只发 EQUIPMENT_LOST）。
    reason: str = ""

    def apply(self, context):
        # 借刀杀人一类把装备转给别人：同样按「失去装备」结算。
        result = context.apply(UnequipAtom(
            self.source_player, self.slot, self.destination, reason=self.reason))
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
