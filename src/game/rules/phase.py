from enum import Enum


class TurnPhase(str, Enum):
    PREPARE = "prepare"
    JUDGE = "judge"
    DRAW = "draw"
    PLAY = "play"
    DISCARD = "discard"
    FINISH = "finish"


TURN_PHASE_ORDER = tuple(TurnPhase)


class PhaseControl:
    def __init__(self):
        self._skipped = set()

    def skip(self, phase):
        self._skipped.add(TurnPhase(phase))

    def is_skipped(self, phase):
        return TurnPhase(phase) in self._skipped

    def clear(self):
        self._skipped.clear()
