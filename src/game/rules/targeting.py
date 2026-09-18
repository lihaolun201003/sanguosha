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
    living = living_players(game)
    if rule is TargetRule.NO_TARGET:
        return []
    if rule is TargetRule.SELF:
        return [actor]
    if rule in (TargetRule.SINGLE_OTHER, TargetRule.ALL_OTHERS):
        return [player for player in living if player is not actor]
    return living


def validate_targets(game, actor, targets, rule, minimum=0, maximum=None):
    targets = list(targets)
    if len({id(target) for target in targets}) != len(targets):
        return False
    candidates = target_candidates(game, actor, rule)
    if rule is TargetRule.ALL_OTHERS:
        return targets == candidates
    if rule is TargetRule.ALL_PLAYERS:
        return targets == candidates
    if len(targets) < minimum or (maximum is not None and len(targets) > maximum):
        return False
    return all(any(target is candidate for candidate in candidates) for target in targets)
