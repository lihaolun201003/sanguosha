"""一将成名武将技能：于禁 / 凌统 / 吴国太 / 张春华 / 徐庶 / 徐盛 / 曹植 /
法正 / 陈宫 / 马谡 / 高顺。

钟会的卡面只印了技能名、正文是空白，规则来源无法确认，因此**没有实现**
（``generals/expansions.py`` 里 ``implemented=False``），这里也不给它写
任何猜测性的技能。
"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom, UnequipAtom
from src.game.conversion import CardConversion, PLAY_CONTEXT
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
    attack_range_targets,
    effective_suit,
    flip_player,
    hand_cards,
    judge,
    limited_used,
    lose_hp,
    lost_hp,
    other_alive_players,
    pindian_possible,
    start_pindian,
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


def _move_anywhere(game, context, owner, card, destination):
    """把 owner 的任意区域里的一张牌移到 destination（装备区按槽位处理）。"""

    if any(item is card for item in owner.hand):
        context.apply(MoveCardAtom(card, source=owner.hand, destination=destination))
        return True
    if any(item is card for item in owner.judgement_zone):
        context.apply(MoveCardAtom(
            card, source=owner.judgement_zone, destination=destination))
        return True
    for slot, equipped in (owner.equipment or {}).items():
        if equipped is card:
            context.apply(UnequipAtom(owner, slot, destination))
            return True
    for zone in (owner.placed_cards or {}).values():
        if any(item is card for item in zone):
            context.apply(MoveCardAtom(card, source=zone, destination=destination))
            return True
    return False


def _is_basic(card):
    return getattr(card, "category", None) == "basic"


def _is_delay_trick(card):
    return (getattr(card, "category", None) == "trick"
            and getattr(card, "name", None) in ("LEBU", "BINGLIANG", "SHANDIAN"))


def _is_non_delay_trick(card):
    return (getattr(card, "category", None) == "trick"
            and getattr(card, "name", None) not in ("LEBU", "BINGLIANG", "SHANDIAN"))


# ==================================================
# 于禁 · 毅重
# ==================================================


class Yizhong(Skill):
    """锁定技：没有装备防具时，黑色的【杀】对你无效。"""

    id = "yizhong"
    name = "毅重"

    def bindings(self):
        return (SkillBinding(EventType.CARD_EFFECT_BEFORE, priority=90),)

    def can_trigger(self, context, event):
        if event.target is not self.owner or not self.owner.alive:
            return False
        card = event.payload.get("card")
        if card is None or getattr(card, "name", None) != "SHA":
            return False
        if getattr(card, "card_color", None) != "black":
            return False
        return context.state.armor_card(self.owner) is None

    def resolve(self, context, event):
        event.payload["blocked_by"] = "毅重"
        event.cancel()
        context.state.add_log("%s 的【毅重】令黑色【杀】无效" % self.owner.name)


# ==================================================
# 凌统 · 旋风
# ==================================================


class Xuanfeng(Skill):
    """你失去装备区里的牌时，可以视为对一名其他角色使用【杀】，
    或对距离 1 以内的一名其他角色造成 1 点伤害。"""

    id = "xuanfeng"
    name = "旋风"

    def bindings(self):
        return (SkillBinding(EventType.EQUIPMENT_LOST, priority=20),)

    def can_trigger(self, context, event):
        if event.target is not self.owner or not self.owner.alive:
            return False
        # 换装（装备区里换上新武器）不算"失去装备区里的牌"意义上的触发源：
        # 官方口径是"失去一次装备区里的牌"，换下旧牌同样算，因此这里不额外过滤。
        return bool(other_alive_players(context.state, self.owner))

    def resolve(self, context, event):
        XuanfengFlow(context.services["engine"], self.owner).start()


class XuanfengFlow(Flow):
    """旋风：两项选一，再为选中的那项挑目标。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "choose"

    def begin(self):
        options = [("sha", "视为对一名其他角色使用一张【杀】")]
        if self._close_targets():
            options.append(("damage", "对距离 1 以内的一名其他角色造成 1 点伤害"))
        options.append(("none", "不发动"))
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【旋风】：请选择一项", reason="xuanfeng",
                   options=tuple(options))
        return self.current_result()

    def _close_targets(self):
        from src.game.rules import DistanceRule

        return [
            other for other in other_alive_players(self.game, self.owner)
            if DistanceRule.distance(self.game, self.owner, other) <= 1
        ]

    def advance(self, response=None):
        if self.stage == "sha":
            return self._after_sha(response)
        if self.stage == "damage":
            return self._after_damage(response)
        option = str(getattr(response, "option", "") or "")
        if option == "sha":
            return self._ask_sha()
        if option == "damage":
            return self._ask_damage()
        return self.complete({"applied": False})

    def _ask_sha(self):
        self.stage = "sha"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【旋风】：请选择【杀】的目标（不计入次数限制）",
                    reason="xuanfeng",
                    candidates=other_alive_players(self.game, self.owner),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _ask_damage(self):
        self.stage = "damage"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【旋风】：请选择受到 1 点伤害的角色",
                    reason="xuanfeng", candidates=self._close_targets(),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_sha(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if targets:
            use_virtual(self.game, self.owner, "SHA", targets=targets)
        return self.complete({"applied": True})

    def _after_damage(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if targets:
            from src.game.flows.damage import DamageContext, DamageFlow

            DamageFlow(self.engine, DamageContext(self.owner, targets[0], 1)).start()
            self.game.add_log("%s 的【旋风】对 %s 造成 1 点伤害"
                              % (self.owner.name, targets[0].name))
        return self.complete({"applied": True})


# ==================================================
# 吴国太 · 甘露 / 补益
# ==================================================


def _ganlu_targets(game, player):
    return other_alive_players(game, player)


def _can_ganlu(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("ganlu", "used", 0):
        return False, "本回合已经发动过"
    if len(_ganlu_targets(game, player)) < 2:
        return False, "需要两名角色"
    return True, ""


def _equipment_count(player):
    return len([card for card in (getattr(player, "equipment", None) or {}).values()
                if card is not None])


def _activate_ganlu(game, player, target=None, cards=None):
    GanluFlow(game.engine, player).start()
    return True


class GanluFlow(Flow):
    """甘露：选两名角色交换装备区的所有牌（数量差不能超过已损失体力）。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.first = None
        self.second = None
        self.stage = "first"

    def begin(self):
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【甘露】：请选择第一名角色", reason="ganlu",
                    candidates=_ganlu_targets(self.game, self.owner),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "first":
            return self._after_first(response)
        return self._after_second(response)

    def _after_first(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        self.first = targets[0]
        self.stage = "second"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【甘露】：请选择第二名角色（与 %s 交换装备）" % self.first.name,
                    reason="ganlu",
                    candidates=[other for other in _ganlu_targets(self.game, self.owner)
                                if other is not self.first],
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_second(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        self.second = targets[0]
        diff = abs(_equipment_count(self.first) - _equipment_count(self.second))
        if diff > max(0, lost_hp(self.owner)):
            self.game.message = ("【甘露】：装备牌数差 %d 超过你已损失的体力值，无法交换。"
                                 % diff)
            return self.complete({"applied": False})
        self.owner.skill_state.set("ganlu", "used", 1, ResetScope.TURN)
        first_cards = [card for card in (self.first.equipment or {}).values() if card]
        second_cards = [card for card in (self.second.equipment or {}).values() if card]
        for slot in list(self.first.equipment):
            if self.first.get_equipment(slot) is not None:
                self.context.apply(UnequipAtom(self.first, slot))
        for slot in list(self.second.equipment):
            if self.second.get_equipment(slot) is not None:
                self.context.apply(UnequipAtom(self.second, slot))
        from src.game.atoms_v2 import EquipCardAtom

        for card in first_cards:
            self.context.apply(EquipCardAtom(self.second, card))
        for card in second_cards:
            self.context.apply(EquipCardAtom(self.first, card))
        self.game.add_log("%s 的【甘露】交换了 %s 与 %s 的装备牌"
                          % (self.owner.name, self.first.name, self.second.name))
        return self.complete({"applied": True})


class Buyi(Skill):
    """当有角色进入濒死状态时，展示其一张手牌，不为基本牌则其弃置该牌并回复 1 点。"""

    id = "buyi"
    name = "补益"

    def bindings(self):
        return (SkillBinding(EventType.DYING_ENTERED, priority=80),)

    def can_trigger(self, context, event):
        dying = event.target
        if dying is None or not self.owner.alive:
            return False
        return bool(getattr(dying, "hand", ()))

    def resolve(self, context, event):
        BuyiFlow(context.services["engine"], self.owner, event.target).start()


class BuyiFlow(Flow):
    def __init__(self, engine, owner, dying):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.dying = dying
        self.stage = "select"

    def begin(self):
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【补益】：请选择展示 %s 的一张手牌" % self.dying.name,
                  reason="buyi", candidates=list(self.dying.hand),
                  min_cards=1, max_cards=1, zone="public_pool",
                  context={"zone_owner": self.dying})
        return self.current_result()

    def advance(self, response=None):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self.complete({"applied": False})
        card = cards[0]
        label = getattr(card, "display_name", "?")
        if _is_basic(card):
            self.game.add_log("【补益】展示了【%s】（基本牌），不生效" % label)
            return self.complete({"applied": False})
        if any(item is card for item in self.dying.hand):
            self.context.apply(MoveCardAtom(
                card, source=self.dying.hand,
                destination=self.game.deck.discard_pile))
        self.context.apply(RecoverHpAtom(self.dying, 1))
        self.game.add_log("【补益】展示了【%s】，%s 弃置它并回复 1 点体力"
                          % (label, self.dying.name))
        return self.complete({"applied": True})


# ==================================================
# 张春华 · 绝情 / 伤逝
# ==================================================


class Jueqing(Skill):
    """锁定技：你造成的伤害均视为体力流失。"""

    id = "jueqing"
    name = "绝情"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_CREATED, priority=80),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.source is not self.owner:
            return False
        if getattr(damage, "cancelled", False):
            return False
        return int(damage.amount) > 0

    def resolve(self, context, event):
        game = context.state
        damage = event.payload["damage"]
        amount = max(0, int(damage.amount))
        damage.cancelled = True
        damage.effects.append("【绝情】改为体力流失")
        # 伤害变成体力流失：不触发受伤类技能、不吃防具与加成，
        # 但体力降到 0 依然进入濒死（由 Game.lose_hp 统一负责）。
        if amount:
            lose_hp(game, damage.target, amount, source=self.owner, reason="绝情")
        game.add_log("%s 的【绝情】将伤害改为体力流失（%s 失去 %d 点体力）"
                     % (self.owner.name, damage.target.name, amount))


class Shangshi(Skill):
    """除弃牌阶段外，手牌数小于已损失体力值时，立即补至该值。"""

    id = "shangshi"
    name = "伤逝"

    def bindings(self):
        return (
            SkillBinding(EventType.CARD_LOST, priority=-20),
            SkillBinding(EventType.CARD_DISCARDED, priority=-20),
            SkillBinding(EventType.DAMAGE_SETTLED, priority=-30),
            SkillBinding(EventType.PHASE_END, priority=-30),
        )

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        game = context.state
        if game.phase == "discard" and game.current_turn_player is self.owner:
            return False
        if event.payload.get("owner") is not None and event.payload.get("owner") is not self.owner:
            return False
        return len(self.owner.hand) < lost_hp(self.owner)

    def resolve(self, context, event):
        game = context.state
        need = lost_hp(self.owner) - len(self.owner.hand)
        if need <= 0:
            return
        context.apply(DrawCardsAtom(self.owner, need))
        game.add_log("%s 的【伤逝】将手牌补至 %d 张"
                     % (self.owner.name, len(self.owner.hand)))


# ==================================================
# 徐庶 · 无言 / 举荐
# ==================================================


class Wuyan(Skill):
    """锁定技：你使用的非延时锦囊对其他角色无效；其他角色的非延时锦囊对你无效。"""

    id = "wuyan"
    name = "无言"

    def bindings(self):
        return (SkillBinding(EventType.CARD_EFFECT_BEFORE, priority=85),)

    def can_trigger(self, context, event):
        card = event.payload.get("card")
        if card is None or not _is_non_delay_trick(card):
            return False
        if event.source is self.owner and event.target is not self.owner:
            return True
        return event.target is self.owner and event.source is not self.owner

    def resolve(self, context, event):
        event.payload["blocked_by"] = "无言"
        event.cancel()
        context.state.add_log("%s 的【无言】令【%s】无效"
                              % (self.owner.name,
                                 getattr(event.payload.get("card"), "display_name", "?")))


def _jujian_targets(game, player):
    return other_alive_players(game, player)


def _can_jujian(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("jujian", "used", 0):
        return False, "本回合已经发动过"
    if not player.hand:
        return False, "没有可以弃置的手牌"
    if not _jujian_targets(game, player):
        return False, "没有其他角色"
    return True, ""


def _activate_jujian(game, player, target=None, cards=None):
    """举荐：弃至多三张牌令一名其他角色摸等量的牌；三张同类则回复 1 点。"""

    if target is None:
        return False
    # 弃哪几张由玩家自己挑（张数上限写在 spec 的 max_cost_cards 里）。
    # 这里不再有任何"没传就替他挑一张"的兜底：那种兜底会让真人点了技能
    # 却看到程序自己丢了牌。
    chosen = list(cards or ())[:3]
    if not chosen:
        game.message = "【举荐】：请先选择要弃置的牌。"
        return False
    for card in chosen:
        if any(item is card for item in player.hand):
            game.engine.context.apply(MoveCardAtom(
                card, source=player.hand, destination=game.deck.discard_pile))
    game.engine.context.apply(DrawCardsAtom(target, len(chosen)))
    player.skill_state.set("jujian", "used", 1, ResetScope.TURN)
    categories = {getattr(card, "category", None) for card in chosen}
    bonus = ""
    if len(chosen) >= 3 and len(categories) == 1:
        game.engine.context.apply(RecoverHpAtom(player, 1))
        bonus = "，并回复 1 点体力"
    game.add_log("%s 发动【举荐】，弃 %d 张牌令 %s 摸 %d 张牌%s"
                 % (player.name, len(chosen), target.name, len(chosen), bonus))
    return True


# ==================================================
# 徐盛 · 破军
# ==================================================


class Pojun(Skill):
    """你使用【杀】造成伤害后，令受伤角色摸 X 张牌（X = 其当前体力，至多 5），
    然后其武将牌翻面。"""

    id = "pojun"
    name = "破军"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED, priority=-15),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        damage = event.payload.get("damage")
        amount = int(event.payload.get("amount", 0) or 0)
        if damage is None or amount <= 0:
            return False
        card = getattr(damage, "card", None)
        if card is None or getattr(card, "name", None) != "SHA":
            return False
        target = getattr(damage, "target", None)
        return target is not None and target is not self.owner and target.alive

    def resolve(self, context, event):
        game = context.state
        target = event.payload["damage"].target
        count = max(0, min(5, int(target.hp)))
        if count:
            context.apply(DrawCardsAtom(target, count))
        flip_player(game, target, reason="破军")
        game.add_log("%s 的【破军】令 %s 摸 %d 张牌并翻面"
                     % (self.owner.name, target.name, count))


# ==================================================
# 曹植 · 落英 / 酒诗
# ==================================================


class Luoying(Skill):
    """其他角色的梅花牌因弃置或判定进入弃牌堆时，你可以获得之。"""

    id = "luoying"
    name = "落英"

    def bindings(self):
        return (SkillBinding(EventType.CARD_DISCARDED, priority=15),)

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        card = event.payload.get("card")
        owner = event.payload.get("owner")
        if card is None or owner is self.owner:
            return False
        if getattr(card, "suit", None) != "club":
            return False
        return any(item is card for item in context.state.deck.discard_pile)

    def resolve(self, context, event):
        game = context.state
        card = event.payload["card"]
        if not any(item is card for item in game.deck.discard_pile):
            return
        game.deck.discard_pile.remove(card)
        self.owner.hand.append(card)
        game.add_log("%s 的【落英】获得了【%s】"
                     % (self.owner.name, getattr(card, "display_name", "?")))


def _can_jiushi(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if not getattr(player, "face_up", True):
        return False, "你的武将牌已经背面朝上"
    if player.jiu_used:
        return False, "本回合已经使用过【酒】"
    return True, ""


def _activate_jiushi(game, player, target=None, cards=None):
    """酒诗：翻面来视为使用一张【酒】。"""

    flip_player(game, player, reason="酒诗")
    from src.game.engine import UseCardAction

    from ..mechanics import virtual_card

    virtual = virtual_card("JIU", player, (), category="basic")
    game.engine.submit(UseCardAction(
        player, virtual, [player], ignore_usage_limit=False))
    game.add_log("%s 发动【酒诗】，翻面并视为使用一张【酒】" % player.name)
    return True


class JiushiBack(Skill):
    """酒诗：背面朝上时受到伤害，可在伤害结算后翻回正面。

    工厂类的 ``id`` 必须与 SkillDef 的 ``jiushi`` 一致，否则技能卸载不掉。
    """

    id = "jiushi"
    name = "酒诗"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED, priority=-40),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        if not self.owner.alive:
            return False
        return not getattr(self.owner, "face_up", True)

    def resolve(self, context, event):
        game = context.state
        flip_player(game, self.owner, reason="酒诗")
        game.add_log("%s 的【酒诗】在伤害结算后翻回正面" % self.owner.name)


# ==================================================
# 法正 · 恩怨 / 眩惑
# ==================================================


class Enyuan(Skill):
    """锁定技：其他角色每令你回复 1 点体力，该角色摸一张牌；
    其他角色每对你造成一次伤害，须给你一张红桃手牌，否则其失去 1 点体力。"""

    id = "enyuan"
    name = "恩怨"

    def bindings(self):
        return (
            SkillBinding(EventType.HP_RECOVERED, priority=10),
            SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=15),
        )

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        if event.name is EventType.HP_RECOVERED:
            healed = event.target
            source = event.source
            return healed is self.owner and source is not None and source is not self.owner
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        source = getattr(damage, "source", None)
        return source is not None and source is not self.owner and source.alive

    def resolve(self, context, event):
        game = context.state
        if event.name is EventType.HP_RECOVERED:
            benefactor = event.source
            context.apply(DrawCardsAtom(benefactor, 1))
            game.add_log("%s 的【恩怨】令 %s 摸一张牌" % (self.owner.name, benefactor.name))
            return
        EnyuanFlow(context.services["engine"], self.owner,
                   event.payload["damage"].source).start()


class EnyuanFlow(Flow):
    def __init__(self, engine, owner, source):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.source = source
        self.stage = "confirm"

    def begin(self):
        hearts = hand_cards(
            self.source,
            lambda card: effective_suit(self.game, card, self.source) == "heart")
        if not hearts:
            return self._punish()
        ask_confirm(self.engine, self, source=self.owner, target=self.source,
                    prompt="【恩怨】：是否给 %s 一张红桃手牌？否则你失去 1 点体力。"
                           % self.owner.name,
                    reason="enyuan")
        return self.current_result()

    def advance(self, response=None):
        # 两个窗口按 stage 区分：先问"给不给红桃牌"，再问"给哪一张"。
        # 这段判断**必须**留在类里：曾经它被写成一个模块级的 monkey patch
        # （``EnyuanFlow.advance = _enyuan_advance``），而 patch 函数内部又
        # 调用 ``EnyuanFlow.advance``——替换之后那就是它自己，伤害结算一到
        # 【恩怨】就无限递归。
        if self.stage == "card":
            return self._after_card(response)
        if response is None or not response.confirmed:
            return self._punish()
        return self._ask_card()

    def _ask_card(self):
        hearts = hand_cards(
            self.source,
            lambda card: effective_suit(self.game, card, self.source) == "heart")
        if not hearts:
            return self._punish()
        self.stage = "card"
        ask_cards(self.engine, self, source=self.owner, target=self.source,
                  prompt="【恩怨】：请选择给 %s 的一张红桃手牌" % self.owner.name,
                  reason="enyuan", candidates=hearts, min_cards=1, max_cards=1)
        return self.current_result()

    def _punish(self):
        game = self.game
        lose_hp(game, self.source, 1, source=self.owner, reason="恩怨")
        game.add_log("%s 的【恩怨】令 %s 失去 1 点体力" % (self.owner.name, self.source.name))
        return self.complete({"applied": True})

    def _after_card(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        for card in cards:
            if any(item is card for item in self.source.hand):
                self.context.apply(MoveCardAtom(
                    card, source=self.source.hand, destination=self.owner.hand))
        if cards:
            self.game.add_log("%s 给了 %s 一张红桃手牌" % (self.source.name, self.owner.name))
        else:
            return self._punish()
        return self.complete({"applied": True})




def _can_xuanhuo(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("xuanhuo", "used", 0):
        return False, "本回合已经发动过"
    hearts = hand_cards(
        player, lambda card: effective_suit(game, card, player) == "heart")
    if not hearts:
        return False, "没有红桃手牌"
    if len(other_alive_players(game, player)) < 2:
        return False, "需要其他角色与一名牌的目标"
    return True, ""


def _is_xuanhuo_source(game, player, card):
    """眩惑的素材：一张红桃手牌（花色按当前生效的花色算）。"""

    return hand_cards(player, lambda item: item is card) and (
        effective_suit(game, card, player) == "heart")


def _activate_xuanhuo(game, player, target=None, cards=None):
    if target is None:
        return False
    # 交给谁、交哪一张都由玩家自己决定（见 SkillDef 的 spec）。这里只做
    # 最后一层复核：没有素材就什么也不发生，**绝不替他挑一张**。
    sources = list(cards or ())
    if not sources:
        game.message = "【眩惑】：请先选择一张红桃手牌。"
        return False
    player.skill_state.set("xuanhuo", "used", 1, ResetScope.TURN)
    game.engine.context.apply(MoveCardAtom(
        sources[0], source=player.hand, destination=target.hand))
    game.add_log("%s 发动【眩惑】，将一张红桃手牌交给 %s" % (player.name, target.name))
    XuanhuoFlow(game.engine, player, target).start()
    return True


class XuanhuoFlow(Flow):
    """眩惑：获得目标的一张牌，并立即交给除其以外的其他角色。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.stage = "take"

    def begin(self):
        if not _cards_of(self.target):
            return self.complete({"applied": False})
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【眩惑】：请选择获得 %s 的一张牌" % self.target.name,
                  reason="xuanhuo", candidates=_cards_of(self.target),
                  min_cards=1, max_cards=1, zone="public_pool",
                  context={"zone_owner": self.target})
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "take":
            return self._after_take(response)
        return self._after_give(response)

    def _after_take(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self.complete({"applied": False})
        self.card = cards[0]
        if not _move_anywhere(self.game, self.context, self.target, self.card,
                              self.owner.hand):
            return self.complete({"applied": False})
        others = [other for other in other_alive_players(self.game, self.owner)
                  if other is not self.target]
        if not others:
            return self.complete({"applied": True})
        self.stage = "give"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【眩惑】：请选择接受这张牌的角色（不能是 %s）"
                           % self.target.name,
                    reason="xuanhuo", candidates=others,
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_give(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if targets and any(item is self.card for item in self.owner.hand):
            self.context.apply(MoveCardAtom(
                self.card, source=self.owner.hand, destination=targets[0].hand))
            self.game.add_log("%s 的【眩惑】把该牌交给了 %s"
                              % (self.owner.name, targets[0].name))
        return self.complete({"applied": True})


# ==================================================
# 陈宫 · 明策 / 智迟
# ==================================================


def _mingce_sources(player):
    return hand_cards(player, lambda card: (
        getattr(card, "category", None) == "equipment"
        or getattr(card, "name", None) == "SHA"))


def _mingce_targets(game, player):
    return other_alive_players(game, player)


def _can_mingce(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("mingce", "used", 0):
        return False, "本回合已经发动过"
    if not _mingce_sources(player):
        return False, "没有装备牌或【杀】"
    if not _mingce_targets(game, player):
        return False, "没有其他角色"
    return True, ""


def _is_mingce_source(game, player, card):
    """明策的素材：一张装备牌或一张【杀】手牌。"""

    return bool((getattr(card, "category", None) == "equipment")
                or (getattr(card, "name", None) == "SHA"))


def _activate_mingce(game, player, target=None, cards=None):
    if target is None:
        return False
    # 交给哪一张由玩家自己挑；没有素材就直接不发动。
    sources = list(cards or ())
    if not sources:
        game.message = "【明策】：请先选择一张装备牌或【杀】。"
        return False
    player.skill_state.set("mingce", "used", 1, ResetScope.TURN)
    game.engine.context.apply(MoveCardAtom(
        sources[0], source=player.hand, destination=target.hand))
    game.add_log("%s 发动【明策】，将【%s】交给 %s"
                 % (player.name, getattr(sources[0], "display_name", "?"), target.name))
    MingceFlow(game.engine, player, target).start()
    return True


class MingceFlow(Flow):
    """明策：目标选择「视为对其攻击范围内由你指定的一名角色使用【杀】」或「摸一张牌」。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.stage = "choose"

    def begin(self):
        options = [("draw", "摸一张牌")]
        if self._victims():
            options.insert(0, ("sha", "视为对其攻击范围内由你指定的一名角色使用【杀】"))
        ask_option(self.engine, self, source=self.owner, target=self.target,
                   prompt="【明策】：请选择一项", reason="mingce",
                   options=tuple(options))
        return self.current_result()

    def _victims(self):
        return attack_range_targets(self.game, self.target)

    def advance(self, response=None):
        if self.stage == "victim":
            return self._after_victim(response)
        option = str(getattr(response, "option", "") or "")
        if option != "sha" or not self._victims():
            self.context.apply(DrawCardsAtom(self.target, 1))
            self.game.add_log("【明策】：%s 选择摸一张牌" % self.target.name)
            return self.complete({"applied": True})
        self.stage = "victim"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【明策】：请选择 %s 要攻击的目标" % self.target.name,
                    reason="mingce", candidates=self._victims(),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_victim(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if targets:
            use_virtual(self.game, self.target, "SHA", targets=targets)
            self.game.add_log("【明策】：%s 视为对 %s 使用一张【杀】"
                              % (self.target.name, targets[0].name))
        return self.complete({"applied": True})


class Zhichi(Skill):
    """锁定技：你的回合外，你受到一次伤害后，直到本回合结束，
    任何【杀】或非延时锦囊均对你无效。"""

    id = "zhichi"
    name = "智迟"

    def bindings(self):
        return (
            SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=25),
            SkillBinding(EventType.CARD_EFFECT_BEFORE, priority=88),
            SkillBinding(EventType.TURN_END, priority=-5),
        )

    def can_trigger(self, context, event):
        if event.name is EventType.DAMAGE_TARGET_AFTER:
            damage = event.payload.get("damage")
            if damage is None or damage.target is not self.owner:
                return False
            return (self.owner.alive
                    and context.state.current_turn_player is not self.owner)
        if event.name is EventType.TURN_END:
            return bool(self.owner.skill_state.get(self.id, "active", 0))
        if not self.owner.skill_state.get(self.id, "active", 0):
            return False
        card = event.payload.get("card")
        if card is None or event.target is not self.owner:
            return False
        return (getattr(card, "name", None) == "SHA"
                or _is_non_delay_trick(card))

    def resolve(self, context, event):
        game = context.state
        if event.name is EventType.DAMAGE_TARGET_AFTER:
            self.owner.skill_state.set(self.id, "active", 1, ResetScope.TURN)
            game.add_log("%s 的【智迟】生效：本回合内【杀】与非延时锦囊对其无效"
                         % self.owner.name)
            return
        if event.name is EventType.TURN_END:
            self.owner.skill_state.set(self.id, "active", 0, ResetScope.TURN)
            return
        event.payload["blocked_by"] = "智迟"
        event.cancel()
        game.add_log("%s 的【智迟】令【%s】无效"
                     % (self.owner.name,
                        getattr(event.payload.get("card"), "display_name", "?")))


# ==================================================
# 马谡 · 心战 / 挥泪
# ==================================================


def _can_xinzhan(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("xinzhan", "used", 0):
        return False, "本回合已经发动过"
    if len(player.hand) <= int(player.max_hp):
        return False, "手牌数需要大于体力上限"
    if len(game.deck.draw_pile) < 1:
        return False, "牌堆没有牌"
    return True, ""


def _activate_xinzhan(game, player, target=None, cards=None):
    """心战：观看牌堆顶三张，获得其中任意数量的红桃牌，其余按任意顺序放回。"""

    count = min(3, len(game.deck.draw_pile))
    viewed = list(game.deck.draw_pile[-count:])
    player.skill_state.set("xinzhan", "used", 1, ResetScope.TURN)
    names = "、".join((getattr(card, "identity_label", "") or "?") for card in viewed)
    game.add_log("%s 发动【心战】，观看牌堆顶 %d 张：%s" % (player.name, count, names))
    hearts = [card for card in viewed if getattr(card, "suit", None) == "heart"]
    others = [card for card in viewed if card not in hearts]
    game.start_card_selection(
        zone="public_pool",
        candidates=[(card, None) for card in viewed],
        number=len(hearts),
        prompt="【心战】：请选择要获得的红桃牌（其余以原顺序放回牌堆顶）",
        on_complete=lambda ordered: _apply_xinzhan(
            game, player, viewed, hearts, ordered),
        owner=player,
        cancellable=True,
    )
    return True


def _apply_xinzhan(game, owner, viewed, hearts, ordered):
    """把选中的红桃牌收进手牌，其余按玩家给出的顺序（或原顺序）放回牌堆顶。

    这三张牌**从未离开牌堆**——洗牌、抽牌、判定的语义都不受影响，
    这里只做"从顶部取走几张、再把剩下的按顺序放回去"。
    """

    ordered_cards = [card for card, _rect, _key in (ordered or ())]
    if ordered_cards and set(id(card) for card in ordered_cards) == set(id(card) for card in viewed):
        chosen = [card for card in ordered_cards
                  if any(card is heart for heart in hearts)]
        rest = [card for card in ordered_cards if card not in chosen]
    else:
        chosen = list(hearts)
        rest = [card for card in viewed if card not in chosen]

    pile = game.deck.draw_pile
    for card in viewed:
        for index, item in enumerate(pile):
            if item is card:
                pile.pop(index)
                break
    # 放回：``pile[-1]`` 是下一个被摸到的牌，因此按顺序 append 就能保持顺序。
    for card in rest:
        pile.append(card)
    for card in chosen:
        owner.hand.append(card)

    game.message = "【心战】：获得了 %d 张红桃牌。" % len(chosen)
    game.add_log("%s 的【心战】获得了 %d 张红桃牌，%d 张放回牌堆顶"
                 % (owner.name, len(chosen), len(rest)))


class Huilei(Skill):
    """锁定技：杀死你的角色立即弃置所有牌。"""

    id = "huilei"
    name = "挥泪"

    def bindings(self):
        return (SkillBinding(EventType.DEATH, priority=-60),)

    def can_trigger(self, context, event):
        if event.target is not self.owner:
            return False
        killer = event.source
        return killer is not None and killer is not self.owner and killer.alive

    def resolve(self, context, event):
        game = context.state
        killer = event.source
        count = 0
        for card in list(getattr(killer, "hand", ()) or ()):
            context.apply(MoveCardAtom(
                card, source=killer.hand, destination=game.deck.discard_pile))
            count += 1
        for slot in list(killer.equipment):
            if killer.get_equipment(slot) is not None:
                context.apply(UnequipAtom(
                    killer, slot, game.deck.discard_pile))
                count += 1
        for card in list(getattr(killer, "judgement_zone", ()) or ()):
            context.apply(MoveCardAtom(
                card, source=killer.judgement_zone,
                destination=game.deck.discard_pile))
            count += 1
        game.add_log("%s 被【挥泪】，弃置了全部 %d 张牌" % (killer.name, count))


# ==================================================
# 高顺 · 陷阵 / 禁酒
# ==================================================


def _xianzhen_targets(game, player):
    return other_alive_players(game, player)


def _can_xianzhen(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("xianzhen", "used", 0):
        return False, "本回合已经发动过"
    if not player.hand:
        return False, "需要一张手牌拼点"
    if not _xianzhen_targets(game, player):
        return False, "没有其他角色"
    return True, ""


def _activate_xianzhen(game, player, target=None, cards=None):
    # 拼点真的能成立（双方都有手牌）才扣技能次数：只要目标空手，
    # 拼点会当场取消，次数却已经用掉——白付一次机会。
    if not pindian_possible(player, target):
        return False
    player.skill_state.set("xianzhen", "used", 1, ResetScope.TURN)
    player.skill_state.set("xianzhen", "target_id", id(target), ResetScope.TURN)
    start_pindian(
        game.engine, player, target, reason="xianzhen",
        on_complete=lambda result: _finish_xianzhen(game, player, target, result))
    game.add_log(player.name + " 发动【陷阵】，与 " + target.name + " 拼点")
    return True


def _finish_xianzhen(game, player, target, result):
    if result is None or result.cancelled:
        return
    if result.initiator_wins:
        player.skill_state.set("xianzhen", "won", 1, ResetScope.TURN)
        game.add_log("%s 的【陷阵】获胜：无视与 %s 的距离与防具，"
                     "并可对其使用任意数量的【杀】" % (player.name, target.name))
        game.message = player.name + " 的【陷阵】获胜。"
    else:
        player.skill_state.set("xianzhen", "lost", 1, ResetScope.TURN)
        game.add_log("%s 的【陷阵】没赢：本回合不能使用【杀】" % player.name)


def _xianzhen_won(game, query):
    player = query.get("player")
    return bool(player is not None and player.skill_state.get("xianzhen", "won", 0))


def _xianzhen_lost(game, query):
    player = query.get("player")
    return bool(player is not None and player.skill_state.get("xianzhen", "lost", 0))


def _xianzhen_ignore_distance(game, query):
    """陷阵赢了之后，计算与拼点对手的距离视为 0。"""

    source = query.get("source")
    target = query.get("target")
    if source is None or target is None:
        return 0
    if not source.skill_state.get("xianzhen", "won", 0):
        return 0
    if source.skill_state.get("xianzhen", "target_id", 0) != id(target):
        return 0
    from src.game.rules import DistanceRule

    # 用**基础距离**当基数：这里正在算的就是"距离修正"，再调 distance()
    # 会把本修正重新算一遍（无限递归）。减去基础距离后，最终距离被
    # distance() 夹到最小值 1 = "视为相邻，永远够得着"。
    return -DistanceRule.base_distance(game, source, target)


class XianzhenArmorIgnore(Skill):
    """陷阵赢了：对拼点对手无视防具。"""

    id = "xianzhen_armor"
    name = "陷阵（无视防具）"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_MODIFY, priority=60),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.source is not self.owner:
            return False
        if not self.owner.skill_state.get("xianzhen", "won", 0):
            return False
        if self.owner.skill_state.get("xianzhen", "target_id", 0) != id(damage.target):
            return False
        return context.state.armor_card(damage.target) is not None

    def resolve(self, context, event):
        damage = event.payload["damage"]
        armor = context.state.armor_card(damage.target)
        if armor is not None and getattr(armor, "name", None) in ("BAIYIN",):
            return
        damage.effects.append("【陷阵】无视防具")
        # 防具的减免会在 DAMAGE_MODIFY 里按装备重新算；这里把结论记在伤害上，
        # 由装备控制器在后面统一跳过 —— 具体实现见 EquipmentEventSkill 的
        # 「无视防具」分支（青釭剑用的是同一条路径）。
        damage.ignore_armor = True


# ==================================================
# 技能表
# ==================================================

YIJIANG_SKILLS = (
    triggered(
        "yizhong",
        "毅重",
        "锁定技，当你没有装备防具时，黑色的【杀】对你无效。",
        factory=Yizhong,
        kind=SkillKind.LOCKED,
    ),
    triggered(
        "xuanfeng",
        "旋风",
        "每当你失去一次装备区里的牌时，你可以执行下列两项中的一项："
        "1. 视为对任意一名其他角色使用一张【杀】（此【杀】不计入每回合的使用限制）；"
        "2. 对与你距离 1 以内的一名其他角色造成 1 点伤害。",
        factory=Xuanfeng,
    ),
    active(
        "ganlu",
        "甘露",
        "出牌阶段限一次，你可以选择两名角色，交换他们装备区里的所有牌。"
        "以此法交换的装备牌数差不能超过你已损失的体力值。",
        can_activate=_can_ganlu,
        activate=_activate_ganlu,
        spec=ActiveSkillSpec(),
        tags=("active",),
    ),
    triggered(
        "buyi",
        "补益",
        "当有角色进入濒死状态时，你可以展示该角色的一张手牌："
        "若此牌不为基本牌，则该角色弃置此牌并回复 1 点体力。",
        factory=Buyi,
    ),
    triggered(
        "jueqing",
        "绝情",
        "锁定技，你造成的伤害均视为体力流失。",
        factory=Jueqing,
        kind=SkillKind.LOCKED,
    ),
    triggered(
        "shangshi",
        "伤逝",
        "除弃牌阶段外，每当你的手牌数小于你已损失的体力值时，"
        "你可以立即将手牌补至等同于你已损失的体力值。",
        factory=Shangshi,
    ),
    triggered(
        "wuyan",
        "无言",
        "锁定技，你使用的非延时类锦囊对其他角色无效；"
        "其他角色使用的非延时类锦囊对你无效。",
        factory=Wuyan,
        kind=SkillKind.LOCKED,
    ),
    active(
        "jujian",
        "举荐",
        "出牌阶段限一次，你可以弃置至多三张牌，然后令一名其他角色摸等量的牌。"
        "若你以此法弃置不少于三张且均为同一类别，你回复 1 点体力。",
        can_activate=_can_jujian,
        activate=_activate_jujian,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_jujian_targets,
            target_prompt="【举荐】：请选择摸牌的角色",
            # 举荐的牌是**真的要弃置**（不是素材），所以走费用语义；
            # 上限来自规则本身："至多三张"。
            variable_cost=True,
            max_cost_cards=3,
            cost_prompt="【举荐】：请选择至多三张牌弃置",
        ),
        tags=("active", "card_transfer"),
    ),
    triggered(
        "pojun",
        "破军",
        "每当你使用【杀】造成一次伤害后，你可以令受到该伤害的角色摸 X 张牌"
        "（X 为该角色当前的体力值，至多 5 张），然后该角色将其武将牌翻面。",
        factory=Pojun,
    ),
    triggered(
        "luoying",
        "落英",
        "当其他角色的梅花牌因弃置或判定而进入弃牌堆时，你可以获得之。",
        factory=Luoying,
    ),
    SkillDef(
        id="jiushi",
        name="酒诗",
        description="若你的武将牌正面朝上，你可以将武将牌翻面来视为使用一张【酒】；"
        "当你的武将牌背面朝上时你受到伤害，你可以在伤害结算后将之翻回正面。",
        kind=SkillKind.ACTIVE,
        factory=JiushiBack,
        can_activate=_can_jiushi,
        activate=_activate_jiushi,
        active_spec=ActiveSkillSpec(),
        tags=("active",),
    ),
    triggered(
        "enyuan",
        "恩怨",
        "锁定技，其他角色每令你回复 1 点体力，该角色摸一张牌；"
        "其他角色每对你造成一次伤害，须给你一张红桃手牌，否则该角色失去 1 点体力。",
        factory=Enyuan,
        kind=SkillKind.LOCKED,
    ),
    active(
        "xuanhuo",
        "眩惑",
        "出牌阶段限一次，你可以将一张红桃手牌交给一名其他角色，"
        "然后你获得该角色的一张牌并立即交给除该角色外的其他角色。",
        can_activate=_can_xuanhuo,
        activate=_activate_xuanhuo,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_ganlu_targets,
            target_prompt="【眩惑】：请选择获得其一张牌的角色",
            # 素材牌不是费用：它要交到目标手上，去向由技能自己的结算决定。
            cost_cards=1,
            keep_cards=True,
            cost_prompt="【眩惑】：请选择一张红桃手牌交给目标",
            cost_candidates=_is_xuanhuo_source,
        ),
        tags=("active", "card_transfer"),
    ),
    active(
        "mingce",
        "明策",
        "出牌阶段限一次，你可以交给其他任一角色一张装备牌或【杀】，"
        "该角色进行二选一：1. 视为对其攻击范围内、由你指定的一名角色使用一张【杀】；"
        "2. 摸一张牌。",
        can_activate=_can_mingce,
        activate=_activate_mingce,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_mingce_targets,
            target_prompt="【明策】：请选择接受装备牌或【杀】的角色",
            cost_cards=1,
            keep_cards=True,
            cost_prompt="【明策】：请选择一张装备牌或【杀】交给目标",
            cost_candidates=_is_mingce_source,
        ),
        tags=("active", "card_transfer"),
    ),
    triggered(
        "zhichi",
        "智迟",
        "锁定技，你的回合外，你每受到一次伤害，直到该回合结束，"
        "任何【杀】或非延时类锦囊均对你无效。",
        factory=Zhichi,
        kind=SkillKind.LOCKED,
    ),
    active(
        "xinzhan",
        "心战",
        "出牌阶段限一次，若你的手牌数大于你的体力上限，"
        "你可以观看牌堆顶的三张牌，然后展示其中任意数量的红桃牌并获得之，"
        "其余以任意顺序置于牌堆顶。",
        can_activate=_can_xinzhan,
        activate=_activate_xinzhan,
        spec=ActiveSkillSpec(),
        tags=("active",),
        needs_local_ui=True,
    ),
    triggered(
        "huilei",
        "挥泪",
        "锁定技，杀死你的角色立即弃置所有牌。",
        factory=Huilei,
        kind=SkillKind.LOCKED,
    ),
    active(
        "xianzhen",
        "陷阵",
        "出牌阶段限一次，你可以与一名角色拼点。若你赢，你获得以下技能直到回合结束："
        "无视与该角色的距离及其防具；可对该角色使用任意数量的【杀】。"
        "若你没赢，你不能使用【杀】直到回合结束。",
        can_activate=_can_xianzhen,
        activate=_activate_xianzhen,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_xianzhen_targets,
            target_prompt="【陷阵】：请选择拼点的角色",
            # 拼点牌**不能**走 cost 通道：那条路是"先支付再结算"，牌先被弃掉，
            # 拼点流程还要再收一张 → 一次拼点掉两张牌；只剩一张时费用先被扣光、
            # 拼点成立不了，技能没发动而牌已经没了。拼点牌由拼点流程自己收集。
        ),
        modifiers=(
            ModifierSpec(kind=ModifierKind.DISTANCE_OUTGOING,
                         value=_xianzhen_ignore_distance, roles=("source",)),
            ModifierSpec(kind=ModifierKind.SLASH_QUOTA, value=99, roles=("player",),
                         condition=lambda game, query: _xianzhen_won(game, query)),
            ModifierSpec(kind=ModifierKind.SLASH_FORBIDDEN, value=True, roles=("player",),
                         condition=lambda game, query: _xianzhen_lost(game, query)),
        ),
        tags=("active", "pindian"),
    ),
    SkillDef(
        id="jinjiu",
        name="禁酒",
        description="锁定技，你的【酒】均视为【杀】。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(
                skill_id="jinjiu",
                matches=lambda card: (not getattr(card, "is_virtual", False)
                                      and getattr(card, "name", None) == "JIU"),
                name="SHA", contexts=(PLAY_CONTEXT,)),
        ),
        tags=("conversion",),
    ),
)
