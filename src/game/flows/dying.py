"""Resumable dying and self-rescue flow."""

from src.game.atoms_v2 import RecoverHpAtom
from src.game.engine import Event, EventType, Flow, FlowResult, FlowStatus
from src.game.engine.pending import PendingRequestType


class DyingFlow(Flow):
    def __init__(
        self,
        engine,
        *,
        dying_player,
        source=None,
        cause=None,
        rescue_order=None,
        on_complete=None,
    ):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.dying_player = dying_player
        self.source = source
        self.cause = cause
        self.rescue_order = list(rescue_order or (
            [dying_player]
            + [player for player in self.game.seats.alive_players_in_order(start_after=dying_player) if player is not dying_player]
        ))
        self.rescue_index = 0
        self.current_rescuer = self.rescue_order[0] if self.rescue_order else None
        self.on_complete = on_complete
        self.entered = False

    def advance(self, response=None):
        if not self.entered:
            self.entered = True
            self.context.emit(
                Event(
                    EventType.DYING_ENTERED,
                    source=self.source,
                    target=self.dying_player,
                    payload={"flow": self, "cause": self.cause},
                )
            )
            if self.dying_player is self.game.player:
                self.game.message = (
                    "你进入濒死状态，可以使用【桃】或【酒】自救。"
                )
            else:
                self.game.message = "电脑进入濒死状态。"

        if response is not None:
            if response.card is not None:
                self.context.apply(RecoverHpAtom(self.dying_player, 1))
                self.game.message = self.dying_player.name + "回复了 1 点体力。"
                self.rescue_index = 0 if self.dying_player.hp <= 0 else self.rescue_index + 1
            else:
                self.rescue_index += 1

        if self.dying_player.hp > 0:
            self.context.emit(
                Event(
                    EventType.DYING_EXITED,
                    source=self.source,
                    target=self.dying_player,
                    payload={"flow": self},
                )
            )
            return self._finish({"rescued": True})

        while self.rescue_index < len(self.rescue_order):
            self.current_rescuer = self.rescue_order[self.rescue_index]
            if self.current_rescuer.alive and (self.current_rescuer is self.dying_player or self.current_rescuer.hp > 0):
                allowed_names = {"TAO", "JIU"} if self.current_rescuer is self.dying_player else {"TAO"}
                allowed = {card.name for card in self.current_rescuer.hand if card.name in allowed_names}
                if allowed:
                    break
            self.rescue_index += 1
        else:
            return self._die()

        request = self.engine.pending.create(
            PendingRequestType.RESPOND_CARD,
            source=self.source,
            target=self.current_rescuer,
            prompt=("濒死：使用【桃】或【酒】自救" if self.current_rescuer is self.dying_player else
                    "是否使用【桃】救援 " + self.dying_player.name + "？"),
            owner_flow=self,
            allowed_cards=allowed,
            min_cards=0,
            max_cards=1,
            request_context={
                "reason": "dying_rescue",
                "dying_player": self.dying_player,
                "rescue_order": tuple(self.rescue_order),
                "current_rescuer": self.current_rescuer,
            },
        )
        self.wait(request)
        self.engine.present_or_auto_resolve(request)
        return FlowResult(self.status, self.result)

    def _die(self):
        from .death import DeathFlow

        death = DeathFlow(
            self.engine,
            dead_player=self.dying_player,
            source=self.source,
            cause=self.cause,
        )
        death_result = death.start()
        return self._finish(
            {"rescued": False, "death": death_result.value}
        )

    def _finish(self, value):
        if self.status is FlowStatus.COMPLETED:
            return FlowResult(self.status, self.result)
        result = self.complete(value)
        if self.on_complete is not None:
            self.on_complete(result)
        return result
