"""Canonical circular seat order for two to eight local players."""


class SeatManager:
    def __init__(self, game):
        self.game = game

    def all_players(self):
        return sorted(self.game.players, key=lambda player: player.seat)

    def alive_players_in_order(self, start_after=None, include_start=False):
        alive = [player for player in self.all_players() if player.alive and player.hp > 0]
        if not alive or start_after is None:
            return alive
        ordered = self.all_players()
        try:
            start = ordered.index(start_after)
        except ValueError:
            return alive
        result = []
        offset = 0 if include_start else 1
        for step in range(offset, len(ordered) + offset):
            player = ordered[(start + step) % len(ordered)]
            if (include_start or player is not start_after) and player.alive and player.hp > 0 and player not in result:
                result.append(player)
        return result

    def players_from_seat(self, seat):
        ordered = self.all_players()
        if not ordered:
            return []
        index = next((i for i, player in enumerate(ordered) if player.seat >= seat), 0)
        return ordered[index:] + ordered[:index]

    def next_alive_player(self, player):
        ordered = self.alive_players_in_order(start_after=player)
        return ordered[0] if ordered and ordered[0] is not player else None

    def previous_alive_player(self, player):
        ordered = self.alive_players_in_order(start_after=player)
        return ordered[-1] if ordered and ordered[-1] is not player else None

    def distance(self, source, target):
        alive = self.alive_players_in_order()
        if source is target:
            return 0
        if source not in alive or target not in alive:
            return 999
        left = alive.index(source)
        right = alive.index(target)
        clockwise = (right - left) % len(alive)
        counterclockwise = (left - right) % len(alive)
        return min(clockwise, counterclockwise)
