"""Authoritative turn/phase flow with delayed-trick judgment and phase skips."""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom
from src.game.engine import Event, EventType, Flow, FlowStatus
from src.game.rules import PhaseControl, TURN_PHASE_ORDER, TurnPhase

from .damage import DamageContext, DamageFlow
from .judge import JudgeFlow


class TurnFlow(Flow):
    """One character's turn.

    The flow is resumable in the interactive mode: a judgement-phase Lightning
    hit may drop the active character into dying, and the remaining phases only
    continue once that child flow is finished.
    """

    PHASES_BEFORE_PLAY = (TurnPhase.PREPARE, TurnPhase.JUDGE, TurnPhase.DRAW)

    def __init__(self, engine, player, on_complete=None, on_play_phase=None):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.player = player
        self.on_complete = on_complete
        self.on_play_phase = on_play_phase
        self.phase_control = PhaseControl()
        self.phase_history = []
        self._phase_index = 0
        self._paused_child = None

    # ==================================================
    # 交互式回合
    # ==================================================

    def begin_interactive(self):
        self.status = FlowStatus.RUNNING
        self.game.current_turn_player = self.player
        self.context.emit(Event(EventType.TURN_START, source=self.player, payload={"flow": self}))
        self._phase_index = 0
        self._paused_child = None
        return self._advance_interactive()

    def _advance_interactive(self):
        phases = self.PHASES_BEFORE_PLAY
        while self._phase_index < len(phases):
            if not self.player.is_alive:
                # 当前行动角色在自己的回合中阵亡：回合立即结束。
                break
            phase = phases[self._phase_index]
            self._phase_index += 1
            self.game.turn_phase = phase
            self.game.phase = phase.value
            self.context.emit(Event(EventType.PHASE_START, source=self.player, payload={"phase": phase, "flow": self, "skipped": self.phase_control.is_skipped(phase)}))
            self.phase_history.append(phase)
            paused = False
            if not self.phase_control.is_skipped(phase):
                paused = self._execute_phase(phase)
            self.context.emit(Event(EventType.PHASE_END, source=self.player, payload={"phase": phase, "flow": self, "skipped": self.phase_control.is_skipped(phase)}))
            if paused:
                return False
        self.game.skipped_phases = set(self.phase_control._skipped)
        if not self.player.is_alive:
            self._finish_dead_turn()
            return False
        return self._enter_play_phase()

    def _enter_play_phase(self):
        if self.phase_control.is_skipped(TurnPhase.PLAY):
            self.game.turn_phase = TurnPhase.DISCARD
            self.game.phase = "discard"
            return False
        self.game.turn_phase = TurnPhase.PLAY
        self.game.phase = "play"
        self.context.emit(Event(EventType.PHASE_START, source=self.player, payload={"phase": TurnPhase.PLAY, "flow": self, "skipped": False}))
        self.phase_history.append(TurnPhase.PLAY)
        return True

    def _resume_after_child(self):
        """Called when a paused judgement-phase child flow has finished."""

        if self.status is FlowStatus.COMPLETED or self._paused_child is None:
            return
        self._paused_child = None
        self.pending_request = None
        self.status = FlowStatus.RUNNING
        ready = self._advance_interactive()
        if self.status is FlowStatus.WAITING:
            return
        if self.on_play_phase is not None:
            self.on_play_phase(ready)

    def _pause_for(self, child):
        self._paused_child = child
        self.wait(child)
        return True

    def _finish_dead_turn(self):
        self.context.emit(Event(EventType.TURN_END, source=self.player, payload={"flow": self}))
        self.game.skipped_phases = set(self.phase_control._skipped)
        self.complete({"player": self.player, "phases": tuple(self.phase_history), "skipped": set(self.phase_control._skipped)})
        if self.on_play_phase is not None:
            # 让回合驱动方知道本回合不会再有出牌阶段，需要直接收尾。
            self.on_play_phase(False)

    # ==================================================
    # 全自动回合
    # ==================================================

    def finish_interactive(self):
        if self.status is FlowStatus.COMPLETED:
            return self.current_result()
        if self.game.turn_phase is TurnPhase.PLAY:
            self.context.emit(Event(EventType.PHASE_END, source=self.player, payload={"phase": TurnPhase.PLAY, "flow": self, "skipped": False}))
        self.context.emit(Event(EventType.TURN_END, source=self.player, payload={"flow": self}))
        return self.complete({"player": self.player, "phases": tuple(self.phase_history), "skipped": set(self.phase_control._skipped)})

    def advance(self, response=None):
        self.game.current_turn_player = self.player
        self.context.emit(Event(EventType.TURN_START, source=self.player, payload={"flow": self}))
        for phase in TURN_PHASE_ORDER:
            if not self.player.is_alive or self.game.game_over:
                break
            self.game.turn_phase = phase
            self.game.phase = phase.value
            self.context.emit(Event(EventType.PHASE_START, source=self.player, payload={"phase": phase, "flow": self, "skipped": self.phase_control.is_skipped(phase)}))
            self.phase_history.append(phase)
            if not self.phase_control.is_skipped(phase):
                self._execute_phase(phase)
            self.context.emit(Event(EventType.PHASE_END, source=self.player, payload={"phase": phase, "flow": self, "skipped": self.phase_control.is_skipped(phase)}))
            if self.game.game_over:
                break
        self.context.emit(Event(EventType.TURN_END, source=self.player, payload={"flow": self}))
        self.game.skipped_phases = set(self.phase_control._skipped)
        result = self.complete({"player": self.player, "phases": tuple(self.phase_history), "skipped": set(self.phase_control._skipped)})
        if self.on_complete:
            self.on_complete(result)
        return result

    # ==================================================
    # 阶段执行
    # ==================================================

    def _execute_phase(self, phase):
        """Run one phase.  Returns True when the flow must wait for a child."""

        if phase is TurnPhase.JUDGE:
            return self._resolve_judgement_zone()
        if phase is TurnPhase.DRAW:
            self.context.apply(DrawCardsAtom(self.player, 2))
        elif phase is TurnPhase.DISCARD:
            while len(self.player.hand) > max(0, self.player.hp):
                self.context.apply(MoveCardAtom(self.player.hand[-1], source=self.player.hand, destination=self.game.deck.discard_pile))
        return False

    def _resolve_judgement_zone(self):
        # Later delayed tricks resolve first; the order is explicit, not an
        # accidental forward-list iteration.
        for delayed in list(reversed(self.player.judgement_zone)):
            if delayed not in self.player.judgement_zone or self.player.hp <= 0:
                continue
            result = JudgeFlow(self.engine, self.player, delayed.name.lower()).start().value
            if delayed.name == "LEBU":
                if result is None or result.suit != "heart":
                    self.phase_control.skip(TurnPhase.PLAY)
                self.engine.discard_zone_card(delayed, self.player.judgement_zone)
            elif delayed.name == "BINGLIANG":
                if result is None or result.suit != "club":
                    self.phase_control.skip(TurnPhase.DRAW)
                self.engine.discard_zone_card(delayed, self.player.judgement_zone)
            elif delayed.name == "SHANDIAN":
                rank_value = {"A": 1, "J": 11, "Q": 12, "K": 13}.get(result.rank, int(result.rank) if result and str(result.rank).isdigit() else 0) if result else 0
                hit = result is not None and result.suit == "spade" and 2 <= rank_value <= 9
                if hit:
                    self.engine.discard_zone_card(delayed, self.player.judgement_zone)
                    damage = DamageFlow(
                        self.engine,
                        DamageContext(None, self.player, 3, nature="thunder", card=delayed),
                        on_complete=lambda _result: self._resume_after_child(),
                    )
                    outcome = damage.start()
                    if outcome.status is FlowStatus.WAITING:
                        # 闪电造成濒死：先处理求桃流程，再继续本回合。
                        return self._pause_for(damage)
                else:
                    next_player = self._next_alive_player(self.player)
                    destination = next_player.judgement_zone if next_player and not any(card.name == "SHANDIAN" for card in next_player.judgement_zone) else self.game.deck.discard_pile
                    if destination is self.game.deck.discard_pile:
                        self.engine.discard_zone_card(delayed, self.player.judgement_zone)
                    else:
                        self.context.apply(MoveCardAtom(delayed, source=self.player.judgement_zone, destination=destination))
        return False

    def _next_alive_player(self, player):
        candidate = self.game.seats.next_alive_player(player)
        if candidate is None:
            return None
        visited = set()
        while candidate not in visited:
            visited.add(candidate)
            if not any(card.name == "SHANDIAN" for card in candidate.judgement_zone):
                return candidate
            candidate = self.game.seats.next_alive_player(candidate)
            if candidate is player:
                break
        return None
