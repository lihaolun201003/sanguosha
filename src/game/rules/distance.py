"""Authoritative distance and attack-range queries."""


class DistanceRule:
    @staticmethod
    def base_distance(game, source, target):
        """基础距离：座位环 + 双方坐骑，**不含任何技能修正**。

        为什么要有这个单独的口子：有些技能修饰本身就是"把与某人的距离视为
        0"（陷阵），它需要"没有本修饰时的距离"当基数。如果那种修饰在计算
        过程中再调用 ``distance()``，就会把本修饰重新算一遍——
        无限递归，整局直接崩（曾经就是这样把一个自由混战打到 RecursionError）。
        """

        if source is target:
            return 0
        distance = game.seats.distance(source, target) if hasattr(game, "seats") else 1
        if target.has_defensive_horse:
            distance += 1
        if source.has_offensive_horse:
            distance -= 1
        return distance

    @staticmethod
    def distance(game, source, target):
        if source is target:
            return 0
        distance = DistanceRule.base_distance(game, source, target)
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
