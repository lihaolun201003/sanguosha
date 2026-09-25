"""SP 武将技能：杨修 / 貂蝉 / 公孙瓒 / 关羽 / 吕布（两形态）/ 袁术 等。

SP 目录里有大量与标准版同名的武将（貂蝉 / 孙尚香 / 庞德 / 马超 / 贾诩 /
蔡文姬），它们的技能组与标准版**相同**（共用同一份技能定义），差别只在
势力 / 版本，因此这里只实现 SP 独有的技能。
"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, UnequipAtom
from src.game.engine import EventType, Flow
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..definitions import (
    ActiveSkillSpec,
    ModifierSpec,
    SkillDef,
    SkillKind,
    active,
    triggered,
)
from ..mechanics import (
    ask_cards,
    ask_confirm,
    ask_option,
    ask_targets,
    awaken,
    effective_suit,
    flip_player,
    gain_skill,
    hand_cards,
    limited_used,
    lose_hp,
    lost_hp,
    other_alive_players,
    use_virtual,
)
from ..modifiers import ModifierKind
from ..state import ResetScope


def _cards_of(player):
    cards = list(getattr(player, "hand", ()) or ())
    for card in (getattr(player, "equipment", None) or {}).values():
        if card is not None:
            cards.append(card)
    cards.extend(list(getattr(player, "judgement_zone", ()) or ()))
    return cards


def _delay_tricks(player):
    return [
        card for card in (getattr(player, "judgement_zone", ()) or ())
        if getattr(card, "name", None) in ("LEBU", "BINGLIANG", "SHANDIAN")
    ]


# ==================================================
# 杨修 · 啖酪 / 鸡肋
# ==================================================


class Danlao(Skill):
    """当一个锦囊指定了包括你在内的多名目标时，你可以摸一张牌，
    若如此做，该锦囊对你无效。"""

    id = "danlao"
    name = "啖酪"

    def bindings(self):
        return (SkillBinding(EventType.CARD_EFFECT_BEFORE, priority=70),)

    def can_trigger(self, context, event):
        if event.target is not self.owner or not self.owner.alive:
            return False
        card = event.payload.get("card")
        if card is None or getattr(card, "category", None) != "trick":
            return False
        targets = list(event.payload.get("targets", ()) or ())
        if len(targets) <= 1 and event.payload.get("use_flow") is not None:
            targets = list(getattr(event.payload["use_flow"], "targets", ()) or ())
        return len(targets) > 1 and any(target is self.owner for target in targets)

    def resolve(self, context, event):
        DanlaoFlow(context.services["engine"], self.owner, event).start()


class DanlaoFlow(Flow):
    def __init__(self, engine, owner, event):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.event = event
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【啖酪】：是否摸一张牌，令此锦囊对你无效？",
                    reason="danlao")
        return self.current_result()

    def advance(self, response=None):
        if response is None or not response.confirmed:
            return self.complete({"applied": False})
        self.context.apply(DrawCardsAtom(self.owner, 1))
        self.event.payload["blocked_by"] = "啖酪"
        self.event.cancel()
        self.game.add_log("%s 的【啖酪】摸一张牌并令该锦囊对其无效" % self.owner.name)
        return self.complete({"applied": True})


CATEGORY_LABELS = (
    ("basic", "基本牌"),
    ("trick", "锦囊牌"),
    ("equipment", "装备牌"),
)


class Jilei(Skill):
    """你受到伤害时，可以说出一种牌的类别（基本牌 / 锦囊牌 / 装备牌），
    对你造成伤害的角色直到本回合结束不能使用、打出或弃置该类别的手牌。"""

    id = "jilei"
    name = "鸡肋"

    def bindings(self):
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
                  event.payload["damage"].source).start()


class JileiFlow(Flow):
    def __init__(self, engine, owner, source):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.source = source
        self.stage = "choose"

    def begin(self):
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【鸡肋】：请说出一种牌的类别",
                   reason="jilei", options=CATEGORY_LABELS)
        return self.current_result()

    def advance(self, response=None):
        category = str(getattr(response, "option", "") or "")
        if category not in [value for value, _label in CATEGORY_LABELS]:
            return self.complete({"applied": False})
        from ..mechanics import set_forbidden_category

        set_forbidden_category(self.game, self.owner, self.source, category)
        label = dict(CATEGORY_LABELS)[category]
        self.game.add_log("%s 的【鸡肋】：%s 直到本回合结束不能使用、打出或弃置%s"
                          % (self.owner.name, self.source.name, label))
        self.game.message = ("【鸡肋】生效：%s 本回合不能使用、打出或弃置%s。"
                             % (self.source.name, label))
        return self.complete({"applied": True})


# ==================================================
# SP 貂蝉 · 离间（此【决斗】不能被【无懈可击】响应）
# ==================================================


def _sp_lijian_males(game, player):
    return [
        other for other in game.get_alive_players()
        if other is not player and getattr(other, "gender", None) == "male"
    ]


def _can_sp_lijian(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("lijian_sp", "used", 0):
        return False, "本阶段已经发动过"
    if not player.hand:
        return False, "需要弃置一张牌"
    if len(_sp_lijian_males(game, player)) < 2:
        return False, "需要两名男性角色"
    return True, ""


def _activate_sp_lijian(game, player, target=None, cards=None):
    """SP 离间：弃一张牌，令一名男性对另一名男性使用一张不能被无懈的【决斗】。"""

    males = _sp_lijian_males(game, player)
    if len(males) < 2:
        return False
    actor, defender = males[0], males[1]
    if target is not None and target in males:
        actor = target
        defender = next((item for item in males if item is not actor), males[1])
    player.skill_state.set("lijian_sp", "used", 1, ResetScope.PHASE)
    use_virtual(game, actor, "JUEDOU", targets=[defender], skip_wuxie=True)
    game.add_log("%s 发动【离间】：%s 与 %s 决斗（不能被【无懈可击】响应）"
                 % (player.name, actor.name, defender.name))
    return True


# ==================================================
# 公孙瓒 · 义从
# ==================================================


def _yicong_outgoing(game, query):
    source = query.get("source")
    if source is None:
        return 0
    return -1 if int(source.hp) > 2 else 0


def _yicong_incoming(game, query):
    target = query.get("target")
    if target is None:
        return 0
    return 1 if int(target.hp) <= 2 else 0


# ==================================================
# SP 关羽 · 单骑
# ==================================================


def _lord_is_caocao(game):
    mode = getattr(game, "mode", None)
    lord = mode.lord() if mode is not None and hasattr(mode, "lord") else None
    return lord is not None and getattr(lord, "general_id", None) == "caocao"


class Danqi(Skill):
    """觉醒技：回合开始阶段，若你的手牌数大于当前体力值且本局主公为曹操，
    减 1 点体力上限并永久获得【马术】。"""

    id = "danqi"
    name = "单骑"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=55),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.PREPARE:
            return False
        if limited_used(self.owner, self.id):
            return False
        if not _lord_is_caocao(context.state):
            return False
        return len(self.owner.hand) > int(self.owner.hp)

    def resolve(self, context, event):
        awaken(context.state, self.owner, self.id, max_hp_delta=-1,
               gain=("mashu",), name="单骑")


# ==================================================
# SP 吕布（暴怒的战神）· 修罗 / 神威 / 神戟
# ==================================================


def _can_xiuluo(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "prepare":
        return False, "只能在你的回合开始阶段发动"
    if player.skill_state.get("xiuluo", "used", 0):
        return False, "本回合已经发动过"
    if not player.hand:
        return False, "需要一张手牌"
    if not _delay_tricks(player):
        return False, "判定区里没有延时锦囊"
    return True, ""


def _activate_xiuluo(game, player, target=None, cards=None):
    # 弃一张手牌是技能的**费用**（cost_cards=1），已由引擎支付；
    # 这里只按花色挑出要被弃置的延时锦囊。
    chosen = list(cards or [])
    suit = getattr(chosen[0], "suit", None) if chosen else None
    matching = [card for card in _delay_tricks(player)
                if suit is not None and getattr(card, "suit", None) == suit]
    if not matching:
        game.message = "【修罗】：没有与弃牌同花色的延时锦囊。"
        return False
    game.engine.context.apply(MoveCardAtom(
        matching[0], source=player.judgement_zone,
        destination=game.deck.discard_pile))
    player.skill_state.set("xiuluo", "used", 1, ResetScope.TURN)
    game.add_log("%s 发动【修罗】，弃置一张同花色手牌并弃掉判定区里的【%s】"
                 % (player.name, getattr(matching[0], "display_name", "?")))
    return True


def _shenji_extra(game, query):
    """神戟：没装备武器时，【杀】可指定至多三名角色（= 额外两个目标）。"""

    player = query.get("player")
    if player is None:
        return 0
    if player.get_equipment("weapon") is not None:
        return 0
    return 2


# ==================================================
# 袁术 · 庸肆 / 伪帝
# ==================================================


def _kingdom_count(game):
    kingdoms = {
        getattr(player, "kingdom", None) for player in game.get_alive_players()
    }
    kingdoms.discard(None)
    return max(1, len(kingdoms))


def _yongsi_draw(game, query):
    return _kingdom_count(game)


def _yongsi_hand_limit(game, query):
    """庸肆：弃牌阶段至少须弃掉等同于全场势力数的牌 → 手牌上限按此下调。"""

    return -_kingdom_count(game)


class Weidi(Skill):
    """锁定技：你拥有当前主公的主公技。"""

    id = "weidi"
    name = "伪帝"

    def bindings(self):
        return (
            SkillBinding(EventType.TURN_START, priority=60),
            SkillBinding(EventType.TURN_END, priority=-90),
        )

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        if event.name is EventType.TURN_END:
            return bool(self.owner.skill_state.get(self.id, "borrowed", None))
        return True

    def resolve(self, context, event):
        game = context.state
        if event.name is EventType.TURN_END:
            for skill_id in list(self.owner.skill_state.get(self.id, "borrowed", ()) or ()):
                if game.skills.has(self.owner, skill_id):
                    game.skills.unbind(self.owner, skill_id)
            self.owner.skill_state.clear(self.id, "borrowed")
            return
        mode = getattr(game, "mode", None)
        lord = mode.lord() if mode is not None and hasattr(mode, "lord") else None
        if lord is None or lord is self.owner:
            return
        general = game.generals.get(getattr(lord, "general_id", None))
        borrowed = []
        for skill_id in getattr(general, "skill_ids", ()) or ():
            definition = game.skill_registry.get(skill_id)
            if definition is None or not definition.is_lord_skill:
                continue
            if game.skills.has(self.owner, skill_id):
                continue
            gain_skill(game, self.owner, skill_id)
            borrowed.append(skill_id)
        if borrowed:
            self.owner.skill_state.set(self.id, "borrowed", tuple(borrowed),
                                       ResetScope.PERSISTENT)
            game.add_log("%s 的【伪帝】获得了主公的主公技" % self.owner.name)


# ==================================================
# 技能表
# ==================================================

SP_SKILLS = (
    triggered(
        "danlao",
        "啖酪",
        "当一个锦囊指定了包括你在内的多名目标时，你可以立即摸一张牌，"
        "若如此做，该锦囊对你无效。",
        factory=Danlao,
    ),
    triggered(
        "jilei",
        "鸡肋",
        "当你受到伤害时，你可以说出一种牌的类别（基本牌 / 锦囊牌 / 装备牌），"
        "对你造成伤害的角色直到本回合结束不能使用、打出或弃置该类别的手牌。",
        factory=Jilei,
    ),
    active(
        "lijian_sp",
        "离间",
        "出牌阶段限一次，你可以弃一张牌并选择两名男性角色，"
        "视为其中一名角色对另一名角色使用一张【决斗】"
        "（此【决斗】不能被【无懈可击】响应）。",
        can_activate=_can_sp_lijian,
        activate=_activate_sp_lijian,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_sp_lijian_males,
            target_prompt="【离间】：请选择发起决斗的男性角色",
            cost_cards=1,
            cost_prompt="【离间】：请选择一张牌弃置",
        ),
        tags=("active",),
    ),
    SkillDef(
        id="yicong",
        name="义从",
        description="锁定技，只要你的体力值大于 2 点，你计算与其他角色的距离时始终 -1；"
        "只要你的体力值为 2 点或更低，其他角色计算与你的距离时始终 +1。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.DISTANCE_OUTGOING, value=_yicong_outgoing,
                         roles=("source",)),
            ModifierSpec(kind=ModifierKind.DISTANCE_INCOMING, value=_yicong_incoming,
                         roles=("target",)),
        ),
    ),
    triggered(
        "danqi",
        "单骑",
        "觉醒技，回合开始阶段，若你的手牌数大于你当前的体力值，"
        "且本局游戏的主公为曹操，你须减 1 点体力上限并永久获得技能【马术】。",
        factory=Danqi,
        tags=("awakening",),
    ),
    active(
        "xiuluo",
        "修罗",
        "回合开始阶段，你可以弃一张手牌来弃置你判定区里的延时类锦囊（必须花色相同）。",
        can_activate=_can_xiuluo,
        activate=_activate_xiuluo,
        spec=ActiveSkillSpec(
            cost_cards=1,
            cost_prompt="【修罗】：请选择一张手牌弃置（需与目标延时锦囊花色相同）",
        ),
        tags=("active",),
    ),
    SkillDef(
        id="shenwei",
        name="神威",
        description="锁定技，摸牌阶段，你额外摸两张牌；你的手牌上限 +2。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.DRAW_COUNT, value=2, roles=("player",)),
            ModifierSpec(kind=ModifierKind.HAND_LIMIT, value=2, roles=("player",)),
        ),
    ),
    SkillDef(
        id="shenji",
        name="神戟",
        description="没装备武器时，你使用的【杀】可以指定至多三名角色为目标。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.SLASH_TARGETS, value=_shenji_extra,
                         roles=("player",)),
        ),
    ),
    SkillDef(
        id="yongsi",
        name="庸肆",
        description="锁定技，摸牌阶段，你额外摸 X 张牌（X 为场上现存势力数）；"
        "弃牌阶段，你至少须弃掉等同于场上现存势力数的牌（不足则全弃）。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.DRAW_COUNT, value=_yongsi_draw,
                         roles=("player",)),
            ModifierSpec(kind=ModifierKind.HAND_LIMIT, value=_yongsi_hand_limit,
                         roles=("player",)),
        ),
    ),
    triggered(
        "weidi",
        "伪帝",
        "锁定技，你拥有当前主公的主公技。",
        factory=Weidi,
        kind=SkillKind.LOCKED,
    ),
)
