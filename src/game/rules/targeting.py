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


def _skill_allows(game, actor, candidates, card):
    """技能层面的目标合法性（空城 / 谦逊一类）。

    过滤规则来自 modifier（TARGET_FORBIDDEN），这里不认具体武将。
    """

    if card is None or not hasattr(game, "target_forbidden"):
        return candidates
    return [
        player for player in candidates
        if not game.target_forbidden(player, source=actor, card=card)
    ]


def target_candidates(game, actor, rule, card=None):
    """Legal targets in seat order, starting from the actor.

    Group tricks (Nanman, Wanjian, Taoyuan, Wugu) resolve in that order, so the
    candidate list is the same list the flows will walk.  ``card`` 用于技能层的
    目标限制（空城 / 谦逊）；不传则只做基础的座次过滤。
    """

    if rule is TargetRule.NO_TARGET:
        return []
    if rule is TargetRule.SELF:
        return [actor]
    ordered = game.seats.alive_players_in_order(start_after=actor, include_start=True)
    if rule in (TargetRule.SINGLE_OTHER, TargetRule.ALL_OTHERS):
        return _skill_allows(game, actor, [p for p in ordered if p is not actor], card)
    return _skill_allows(game, actor, ordered, card)


def validate_targets(game, actor, targets, rule, minimum=0, maximum=None, card=None):
    targets = list(targets)
    if len({id(target) for target in targets}) != len(targets):
        return False
    candidates = target_candidates(game, actor, rule, card=card)
    if rule in (TargetRule.ALL_OTHERS, TargetRule.ALL_PLAYERS):
        # 顺序由座次决定，这里只要求覆盖同一个集合。
        return len(targets) == len(candidates) and all(
            any(target is candidate for candidate in candidates) for target in targets
        )
    if len(targets) < minimum or (maximum is not None and len(targets) > maximum):
        return False
    return all(any(target is candidate for candidate in candidates) for target in targets)
