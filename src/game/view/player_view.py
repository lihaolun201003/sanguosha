"""Public player projection ready for a future remote ViewState."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlayerPublicState:
    player_id: str
    seat: int
    name: str
    alive: bool
    hp: int
    max_hp: int
    hand_count: int
    equipment: dict
    judgement_zone: tuple
    chained: bool

    @classmethod
    def from_player(cls, player):
        return cls(
            player.player_id, player.seat, player.name, player.alive,
            player.hp, player.max_hp, len(player.hand),
            dict(player.equipment), tuple(player.judgement_zone), player.chained,
        )
