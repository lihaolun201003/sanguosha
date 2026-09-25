"""``src.game.view``：Phase 11.3 的逐人只读视图层。

* ``view_model``   —— ``ClientGameView`` / ``PlayerView`` / ``ViewCard`` 等纯数据 DTO
* ``visibility``   —— 逐人可见性规则的唯一出处（手牌 / 身份 / 隐藏区域）
* ``view_builder`` —— 在一份权威 ``Game`` 上为某一名观众生成视图
* ``presentation`` —— 房主侧把引擎事件翻译成逐人过滤后的表现事件
"""

from .player_view import PlayerPublicState
from .view_builder import build_view
from .view_model import (
    ActionView,
    ClientGameView,
    JudgeView,
    PlayerView,
    PoolEntryView,
    ResponseView,
    ResultView,
    SelectionView,
    ViewCard,
)
from .visibility import (
    card_is_visible_to,
    hand_is_visible,
    secret_selection_zone,
    visible_hand_cards,
    visible_identity_of,
)

__all__ = [
    "PlayerPublicState",
    "build_view",
    "ClientGameView",
    "PlayerView",
    "ViewCard",
    "ActionView",
    "ResponseView",
    "JudgeView",
    "SelectionView",
    "PoolEntryView",
    "ResultView",
    "visible_hand_cards",
    "visible_identity_of",
    "hand_is_visible",
    "card_is_visible_to",
    "secret_selection_zone",
]
