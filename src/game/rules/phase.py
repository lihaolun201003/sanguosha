from enum import Enum


class TurnPhase(str, Enum):
    PREPARE = "prepare"
    JUDGE = "judge"
    DRAW = "draw"
    PLAY = "play"
    DISCARD = "discard"
    FINISH = "finish"


TURN_PHASE_ORDER = tuple(TurnPhase)

#: 阶段的中文名。规则层需要它把"跳过了什么"写进表现事件
#: （``EventType.PHASE_SKIPPED``，见 flows/turn.py）；UI 的提示文案仍由 UI 决定。
PHASE_NAMES = {
    TurnPhase.PREPARE: "准备",
    TurnPhase.JUDGE: "判定",
    TurnPhase.DRAW: "摸牌",
    TurnPhase.PLAY: "出牌",
    TurnPhase.DISCARD: "弃牌",
    TurnPhase.FINISH: "结束",
}


class PhaseControl:
    def __init__(self):
        self._skipped = set()

    def skip(self, phase):
        self._skipped.add(TurnPhase(phase))

    def is_skipped(self, phase):
        return TurnPhase(phase) in self._skipped

    def clear(self):
        self._skipped.clear()
