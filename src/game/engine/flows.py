"""Resumable flow convention compatible with a synchronous Pygame loop."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .events import Event, EventType


class FlowStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class FlowResult:
    status: FlowStatus
    value: Any = None


class Flow(ABC):
    """A rules process that may pause for a UI or AI response."""

    def __init__(self, context: "GameContext") -> None:
        self.context = context
        self.status = FlowStatus.READY
        self.pending_request: Any = None
        self.result: Any = None

    def start(self) -> FlowResult:
        if self.status is not FlowStatus.READY:
            raise RuntimeError("flow has already started")
        self.status = FlowStatus.RUNNING
        self._emit(EventType.FLOW_STARTED)
        return self.advance()

    def resume(self, response: Any) -> FlowResult:
        if self.status is not FlowStatus.WAITING:
            raise RuntimeError("only a waiting flow can be resumed")
        self.pending_request = None
        self.status = FlowStatus.RUNNING
        self._emit(EventType.FLOW_RESUMED, response=response)
        return self.advance(response)

    def wait(self, request: Any) -> FlowResult:
        self.pending_request = request
        self.status = FlowStatus.WAITING
        self._emit(EventType.FLOW_WAITING, request=request)
        return FlowResult(self.status, request)

    def current_result(self) -> FlowResult:
        return FlowResult(self.status, self.result)

    def complete(self, value: Any = None) -> FlowResult:
        self.pending_request = None
        self.result = value
        self.status = FlowStatus.COMPLETED
        self._emit(EventType.FLOW_FINISHED, result=value)
        return FlowResult(self.status, value)

    def cancel(self, reason: Any = None) -> FlowResult:
        self.pending_request = None
        self.result = reason
        self.status = FlowStatus.CANCELLED
        self._emit(EventType.FLOW_CANCELLED, reason=reason)
        return FlowResult(self.status, reason)

    def _emit(self, event_type: EventType, **payload: Any) -> None:
        self.context.emit(Event(event_type, source=self, payload=payload))

    @abstractmethod
    def advance(self, response: Any = None) -> FlowResult:
        raise NotImplementedError


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import GameContext
