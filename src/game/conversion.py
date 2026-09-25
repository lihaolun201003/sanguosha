"""Card conversion: using one physical card as another (武圣 / 龙胆).

A conversion never renames a real ``Card``.  It produces a ``VirtualCard`` that
carries the virtual identity plus the real ``source_cards``, so rules and
skills can still see the original suit / rank / colour / count.

Conversions are declared by skills exactly like modifiers: the SkillManager
registers them on bind and removes them on unbind.
"""

from dataclasses import dataclass, field, replace
from itertools import count
from typing import Any, Callable, Optional, Tuple

from src.card import display_name_for

_VIRTUAL_IDS = count(1)

# 允许转换的使用场合
PLAY_CONTEXT = "play"          # 出牌阶段主动使用
RESPONSE_CONTEXT = "response"  # 响应（闪 / 杀 / 无懈 …）
RESCUE_CONTEXT = "rescue"      # 濒死自救

# source card 的合法区域：由 Conversion 自己声明，引擎不写死"只有手牌"。
HAND_ZONE = "hand"
EQUIPMENT_ZONE = "equipment"
SOURCE_ZONES = (HAND_ZONE, EQUIPMENT_ZONE)


@dataclass(frozen=True, eq=False)
class VirtualCard:
    """One card identity produced by a skill from real source cards."""

    name: str
    source_cards: Tuple[Any, ...]
    skill_id: str = ""
    owner: Any = None
    category: str = "basic"
    subtype: Optional[str] = None
    nature: str = "normal"

    def __post_init__(self):
        object.__setattr__(self, "id", "virtual-%d" % next(_VIRTUAL_IDS))

    # ---- 引擎与 UI 需要的牌面属性 ----

    _virtual = True

    @property
    def is_virtual(self):
        return True

    @property
    def display_name(self):
        return display_name_for(self.name, self.nature)

    @property
    def primary_source(self):
        return self.source_cards[0] if self.source_cards else None

    @property
    def suit(self):
        source = self.primary_source
        return getattr(source, "suit", None)

    @property
    def rank(self):
        source = self.primary_source
        return getattr(source, "rank", None)

    @property
    def card_color(self):
        source = self.primary_source
        return getattr(source, "card_color", None)

    @property
    def color(self):
        source = self.primary_source
        return getattr(source, "color", (235, 220, 175))

    @property
    def attack_range(self):
        return 1

    @property
    def suit_symbol(self):
        source = self.primary_source
        return getattr(source, "suit_symbol", "")

    @property
    def suit_name(self):
        source = self.primary_source
        return getattr(source, "suit_name", "")

    @property
    def identity_label(self):
        if not self.suit_symbol or not self.rank:
            return ""
        return "%s %s" % (self.suit_symbol, self.rank)

    @property
    def description(self):
        return None

    def __repr__(self):
        return "VirtualCard(%s from %d card(s))" % (self.name, len(self.source_cards))


@dataclass(frozen=True)
class CardConversion:
    """One skill's rule for turning real cards into a virtual card.

    ``source_count`` 保持向后兼容（1..N 的旧写法），需要区间时用
    ``min_sources`` / ``max_sources``。``source_zones`` 声明实体牌可以
    从哪些区域取出，引擎不会默认只看手牌。
    """

    skill_id: str
    matches: Callable[[Any], bool]            # 源牌谓词
    name: str                                 # 结果牌名（"SHA" 等）
    category: str = "basic"
    subtype: Optional[str] = None
    nature: str = "normal"
    source_count: int = 1
    contexts: Tuple[str, ...] = (PLAY_CONTEXT, RESPONSE_CONTEXT)
    source_zones: Tuple[str, ...] = (HAND_ZONE,)
    min_sources: Optional[int] = None
    max_sources: Optional[int] = None
    # 结果与正常使用完全相同时，是否仍然保留这个转化 Action。
    # 默认 False：同一张牌的同名同结果只显示正常使用，避免 Picker 出现重复项。
    keep_with_normal: bool = False
    # 时机条件：callable(game, player) -> bool。例如【急救】只在回合外可用。
    # None 表示任何时候都可用。只影响"能否被选中"，不改变牌本身。
    available: Any = None
    # 需要看**拥有者状态**的源牌谓词：callable(game, owner, card) -> bool。
    # ``matches`` 只看得见牌本身（"是不是红色"），而【双雄】这类判断是
    # "这张牌对**这个**角色算不算素材"（颜色与本次判定不同）。绑定技能时
    # ``for_owner`` 会把它折进 ``matches``，于是所有拿不到 owner 的查询点
    # （CardActionDiscovery / UI 高亮 / 引擎校验）读到的都是同一份结果，
    # 不会出现"界面高亮合法、引擎却拒绝"的分叉。
    owner_matches: Any = None
    # 张数由当前局面决定时的取值函数：callable(game, owner) -> int。
    # 例：龙魂 X = 自己的当前体力值（至少 1）。声明了它时，
    # min_sources / max_sources 只作为"兜底范围"，实际判定一律走
    # ``bounds()``——这样响应路径（凑不凑得齐）、UI 高亮与引擎校验
    # 三处读的都是同一个数。
    count_for: Any = None

    def __post_init__(self):
        low = int(self.min_sources if self.min_sources is not None else self.source_count)
        high = int(self.max_sources if self.max_sources is not None else self.source_count)
        if low < 1 or high < low:
            raise ValueError("invalid source range: %d..%d" % (low, high))
        object.__setattr__(self, "min_sources", low)
        object.__setattr__(self, "max_sources", high)

    # ---- 谓词 ----

    def bounds(self, game, owner):
        """本次实际需要的 source 张数范围 (low, high)。

        声明了 ``count_for`` 时由它决定（X 随局面变化，例如龙魂 = 当前体力值）；
        否则用声明好的 min_sources / max_sources。
        """

        if self.count_for is None or owner is None:
            return self.min_sources, self.max_sources
        try:
            value = int(self.count_for(game, owner))
        except (TypeError, ValueError, AttributeError):
            return self.min_sources, self.max_sources
        value = max(1, value)
        return value, value

    def accepts_count(self, count, game, owner):
        low, high = self.bounds(game, owner)
        return low <= int(count) <= high

    def accepts(self, card, context=None):
        if context is not None and context not in self.contexts:
            return False
        return bool(self.matches(card))

    def accepts_many(self, cards, context=None, game=None, owner=None):
        if context is not None and context not in self.contexts:
            return False
        count = len(cards)
        if game is not None and owner is not None:
            if not self.accepts_count(count, game, owner):
                return False
        elif count < self.min_sources or count > self.max_sources:
            return False
        return all(bool(self.matches(card)) for card in cards)

    def uses_zone(self, zone):
        return zone in self.source_zones

    # ---- 拥有者绑定 ----

    def for_owner(self, owner, game=None):
        """折叠成"某个角色专用的"声明（技能绑定时调用一次）。

        只有声明了 ``owner_matches`` 才会产生新副本；其余技能拿到的还是
        原对象，行为与绑定方式完全不变。
        """

        if self.owner_matches is None:
            return self
        base = self.matches
        extra = self.owner_matches

        def bound(card, _base=base, _extra=extra, _owner=owner, _game=game):
            return bool(_base(card)) and bool(_extra(_game, _owner, card))

        return replace(self, matches=bound, owner_matches=None)

    def build(self, owner, cards):
        return VirtualCard(
            name=self.name,
            source_cards=tuple(cards),
            skill_id=self.skill_id,
            owner=owner,
            category=self.category,
            subtype=self.subtype,
            nature=self.nature,
        )


@dataclass
class _Registration:
    conversion: CardConversion
    owner: Any = None
    order: int = 0


class ConversionRegistry:
    """Owner-scoped registry of active conversions."""

    def __init__(self, game):
        self.game = game
        self._items = []
        self._next_order = 1

    # ---- 注册 / 注销 ----

    def register(self, conversion, owner=None):
        registration = _Registration(conversion, owner, self._next_order)
        self._next_order += 1
        self._items.append(registration)
        return registration

    def unregister_owner(self, owner):
        before = len(self._items)
        self._items = [item for item in self._items if item.owner is not owner]
        return before - len(self._items)

    def unregister_owner_skill(self, owner, skill_id):
        before = len(self._items)
        self._items = [
            item for item in self._items
            if not (item.owner is owner and item.conversion.skill_id == skill_id)
        ]
        return before - len(self._items)

    def clear(self):
        self._items.clear()

    # ---- 查询 ----

    def sorted_items(self):
        return sorted(self._items, key=lambda item: item.order)

    def options_for(self, game, actor, card, context=None):
        """某张实体牌当前有哪些转换用法：[(conversion, VirtualCard)]。"""

        options = []
        for item in self.sorted_items():
            conversion = item.conversion
            if not conversion.accepts(card, context):
                continue
            options.append((conversion, conversion.build(actor, (card,))))
        return options

    def candidates_for(self, game, actor, card_name, context, zone=None):
        """需要某个牌名时，actor 能通过哪些转换凑出来：[(VirtualCard, conversion)]。"""

        zone = actor.hand if zone is None else zone
        result = []
        for item in self.sorted_items():
            conversion = item.conversion
            if conversion.name != card_name:
                continue
            if context is not None and context not in conversion.contexts:
                continue
            if conversion.source_count != 1:
                continue
            for card in list(zone):
                if not conversion.matches(card):
                    continue
                result.append((conversion.build(actor, (card,)), conversion))
        return result

    def __len__(self):
        return len(self._items)
