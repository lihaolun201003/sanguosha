"""Unified action context for card action discovery.

UI, AI, ResponseSystem and the rescue flow all ask the same question:

    某个玩家，在某个场合下，这些实体牌现在能做什么？

That situation is a ``CardActionContext`` — never a scattered set of
``if game.phase == ...`` / ``if response_name == ...`` checks inside the UI.
"""

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from src.game.conversion import PLAY_CONTEXT, RESCUE_CONTEXT, RESPONSE_CONTEXT

# 三个场合（转换声明也复用同一套常量，避免出现第二套字符串）。
CONTEXTS = (PLAY_CONTEXT, RESPONSE_CONTEXT, RESCUE_CONTEXT)


class ActionKind:
    """两种 Action：实体牌本身能用，还是被技能转化后才能用。"""

    NORMAL = "normal"
    CONVERSION = "conversion"


@dataclass(frozen=True)
class CardActionContext:
    """一次 Action Discovery 的完整场合描述。

    ``requirement`` 是响应 / 救援时引擎需要的牌名（例如 "SHAN"、"TAO"）；
    ``allowed_names`` 是这次请求允许的结果牌名集合。两者都为空的场合
    （PLAY）意味着任何结果牌都允许，具体合法性交给 CardEffect 判定。
    """

    actor: Any
    context: str
    requirement: Optional[str] = None
    allowed_names: Tuple[str, ...] = ()
    pending_request: Any = None
    phase: str = ""

    # ---- 场合判断 ----

    @property
    def is_play(self):
        return self.context == PLAY_CONTEXT

    @property
    def is_response(self):
        return self.context == RESPONSE_CONTEXT

    @property
    def is_rescue(self):
        return self.context == RESCUE_CONTEXT

    # ---- 结果牌过滤 ----

    def allows(self, name):
        """这次场合是否接受某个结果牌名。"""

        if not self.allowed_names:
            return True
        return name in self.allowed_names

    def describe_requirement(self):
        if self.requirement:
            return self.requirement
        if self.allowed_names:
            return "、".join(sorted(self.allowed_names))
        return ""


# ==================================================
# 稳定 Action ID
#
# Picker / 测试 / AI / Pending 都依赖这些字符串，不允许出现对象地址，
# 也不允许用 name+suit+rank 这类会撞车的组合。
# ==================================================


def card_uid(card):
    if card is None:
        return "-"
    return str(getattr(card, "id", None) or ("anon-%d" % id(card)))


def source_uid(cards):
    return "+".join(card_uid(card) for card in cards) if cards else "-"


def normal_action_id(card, context):
    return "normal:%s:%s" % (card_uid(card), context)


def conversion_action_id(skill_id, cards, result_name, context):
    return "convert:%s:%s:%s:%s" % (
        skill_id or "-",
        source_uid(cards),
        result_name or "-",
        context,
    )
