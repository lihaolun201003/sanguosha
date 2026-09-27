"""一将成名武将技能：于禁 / 凌统 / 吴国太 / 张春华 / 徐庶 / 徐盛 / 曹植 /
法正 / 陈宫 / 马谡 / 高顺。

钟会的卡面只印了技能名、正文是空白，规则来源无法确认，因此**没有实现**
（``generals/expansions.py`` 里 ``implemented=False``），这里也不给它写
任何猜测性的技能。
"""

from src.game.atoms_v2 import (
    DISCARD_REASON,
    TAKE_REASON,
    DrawCardsAtom,
    MoveCardAtom,
    RecoverHpAtom,
    UnequipAtom,
)
from src.game.conversion import (
    CardConversion,
    PLAY_CONTEXT,
    RESPONSE_CONTEXT,
)
from src.game.engine import EventType, Flow
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..definitions import (
    ActiveSkillSpec,
    CostZone,
    ModifierSpec,
    SkillDef,
    SkillKind,
    active,
    triggered,
)
from ..mechanics import (
    optional_trigger,
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


def _move_anywhere(game, context, owner, card, destination, *, reason=""):
    """把 owner 的任意区域里的一张牌移到 destination（装备区按槽位处理）。

    ``reason`` 是这次移动的规则原因（见 ``atoms_v2`` 的原因词汇表）：装备区
    的牌不会因为"最终到了别处"就自动带上语义，调用方要说明白这是弃置、被拿走
    还是别的什么。留空的路径保持原状（不发牌移动通知）。
    """

    if any(item is card for item in owner.hand):
        context.apply(MoveCardAtom(
            card, source=owner.hand, destination=destination,
            reason=reason or None))
        return True
    if any(item is card for item in owner.judgement_zone):
        context.apply(MoveCardAtom(
            card, source=owner.judgement_zone, destination=destination,
            reason=reason or None))
        return True
    for slot, equipped in (owner.equipment or {}).items():
        if equipped is card:
            context.apply(UnequipAtom(owner, slot, destination, reason=reason))
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


def _xuanhuo_targets(game, player):
    """【眩惑】的目标候选：必须是**其他**角色（牌要交到别人手上）。"""

    return other_alive_players(game, player)


def _equipment_count(player):
    return len([card for card in (getattr(player, "equipment", None) or {}).values()
                if card is not None])


def _ganlu_max_diff(player):
    """【甘露】允许的装备牌数差上限 X = 你已损失的体力值。"""

    return max(0, lost_hp(player))


def _ganlu_candidates(game, player):
    """【甘露】的两名角色候选：**所有**存活角色，包含吴国太本人。

    官方文本是"你可以选择两名角色"，并没有限定"其他角色"——她既可以与
    队友互换装备，也可以把对手的装备换到自己身上，两人局里同样能选自己
    与对方。旧实现只列"其他角色"，于是两人局永远报"需要两名角色"。
    """

    return list(game.get_alive_players())


def _ganlu_legal(first, second, owner):
    """这两个角色现在能不能按【甘露】交换装备（官方：数差不能超过 X）。"""

    if first is None or second is None or first is second:
        return False
    if not getattr(first, "alive", True) or not getattr(second, "alive", True):
        return False
    return (abs(_equipment_count(first) - _equipment_count(second))
            <= _ganlu_max_diff(owner))


def _ganlu_second_candidates(game, owner, first):
    """选定第一名之后的第二名候选：数差超上限的组合**在这里就被排除**。

    过滤放在候选层（规则层），玩家不会先选中一个必然失败的组合、提交之后
    才被告知"数差超上限"——那种"先让你点、再告诉你不行"的路径既浪费一次
    选择，也让技能看起来像是发动失败。
    """

    return [other for other in _ganlu_candidates(game, owner)
            if other is not first and _ganlu_legal(first, other, owner)]


def _ganlu_pairs(game, owner):
    """现在**真的能交换**的无序角色对（含吴国太本人）。"""

    candidates = _ganlu_candidates(game, owner)
    return [(first, second)
            for index, first in enumerate(candidates)
            for second in candidates[index + 1:]
            if _ganlu_legal(first, second, owner)]


def _ganlu_first_candidates(game, owner):
    """第一名候选：至少要有一个合法搭档才列出来（按座次）。

    没有搭档的角色（例如装备数差对**所有**人都超上限的那位）不出现在候选里
    ——他的存在只会让玩家选中一个注定失败的第一步。注意合法组合是无序对，
    搭档可能在组合的任意一侧，所以这里取的是"出现在任一合法组合里的人"。
    """

    paired = set()
    for first, second in _ganlu_pairs(game, owner):
        paired.add(id(first))
        paired.add(id(second))
    return [item for item in _ganlu_candidates(game, owner)
            if id(item) in paired]


def _can_ganlu(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("ganlu", "used", 0):
        return False, "本回合已经发动过"
    if not _ganlu_pairs(game, player):
        return False, "没有装备牌数差在你已损失的体力值以内的两名角色"
    return True, ""


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
                    candidates=_ganlu_first_candidates(self.game, self.owner),
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
        seconds = _ganlu_second_candidates(self.game, self.owner, self.first)
        # 服务端复核：引擎已经按候选校验过一次（不在候选里的提交会被拒），
        # 这里挡的是"候选在两次询问之间变了 / 绕过界面的调用方"。
        if not seconds:
            self.game.message = (
                "【甘露】：没有可以与 %s 交换装备的角色（装备牌数差"
                "不能超过你已损失的体力值）。" % self.first.name)
            return self.complete({"applied": False})
        self.stage = "second"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【甘露】：请选择第二名角色（与 %s 交换装备）" % self.first.name,
                    reason="ganlu", candidates=seconds,
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_second(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        self.second = targets[0]
        if not _ganlu_legal(self.first, self.second, self.owner):
            self.game.message = ("【甘露】：这两名角色的装备牌数差超过你已损失的"
                                 "体力值，无法交换。")
            return self.complete({"applied": False})
        self.owner.skill_state.set("ganlu", "used", 1, ResetScope.TURN)
        first_cards = [card for card in (self.first.equipment or {}).values() if card]
        second_cards = [card for card in (self.second.equipment or {}).values() if card]
        for slot in list(self.first.equipment):
            if self.first.get_equipment(slot) is not None:
                # 交换装备：对本人来说装备是**被换走**（失去牌），不是弃置。
                self.context.apply(UnequipAtom(
                    self.first, slot, reason=TAKE_REASON))
        for slot in list(self.second.equipment):
            if self.second.get_equipment(slot) is not None:
                self.context.apply(UnequipAtom(
                    self.second, slot, reason=TAKE_REASON))
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


#: 【举荐】的输入契约：弃置**至多三张牌**，然后令一名其他角色摸等量的牌。
#: 项目选用版本的文本是"弃置至多三张**牌**"（不是"手牌"），所以费用区域与
#: 【制衡】一致——手牌与装备区的牌都能支付。区域只有这一处声明，候选、界面
#: 高亮、引擎校验、远程下发全部由 ``allowed_zones`` 派生，没有按技能名的分支。
JUJIAN_SPEC = ActiveSkillSpec(
    needs_target=True,
    target_candidates=_jujian_targets,
    target_prompt="【举荐】：请选择摸牌的角色",
    # 举荐的牌是**真的要弃置**（不是素材），所以走费用语义；
    # 上限来自规则本身："至多三张"。
    variable_cost=True,
    max_cost_cards=3,
    cost_prompt="【举荐】：请选择至多三张牌弃置",
    allowed_zones=(CostZone.HAND, CostZone.EQUIPMENT),
)


def _jujian_cost_candidates(game, player):
    """这次发动可以支付的牌（与引擎校验、界面高亮同一份判断）。"""

    from src.game.skills.activation import cost_candidates

    return cost_candidates(game, player, JUJIAN_SPEC)


def _can_jujian(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("jujian", "used", 0):
        return False, "本回合已经发动过"
    # 手牌 0、装备区有牌时照样能发动：费用是"牌"，不是"手牌"。
    if not _jujian_cost_candidates(game, player):
        return False, "没有可以弃置的牌"
    if not _jujian_targets(game, player):
        return False, "没有其他角色"
    return True, ""


def _activate_jujian(game, player, target=None, cards=None):
    """举荐：弃至多三张牌令一名其他角色摸等量的牌；三张同类则回复 1 点。

    费用牌**已经由引擎按 ``JUJIAN_SPEC`` 弃置**（手牌走 MoveCardAtom，装备区
    走 UnequipAtom），所以这里不再自己移动任何牌——技能自己动一次就等于
    引擎动的那次白动，两边谁都说不清"到底弃了几张"。
    """

    if target is None:
        return False
    # 弃哪几张由玩家自己挑（张数上限写在 spec 的 max_cost_cards 里）。
    # 这里不再有任何"没传就替他挑一张"的兜底：那种兜底会让真人点了技能
    # 却看到程序自己丢了牌。
    chosen = list(cards or ())
    if not chosen:
        game.message = "【举荐】：请先选择要弃置的牌。"
        return False
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
        damage = event.payload["damage"]
        optional_trigger(
            context, self.owner,
            prompt="【破军】：是否令 %s 摸牌并翻面？" % damage.target.name,
            reason="pojun", label="破军",
            effect=lambda flow: self._break(flow, damage.target)).start()

    def _break(self, flow, target):
        game = flow.game
        if target is None or not target.alive:
            return False
        count = max(0, min(5, int(target.hp)))
        if count:
            flow.context.apply(DrawCardsAtom(target, count))
        flip_player(game, target, reason="破军")
        game.add_log("%s 的【破军】令 %s 摸 %d 张牌并翻面"
                     % (self.owner.name, target.name, count))
        return True


# ==================================================
# 曹植 · 落英 / 酒诗
# ==================================================


class Luoying(Skill):
    """其他角色的梅花牌因弃置或判定进入弃牌堆时，你可以获得之。

    "因弃置或判定"是硬约束（官方 FAQ）：**使用 / 打出 / 重铸 / 拼点后置入
    弃牌堆都不算**，无主的牌（五谷没人要的、不屈牌）也不算。所以这里读规则层
    给的 ``reason`` 与 ``owner``，不自己猜牌是怎么进弃牌堆的。

    以前只看"牌在弃牌堆里、不是我的"——而使用后的牌经过处理区（
    ``game.processing_zone``，不属于任何角色）进入弃牌堆时归属查不出来，
    于是曹植会把自己刚用掉的梅花牌**收回来**：【铁索连环】因此能无限次使用，
    整局永远打不完（Phase 18.5 批量试玩实测到了这个死循环）。
    """

    id = "luoying"
    name = "落英"

    #: 只有这两种原因算"因弃置或判定进入弃牌堆"（见 ``MoveCardAtom.reason``）。
    REASONS = ("discard", "judge")

    def bindings(self):
        return (SkillBinding(EventType.CARD_DISCARDED, priority=15),)

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        card = event.payload.get("card")
        owner = event.payload.get("owner")
        if card is None or owner is None or owner is self.owner:
            return False
        if str(event.payload.get("reason") or "discard") not in self.REASONS:
            return False
        if getattr(card, "suit", None) != "club":
            return False
        return any(item is card for item in context.state.deck.discard_pile)

    def resolve(self, context, event):
        card = event.payload["card"]
        optional_trigger(
            context, self.owner,
            prompt="【落英】：是否获得【%s】？" % getattr(card, "display_name", "梅花牌"),
            reason="luoying", label="落英",
            effect=lambda flow: self._gain(flow, card)).start()

    def _gain(self, flow, card):
        pile = flow.game.deck.discard_pile
        if not any(item is card for item in pile):
            return False
        flow.context.apply(MoveCardAtom(
            card, source=pile, destination=self.owner.hand))
        flow.game.add_log("%s 的【落英】获得了【%s】"
                          % (self.owner.name, getattr(card, "display_name", "?")))
        return True


def _jiushi_card(player):
    """【酒诗】"视为使用"的那张虚拟【酒】。"""

    from ..mechanics import virtual_card

    return virtual_card("JIU", player, (), category="basic")


def _jiushi_usable(game, player):
    """这张虚拟【酒】此刻真的能用吗；返回 ``(ok, reason)``。

    走的是**规则层唯一那份可用性查询**：提交 ``UseCardAction`` 之后
    ``UseCardFlow`` 第一步调用的就是这个 ``CardEffect.can_use``（【急袭】探测
    【顺手牵羊】的合法目标、【奇袭】探测单目标合法性用的是同一个入口）。
    这里不重写任何一条【酒】的规则——"本回合喝过没有 / 手里有没有【杀】 /
    有没有攻击范围内的目标"全部由它回答。
    """

    from src.game.engine import UseCardAction

    virtual = _jiushi_card(player)
    effect = game.engine.card_effects.get(virtual)
    if effect is None:                                    # pragma: no cover - 防御
        return False, "现在不能使用【酒】"
    valid, reason = effect.can_use(
        game, UseCardAction(player, virtual, [player], ignore_usage_limit=False))
    if not valid:
        return False, str(reason or "现在不能使用【酒】")
    return True, ""


def _can_jiushi(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    # 时机：只在自己的出牌阶段。以前没有这一条——响应窗口 / 别人的回合里
    # 技能也是亮的，点了先把武将牌翻面，那张【酒】却用不出去（"你没有
    # 【杀】，现在不能使用【酒】"），白翻一面。
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if not getattr(player, "face_up", True):
        return False, "你的武将牌已经背面朝上"
    # 出牌阶段限一次：这次发动的产物就是一张【酒】，所以【酒】自己的
    # "每回合限一次"（``jiu_used``）已经把它限死；这里再记一道技能自己的
    # 标记（成功之后才写），挡住"翻了面又被人翻回来、本回合还能再翻一次"
    # 这条侧面。
    if player.skill_state.get("jiushi", "used", 0):
        return False, "本回合已经发动过【酒诗】"
    if player.jiu_used:
        return False, "本回合已经使用过【酒】"
    usable, reason = _jiushi_usable(game, player)
    if not usable:
        return False, reason
    return True, ""


def _activate_jiushi(game, player, target=None, cards=None):
    """酒诗：翻面来视为使用一张【酒】。

    顺序固定为**预验证 → 支付 → 结算**：``flip_player``（翻面）是这次发动的
    费用，只有在"这张虚拟【酒】此刻真的用得出"确认之后才执行。判据不过就
    什么都不做——不翻面、不写标记、不动任何牌，只把原因写进提示。以前是
    先翻面再提交，任何一条使用被拒的路径都会留下"翻了面、什么都没发生"。
    """

    from src.game.engine import UseCardAction

    # 与 ``can_activate`` 同一份判据复核一遍：按钮（或 AI 的候选表）是上一次
    # 刷新的结论，从那一刻到这次提交之间状态可能已经变了。
    allowed, reason = _can_jiushi(game, player)
    if not allowed:
        game.message = "【酒诗】：" + reason
        return False

    flip_player(game, player, reason="酒诗")              # 费用
    virtual = _jiushi_card(player)
    game.engine.submit(UseCardAction(                     # 结算
        player, virtual, [player], ignore_usage_limit=False))
    # 限次标记写在成功之后：上面那条被拒绝的路径一次都不写。
    player.skill_state.set("jiushi", "used", 1, ResetScope.TURN)
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
                              self.owner.hand, reason=TAKE_REASON):
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
    """心战：观看牌堆顶三张，获得其中任意数量的红桃牌，其余按任意顺序放回。

    观看与选择都走**统一请求通道**（不再用只有本地界面才有的选牌通道），
    因此本地真人 / 远程真人 / AI 三条路完全一致：牌堆顶内容只发给技能拥有者，
    公开战报不写出牌面。
    """

    count = min(3, len(game.deck.draw_pile))
    if count <= 0:
        return False
    viewed = list(game.deck.draw_pile[-count:])
    player.skill_state.set("xinzhan", "used", 1, ResetScope.TURN)
    game.add_log("%s 发动【心战】，观看牌堆顶 %d 张牌" % (player.name, count))
    XinzhanFlow(game.engine, player, viewed).start()
    return True


def _xinzhan_hearts(cards):
    return [card for card in cards if getattr(card, "suit", None) == "heart"]


class XinzhanFlow(Flow):
    """心战：先取红桃（0 到全部，只有红桃可作候选），再按任意顺序放回其余。"""

    def __init__(self, engine, owner, viewed):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.viewed = list(viewed)
        self.taken = []
        self.stage = "take"

    def begin(self):
        hearts = _xinzhan_hearts(self.viewed)
        if not hearts:
            return self._ask_order()
        self.stage = "take"
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【心战】：请选择要获得的红桃牌（可一张都不拿）",
                  reason="xinzhan", candidates=hearts,
                  min_cards=0, max_cards=len(hearts), zone="public_pool",
                  context={"cancellable": True})
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "take":
            return self._after_take(response)
        ordered = () if response is None or response.passed else response.cards
        self._apply(ordered)
        return self.complete({"applied": True})

    def _after_take(self, response):
        wanted = list(getattr(response, "cards", ()) or ())
        hearts = _xinzhan_hearts(self.viewed)
        # 只认红桃候选（引擎已校验，这里再兜一次"牌被换走"的极端）。
        self.taken = [card for card in wanted
                      if any(card is heart for heart in hearts)]
        return self._ask_order()

    def _ask_order(self):
        """剩下的牌按玩家给的顺序放回；放弃 = 保持原序。"""

        rest = [card for card in self.viewed
                if not any(card is item for item in self.taken)]
        if len(rest) <= 1:
            return self._finish(())
        self.stage = "order"
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【心战】：请按放回牌堆顶的顺序依次选择（先选的在上）",
                  reason="xinzhan_order", candidates=rest,
                  min_cards=len(rest), max_cards=len(rest), zone="public_pool",
                  context={"cancellable": True})
        return self.current_result()

    def _finish(self, ordered):
        self._apply(ordered)
        return self.complete({"applied": True})

    def _apply(self, ordered):
        """取走的进手牌；其余的按玩家顺序放回牌堆顶。

        这三张牌**从未离开牌堆**——洗牌、抽牌、判定的语义都不受影响，
        这里只做"从顶部取走几张、再把剩下的按顺序放回去"。
        顺序约定与【观星】一致：``draw_pile[-1]`` 是下一张被摸到的牌，
        玩家提交的第一张是"最先摸到"的那张，所以显式顺序要反向 append；
        **放弃排序则保持原来的牌堆顺序**，不做任何重排。
        """

        taken = list(self.taken)
        pile = self.game.deck.draw_pile
        rest = [card for card in self.viewed
                if not any(card is item for item in taken)]
        chosen = [card for card in list(ordered or ())
                  if any(card is item for item in rest)]
        explicit = len(chosen) == len(rest)
        if not explicit:
            # 放弃排序（或顺序不完整）：保持原顺序，不冒险重排。
            chosen = rest
        for card in self.viewed:
            for index, item in enumerate(pile):
                if item is card:
                    pile.pop(index)
                    break
        for card in (reversed(chosen) if explicit else rest):
            pile.append(card)
        for card in taken:
            self.owner.hand.append(card)
        self.game.message = "【心战】：获得了 %d 张红桃牌。" % len(taken)
        self.game.add_log("%s 的【心战】获得了 %d 张红桃牌，%d 张放回牌堆顶"
                          % (self.owner.name, len(taken), len(rest)))


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
                    killer, slot, game.deck.discard_pile,
                    reason=DISCARD_REASON))
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
    """能拼点的其他角色：**双方都有手牌**才列出来。

    空手的目标拼不出点（``PindianFlow.start`` 会当场取消），旧实现把他列成
    可选，玩家选中、技能已经播报之后才失败。过滤走共用的 ``pindian_possible``
    （与【天义】【驱虎】同一判据），技能自己不写第二套判断。
    """

    return [
        other for other in other_alive_players(game, player)
        if pindian_possible(player, other)
    ]


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
        return False, "没有可以拼点的角色"
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
        "以此法交换的装备牌数差不能超过 X（X 为你已损失的体力值）。",
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
        spec=JUJIAN_SPEC,
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
        description="出牌阶段限一次，若你的武将牌正面朝上，你可以将武将牌翻面来"
        "视为使用一张【酒】；"
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
            target_candidates=_xuanhuo_targets,
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
        # 锁定技：这不是"可以点技能选择转化"，而是**替换**——手里的【酒】
        # 就是【杀】，因此 ① 技能栏不该给一个可选的发动入口，② 原牌名那条路
        # （把【酒】当【酒】喝掉、濒死时拿【酒】自救）必须彻底关掉。
        # 写法与【武神】的 ``locks_source`` 完全一致：发现层不再为这些牌生成
        # "普通使用"那一条，引擎侧的用牌 / 响应两个权威入口也一起拒绝按
        # 原牌名提交。使用 / 打出 / 响应三个场合都要覆盖（决斗、南蛮入侵
        # 要的是【杀】，因此上下文必须同时包含响应）。
        kind=SkillKind.LOCKED,
        conversions=(
            CardConversion(
                skill_id="jinjiu",
                matches=lambda card: (not getattr(card, "is_virtual", False)
                                      and getattr(card, "name", None) == "JIU"),
                name="SHA",
                contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT),
                locks_source=True),
        ),
        tags=("conversion", "locked"),
    ),
)
