"""Small structured result state introduced by the core combat migration."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class GameOutcome(str, Enum):
    PLAYER_WIN = "player_win"
    AI_WIN = "ai_win"
    LAST_SURVIVOR = "last_survivor"
    HUMAN_ELIMINATED = "human_eliminated"


@dataclass(frozen=True)
class GameResult:
    outcome: GameOutcome
    winner: Any
    loser: Any
    reason: str = "death"

    @property
    def winner_player_id(self):
        return getattr(self.winner, "player_id", None)
