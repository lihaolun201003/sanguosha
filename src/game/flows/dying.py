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
        self.stage = "enter"

    def resume_from_child(self, result):
        """从"濒死时技能窗口"（涅槃 / 补益）恢复。

        那条流程的结果**不是**一条救援回答，不能拿它当 response 去
        "回复体力 / 记一次放弃"，所以这里显式传 None：从"找人救援"继续。
        """

        return self.advance(None)

    def advance(self, response=None):
        if self.stage == "awaiting_death":
            # 死亡结算（里面可能有行殇一类要回答的技能）跑完了：补濒死收尾。
            self.stage = "settled"
            return self._finish(
                {"rescued": False, "death": getattr(response, "value", None)})
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
            # 涅槃 / 补益这类"濒死时"技能在这条事件里开窗口：先等它们处理完
            # （可能已经把人救回来），再决定要不要问桃。
            guard = self.guard_child_flows()
            if guard is not None:
                return guard

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
                allowed = self._rescue_names_for(self.current_rescuer)
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

    def _rescue_names_for(self, rescuer):
        """这个救援者现在能满足哪些救援牌名（真实牌与技能转化一并计算）。

        求桃前先问一次规则层"现在允许救援吗"：【完杀】这类能力会在施加者的
        回合里禁止其他人用【桃】，因此这里的过滤是**规则**而不是技能特例。
        """

        names = ("TAO", "JIU") if rescuer is self.dying_player else ("TAO",)
        forbidden = getattr(self.game, "rescue_forbidden", None)
        if callable(forbidden):
            names = tuple(
                name for name in names
                if not forbidden(rescuer, self.dying_player, name)
            )
            if not names:
                return set()
        context = self.game.card_actions.rescue_context(
            rescuer, dying_player=self.dying_player, allowed_names=names)
        satisfied = []
        for option in self.game.card_actions.usable_options(rescuer, context):
            if option.result_name not in satisfied:
                satisfied.append(option.result_name)
        return set(satisfied)

    def _die(self):
        # 求桃全部失败之后、真正结算死亡之前，给"不死"类技能最后一次机会
        # （不屈一类）。它是一条**通用检查点**，规则层不认识任何具体武将。
        if self._prevent_death():
            self.context.emit(
                Event(
                    EventType.DYING_EXITED,
                    source=self.source,
                    target=self.dying_player,
                    payload={"flow": self, "prevented": True},
                )
            )
            return self._finish({"rescued": False, "prevented": True})

        from .death import DeathFlow

        death = DeathFlow(
            self.engine,
            dead_player=self.dying_player,
            source=self.source,
            cause=self.cause,
        )
        death_result = death.start()
        if death_result.status is FlowStatus.WAITING:
            # 死亡结算里有要回答的技能（行殇）：等它结束再收尾濒死，
            # 否则濒死流程会带着"还没定论的死亡结果"往下走。
            self.stage = "awaiting_death"
            self.wait(death)
            return self.current_result()
        return self._finish(
            {"rescued": False, "death": death_result.value}
        )

    def _prevent_death(self):
        """问一遍所有"不死"能力；返回是否真的被阻止了。"""

        if not getattr(self.dying_player, "alive", True):
            return False
        event = self.context.emit(Event(
            EventType.DYING_BEFORE_DEATH,
            source=self.source,
            target=self.dying_player,
            payload={"flow": self, "cause": self.cause, "prevented": False},
        ))
        return bool(event.payload.get("prevented"))

    def _finish(self, value):
        if self.status is FlowStatus.COMPLETED:
            return FlowResult(self.status, self.result)
        return self.complete(value)

    def on_settled(self, result):
        if self.on_complete is not None:
            self.on_complete(result)
