"""Atomic state-change boundary for Engine V2."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict

from .events import Event, EventType


@dataclass
class AtomResult:
    applied: bool = True
    cancelled: bool = False
    data: Dict[str, Any] = field(default_factory=dict)


class Atom(ABC):
    """One observable, indivisible state change."""

    @abstractmethod
    def apply(self, context: "GameContext") -> AtomResult:
        raise NotImplementedError


def apply_atom(context: "GameContext", atom: Atom) -> AtomResult:
    before = context.emit(
        Event(
            EventType.ATOM_BEFORE,
            source=atom,
            payload={"atom": atom},
        )
    )

    if before.cancelled:
        result = AtomResult(applied=False, cancelled=True)
        context.emit(
            Event(
                EventType.ATOM_CANCELLED,
                source=atom,
                payload={"atom": atom, "result": result},
            )
        )
        return result

    result = atom.apply(context)
    if not isinstance(result, AtomResult):
        raise TypeError("Atom.apply() must return AtomResult")

    context.emit(
        Event(
            EventType.ATOM_AFTER,
            source=atom,
            payload={"atom": atom, "result": result},
        )
    )
    return result


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import GameContext
