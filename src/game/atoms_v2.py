"""Concrete state-change atoms used by migrated combat flows."""

from dataclasses import dataclass

from .engine import Atom, AtomResult


def _remove_identity(items, value):
    for index, item in enumerate(items):
        if item is value:
            items.pop(index)
            return True
    return False


@dataclass
class MoveCardAtom(Atom):
    card: object
    source: object = None
    destination: object = None

    def apply(self, context):
        if self.source is not None and not _remove_identity(self.source, self.card):
            raise ValueError("card is no longer in the expected source zone")
        if self.destination is not None:
            self.destination.append(self.card)
        return AtomResult(
            data={
                "card": self.card,
                "source": self.source,
                "destination": self.destination,
            }
        )


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
    target: object
    amount: int

    def apply(self, context):
        amount = max(0, int(self.amount))
        before = self.target.hp
        self.target.hp = min(self.target.max_hp, self.target.hp + amount)
        return AtomResult(
            data={
                "target": self.target,
                "amount": self.target.hp - before,
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
        self.target.set_equipment(self.card)
        return AtomResult(data={"target": self.target, "card": self.card, "old": old})


@dataclass
class TransferEquipmentAtom(Atom):
    source_player: object
    slot: str
    destination: object

    def apply(self, context):
        card = self.source_player.remove_equipment(self.slot)
        if card is None:
            raise ValueError("equipment slot is empty")
        self.destination.append(card)
        return AtomResult(data={"card": card, "source": self.source_player, "slot": self.slot, "destination": self.destination})


@dataclass
class SetChainedAtom(Atom):
    target: object
    chained: bool

    def apply(self, context):
        before = bool(self.target.chained)
        self.target.chained = bool(self.chained)
        return AtomResult(data={"target": self.target, "before": before, "chained": self.target.chained})
