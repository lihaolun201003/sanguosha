"""Reusable target contracts for card effects."""

from enum import Enum


class TargetRule(str, Enum):
    NO_TARGET = "no_target"
    SELF = "self"
    SINGLE_OTHER = "single_other"
    SINGLE_ANY = "single_any"
    MULTIPLE = "multiple"
    ALL_OTHERS = "all_others"
    ALL_PLAYERS = "all_players"


def living_players(game):
    return game.get_alive_players()


def target_candidates(game, actor, rule):
    """Legal targets in seat order, starting from the actor.

    Group tricks (Nanman, Wanjian, Taoyuan, Wugu) resolve in that order, so the
    candidate list is the same list the flows will walk.
    """

    if rule is TargetRule.NO_TARGET:
        return []
    if rule is TargetRule.SELF:
        return [actor]
    ordered = game.seats.alive_players_in_order(start_after=actor, include_start=True)
    if rule in (TargetRule.SINGLE_OTHER, TargetRule.ALL_OTHERS):
        return [player for player in ordered if player is not actor]
    return ordered


def validate_targets(game, actor, targets, rule, minimum=0, maximum=None):
    targets = list(targets)
    if len({id(target) for target in targets}) != len(targets):
        return False
    candidates = target_candidates(game, actor, rule)
    if rule in (TargetRule.ALL_OTHERS, TargetRule.ALL_PLAYERS):
        # 顺序由座次决定，这里只要求覆盖同一个集合。
        return len(targets) == len(candidates) and all(
            any(target is candidate for candidate in candidates) for target in targets
        )
    if len(targets) < minimum or (maximum is not None and len(targets) > maximum):
        return False
    return all(any(target is candidate for candidate in candidates) for target in targets)
