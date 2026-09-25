"""Death cleanup; the outcome itself is decided by the active GameMode."""

from src.game.engine import Event, EventType, Flow
from src.game.atoms_v2 import MoveCardAtom, UnequipAtom


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
        # 先落死亡标记再清空区域：死亡清理不是"失去装备"，枭姬一类技能
        # 不会因为角色退场而被触发。
        self.dead_player.alive = False
        for card in list(self.dead_player.hand):
            self.context.apply(MoveCardAtom(card, source=self.dead_player.hand, destination=self.game.deck.discard_pile))
        for card in list(self.dead_player.judgement_zone):
            self.context.apply(MoveCardAtom(card, source=self.dead_player.judgement_zone, destination=self.game.deck.discard_pile))
        for slot, card in list(self.dead_player.equipment.items()):
            if card is not None:
                self.context.apply(UnequipAtom(
                    self.dead_player, slot, self.game.deck.discard_pile))
        self.dead_player.chained = False

        # 胜负与死亡奖惩都属于模式规则：这里只做通用清理，然后交给
        # GameMode 判定"这次死亡意味着什么"。核心流程不认识身份。
        mode = getattr(self.game, "mode", None)
        if mode is not None:
            mode.on_death(self.dead_player, self.source)
        resolution = mode.resolve_death(self) if mode is not None else None

        result = self.game.result if (resolution is not None and resolution.finished) else None
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
        # 行殇一类"阵亡时"技能在这条事件里开窗口：遗物分配没定下来之前，
        # 死亡清理就不能算结束（技能卸载、回合推进都要等）。
        guard = self.guard_child_flows()
        if guard is not None:
            return guard
        return self._settle(result)

    def _settle(self, result):
        # 死亡结算完毕后再卸载普通技能：DEATH 事件期间"死亡时"类技能
        # 仍然有机会响应，卸载顺序不会把死亡结算技能一起掐掉。
        skills = getattr(self.game, "skills", None)
        if skills is not None:
            skills.on_player_death(self.dead_player)
        return self.complete(result)
