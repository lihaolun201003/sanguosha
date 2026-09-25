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
        # 技能修正（马术 / 飞影一类）统一由 Game 汇总，规则层不查技能表。
        if hasattr(game, "distance_modifier"):
            distance += game.distance_modifier(source, target)
        return max(1, distance)

    @classmethod
    def in_range(cls, game, source, target, limit):
        return cls.distance(game, source, target) <= limit

    @classmethod
    def attack_range(cls, game, source):
        bonus = game.attack_range_bonus(source) if hasattr(game, "attack_range_bonus") else 0
        return max(1, source.attack_range + bonus)

    @classmethod
    def in_attack_range(cls, game, source, target):
        return cls.in_range(game, source, target, cls.attack_range(game, source))
