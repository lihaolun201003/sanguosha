"""Reusable judgment flow."""

from dataclasses import dataclass

from src.game.atoms_v2 import MoveCardAtom
from src.game.engine import Event, EventType, Flow


@dataclass(frozen=True)
class JudgeResult:
    card: object
    suit: str
    color: str
    rank: str
    source: object = None
    target: object = None
    reason: str = "judge"


class JudgeFlow(Flow):
    def __init__(self, engine, owner, reason="judge"):
        super().__init__(engine.context)
        self.engine = engine
        self.owner = owner
        self.reason = reason

    def advance(self, response=None):
        self.context.emit(Event(EventType.JUDGE_STARTED, source=self.owner, payload={"reason": self.reason, "flow": self}))
        card = self.engine.game.deck.draw()
        if card is None:
            return self.complete(None)
        result = JudgeResult(card, card.suit, card.card_color, card.rank, self.owner, self.owner, self.reason)
        self.engine.game.judge_card = card
        self.context.emit(Event(EventType.JUDGE_REVEALED, source=self.owner, payload={"result": result, "flow": self}))
        self.context.emit(Event(EventType.JUDGE_BEFORE_RESULT, source=self.owner, target=self.owner, payload={"result": result, "flow": self}))
        self.context.emit(Event(EventType.JUDGE_RESULT, source=self.owner, target=self.owner, payload={"result": result, "flow": self}))
        self.context.apply(MoveCardAtom(card, destination=self.engine.game.deck.discard_pile))
        self.context.emit(Event(EventType.JUDGE_FINISHED, source=self.owner, payload={"result": result, "flow": self}))
        self.engine.game.judge_card = None
        return self.complete(result)
