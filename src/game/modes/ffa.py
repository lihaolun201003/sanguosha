"""本地自由混战：现有默认玩法，逻辑全部来自 GameMode 默认实现。"""

from .base import GameMode


class FreeForAllMode(GameMode):
    id = "ffa"
    name = "自由混战"
    description = "2～8 人各自为战，最后存活者获胜。"
    uses_identities = False
    general_choice_count = 3
