"""林包武将技能：孙坚 / 孟获 / 徐晃 / 曹丕 / 祝融 / 董卓 / 贾诩 / 鲁肃。"""

from src.game.atoms_v2 import DISCARD_REASON, DrawCardsAtom, MoveCardAtom, UnequipAtom
from src.game.conversion import PLAY_CONTEXT, CardConversion
from src.game.engine import EventType, Flow, FlowStatus
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..activation import emit_skill_triggered
from ..definitions import (
    ActiveSkillSpec,
    CostZone,
    ModifierSpec,
    PhaseReplacement,
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
    consume_limited,
    flip_player,
    hand_cards,
    judge,
    limited_used,
    lose_hp,
    lost_hp,
    other_alive_players,
    sha_use_options,
    start_pindian,
    use_virtual,
)
from ..modifiers import ModifierKind
from ..state import ResetScope


def _is_black(card):
    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "card_color", None) == "black"


def _is_black_basic_or_equipment(card):
    if getattr(card, "is_virtual", False):
        return False
    if getattr(card, "card_color", None) != "black":
        return False
    return getattr(card, "category", None) in ("basic", "equipment")


def _all_cards_of(player):
    cards = list(getattr(player, "hand", ()) or ())
    for card in (getattr(player, "equipment", None) or {}).values():
        if card is not None:
            cards.append(card)
    cards.extend(list(getattr(player, "judgement_zone", ()) or ()))
    return cards


# ==================================================
# 孙坚 · 英魂
# ==================================================


class Yinghun(Skill):
    """英魂：**准备阶段**（回合开始阶段）的可选流程。

    官方时机是"回合开始阶段"，而回合驱动会自动跑完准备、判定、摸牌阶段才
    把操作交回出牌阶段——所以旧的"出牌阶段里点技能名发动"永远不可达：
    受伤的孙坚在自己的真实回合里，技能查询的回答是"只能在你的回合开始阶段
    发动"。改由 PHASE_START(PREPARE) 打开窗口（与【观星】【凿险】同型），
    确认、目标、抉择全部发生在准备阶段之内，结束后回合才继续判定 / 摸牌。
    """

    id = "yinghun"
    name = "英魂"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=45),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if context.state.game_over:
            return False
        if event.payload.get("phase") is not TurnPhase.PREPARE:
            return False
        if event.payload.get("skipped"):
            return False
        if self.owner.skill_state.get(self.id, "used", 0):
            return False
        if lost_hp(self.owner) <= 0:
            return False
        return bool(other_alive_players(context.state, self.owner))

    def resolve(self, context, event):
        YinghunFlow(context.services["engine"], self.owner).start()


class YinghunFlow(Flow):
    """英魂全过程：选目标（取消 = 不发动）→ 两项选一 → 目标自己弃牌。

    官方文本："准备阶段，若你已受伤，你可以令一名其他角色摸X张牌，然后弃置
    一张牌；或令其摸一张牌，然后弃置X张牌（X 为你已损失的体力值）。"
    """

    def __init__(self, engine, owner, target=None):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.x = max(1, lost_hp(owner))
        self.mode = ""
        self.stage = "target" if target is None else "choose"

    def begin(self):
        if self.target is not None:
            self.owner.skill_state.set("yinghun", "used", 1, ResetScope.TURN)
            return self._ask_mode()
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【英魂】：X = %d，是否令一名其他角色摸牌并弃牌？"
                           % self.x,
                    reason="yinghun",
                    candidates=other_alive_players(self.game, self.owner),
                    min_targets=0, max_targets=1)
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "target":
            return self._after_target(response)
        if self.stage == "choose":
            return self._after_choice(response)
        return self._after_discard(response)

    # ---- 1. 选目标（不选 = 不发动，什么都不结算）----

    def _after_target(self, response):
        chosen = [target for target in (getattr(response, "targets", None) or ())
                  if any(target is other for other in
                         other_alive_players(self.game, self.owner))]
        if not chosen:
            # 取消选目标 = 这次不发动：不写 used（本回合仍可再考虑），
            # 也不摸牌、不弃牌。
            self.game.add_log("%s 放弃发动【英魂】" % self.owner.name)
            return self.complete({"applied": False})
        self.target = chosen[0]
        self.owner.skill_state.set("yinghun", "used", 1, ResetScope.TURN)
        return self._ask_mode()

    # ---- 2. 两项选一 ----

    def _ask_mode(self):
        self.stage = "choose"
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【英魂】：X = %d，请选择一项" % self.x,
                   reason="yinghun",
                   options=(("draw_x", "令其摸 %d 张牌，然后弃一张牌" % self.x),
                            ("discard_x", "令其摸一张牌，然后弃 %d 张牌" % self.x)))
        return self.current_result()

    def _after_choice(self, response):
        option = str(getattr(response, "option", "") or "")
        if option == "discard_x":
            self.mode = "discard_x"
            self.context.apply(DrawCardsAtom(self.target, 1))
            return self._ask_discard(self.x)
        self.mode = "draw_x"
        self.context.apply(DrawCardsAtom(self.target, self.x))
        return self._ask_discard(1)

    def _ask_discard(self, count):
        candidates = list(getattr(self.target, "hand", ()) or ())
        if not candidates:
            return self._finish()
        count = min(count, len(candidates))
        self.stage = "discard"
        ask_cards(self.engine, self, source=self.owner, target=self.target,
                  prompt="【英魂】：请选择弃置 %d 张手牌" % count,
                  reason="yinghun", candidates=candidates,
                  min_cards=count, max_cards=count)
        return self.current_result()

    def _after_discard(self, response):
        for card in list(getattr(response, "cards", ()) or ()):
            if any(item is card for item in self.target.hand):
                self.context.apply(MoveCardAtom(
                    card, source=self.target.hand,
                    destination=self.game.deck.discard_pile))
        return self._finish()

    def _finish(self):
        self.game.add_log("%s 发动【英魂】→ %s（X = %d，%s）"
                          % (self.owner.name, self.target.name, self.x, self.mode))
        return self.complete({"applied": True})


# ==================================================
# 孟获 · 祸首 / 再起
# ==================================================


def _nanman_ignore(game, query):
    """祸首 / 巨象的【南蛮入侵】免疫（TARGET_FORBIDDEN 的判定体）。"""

    card = query.get("card")
    if card is None or getattr(card, "name", None) != "NANMAN":
        return 0
    return 1


def _nanman_source(game, query):
    """祸首：这名角色是任何【南蛮入侵】造成伤害的来源。"""

    card = query.get("card")
    return bool(card is not None and getattr(card, "name", None) == "NANMAN")


def _can_zaiqi(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if lost_hp(player) <= 0:
        return False, "你未受伤，不能发动"
    if not game.deck.draw_pile:
        return False, "牌堆没有牌"
    return True, ""


def _apply_zaiqi(game, player):
    """再起：放弃摸牌，亮出牌堆顶 X 张，红桃回复体力后弃置，其余收进手牌。"""

    x = min(lost_hp(player), len(game.deck.draw_pile))
    if x <= 0:
        return False
    revealed = []
    for _ in range(x):
        card = game.deck.draw()
        if card is None:
            break
        revealed.append(card)
    hearts = [card for card in revealed if getattr(card, "suit", None) == "heart"]
    others = [card for card in revealed if card not in hearts]
    names = "、".join((getattr(card, "identity_label", "") or "?") for card in revealed)
    game.add_log("%s 发动【再起】，亮出 %s" % (player.name, names))
    if hearts:
        from src.game.atoms_v2 import RecoverHpAtom

        before = player.hp
        game.engine.context.apply(RecoverHpAtom(player, len(hearts)))
        game.add_log("%s 因【再起】回复 %d 点体力" % (player.name, player.hp - before))
    for card in hearts:
        game.deck.discard(card)
    for card in others:
        player.hand.append(card)
    if others:
        game.message = player.name + " 将其余 %d 张牌收入手牌。" % len(others)
    return True


# ==================================================
# 徐晃 · 断粮
# ==================================================


def _duanliang_distance(game, query):
    card = query.get("card")
    if card is None or getattr(card, "name", None) != "BINGLIANG":
        return None
    return 2


# ==================================================
# 曹丕 · 行殇 / 放逐 / 颂威
# ==================================================


class Xingshang(Skill):
    """你可以立即获得死亡角色的所有牌。"""

    id = "xingshang"
    name = "行殇"

    def bindings(self):
        return (SkillBinding(EventType.DEATH, priority=20),)

    def can_trigger(self, context, event):
        dead = event.target
        if dead is None or dead is self.owner or not self.owner.alive:
            return False
        return bool(_all_cards_of(dead))

    def resolve(self, context, event):
        XingshangFlow(context.services["engine"], self.owner, event.target).start()


class XingshangFlow(Flow):
    def __init__(self, engine, owner, dead):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.dead = dead
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【行殇】：是否获得 %s 的所有牌？" % self.dead.name,
                    reason="xingshang")
        return self.current_result()

    def advance(self, response=None):
        if response is None or not response.confirmed:
            return self.complete({"applied": False})
        count = 0
        for card in list(getattr(self.dead, "hand", ()) or ()):
            self.context.apply(MoveCardAtom(
                card, source=self.dead.hand, destination=self.owner.hand))
            count += 1
        for slot in list(self.dead.equipment):
            if self.dead.get_equipment(slot) is not None:
                self.context.apply(UnequipAtom(
                    self.dead, slot, self.owner.hand))
                count += 1
        for card in list(getattr(self.dead, "judgement_zone", ()) or ()):
            self.context.apply(MoveCardAtom(
                card, source=self.dead.judgement_zone, destination=self.owner.hand))
            count += 1
        self.game.add_log("%s 的【行殇】获得 %s 的 %d 张牌"
                          % (self.owner.name, self.dead.name, count))
        return self.complete({"applied": True, "count": count})


class Fangzhu(Skill):
    """你每受到一次伤害，可令一名其他角色摸 X 张牌（X = 已损失体力）然后翻面。"""

    id = "fangzhu"
    name = "放逐"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=20),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        if not self.owner.alive:
            return False
        return bool(other_alive_players(context.state, self.owner))

    def resolve(self, context, event):
        FangzhuFlow(context.services["engine"], self.owner).start()


class FangzhuFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "target"

    def begin(self):
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【放逐】：请选择摸牌并翻面的角色",
                    reason="fangzhu",
                    candidates=other_alive_players(self.game, self.owner),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def advance(self, response=None):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        target = targets[0]
        x = max(1, lost_hp(self.owner))
        self.context.apply(DrawCardsAtom(target, x))
        flip_player(self.game, target, reason="放逐")
        self.game.add_log("%s 的【放逐】令 %s 摸 %d 张牌并翻面"
                          % (self.owner.name, target.name, x))
        return self.complete({"applied": True})


class Songwei(Skill):
    """主公技：其他魏势力角色的判定牌生效后，**其**可以令你摸一张牌（黑色）。"""

    id = "songwei"
    name = "颂威"

    def bindings(self):
        return (SkillBinding(EventType.JUDGE_FINISHED, priority=10),)

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        result = event.payload.get("result")
        judged = getattr(result, "source", None)
        if result is None or judged is None or judged is self.owner:
            return False
        if getattr(judged, "kingdom", None) != "wei":
            return False
        if not getattr(judged, "alive", True):
            # 判定者已经阵亡：没人能做这个决定（不替他自动发动）。
            return False
        return getattr(result, "color", None) == "black"

    def resolve(self, context, event):
        result = event.payload.get("result")
        judged = getattr(result, "source", None)
        SongweiFlow(context.services["engine"], self.owner, judged,
                    getattr(result, "identity_label", "") or "?").start()


class SongweiFlow(Flow):
    """颂威：是否发动由**进行判定的那名魏势力角色**决定，不是曹丕。

    官方："主公技，其他魏势力角色的判定牌生效后，若判定牌为黑色，**其**可以
    令你摸一张牌。" 决定权在那名魏势力角色手里——他可以选择不发动。以前这里
    问的是技能拥有者自己（曹丕自问自答），等于把别人的选择权拿走了。
    """

    def __init__(self, engine, owner, judged, label):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.judged = judged
        self.label = label
        self.stage = "confirm"

    def begin(self):
        if self.judged is None or not getattr(self.judged, "alive", True):
            return self.complete({"applied": False})
        ask_confirm(
            self.engine, self, source=self.owner, target=self.judged,
            prompt="【颂威】：你的判定牌为黑色（%s），是否令 %s 摸一张牌？"
                   % (self.label, self.owner.name),
            reason="songwei")
        return self.current_result()

    def advance(self, response=None):
        if response is None or not response.confirmed:
            self.game.add_log("%s 放弃发动【颂威】" % self.judged.name)
            return self.complete({"applied": False})
        self.context.apply(DrawCardsAtom(self.owner, 1))
        self.game.add_log("%s 的【颂威】令 %s 摸一张牌"
                          % (self.judged.name, self.owner.name))
        return self.complete({"applied": True})


# ==================================================
# 祝融 · 巨象 / 烈刃
# ==================================================


class Juxiang(Skill):
    """锁定技：【南蛮入侵】对你无效；**其他角色使用**的【南蛮入侵】结算完毕
    进入弃牌堆时你立即获得它。"""

    id = "juxiang"
    name = "巨象"

    def bindings(self):
        # 时机必须是"这张牌作为锦囊结算完毕"，不能用通用的「进弃牌堆」：
        # 那条通知对**一切**弃牌途径都发（自己用的南蛮结算、别人弃牌阶段
        # 把南蛮弃掉、被拆顺拆掉……），挂上去就等于南蛮全归祝融，白送一大截强度。
        return (SkillBinding(EventType.CARD_USE_FINISHED, priority=10),)

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        card = event.payload.get("card")
        if card is None or getattr(card, "name", None) != "NANMAN":
            return False
        if event.source is self.owner:
            return False
        # 牌可能已经不在弃牌堆（被【奸雄】一类技能取走），那就不再拿。
        return any(item is card for item in context.state.deck.discard_pile)

    def resolve(self, context, event):
        card = event.payload["card"]
        game = context.state
        if not any(item is card for item in game.deck.discard_pile):
            return
        game.deck.discard_pile.remove(card)
        self.owner.hand.append(card)
        game.add_log("%s 的【巨象】获得了【南蛮入侵】" % self.owner.name)


class Lieren(Skill):
    """你使用【杀】造成一次伤害后，可与受伤角色拼点，赢了获得其一张牌。"""

    id = "lieren"
    name = "烈刃"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED, priority=-20),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        damage = event.payload.get("damage")
        if damage is None or int(event.payload.get("amount", 0) or 0) <= 0:
            return False
        card = getattr(damage, "card", None)
        if card is None or getattr(card, "name", None) != "SHA":
            return False
        target = getattr(damage, "target", None)
        if target is None or target is self.owner or not target.alive:
            return False
        return bool(getattr(self.owner, "hand", ())) and bool(getattr(target, "hand", ()))

    def resolve(self, context, event):
        damage = event.payload["damage"]
        start_pindian(
            context.services["engine"], self.owner, damage.target, reason="lieren",
            on_complete=lambda result: self._after(context, damage.target, result))

    def _after(self, context, target, result):
        if result is None or result.cancelled or not result.initiator_wins:
            return
        game = context.state
        LierenFlow(context.services["engine"], self.owner, target).start()


class LierenFlow(Flow):
    """烈刃赢了：获得受伤角色的一张牌。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.stage = "select"

    def begin(self):
        candidates = _all_cards_of(self.target)
        if not candidates:
            return self.complete({"applied": False})
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【烈刃】：请选择获得 %s 的一张牌" % self.target.name,
                  reason="lieren", candidates=candidates,
                  min_cards=1, max_cards=1, zone="public_pool",
                  context={"zone_owner": self.target})
        return self.current_result()

    def advance(self, response=None):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self.complete({"applied": False})
        card = cards[0]
        if any(item is card for item in self.target.hand):
            self.context.apply(MoveCardAtom(
                card, source=self.target.hand, destination=self.owner.hand))
        else:
            for slot, equipped in (self.target.equipment or {}).items():
                if equipped is card:
                    self.context.apply(UnequipAtom(self.target, slot))
                    self.owner.hand.append(card)
                    break
            else:
                return self.complete({"applied": False})
        self.game.add_log("%s 的【烈刃】获得 %s 的一张牌"
                          % (self.owner.name, self.target.name))
        return self.complete({"applied": True})


# ==================================================
# 董卓 · 酒池 / 肉林 / 崩坏 / 暴虐
# ==================================================


def _roulin_from_source(game, query):
    """肉林：董卓对女性角色使用【杀】时，对方需连续两张【闪】。"""

    card = query.get("card")
    target = query.get("target")
    if card is None or getattr(card, "name", None) != "SHA" or target is None:
        return 0
    return 1 if getattr(target, "gender", None) == "female" else 0


def _roulin_from_target(game, query):
    """肉林：女性角色对董卓使用【杀】时，同样需要连续两张【闪】。"""

    card = query.get("card")
    source = query.get("source")
    if card is None or getattr(card, "name", None) != "SHA" or source is None:
        return 0
    return 1 if getattr(source, "gender", None) == "female" else 0


class Benguai(Skill):
    """锁定技：结束阶段若你的体力不是全场最少（或之一），须减 1 点体力或上限。"""

    id = "benguai"
    name = "崩坏"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=15),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.FINISH:
            return False
        return self._above_lowest(context.state)

    def _above_lowest(self, game):
        """你的体力**不是**全场最少的（或之一）时必须发动。"""

        alive = list(game.get_alive_players())
        if not alive:
            return False
        lowest = min(int(player.hp) for player in alive)
        return int(self.owner.hp) > lowest

    def resolve(self, context, event):
        BenguaiFlow(context.services["engine"], self.owner).start()


class BenguaiFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "choose"

    def begin(self):
        options = []
        if int(self.owner.hp) > 1:
            options.append(("hp", "减 1 点体力"))
        if int(self.owner.max_hp) > 1:
            options.append(("max_hp", "减 1 点体力上限"))
        if not options:
            return self.complete({"applied": False})
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【崩坏】：你的体力不是全场最少，请选择一项",
                   reason="benguai", options=tuple(options))
        return self.current_result()

    def advance(self, response=None):
        option = str(getattr(response, "option", "") or "")
        if option == "max_hp":
            self.owner.max_hp = max(1, int(self.owner.max_hp) - 1)
            if self.owner.hp > self.owner.max_hp:
                self.owner.hp = self.owner.max_hp
            self.game.add_log("%s 的【崩坏】减 1 点体力上限" % self.owner.name)
        else:
            lose_hp(self.game, self.owner, 1, reason="崩坏")
        return self.complete({"applied": True})


class Baonue(Skill):
    """主公技：其他群势力角色造成伤害后，其可以令董卓判定，黑桃则董卓回血。"""

    id = "baonue"
    name = "暴虐"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED, priority=5),)

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        source = event.source
        if source is None or source is self.owner:
            return False
        if not getattr(source, "alive", True):
            # 伤害来源已经阵亡：没人能做这个决定（不替他自动发动）。
            return False
        if getattr(source, "kingdom", None) != "qun":
            return False
        damage = event.payload.get("damage")
        if damage is None or int(event.payload.get("amount", 0) or 0) <= 0:
            return False
        # 官方（旧版）**没有**"董卓必须已受伤"这个前提：满体力时照样问伤害
        # 来源要不要发动，判定为黑桃才回血（满体力时回血自然无效，但流程与
        # 询问照走）。这里曾经用 ``self.owner.hp < self.owner.max_hp`` 当门槛，
        # 等于替伤害来源做了决定——他连"要不要赌一次判定"都问不到。
        return True

    def resolve(self, context, event):
        BaonueFlow(context.services["engine"], self.owner, event.source).start()


class BaonueFlow(Flow):
    """暴虐：判定与否由**造成伤害的那名其他群势力角色**决定，黑桃才回血。

    官方："主公技，其他群势力角色造成伤害后，**其**可以令你进行一次判定，
    若结果为**黑桃**，你回复 1 点体力。" 旧实现两处都与官方相反：直接判定
    （不问伤害来源愿不愿意），而且把结果判据写成了"黑色"——梅花判定也会
    让董卓回血。判定本身仍然由董卓执行（"令**你**进行一次判定"）。

    第三处偏差是"董卓必须已受伤"这个门槛（``can_trigger`` 的结尾）：官方
    文本里没有它，而且它是替伤害来源做决定——董卓满体力时他连"要不要赌一次
    判定"都问不到。门槛已删去：满体力时照样询问、照样判定，只是回血无效。
    """

    def __init__(self, engine, owner, source):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.source = source
        self.stage = "confirm"

    def begin(self):
        if self.source is None or not getattr(self.source, "alive", True):
            return self.complete({"applied": False})
        ask_confirm(
            self.engine, self, source=self.owner, target=self.source,
            prompt="【暴虐】：是否令 %s 进行一次判定？（黑桃则 %s 回复 1 点体力）"
                   % (self.owner.name, self.owner.name),
            reason="baonue")
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "judge":
            # 判定流程（含改判窗口）已经收尾：结果由 ``_after_judge`` 接。
            return self.complete({"applied": True})
        if response is None or not response.confirmed:
            self.game.add_log("%s 放弃发动【暴虐】" % self.source.name)
            return self.complete({"applied": False})
        return self._begin_judge()

    def _begin_judge(self):
        self.stage = "judge"
        flow, result = judge(self.engine, self.owner, "baonue")
        if result is None:
            flow.on_complete = self._after_judge
            self.wait(flow)
            return self.current_result()
        return self._after_judge(result)

    def _after_judge(self, result):
        game = self.game
        if result is not None and getattr(result, "suit", None) == "spade":
            from src.game.atoms_v2 import RecoverHpAtom

            before = self.owner.hp
            self.context.apply(RecoverHpAtom(self.owner, 1))
            game.add_log("%s 的【暴虐】判定为黑桃，回复 %d 点体力"
                         % (self.owner.name, self.owner.hp - before))
        else:
            game.add_log("%s 的【暴虐】判定不是黑桃" % self.owner.name)
        return self.complete({"applied": True})


# ==================================================
# 贾诩 · 完杀 / 乱武 / 帷幕
# ==================================================


def _wansha_forbidden(game, query):
    """完杀：在自己（= modifier 的拥有者）的回合内，除自己以外只有濒死角色
    才能使用【桃】。施加限制的人由 ``Game.rescue_forbidden`` 从 modifier 上
    带进来（query["owner"]），不写死在技能里。"""

    owner = query.get("owner")
    rescuer = query.get("rescuer")
    dying = query.get("dying")
    if owner is None or rescuer is None or dying is None:
        return False
    if game.current_turn_player is not owner:
        return False
    if rescuer is owner:
        return False
    return rescuer is not dying


def _weimu_forbidden(game, query):
    """帷幕：不能成为黑色锦囊牌的目标。"""

    card = query.get("card")
    if card is None:
        return 0
    if getattr(card, "category", None) != "trick":
        return 0
    if getattr(card, "card_color", None) != "black":
        return 0
    return 1


def _can_luanwu(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if limited_used(player, "luanwu"):
        return False, "限定技已经发动过"
    if not other_alive_players(game, player):
        return False, "没有其他角色"
    return True, ""


def _activate_luanwu(game, player, target=None, cards=None):
    consume_limited(game, player, "luanwu", note="乱武")
    LuanwuFlow(game.engine, player).start()
    game.add_log(player.name + " 发动限定技【乱武】")
    return True


class LuanwuFlow(Flow):
    """乱武：其他角色依次"对距离最近的角色使用一张【杀】，否则失去 1 点体力"。

    三个决定全部在被问的角色手里：**出不出**【杀】、**用哪一张**（实体【杀】
    / 火杀 / 【武圣】一类转化）、**并列最近时打谁**。技能只负责问。

    出过【杀】就不再失去体力——以前 `_make_them_sha` 提交动作后没有返回，
    调用方以为"没出成"，紧接着又扣了他 1 点体力（实测 AI 出杀后体力 4 → 3）。
    """

    SHA = "sha"
    PUNISH = "punish"

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.order = []
        self.index = 0
        self.stage = "run"
        #: 正在被问的角色 / 他这次可选的最近目标 / 可用的【杀】方式。
        self.current = None
        self.victims = []
        self.victim = None
        self.options = []

    def begin(self):
        self.order = [
            player for player in self.game.seats.alive_players_in_order(
                start_after=self.owner)
            if player is not self.owner
        ]
        self.index = 0
        return self._next()

    def advance(self, response=None):
        if self.stage == "choice":
            return self._after_choice(response)
        if self.stage == "victim":
            return self._after_victim(response)
        if self.stage == "source":
            return self._after_source(response)
        return self._next()

    # ---- 逐个结算 ----

    def _next(self):
        if self.game.game_over:
            return self.complete({"applied": True})
        while self.index < len(self.order):
            player = self.order[self.index]
            self.index += 1
            if not getattr(player, "alive", True) or int(player.hp) <= 0:
                continue
            self.current = player
            self.victims = self._legal_victims(player)
            if not self.victims:
                # 没有合法目标，或手里没有一张**真的能用**的【杀】（距离不够
                # 也算用不了）：直接失去体力。不摆一个只能点"不杀"的假入口。
                self.current = None
                self._punish(player)
                continue
            self.stage = "choice"
            ask_option(self.engine, self, source=self.owner, target=player,
                       prompt="【乱武】：对 %s 使用一张【杀】，否则失去 1 点体力"
                              % self._victim_label(),
                       reason="luanwu",
                       options=((self.SHA, "对距离最近的角色使用一张【杀】"),
                                (self.PUNISH, "不使用【杀】，失去 1 点体力")))
            return self.current_result()
        self.current = None
        return self.complete({"applied": True})

    def _after_choice(self, response):
        if (str(getattr(response, "option", "") or "") != self.SHA
                or self.current is None):
            return self._give_up()
        if len(self.victims) == 1:
            self.victim = self.victims[0]
            return self._ask_source()
        self.stage = "victim"
        ask_targets(self.engine, self, source=self.owner, target=self.current,
                    prompt="【乱武】：请选择这次【杀】的目标（距离并列）",
                    reason="luanwu", candidates=list(self.victims),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_victim(self, response):
        chosen = [
            target for target in (getattr(response, "targets", None) or ())
            if any(target is candidate for candidate in self.victims)
        ]
        if self.current is None or not chosen:
            # 取消选目标 = 这次不出【杀】，按规则失去 1 点体力。
            return self._give_up()
        self.victim = chosen[0]
        return self._ask_source()

    # ---- 用哪一种【杀】 ----

    def _ask_source(self):
        self.options = sha_use_options(self.game, self.current, self.victim)
        if not self.options:
            return self._give_up()
        if len(self.options) == 1:
            return self._submit(self.options[0])
        cards = []
        for option in self.options:
            for card in option.source_cards:
                if not any(card is other for other in cards):
                    cards.append(card)
        self.stage = "source"
        ask_cards(self.engine, self, source=self.owner, target=self.current,
                  prompt="【乱武】：请选择对 %s 使用的【杀】" % self.victim.name,
                  reason="luanwu", candidates=cards, min_cards=1, max_cards=1)
        return self.current_result()

    def _after_source(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self._give_up()
        chosen = cards[0]
        option = next(
            (item for item in self.options
             if any(card is chosen for card in item.source_cards)), None)
        if option is None:
            # 素材在收集期间被移走：按"无法使用【杀】"处理，不硬来。
            return self._give_up()
        return self._submit(option)

    # ---- 真正使用（走正常 UseCardFlow，闪 / 伤害 / 濒死全由它负责）----

    def _submit(self, option):
        actions = self.game.card_actions
        virtual = actions.effective_card(option)
        player, victim = self.current, self.victim
        if (virtual is None or player is None or victim is None
                or not player.alive or not victim.alive or victim.hp <= 0):
            return self._give_up()
        for card in option.source_cards:
            if not any(item is card for item in player.hand):
                return self._give_up()
        from src.game.engine import UseCardAction

        self.stage = "using"
        self.game.add_log("【乱武】：%s 对 %s 使用【%s】"
                          % (player.name, victim.name,
                             getattr(virtual, "display_name", "杀")))
        self.engine.submit(UseCardAction(
            player, virtual, [victim], ignore_usage_limit=True,
            on_complete=lambda _result: self._after_sha()))
        if self.status is FlowStatus.RUNNING:
            # 【杀】的子流程还在跑（等闪 / 结算伤害）：把自己标成等待，
            # 别让调用方以为乱武已经结束。它结束时由 on_complete 接回。
            self.status = FlowStatus.WAITING
        return self.current_result()

    def _after_sha(self):
        """【杀】按正常流程结算完了（含闪 / 伤害 / 濒死）：**不再失去体力**。"""

        self.current = None
        self.victim = None
        self.options = []
        self.stage = "run"
        return self._next()

    def _give_up(self):
        """这名角色不出【杀】：失去 1 点体力，换下一个人。"""

        player = self.current
        self.current = None
        self.victim = None
        self.options = []
        self.stage = "run"
        if player is not None and getattr(player, "alive", True):
            self._punish(player)
        return self._next()

    # ---- 查询 ----

    def _nearest_targets(self, player):
        """与这名角色距离最近的其他存活角色（可能并列）。"""

        from src.game.rules import DistanceRule

        others = [other for other in self.game.get_alive_players()
                  if other is not player]
        if not others:
            return []
        distances = [(DistanceRule.distance(self.game, player, other), other)
                     for other in others]
        best = min(item[0] for item in distances)
        return [other for value, other in distances if value == best]

    def _legal_victims(self, player):
        """并列最近者里**真的能杀到**的那些（不在攻击范围内的不算）。"""

        result = []
        for victim in self._nearest_targets(player):
            if not getattr(victim, "alive", True) or victim.hp <= 0:
                continue
            if sha_use_options(self.game, player, victim):
                result.append(victim)
        return result

    def _victim_label(self):
        names = "、".join(victim.name for victim in self.victims)
        return ("距离最近的 " + names) if names else "距离最近的角色"

    def _punish(self, player):
        self.game.add_log("【乱武】：%s 没有使用【杀】，失去 1 点体力" % player.name)
        lose_hp(self.game, player, 1, source=self.owner, reason="乱武")


# ==================================================
# 鲁肃 · 好施 / 缔盟
# ==================================================


def _can_haoshi(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "draw":
        return False, "只能在你的摸牌阶段发动"
    if not game.deck.draw_pile:
        return False, "牌堆没有牌"
    return True, ""


def _haoshi_flow(game, player):
    return HaoshiFlow(game.engine, player)


class HaoshiFlow(Flow):
    """好施：额外摸两张；若手牌多于五张，须把一半（向下取整）交给手牌最少的人。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.give = 0
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【好施】：是否发动？额外摸两张牌。",
                    reason="haoshi")
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "confirm":
            return self._after_confirm(response)
        if self.stage == "target":
            return self._after_target(response)
        return self._after_cards(response)

    def _after_confirm(self, response):
        if response is None or not response.confirmed:
            return self.complete({"applied": False})
        # 摸牌阶段的基础摸牌由本流程自己完成（阶段替代语义）。
        base = int(self.game.draw_count(self.owner))
        self.context.apply(DrawCardsAtom(self.owner, base + 2))
        self.game.add_log("%s 发动【好施】，摸了 %d 张牌" % (self.owner.name, base + 2))
        if len(self.owner.hand) <= 5:
            return self.complete({"applied": True})
        self.give = len(self.owner.hand) // 2
        others = other_alive_players(self.game, self.owner)
        if not others or self.give <= 0:
            return self.complete({"applied": True})
        fewest = min(len(other.hand) for other in others)
        candidates = [other for other in others if len(other.hand) == fewest]
        self.stage = "target"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【好施】：请选择手牌最少的角色，把 %d 张手牌交给他" % self.give,
                    reason="haoshi", candidates=candidates,
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_target(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": True})
        self.target = targets[0]
        candidates = list(self.owner.hand)
        count = min(self.give, len(candidates))
        self.stage = "cards"
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【好施】：请选择交给 %s 的 %d 张手牌" % (self.target.name, count),
                  reason="haoshi", candidates=candidates,
                  min_cards=count, max_cards=count)
        return self.current_result()

    def _after_cards(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        for card in cards:
            if any(item is card for item in self.owner.hand):
                self.context.apply(MoveCardAtom(
                    card, source=self.owner.hand, destination=self.target.hand))
        self.game.add_log("%s 的【好施】把 %d 张手牌交给了 %s"
                          % (self.owner.name, len(cards), self.target.name))
        return self.complete({"applied": True})


def _can_dimeng(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("dimeng", "used", 0):
        return False, "本回合已经发动过"
    if len(other_alive_players(game, player)) < 2:
        return False, "需要两名其他角色"
    return True, ""


#: 【缔盟】的费用是"弃置 X 张牌"（X = 两名目标角色的手牌数之差）：手牌与装备区
#: 的牌都能支付。张数要等两名目标选定才算得出来，所以它没法在
#: ``ActiveSkillSpec`` 里预先声明（那一步还不知道目标）——这份声明只用来收集
#: 候选牌：``activation.cost_candidates`` 是候选 / 界面高亮 / 引擎校验的同一份
#: 实现，张数、支付与结算由 ``DimengFlow`` 按"预验证 → 支付 → 结算"做。
DIMENG_COST_SPEC = ActiveSkillSpec(
    cost_cards=1,
    cost_prompt="【缔盟】：请弃置等同于两名角色手牌数差的牌",
    allowed_zones=(CostZone.HAND, CostZone.EQUIPMENT),
)


def _dimeng_cost_candidates(game, player):
    """这次发动能支付的牌（手牌 + 装备区；与引擎的候选判断同一份实现）。"""

    from src.game.skills.activation import cost_candidates

    return cost_candidates(game, player, DIMENG_COST_SPEC)


def _activate_dimeng(game, player, target=None, cards=None):
    """缔盟：两名目标与费用都在流程里收集，所以这里**不写** used 标记。

    以前它在流程开始前就把次数记成"已发动"：点了技能又取消、或者手里的牌
    不够支付 X，次数照样被扣掉，一个出牌阶段就此白废。现在次数只在
    "两名目标确认 + 差额算出 + 费用真的付掉"之后才写（见 ``DimengFlow._settle``）。
    """

    DimengFlow(game.engine, player).start()
    return True


class DimengFlow(Flow):
    """缔盟：选两名其他角色 → 弃置 X 张牌（X = 手牌数之差）→ 交换他们的手牌。

    费用事务的三段与 ``activation.plan_activation`` 同序、同判据：
    预验证（目标合法、候选牌够付、每张牌确实还在自己的区域）→ 支付（手牌走
    ``MoveCardAtom``、装备区走 ``UnequipAtom(reason="discard")``）→ 结算
    （交换手牌 + 记次数）。被拒绝或被取消的发动不改任何状态：不消耗次数、
    不发技能事件、不动牌。
    """

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.first = None
        self.second = None
        self.need = 0
        self.stage = "first"

    def begin(self):
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【缔盟】：请选择第一名其他角色",
                    reason="dimeng",
                    candidates=other_alive_players(self.game, self.owner),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "first":
            return self._after_first(response)
        if self.stage == "second":
            return self._after_second(response)
        return self._after_cost(response)

    # ---- 第一步：两名目标（换牌双方由玩家指定，不替玩家挑）----

    def _after_first(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self._abort("没有选择角色")
        self.first = targets[0]
        self.stage = "second"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【缔盟】：请选择第二名其他角色（与 %s 交换手牌）"
                           % self.first.name,
                    reason="dimeng",
                    candidates=[other for other in other_alive_players(self.game, self.owner)
                                if other is not self.first],
                    min_targets=1, max_targets=1)
        return self.current_result()

    # ---- 第二步：差额 → 预验证费用 ----

    def _after_second(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self._abort("没有选择第二名角色")
        self.second = targets[0]
        if self.second is self.first or not getattr(self.second, "alive", True):
            return self._abort("没有选择合法的角色")
        # X = 两名角色手牌数之差（在支付**之前**结算，与官方口径一致）。
        self.need = abs(len(self.first.hand) - len(self.second.hand))
        if self.need <= 0:
            # X = 0：没有费用要支付，直接交换。
            return self._settle()
        candidates = _dimeng_cost_candidates(self.game, self.owner)
        if len(candidates) < self.need:
            # 预验证失败：费用不够 → 整次发动作废，次数与牌一个都不动。
            self.game.message = ("【缔盟】：需要弃置 %d 张牌，但只有 %d 张可以支付，"
                                 "本次发动未结算。" % (self.need, len(candidates)))
            self.game.add_log("%s 的【缔盟】费用不足 %d 张，未发动"
                              % (self.owner.name, self.need))
            return self.complete({"applied": False})
        self.stage = "cost"
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【缔盟】：请弃置 %d 张牌（两名角色手牌数之差）" % self.need,
                  reason="dimeng", candidates=candidates,
                  min_cards=self.need, max_cards=self.need,
                  # 手牌 + 装备混选走公共牌池：只有这个区域能让玩家点到
                  # 装备区的牌（手牌那一侧照旧可以从手牌区点）。
                  zone="public_pool",
                  context={"zone_owner": self.owner, "cancellable": True})
        return self.current_result()

    # ---- 第三步：支付 → 结算 ----

    def _after_cost(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if len(cards) != self.need:
            # 放弃 / 张数不符：一张牌都不付、次数也不消耗。
            return self._abort("没有支付费用")
        from src.game.skills.activation import allowed_zones, cost_placement

        zones = allowed_zones(DIMENG_COST_SPEC)
        entries = []
        for card in cards:
            placement = cost_placement(self.owner, card, zones)
            if placement is None:
                # 提交的费用牌已经不在能支付的区域：拒绝本次发动。
                return self._abort("费用牌已经不在可以支付的区域")
            entries.append((card, placement[0], placement[1]))
        self._pay(entries)
        return self._settle()

    def _pay(self, entries):
        """按预验证的结果支付：手牌走 MoveCardAtom，装备区走 UnequipAtom。

        与 ``skills.activation.pay_cost`` 的两条分支一致——装备区的费用必须带
        "因弃置"的原因，【落英】一类订阅者才照常工作。
        """

        for card, zone, slot in entries:
            if zone is CostZone.EQUIPMENT:
                self.context.apply(UnequipAtom(
                    self.owner, slot, self.game.deck.discard_pile,
                    reason=DISCARD_REASON))
            else:
                self.context.apply(MoveCardAtom(
                    card, source=self.owner.hand,
                    destination=self.game.deck.discard_pile))
        self.game.add_log("%s 的【缔盟】弃置 %d 张牌"
                          % (self.owner.name, len(entries)))

    def _settle(self):
        """交换两名角色的手牌；次数与技能事件都只在这里（成功之后）落地。

        ``spec`` 声明了 ``defer_skill_event``：技能事件不能在"激活"时就发，
        否则玩家取消选目标、或者手里的牌不够支付 X 时，横幅已经播过一遍而
        实际什么都没发生。所以事件挪到这里——目标、差额、费用全部成立之后，
        和 used 标记同一个时刻。取消 / 失败走 ``_abort``，一条事件都不发。
        """

        first_cards = list(self.first.hand)
        second_cards = list(self.second.hand)
        for card in first_cards:
            self.context.apply(MoveCardAtom(
                card, source=self.first.hand, destination=self.second.hand))
        for card in second_cards:
            self.context.apply(MoveCardAtom(
                card, source=self.second.hand, destination=self.first.hand))
        self.owner.skill_state.set("dimeng", "used", 1, ResetScope.TURN)
        self.game.add_log("%s 的【缔盟】交换了 %s 与 %s 的手牌"
                          % (self.owner.name, self.first.name, self.second.name))
        emit_skill_triggered(
            self.engine, self.game.skill_registry.get("dimeng"), self.owner,
            targets=(self.first, self.second))
        return self.complete({"applied": True})

    def _abort(self, reason):
        """本次发动未成立：不改任何状态（不消耗次数、不动牌、不发技能事件）。"""

        self.game.message = "【缔盟】：%s，本次未结算。" % reason
        self.game.add_log("%s 没有发动【缔盟】（%s）" % (self.owner.name, reason))
        return self.complete({"applied": False})


# ==================================================
# 技能表
# ==================================================

FOREST_SKILLS = (
    triggered(
        "yinghun",
        "英魂",
        "准备阶段，若你已受伤，你可以令一名其他角色执行一项："
        "摸 X 张牌然后弃一张牌；或摸一张牌然后弃 X 张牌（X 为你已损失的体力值）。",
        factory=Yinghun,
    ),
    SkillDef(
        id="huoshou",
        name="祸首",
        description="锁定技，【南蛮入侵】对你无效；你是任何【南蛮入侵】造成伤害的来源。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.TARGET_FORBIDDEN, value=_nanman_ignore,
                         roles=("target",)),
            ModifierSpec(kind=ModifierKind.TRICK_SOURCE, value=_nanman_source),
        ),
    ),
    SkillDef(
        id="zaiqi",
        name="再起",
        description="摸牌阶段，若你已受伤，你可以放弃摸牌并改为亮出牌堆顶的 X 张牌"
        "（X 为你已损失的体力值）：其中每有一张红桃，你回复 1 点体力并弃置该牌，"
        "其余的牌收入你的手牌。",
        kind=SkillKind.PASSIVE,
        phase_replacement=PhaseReplacement(
            phase=TurnPhase.DRAW,
            prompt="【再起】：是否放弃摸牌，改为亮出牌堆顶的若干张牌？",
            can_offer=_can_zaiqi,
            apply=_apply_zaiqi,
        ),
    ),
    SkillDef(
        id="duanliang",
        name="断粮",
        description="你可以将一张黑色基本牌或黑色装备牌当【兵粮寸断】使用；"
        "你可以对与你距离 2 以内的角色使用【兵粮寸断】。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(skill_id="duanliang", matches=_is_black_basic_or_equipment,
                           name="BINGLIANG", category="trick",
                           contexts=(PLAY_CONTEXT,)),
        ),
        modifiers=(
            ModifierSpec(kind=ModifierKind.TRICK_DISTANCE,
                         value=_duanliang_distance, roles=("player",)),
        ),
        tags=("conversion",),
    ),
    triggered(
        "xingshang",
        "行殇",
        "你可以立即获得死亡角色的所有牌。",
        factory=Xingshang,
    ),
    triggered(
        "fangzhu",
        "放逐",
        "你每受到一次伤害，你可以令一名其他角色摸 X 张牌（X 为你已损失的体力值），"
        "然后该角色将其武将牌翻面。",
        factory=Fangzhu,
    ),
    triggered(
        "songwei",
        "颂威",
        "主公技，其他魏势力角色的判定牌生效后，若判定牌为黑色，其可以令你摸一张牌。",
        factory=Songwei,
        is_lord_skill=True,
    ),
    SkillDef(
        id="juxiang",
        name="巨象",
        description="锁定技，【南蛮入侵】对你无效；其他角色使用的【南蛮入侵】"
        "结算完毕进入弃牌堆时，你立即获得它。",
        kind=SkillKind.LOCKED,
        factory=Juxiang,
        modifiers=(
            ModifierSpec(kind=ModifierKind.TARGET_FORBIDDEN, value=_nanman_ignore,
                         roles=("target",)),
        ),
    ),
    triggered(
        "lieren",
        "烈刃",
        "当你使用【杀】造成一次伤害后，你可以与受到该伤害的角色拼点："
        "若你赢，你获得该角色的一张牌。",
        factory=Lieren,
    ),
    SkillDef(
        id="jiuchi",
        name="酒池",
        description="你可以将一张黑色手牌当【酒】使用。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(skill_id="jiuchi", matches=_is_black, name="JIU",
                           category="basic", contexts=(PLAY_CONTEXT,)),
        ),
        tags=("conversion",),
    ),
    SkillDef(
        id="roulin",
        name="肉林",
        description="锁定技，你对女性角色、女性角色对你使用【杀】时，"
        "都需连续使用两张【闪】才能抵消。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.RESPONSE_COUNT, value=_roulin_from_source,
                         roles=("source",)),
            ModifierSpec(kind=ModifierKind.RESPONSE_COUNT, value=_roulin_from_target,
                         roles=("target",)),
        ),
    ),
    triggered(
        "benguai",
        "崩坏",
        "锁定技，结束阶段，若你的体力不是全场最少的（或之一），"
        "你须减 1 点体力或 1 点体力上限。",
        factory=Benguai,
        kind=SkillKind.LOCKED,
    ),
    triggered(
        "baonue",
        "暴虐",
        "主公技，其他群势力角色造成伤害后，其可以令你进行一次判定："
        "若结果为黑桃，你回复 1 点体力。",
        factory=Baonue,
        is_lord_skill=True,
    ),
    SkillDef(
        id="wansha",
        name="完杀",
        description="锁定技，你的回合内，除你以外，只有处于濒死状态的角色才能使用【桃】。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.RESCUE_FORBIDDEN, value=_wansha_forbidden),
        ),
    ),
    active(
        "luanwu",
        "乱武",
        "限定技，出牌阶段，你可以令所有其他角色依次对与其距离最近的一名角色使用一张【杀】，"
        "无法如此做者失去 1 点体力。",
        can_activate=_can_luanwu,
        activate=_activate_luanwu,
        spec=ActiveSkillSpec(),
        tags=("active", "limited"),
    ),
    SkillDef(
        id="weimu",
        name="帷幕",
        description="锁定技，你不能成为黑色锦囊牌的目标。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.TARGET_FORBIDDEN, value=_weimu_forbidden,
                         roles=("target",)),
        ),
    ),
    SkillDef(
        id="haoshi",
        name="好施",
        description="摸牌阶段，你可以额外摸两张牌；若此时你的手牌数多于五张，"
        "你必须将一半（向下取整）的手牌交给场上除你以外手牌数最少的一名角色。",
        kind=SkillKind.PASSIVE,
        phase_replacement=PhaseReplacement(
            phase=TurnPhase.DRAW,
            prompt="【好施】：是否额外摸两张牌？",
            can_offer=_can_haoshi,
            flow=_haoshi_flow,
        ),
    ),
    active(
        "dimeng",
        "缔盟",
        "出牌阶段限一次，你可以弃置 X 张牌并选择两名其他角色"
        "（X 为这两名角色手牌数之差），然后交换他们的手牌。",
        can_activate=_can_dimeng,
        activate=_activate_dimeng,
        # ``defer_skill_event``：目标、差额、费用全在 ``DimengFlow`` 里收集，
        # 玩家可以在任何一步取消。技能事件因此不能在"激活"时发，改由流程在
        # 确认成立（``_settle``）时自己调 ``activation.emit_skill_triggered``。
        spec=ActiveSkillSpec(defer_skill_event=True),
        tags=("active",),
    ),
)
