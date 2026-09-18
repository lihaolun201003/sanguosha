"""Runtime services shared by Engine V2 flows, atoms, and skills."""

from typing import Any, Dict, Optional

from .events import Event, EventDispatcher


class GameContext:
    """Bridge between V2 services and the current legacy game state.

    During migration ``state`` is the existing ``Game`` instance.  A dedicated
    GameState can replace that adapter later without changing event, atom, flow,
    or skill APIs.
    """

    def __init__(
        self,
        state: Any,
        *,
        events: Optional[EventDispatcher] = None,
    ) -> None:
        self.state = state
        self.events = events or EventDispatcher()
        self.services: Dict[str, Any] = {}

    def emit(self, event: Event) -> Event:
        return self.events.dispatch(self, event)

    def apply(self, atom: "Atom") -> "AtomResult":
        from .atoms import apply_atom

        return apply_atom(self, atom)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .atoms import Atom, AtomResult
