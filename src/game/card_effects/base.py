"""Card rules are registered independently from physical card data."""

from dataclasses import dataclass
from typing import Any, Callable, Tuple

from src.game.rules import TargetRule, validate_targets


@dataclass(frozen=True)
class AuxiliaryInput:
    """一次用牌在"目标已选之后、真正提交之前"还需要玩家补全的输入。

    它不是这张牌的普通目标（那些走 ``min_targets`` / ``max_targets``），
    而是**效果内部**要求玩家指定的东西：【借刀杀人】的"被杀目标"就是这一类
    ——规则原文是"该角色需对其攻击范围内、**由你指定**的另一名角色使用一张
    【杀】"，这个"另一名角色"必须由使用者自己选，却不是这张牌的牌面目标。

    之所以要在提交之前收：一张牌的合法性与选牌在提交那一刻一起定下来，
    收集期间取消则一张牌都不会动。收集到的值按 ``key`` 放进 metadata，
    效果自己在结算时读它。
    """

    key: str                       # metadata 里的键名
    prompt: str
    candidates: Callable[..., Any]  # callable(game, actor, targets) -> list[player]
    kind: str = "target"


class CardEffect:
    card_name = None
    # Category-level fallback used by equipment: every physical weapon, armor
    # and horse shares one effect instead of forty near-identical entries.
    card_category = None
    category = "trick"
    target_rule = TargetRule.NO_TARGET
    min_targets = 0
    max_targets = 0
    distance_limit = None
    can_respond = False
    # 整张牌是否有一个属于自己的【无懈可击】窗口。它由 UseCardFlow 在效果
    # 开始**之前**统一开启一次，与"效果开始后逐目标要求【杀】/【闪】"是两种
    # 完全不同的语义（TRICK_NEGATION_WINDOW / CARD_RESPONSE_REQUIREMENT）。
    cancellable_by_wuxie = False
    # 逐目标响应型效果（南蛮入侵 / 万箭齐发）：效果阶段自己按目标顺序推进，
    # 一次只向一个目标要响应牌。UI 据此把箭头与提示逐个目标展示。
    sequential_targets = False
    # 这张牌能不能"重铸"（置入弃牌堆并摸一张牌，例如【铁索连环】）。重铸不是
    # 转化：它不产生逻辑牌、也没有目标，所以不在 Card Action Discovery 的候选
    # 里——能力在这里声明，动作由决策来源用 ``metadata={"recast": True}`` 构造，
    # 合法性仍由本效果自己的 ``can_use`` 判定。
    can_recast = False

    def required_inputs(self, game, actor, targets, card=None) -> Tuple[AuxiliaryInput, ...]:
        """使用这张牌还需要玩家补全的**附加输入**（默认没有）。

        返回的每一项都会在目标选好之后、这次使用提交之前，由引擎逐个向
        使用者索取；全部齐了才提交。需要"第二个角色 / 第二张牌"的效果
        （借刀杀人）声明在这里，而不是在结算阶段临时挑一个默认值。
        """

        return ()

    def target_rule_for(self, game, actor, card=None):
        """本次使用的目标规则，默认就是类上声明的 ``target_rule``。

        技能可以改写它（【天义】/【神戟】让一张【杀】多指定目标），因此
        规则层统一走这个方法而不是直接读类属性——否则会出现"技能改写了
        目标规则、结算却不认"的半截能力。
        """

        return self.target_rule

    def target_bounds_for(self, game, actor, card=None):
        """本次使用的目标数量范围 (min, max)，默认就是类上声明的常量。"""

        return self.min_targets, self.max_targets

    def distance_limit_for(self, game, actor, card=None):
        """本次使用的距离上限（None = 不限）。

        默认是类上声明的 ``distance_limit``；技能可以放宽它（断粮让
        【兵粮寸断】能对距离 2 以内的角色使用）。统一走这里，卡牌效果
        就不会出现"技能放宽了距离、结算却还按旧上限拒绝"的半截实现。
        """

        query = getattr(game, "trick_distance_limit", None)
        if callable(query):
            return query(actor, card)
        return type(self).distance_limit

    def can_use(self, game, action):
        if game.game_over:
            return False, "游戏已经结束。"
        if not getattr(action.card, "_virtual", False) and not any(
            card is action.card for card in action.actor.hand
        ):
            return False, "这张牌已经不在使用者手牌中。"
        minimum, maximum = self.target_bounds_for(game, action.actor, action.card)
        if not validate_targets(
            game, action.actor, action.targets,
            self.target_rule_for(game, action.actor, action.card),
            minimum, maximum, card=action.card,
        ):
            return False, "目标不合法。"
        return True, ""

    def begin(self, use_flow):
        raise NotImplementedError

    def resume(self, use_flow, resolution):
        raise RuntimeError(self.card_name + " is not waiting for a response")

    def resume_after_child(self, use_flow, result):
        """效果开始前的技能窗口结束，回来继续效果前奏。

        技能可以在 ``CARD_EFFECT_BEFORE`` 里开一个"要不要让这张牌生效"的
        窗口（啖酪）：那个窗口没答完，效果前奏就不能继续往下走。
        """

        raise RuntimeError(self.card_name + " has no pre-effect window to resume")


class CardEffectRegistry:
    def __init__(self):
        self._effects = {}
        self._by_category = {}

    def register(self, effect):
        if effect.card_name:
            self._effects[effect.card_name] = effect
            return effect
        if effect.card_category:
            self._by_category[effect.card_category] = effect
            return effect
        raise ValueError("CardEffect.card_name or card_category is required")

    def get(self, card_or_name):
        name = getattr(card_or_name, "name", card_or_name)
        effect = self._effects.get(name)
        if effect is not None:
            return effect
        category = getattr(card_or_name, "category", None)
        if category is not None:
            return self._by_category.get(category)
        return None

    def require(self, card_or_name):
        effect = self.get(card_or_name)
        if effect is None:
            raise ValueError("no V2 CardEffect for " + str(getattr(card_or_name, "name", card_or_name)))
        return effect

    def __contains__(self, card_or_name):
        return self.get(card_or_name) is not None

    def __len__(self):
        return len(self._effects)
