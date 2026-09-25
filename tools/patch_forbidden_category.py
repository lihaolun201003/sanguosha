"""补丁 2：鸡肋的"牌类别锁定"通用机制 + 修罗费用修正。

运行方式（在项目根目录）：
    .venv/Scripts/python.exe tools/patch_forbidden_category.py
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def patch(relative_path, pairs):
    path = os.path.join(ROOT, *relative_path.split("/"))
    text = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        if old not in text:
            print("  跳过（未命中）：" + relative_path + " :: "
                  + old.strip().splitlines()[0][:70])
            continue
        text = text.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8").write(text)
    print("已更新 " + relative_path)


patch("src/game/skills/modifiers.py", [(
    '    SLASH_DISTANCE_IGNORE = "slash_distance_ignore"\n',
    '    SLASH_DISTANCE_IGNORE = "slash_distance_ignore"\n'
    '    # 牌类别锁定（鸡肋）：归属**被限制**的角色，value = 被锁的类别字符串\n'
    '    # （"basic" / "trick" / "equipment"）。使用、打出与弃置三条路都读它。\n'
    '    CATEGORY_FORBIDDEN = "category_forbidden"\n',
)])

patch("src/game/core.py", [(
    "    def slash_ignores_distance(self, player, card=None):",
    '''    def category_forbidden(self, player, card):
        """这张牌现在是否被技能锁住（鸡肋：不能使用 / 打出 / 弃置这个类别）。

        锁是**按类别**而不是按牌记的：对方手里原有的牌与新摸到的同类别牌
        一并受限，直到本回合结束由技能自己解除。
        """

        if not hasattr(self, "modifiers") or player is None or card is None:
            return False
        category = getattr(card, "category", None)
        if category is None:
            return False
        query = {"player": player, "card": card, "category": category}
        for modifier in self.modifiers.sorted_for(ModifierKind.CATEGORY_FORBIDDEN):
            if modifier.owner is not player:
                continue
            if not modifier.matches(self, query):
                continue
            value = modifier.value
            value = value(self, query) if callable(value) else value
            if value == category:
                return True
        return False

    def slash_ignores_distance(self, player, card=None):''',
)])

patch("src/game/card_actions/discovery.py", [(
    """        effect = self.game.engine.card_effects.get(card)
        if effect is None:
            return False, "这张牌不能主动使用。\"""",
    """        if self.game.category_forbidden(actor, card):
            return False, "本回合你不能使用这个类别的牌。"
        effect = self.game.engine.card_effects.get(card)
        if effect is None:
            return False, "这张牌不能主动使用。\"""",
)])

patch("src/game/flows/turn.py", [(
    """        elif phase is TurnPhase.DISCARD:
            # 手牌上限同样走统一查询（技能可以修改）。
            while len(self.player.hand) > self.game.hand_limit(self.player):
                self.context.apply(MoveCardAtom(self.player.hand[-1], source=self.player.hand, destination=self.game.deck.discard_pile))
        return False""",
    """        elif phase is TurnPhase.DISCARD:
            # 手牌上限同样走统一查询（技能可以修改）。
            while len(self.player.hand) > self.game.hand_limit(self.player):
                card = self._discardable_card()
                if card is None:
                    break
                self.context.apply(MoveCardAtom(
                    card, source=self.player.hand,
                    destination=self.game.deck.discard_pile))
        return False

    def _discardable_card(self):
        \"\"\"超限弃牌时优先丢没被锁住的牌（鸡肋）。全被锁住时按原规则丢最后一张。\"\"\"

        hand = self.player.hand
        if not hand:
            return None
        for card in reversed(hand):
            if not self.game.category_forbidden(self.player, card):
                return card
        return hand[-1]""",
)])

patch("src/game/controllers/ai.py", [(
    """        ordered = sorted(self.player.hand, key=self.card_value)
        return self.discard_cards(ordered[:surplus])""",
    """        # 被锁住类别的牌（鸡肋）优先保留：不能用、不能打、也不能弃。
        free = [card for card in self.player.hand
                if not self.game.category_forbidden(self.player, card)]
        locked = [card for card in self.player.hand
                  if self.game.category_forbidden(self.player, card)]
        ordered = sorted(free, key=self.card_value) + sorted(locked, key=self.card_value)
        return self.discard_cards(ordered[:surplus])""",
)])

patch("src/game/skills/mechanics.py", [(
    "def gender_of(player):",
    '''def set_forbidden_category(game, applier, target, category, *, skill_id="jilei"):
    """让 ``target`` 直到本回合结束不能使用 / 打出 / 弃置 ``category`` 类别的牌。

    实现是一条挂在**被限制者**身上的 modifier（按类别查询），``applier``
    记在 modifier 上，供回合结束时统一回收。使用、打出与强制弃牌三条路径
    都读同一个查询，因此不会出现"某一条路漏了"的半截限制。
    """

    if game is None or target is None or not category:
        return None
    from ..skills.modifiers import Modifier, ModifierKind

    modifier = Modifier(
        kind=ModifierKind.CATEGORY_FORBIDDEN,
        value=category,
        owner=target,
        skill_id=skill_id,
    )
    modifier.applier = applier
    game.modifiers.register(modifier)
    return modifier


def clear_forbidden_categories(game, applier):
    """回收某个角色施加的全部牌类别锁定（回合结束时调用）。"""

    registry = getattr(game, "modifiers", None)
    if registry is None:
        return 0
    removed = 0
    for kind, items in list(registry._items.items()):
        kept = [
            item for item in items
            if not (getattr(item, "applier", None) is applier
                    and item.kind is ModifierKind.CATEGORY_FORBIDDEN)
        ]
        removed += len(items) - len(kept)
        if kept:
            registry._items[kind] = kept
        else:
            del registry._items[kind]
    return removed


def gender_of(player):''',
)])

# ---- SP：鸡肋的清理时机 + 修罗的费用声明 ----
patch("src/game/skills/expansions/sp.py", [
    (
        """    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=25),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        source = getattr(damage, "source", None)
        return (self.owner.alive and source is not None and source is not self.owner
                and source.alive and int(event.payload.get("amount", 0) or 0) > 0)

    def resolve(self, context, event):
        JileiFlow(context.services["engine"], self.owner,
                  event.payload["damage"].source).start()""",
        """    def bindings(self):
        return (
            SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=25),
            SkillBinding(EventType.TURN_END, priority=-95),
        )

    def can_trigger(self, context, event):
        if event.name is EventType.TURN_END:
            # 「直到本回合结束」：任何人的回合结束时都回收一次，
            # 因为伤害可能发生在别的角色的回合里。
            return True
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        source = getattr(damage, "source", None)
        return (self.owner.alive and source is not None and source is not self.owner
                and source.alive and int(event.payload.get("amount", 0) or 0) > 0)

    def resolve(self, context, event):
        if event.name is EventType.TURN_END:
            from ..mechanics import clear_forbidden_categories

            if clear_forbidden_categories(context.state, self.owner):
                context.state.add_log("【鸡肋】的限制随本回合结束解除")
            return
        JileiFlow(context.services["engine"], self.owner,
                  event.payload["damage"].source).start()""",
    ),
    (
        """        from src.game.skills.mechanics import set_forbidden_category

        set_forbidden_category(self.owner, self.source, category)""",
        """        from ..mechanics import set_forbidden_category

        set_forbidden_category(self.game, self.owner, self.source, category)""",
    ),
    (
        """            cost_prompt="【离间】：请选择一张牌弃置",""",
        """            cost_prompt="【离间】：请选择一张牌弃置",""",
    ),
    (
        """        spec=ActiveSkillSpec(),
        tags=("active",),
    ),
    SkillDef(
        id="shenwei",""",
        """        spec=ActiveSkillSpec(
            cost_cards=1,
            cost_prompt="【修罗】：请选择一张手牌弃置（需与目标延时锦囊花色相同）",
        ),
        tags=("active",),
    ),
    SkillDef(
        id="shenwei",""",
    ),
    (
        """def _activate_xiuluo(game, player, target=None, cards=None):
    chosen = list(cards or [])
    if not chosen or not any(
            getattr(chosen[0], "suit", None) == getattr(card, "suit", None)
            for card in _delay_tricks(player)):
        return False
    suit = getattr(chosen[0], "suit", None)
    matching = [card for card in _delay_tricks(player)
                if getattr(card, "suit", None) == suit]
    if not matching:
        return False
    game.engine.context.apply(MoveCardAtom(
        chosen[0], source=player.hand, destination=game.deck.discard_pile))
    game.engine.context.apply(MoveCardAtom(""",
        """def _activate_xiuluo(game, player, target=None, cards=None):
    # 弃一张手牌是技能的**费用**（cost_cards=1），已由引擎支付；
    # 这里只按花色挑出要被弃置的延时锦囊。
    chosen = list(cards or [])
    suit = getattr(chosen[0], "suit", None) if chosen else None
    matching = [card for card in _delay_tricks(player)
                if suit is not None and getattr(card, "suit", None) == suit]
    if not matching:
        game.message = "【修罗】：没有与弃牌同花色的延时锦囊。"
        return False
    game.engine.context.apply(MoveCardAtom(""",
    ),
])

# ---- SP 技能注册 ----
patch("src/game/skills/expansions/__init__.py", [
    (
        "from .mountain import MOUNTAIN_SKILLS\nfrom .wind import WIND_SKILLS",
        "from .god import GOD_SKILLS\n"
        "from .mountain import MOUNTAIN_SKILLS\n"
        "from .sp import SP_SKILLS\n"
        "from .wind import WIND_SKILLS\n"
        "from .yijiang import YIJIANG_SKILLS",
    ),
    (
        "EXPANSION_SKILLS = WIND_SKILLS + FIRE_SKILLS + FOREST_SKILLS + MOUNTAIN_SKILLS",
        "EXPANSION_SKILLS = (WIND_SKILLS + FIRE_SKILLS + FOREST_SKILLS\n"
        "                    + MOUNTAIN_SKILLS + YIJIANG_SKILLS + GOD_SKILLS\n"
        "                    + SP_SKILLS)",
    ),
    (
        '    "MOUNTAIN_SKILLS",',
        '    "GOD_SKILLS",\n    "MOUNTAIN_SKILLS",\n    "SP_SKILLS",\n    "YIJIANG_SKILLS",',
    ),
])

print("补丁 2 应用完成")
