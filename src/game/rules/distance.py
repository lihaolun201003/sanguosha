"""Authoritative distance and attack-range queries."""


class DistanceRule:
    @staticmethod
    def distance(game, source, target):
        if source is target:
            return 0
        distance = game.seats.distance(source, target) if hasattr(game, "seats") else 1
        if target.has_defensive_horse:
            distance += 1
        if source.has_offensive_horse:
            distance -= 1
        return max(1, distance)

    @classmethod
    def in_range(cls, game, source, target, limit):
        return cls.distance(game, source, target) <= limit

    @classmethod
    def in_attack_range(cls, game, source, target):
        return cls.in_range(game, source, target, source.attack_range)
