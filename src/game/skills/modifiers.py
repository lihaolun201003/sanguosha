"""Continuous rule modifiers (距离 / 手牌上限 / 摸牌数 / 出杀次数 / 攻击范围).

Not everything a skill does is a one-off event.  Some skills permanently bend a
number the rules ask for, so they register a modifier instead of listening to
an event.  Every modifier has an owner (usually the player) so it can be
unbound together with the skill.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Tuple


class ModifierKind(str, Enum):
    # 距离：source 计算到 target 的距离
    DISTANCE_OUTGOING = "distance_outgoing"     # 归属 source（马术：-1）
    DISTANCE_INCOMING = "distance_incoming"     # 归属 target（飞影：+1）
    ATTACK_RANGE = "attack_range"               # 归属使用者
    HAND_LIMIT = "hand_limit"                   # 归属弃牌阶段角色
    DRAW_COUNT = "draw_count"                   # 归属摸牌阶段角色
    SLASH_QUOTA = "slash_quota"                 # 归属出杀角色（额外次数）
    # 出牌阶段【杀】可额外指定的目标数：归属使用者（天义 +1、神戟 至多三名）
    SLASH_TARGETS = "slash_targets"
    # 禁止使用【杀】：归属角色；value 是 callable(game, query) -> bool（天义没赢）
    SLASH_FORBIDDEN = "slash_forbidden"
    # 【杀】无距离限制：归属使用者（武神）。condition 判断具体是哪张【杀】。
    SLASH_DISTANCE_IGNORE = "slash_distance_ignore"
    # 牌类别锁定（鸡肋）：归属**被限制**的角色，value = 被锁的类别字符串
    # （"basic" / "trick" / "equipment"）。使用、打出与弃置三条路都读它。
    CATEGORY_FORBIDDEN = "category_forbidden"
    TRICK_RANGE_IGNORE = "trick_range_ignore"   # 归属使用者：锦囊无距离限制（奇才）
    # 响应需求：归属 source（无双）；被要求响应时需要额外 N 张牌
    RESPONSE_COUNT = "response_count"
    # 伤害加成：归属 source（裸衣）；对目标造成的伤害 +N
    DAMAGE_DEALT = "damage_dealt"
    # 目标合法性：归属 target（空城 / 谦逊）；value 是 callable(game, query) -> bool
    TARGET_FORBIDDEN = "target_forbidden"
    # 出牌阶段额外可用牌（不参与距离校验的强制使用，如离间的决斗）
    FORCED_USE = "forced_use"
    # 花色改写：归属拥有者（红颜）。value 是 callable(game, card, owner) -> suit|None，
    # 返回非 None 表示"这张牌对这名角色而言视为该花色"。
    SUIT_AS = "suit_as"
    # 虚拟防具：归属角色（八阵）。value 是防具牌名（"BAGUA"）。
    # 只在**没有装备防具**时生效，由 Game.armor_of 统一查询。
    VIRTUAL_ARMOR = "virtual_armor"
    # 锦囊伤害的来源改写：归属角色（祸首）。value = callable(game, query) -> bool，
    # query 里带 card / user；命中时这张锦囊造成的伤害以该角色为来源。
    TRICK_SOURCE = "trick_source"
    # 锦囊的使用距离限制改写：归属使用者（断粮）。value = 距离上限（int）。
    TRICK_DISTANCE = "trick_distance"
    # 濒死救援限制：归属施加限制的角色（完杀）。value = callable(game, query) -> bool，
    # query 里带 rescuer / dying / card_name。
    RESCUE_FORBIDDEN = "rescue_forbidden"


@dataclass
class Modifier:
    kind: ModifierKind
    value: Any                       # int 或 callable(game, query) -> int
    owner: Any = None                # 卸载单位（通常是玩家）
    skill_id: str = ""
    priority: int = 0
    order: int = 0
    roles: Tuple[str, ...] = field(default_factory=tuple)
    condition: Any = None            # 可选 callable(game, query) -> bool

    def matches(self, game, query):
        if self.roles and not all(query.get(role) is self.owner for role in self.roles):
            return False
        if self.condition is not None and not self.condition(game, query):
            return False
        return True

    def amount(self, game, query):
        if callable(self.value):
            return int(self.value(game, query))
        return int(self.value)


class ModifierRegistry:
    """Ordered, owner-scoped collection of active modifiers."""

    def __init__(self, game):
        self.game = game
        self._items: Dict[ModifierKind, List[Modifier]] = {}
        self._next_order = 1

    # ==================================================
    # 注册 / 注销
    # ==================================================

    def register(self, modifier):
        modifier.kind = ModifierKind(modifier.kind)
        modifier.order = self._next_order
        self._next_order += 1
        self._items.setdefault(modifier.kind, []).append(modifier)
        return modifier

    def unregister(self, modifier):
        items = self._items.get(modifier.kind)
        if not items:
            return False
        if modifier in items:
            items.remove(modifier)
            return True
        return False

    def unregister_owner(self, owner):
        removed = 0
        for kind, items in list(self._items.items()):
            kept = [item for item in items if item.owner is not owner]
            removed += len(items) - len(kept)
            if kept:
                self._items[kind] = kept
            else:
                del self._items[kind]
        return removed

    def unregister_skill(self, skill_id):
        removed = 0
        for kind, items in list(self._items.items()):
            kept = [item for item in items if item.skill_id != skill_id]
            removed += len(items) - len(kept)
            if kept:
                self._items[kind] = kept
            else:
                del self._items[kind]
        return removed

    def unregister_owner_skill(self, owner, skill_id):
        """卸载某个角色身上的某个技能带来的全部 modifier。

        多人局里同一技能可能被多名玩家持有，所以卸载必须同时匹配
        拥有者与技能 id，不能只按技能 id 清理。
        """

        removed = 0
        for kind, items in list(self._items.items()):
            kept = [
                item
                for item in items
                if not (item.owner is owner and item.skill_id == skill_id)
            ]
            removed += len(items) - len(kept)
            if kept:
                self._items[kind] = kept
            else:
                del self._items[kind]
        return removed

    def clear(self):
        self._items.clear()

    # ==================================================
    # 查询
    # ==================================================

    def sorted_for(self, kind):
        """Stable order: 高优先级优先，其次注册顺序。"""

        items = self._items.get(ModifierKind(kind), ())
        return sorted(items, key=lambda item: (-item.priority, item.order))

    def total(self, kind, **query):
        total = 0
        for modifier in self.sorted_for(kind):
            if modifier.matches(self.game, query):
                total += modifier.amount(self.game, query)
        return total

    def owners(self, kind):
        return tuple(modifier.owner for modifier in self.sorted_for(kind))

    def suit_override(self, card, owner):
        """这张牌对 ``owner`` 而言的有效花色（没有改写时返回 None）。

        按优先级从高到低取第一个生效的改写：多条改写同时存在时，
        高优先级的那条说了算，顺序稳定可复现。
        """

        if card is None:
            return None
        for modifier in self.sorted_for(ModifierKind.SUIT_AS):
            if modifier.owner is not owner:
                continue
            value = modifier.value
            if callable(value):
                result = value(self.game, card, owner)
            else:
                result = value
            if result is not None:
                return result
        return None

    def __len__(self):
        return sum(len(items) for items in self._items.values())
