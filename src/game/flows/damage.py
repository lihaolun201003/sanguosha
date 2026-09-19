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
    def __init__(self, engine, damage, on_complete=None):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.damage = damage
        self.on_complete = on_complete
        self.applied_amount = 0
        self.was_chained = bool(damage.target.chained)
        self._chain_started = False

    def advance(self, response=None):
        damage = self.damage
        if self.game.game_over:
            # 对局已经结束（例如同一传播里有人先触发了胜负）：不再结算
            # 后续伤害，避免在结束后创建等待真人输入的求桃请求。
            return self._complete_damage()
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

        if not damage.cancelled:
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

        for event_type in (
            EventType.DAMAGE_SOURCE_AFTER,
            EventType.DAMAGE_TARGET_AFTER,
        ):
            self.context.emit(
                Event(
                    event_type,
                    source=damage.source,
                    target=damage.target,
                    payload={"damage": damage, "flow": self},
                )
            )

        if damage.target.hp <= 0:
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

        return self._finish()

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
        result = self.complete(
            {
                "damage": self.damage,
                "amount": self.applied_amount,
            }
        )
        if self.on_complete is not None:
            self.on_complete(result)
        return result
