"""Card rules are registered independently from physical card data."""

from src.game.rules import TargetRule, validate_targets


class CardEffect:
    card_name = None
    category = "trick"
    target_rule = TargetRule.NO_TARGET
    min_targets = 0
    max_targets = 0
    distance_limit = None
    can_respond = False
    cancellable_by_wuxie = False

    def can_use(self, game, action):
        if game.game_over:
            return False, "游戏已经结束。"
        if not getattr(action.card, "_virtual", False) and not any(
            card is action.card for card in action.actor.hand
        ):
            return False, "这张牌已经不在使用者手牌中。"
        if not validate_targets(
            game, action.actor, action.targets, self.target_rule,
            self.min_targets, self.max_targets,
        ):
            return False, "目标不合法。"
        return True, ""

    def begin(self, use_flow):
        raise NotImplementedError

    def resume(self, use_flow, resolution):
        raise RuntimeError(self.card_name + " is not waiting for a response")


class CardEffectRegistry:
    def __init__(self):
        self._effects = {}

    def register(self, effect):
        if not effect.card_name:
            raise ValueError("CardEffect.card_name is required")
        self._effects[effect.card_name] = effect
        return effect

    def get(self, card_or_name):
        name = getattr(card_or_name, "name", card_or_name)
        return self._effects.get(name)

    def require(self, card_or_name):
        effect = self.get(card_or_name)
        if effect is None:
            raise ValueError("no V2 CardEffect for " + str(getattr(card_or_name, "name", card_or_name)))
        return effect

    def __contains__(self, card_or_name):
        return self.get(card_or_name) is not None

    def __len__(self):
        return len(self._effects)
