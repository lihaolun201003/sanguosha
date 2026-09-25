"""Observable damage flow using HP atoms."""

from dataclasses import dataclass, field
from uuid import uuid4

from src.game.atoms_v2 import LoseHpAtom
from src.game.engine import Event, EventType, Flow, FlowResult, FlowStatus


@dataclass
class DamageContext:
    source: object
    target: object
    amount: int
    nature: str = "normal"
    card: object = None
    effects: list = field(default_factory=list)
    cancelled: bool = False
    chain_id: str = None
    is_chain_damage: bool = False
    visited_players: set = field(default_factory=set)


class DamageFlow(Flow):
    """一次伤害的完整结算。

    分阶段推进（``stage``），因为**每个事件批次之后都要停一次**：事件回调里
    技能可能开一个需要回答的窗口（天香要选转移目标、放逐要选目标、悲歌要
    选牌），父流程必须等它结束再往下走。阶段化之后从等待里恢复也只是"接着
    下一个阶段跑"，不会把已经发过的事件、已经扣过的血重来一遍。
    """

    def __init__(self, engine, damage, on_complete=None):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.damage = damage
        self.on_complete = on_complete
        self.applied_amount = 0
        self.was_chained = bool(damage.target.chained)
        self._chain_started = False
        self.stage = "start"

    def advance(self, response=None):
        damage = self.damage
        if self.game.game_over:
            # 对局已经结束（例如同一传播里有人先触发了胜负）：不再结算
            # 后续伤害，避免在结束后创建等待真人输入的求桃请求。
            return self._complete_damage()
        if self.stage == "start":
            self._emit_before_events()
            self.stage = "before_hp"
            # 天香（伤害生效前转移目标）在这批事件里开的窗口：不选完不算完，
            # 否则会出现"转移目标还没定，原角色已经先扣了血"。
            guard = self.guard_child_flows()
            if guard is not None:
                return guard
        if self.stage == "before_hp":
            self._apply_hp_loss()
            self.stage = "after_events"
            guard = self.guard_child_flows()
            if guard is not None:
                return guard
        if self.stage == "after_events":
            self._emit_after_events()
            self.stage = "dying"
            # 伤害后技能（放逐 / 恩怨 / 鸡肋）：必须在这张牌的伤害结算内跑完，
            # 也就是"该张牌之后、下一张牌之前"。
            guard = self.guard_child_flows()
            if guard is not None:
                return guard
        if self.stage == "dying":
            self.stage = "done"
            awaiting = self._check_dying()
            if awaiting is not None:
                return awaiting
        return self._finish()

    # ---- 各阶段 ----

    def _emit_before_events(self):
        damage = self.damage
        for event_type in (
            EventType.DAMAGE_CREATED,
            EventType.DAMAGE_SOURCE_BEFORE,
            EventType.DAMAGE_TARGET_BEFORE,
            EventType.DAMAGE_MODIFY,
        ):
            event = self.context.emit(
                Event(
                    event_type,
                    source=damage.source,
                    target=damage.target,
                    payload={"damage": damage, "flow": self},
                )
            )
            if event.cancelled:
                damage.cancelled = True

    def _apply_hp_loss(self):
        damage = self.damage
        if damage.cancelled:
            return
        # 技能造成的伤害加成（裸衣一类）只在这里汇总一次，规则层不认具体武将。
        if damage.source is not None and hasattr(self.game, "damage_dealt_bonus"):
            bonus = self.game.damage_dealt_bonus(
                damage.source, damage.target, damage.card)
            if bonus:
                damage.amount = int(damage.amount) + int(bonus)
        damage.amount = max(0, int(damage.amount))
        result = self.context.apply(
            LoseHpAtom(damage.target, damage.amount)
        )
        self.applied_amount = result.data["amount"]
        self._set_damage_message()
        self.context.emit(
            Event(
                EventType.DAMAGE_APPLIED,
                source=damage.source,
                target=damage.target,
                payload={
                    "damage": damage,
                    "amount": self.applied_amount,
                    "flow": self,
                },
            )
        )

    def _emit_after_events(self):
        damage = self.damage
        for event_type in (
            EventType.DAMAGE_SOURCE_AFTER,
            EventType.DAMAGE_TARGET_AFTER,
        ):
            self.context.emit(
                Event(
                    event_type,
                    source=damage.source,
                    target=damage.target,
                    payload={
                        "damage": damage,
                        "amount": self.applied_amount,
                        "flow": self,
                    },
                )
            )

    def _check_dying(self):
        damage = self.damage
        if damage.target.hp > 0:
            return None
        from .dying import DyingFlow

        dying = DyingFlow(
            self.engine,
            dying_player=damage.target,
            source=damage.source,
            cause=damage,
            on_complete=self._after_dying,
        )
        result = dying.start()
        if result.status is FlowStatus.WAITING:
            return self.wait(dying)
        if self.status is FlowStatus.COMPLETED:
            return FlowResult(self.status, self.result)
        return None

    def _set_damage_message(self):
        damage = self.damage
        card_name = getattr(damage.card, "display_name", "伤害")
        nature_names = {"normal": "普通", "fire": "火焰", "thunder": "雷电"}
        nature = nature_names.get(damage.nature, damage.nature)
        self.game.message = damage.target.name + "受到 " + str(self.applied_amount) + " 点" + nature + "伤害。"
        self.game.add_log(
            (damage.source.name + " 对 " if damage.source else "")
            + damage.target.name + " 造成 " + str(self.applied_amount) + " 点" + nature + "伤害（" + card_name + "）"
        )

        if damage.effects:
            self.game.message += "（" + "；".join(damage.effects) + "）"

    def _after_dying(self, _result):
        if self.status not in (FlowStatus.COMPLETED, FlowStatus.CANCELLED):
            self._finish()

    def _finish(self):
        if self.status is FlowStatus.COMPLETED:
            return FlowResult(self.status, self.result)
        damage = self.damage
        if (
            not self._chain_started
            and not damage.is_chain_damage
            and self.was_chained
            and damage.nature in ("fire", "thunder")
            and self.applied_amount > 0
        ):
            self._chain_started = True
            damage.target.chained = False
            self.context.emit(Event(EventType.CHAIN_STATE_CHANGED, source=damage.source, target=damage.target, payload={"chained": False}))
            visited = set(damage.visited_players)
            visited.add(damage.target)
            targets = [player for player in self.game.seats.alive_players_in_order(start_after=damage.target)
                       if player.chained and player not in visited]
            if targets:
                from .chain_damage import ChainDamageFlow
                chain = ChainDamageFlow(
                    self.engine, source=damage.source, card=damage.card,
                    nature=damage.nature, amount=self.applied_amount,
                    targets=targets, visited=visited,
                    chain_id=damage.chain_id or str(uuid4()),
                    on_complete=lambda _result: self._complete_damage(),
                )
                result = chain.start()
                if result.status is FlowStatus.WAITING:
                    return self.wait(chain)
                return self.current_result()
        return self._complete_damage()

    def _complete_damage(self):
        if self.status is FlowStatus.WAITING:
            self.status = FlowStatus.RUNNING
            self.pending_request = None
        self.context.emit(
            Event(
                EventType.DAMAGE_SETTLED,
                source=self.damage.source,
                target=self.damage.target,
                payload={
                    "damage": self.damage,
                    "amount": self.applied_amount,
                    "flow": self,
                },
            )
        )
        # 悲歌一类"伤害后"技能在这条事件里开窗口：complete() 会把本流程挂起，
        # 等它跑完再真正收尾（on_settled）。
        return self.complete(
            {
                "damage": self.damage,
                "amount": self.applied_amount,
            }
        )

    def on_settled(self, result):
        self.notify_on_complete(result)
