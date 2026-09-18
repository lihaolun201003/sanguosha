"""Authoritative turn/phase flow with delayed-trick judgment and phase skips."""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom
from src.game.engine import Event, EventType, Flow, FlowStatus
from src.game.rules import PhaseControl, TURN_PHASE_ORDER, TurnPhase

from .damage import DamageContext, DamageFlow
from .judge import JudgeFlow


class TurnFlow(Flow):
    def __init__(self, engine, player, on_complete=None):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.player = player
        self.on_complete = on_complete
        self.phase_control = PhaseControl()
        self.phase_history = []

    def begin_interactive(self):
        self.status = FlowStatus.RUNNING
        self.game.current_turn_player = self.player
        self.context.emit(Event(EventType.TURN_START, source=self.player, payload={"flow": self}))
        for phase in (TurnPhase.PREPARE, TurnPhase.JUDGE, TurnPhase.DRAW):
            self.game.turn_phase = phase
            self.game.phase = phase.value
            self.context.emit(Event(EventType.PHASE_START, source=self.player, payload={"phase": phase, "flow": self, "skipped": self.phase_control.is_skipped(phase)}))
            self.phase_history.append(phase)
            if not self.phase_control.is_skipped(phase):
                self._execute_phase(phase)
            self.context.emit(Event(EventType.PHASE_END, source=self.player, payload={"phase": phase, "flow": self, "skipped": self.phase_control.is_skipped(phase)}))
        self.game.skipped_phases = set(self.phase_control._skipped)
        if self.phase_control.is_skipped(TurnPhase.PLAY):
            self.game.turn_phase = TurnPhase.DISCARD
            self.game.phase = "discard" if self.player is self.game.player else "enemy"
            return False
        self.game.turn_phase = TurnPhase.PLAY
        self.game.phase = "play" if self.player is self.game.player else "enemy"
        self.context.emit(Event(EventType.PHASE_START, source=self.player, payload={"phase": TurnPhase.PLAY, "flow": self, "skipped": False}))
        self.phase_history.append(TurnPhase.PLAY)
        return True

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
            self.game.turn_phase = phase
            self.game.phase = "play" if phase is TurnPhase.PLAY and self.player is self.game.player else ("enemy" if phase is TurnPhase.PLAY else phase.value)
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

    def _execute_phase(self, phase):
        if phase is TurnPhase.JUDGE:
            self._resolve_judgement_zone()
        elif phase is TurnPhase.DRAW:
            self.context.apply(DrawCardsAtom(self.player, 2))
        elif phase is TurnPhase.DISCARD:
            while len(self.player.hand) > max(0, self.player.hp):
                self.context.apply(MoveCardAtom(self.player.hand[-1], source=self.player.hand, destination=self.game.deck.discard_pile))

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
                    DamageFlow(self.engine, DamageContext(None, self.player, 3, nature="thunder", card=delayed)).start()
                else:
                    next_player = self._next_alive_player(self.player)
                    destination = next_player.judgement_zone if next_player and not any(card.name == "SHANDIAN" for card in next_player.judgement_zone) else self.game.deck.discard_pile
                    if destination is self.game.deck.discard_pile:
                        self.engine.discard_zone_card(delayed, self.player.judgement_zone)
                    else:
                        self.context.apply(MoveCardAtom(delayed, source=self.player.judgement_zone, destination=destination))

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
