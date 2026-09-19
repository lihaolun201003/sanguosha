"""Death cleanup and free-for-all result resolution."""

from src.game.engine import Event, EventType, Flow
from src.game.engine.state import GameOutcome, GameResult
from src.game.atoms_v2 import MoveCardAtom


class DeathFlow(Flow):
    def __init__(self, engine, *, dead_player, source=None, cause=None):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.dead_player = dead_player
        self.source = source
        self.cause = cause

    def advance(self, response=None):
        request = self.engine.pending.current
        if request is not None and request.target is self.dead_player:
            self.engine.clear_pending_ui()
        for card in list(self.dead_player.hand):
            self.context.apply(MoveCardAtom(card, source=self.dead_player.hand, destination=self.game.deck.discard_pile))
        for card in list(self.dead_player.judgement_zone):
            self.context.apply(MoveCardAtom(card, source=self.dead_player.judgement_zone, destination=self.game.deck.discard_pile))
        for slot, card in list(self.dead_player.equipment.items()):
            if card is not None:
                self.dead_player.remove_equipment(slot)
                self.context.apply(MoveCardAtom(card, destination=self.game.deck.discard_pile))
        self.dead_player.alive = False
        self.dead_player.chained = False
        # 胜负只依据存活标志：正在濒死求桃的角色（hp <= 0 但 alive）还没有
        # 死亡，不能在这一刻把别人判成最后的存活者。
        alive = [player for player in self.game.players if player.alive]
        winner = alive[0] if len(alive) == 1 else None
        human_eliminated = self.dead_player is self.game.player
        no_survivors = not alive
        already_over = self.game.game_over
        # 结束标记只增不减：一次连环传播里真人先死、后续角色再死时，
        # 不能把已经结束的对局重新变成进行中。
        self.game.game_over = already_over or human_eliminated or no_survivors or winner is not None
        if self.game.game_over:
            self.game.phase = "over"

        if already_over:
            # 对局已经结束（例如真人已阵亡）：剩下的只是流程收尾，
            # 不再改写胜负，也不宣布任何 AI 获得最终胜利。
            outcome = None
            reason = "ALREADY_OVER"
        elif winner is not None:
            outcome = GameOutcome.PLAYER_WIN if winner is self.game.player else GameOutcome.AI_WIN
            if len(self.game.players) == 2:
                self.game.message = "你获胜了！" if winner is self.game.player else "你阵亡了！"
            else:
                self.game.message = "你获胜了！" if winner is self.game.player else winner.name + " 获胜"
            reason = "LAST_SURVIVOR"
        elif human_eliminated:
            outcome = GameOutcome.HUMAN_ELIMINATED
            self.game.message = "你已阵亡 / 游戏失败"
            reason = "HUMAN_ELIMINATED"
        elif no_survivors:
            outcome = GameOutcome.NO_SURVIVOR
            self.game.message = "全场阵亡，无人获胜"
            reason = "NO_SURVIVOR"
        else:
            outcome = GameOutcome.LAST_SURVIVOR
            self.game.message = self.dead_player.name + " 阵亡"
            reason = "ELIMINATED"

        if outcome is not None:
            result = GameResult(
                outcome=outcome,
                winner=winner,
                loser=self.dead_player,
                reason=reason,
            )
            self.game.result = result
            self.game.winner = winner
        else:
            result = self.game.result
        self.game.add_log(self.dead_player.name + " 阵亡")
        if not self.game.game_over and self.game.current_turn_player is self.dead_player:
            self.game.current_turn_player = self.game.seats.next_alive_player(self.dead_player)
        if self.game.game_over:
            # 对局结束：不再保留任何等待真人输入的请求。
            self.engine.clear_pending_ui()
        self.context.emit(
            Event(
                EventType.DEATH,
                source=self.source,
                target=self.dead_player,
                payload={
                    "flow": self,
                    "cause": self.cause,
                    "result": result,
                },
            )
        )
        return self.complete(result)
