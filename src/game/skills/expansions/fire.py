"""火包武将技能：典韦 / 卧龙诸葛亮 / 太史慈 / 庞德 / 庞统 / 荀彧 / 袁绍 / 颜良&文丑。"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom
from src.game.conversion import PLAY_CONTEXT, RESPONSE_CONTEXT, CardConversion
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
    hand_cards,
    judge,
    limited_used,
    lose_hp,
    other_alive_players,
    pindian_possible,
    start_pindian,
    use_virtual,
)
from ..modifiers import ModifierKind
from ..state import ResetScope


# ==================================================
# 通用小谓词
# ==================================================

def _is_red(card):
    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "card_color", None) == "red"


def _is_black(card):
    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "card_color", None) == "black"


def _is_club(card):
    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "suit", None) == "club"


def _is_weapon_card(card):
    return (getattr(card, "category", None) == "equipment"
            and getattr(card, "subtype", None) == "weapon")


def _probe_card(name, category="trick"):
    from src.card import Card

    return Card(name=name, category=category, color=(0, 0, 0))


def _card_container(game, owner, card):
    """这张实体牌现在在哪个列表区域里（手牌 / 判定区 / 武将牌上的牌区）。

    装备区是**单值槽位**而不是列表，调用方必须按槽位处理（见猛进），
    所以这里对装备区返回 None。
    """

    for container in (getattr(owner, "hand", None),
                      getattr(owner, "judgement_zone", None)):
        if container is not None and any(item is card for item in container):
            return container
    for zone in (getattr(owner, "placed_cards", None) or {}).values():
        if any(item is card for item in zone):
            return zone
    return None


# ==================================================
# 典韦 · 强袭
# ==================================================


def _can_qiangxi(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("qiangxi", "used", 0):
        return False, "本回合已经发动过"
    if not attack_range_targets(game, player):
        return False, "攻击范围内没有其他角色"
    if int(player.hp) <= 1 and not hand_cards(player, _is_weapon_card):
        return False, "体力只有 1 点且没有武器牌可弃"
    return True, ""


def _activate_qiangxi(game, player, target=None, cards=None):
    if target is None:
        return False
    player.skill_state.set("qiangxi", "used", 1, ResetScope.TURN)
    QiangxiFlow(game.engine, player, target).start()
    return True


class QiangxiFlow(Flow):
    """强袭：先选自减体力还是弃武器牌，再对已选定的目标造成 1 点伤害。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.stage = "cost"

    def begin(self):
        options = []
        if int(self.owner.hp) > 1:
            options.append(("hp", "自减 1 点体力"))
        if hand_cards(self.owner, _is_weapon_card):
            options.append(("weapon", "弃置一张武器牌"))
        if not options:
            return self.complete({"applied": False})
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【强袭】：请选择支付的代价",
                   reason="qiangxi", options=tuple(options))
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "cost":
            return self._after_cost(response)
        return self._after_weapon(response)

    def _after_cost(self, response):
        option = str(getattr(response, "option", "") or "")
        if option == "weapon":
            return self._ask_weapon()
        if int(self.owner.hp) <= 1:
            return self._ask_weapon() if hand_cards(self.owner, _is_weapon_card) \
                else self.complete({"applied": False})
        lose_hp(self.game, self.owner, 1, reason="强袭")
        return self._deal_damage()

    def _ask_weapon(self):
        candidates = hand_cards(self.owner, _is_weapon_card)
        if not candidates:
            return self.complete({"applied": False})
        self.stage = "weapon"
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【强袭】：请选择一张武器牌弃置",
                  reason="qiangxi", candidates=candidates, min_cards=1, max_cards=1)
        return self.current_result()

    def _after_weapon(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self.complete({"applied": False})
        self.context.apply(MoveCardAtom(
            cards[0], source=self.owner.hand, destination=self.game.deck.discard_pile))
        return self._deal_damage()

    def _deal_damage(self):
        from src.game.flows.damage import DamageContext, DamageFlow

        DamageFlow(self.engine, DamageContext(self.owner, self.target, 1)).start()
        self.game.add_log("%s 发动【强袭】，对 %s 造成 1 点伤害"
                          % (self.owner.name, self.target.name))
        return self.complete({"applied": True})


# ==================================================
# 卧龙诸葛亮 · 八阵 / 火计 / 看破
# ==================================================


def _bazhen_armor(game, query):
    """八阵：没有装备防具时视为装备【八卦阵】。

    查询入口是 ``Game.virtual_armor``，它会先看真实防具，因此"装备了防具
    就不再生效"这条条件不需要技能自己再判一次。
    """

    player = query.get("player")
    if player is None or game.armor_card(player) is not None:
        return None
    return "BAGUA"


# ==================================================
# 太史慈 · 天义
# ==================================================


def _tianyi_targets(game, player):
    return other_alive_players(game, player)


def _tianyi_won(game, query):
    player = query.get("player")
    return bool(player is not None and player.skill_state.get("tianyi", "won", 0))


def _tianyi_lost(game, query):
    player = query.get("player")
    return bool(player is not None and player.skill_state.get("tianyi", "lost", 0))


def _can_tianyi(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("tianyi", "used", 0):
        return False, "本回合已经发动过"
    if not player.hand:
        return False, "需要一张手牌拼点"
    if not _tianyi_targets(game, player):
        return False, "没有可以拼点的角色"
    return True, ""


def _activate_tianyi(game, player, target=None, cards=None):
    # 拼点真的能成立（双方都有手牌）才扣技能次数：只要目标空手，
    # 拼点会当场取消，次数却已经用掉——白付一次机会。
    if not pindian_possible(player, target):
        return False
    player.skill_state.set("tianyi", "used", 1, ResetScope.TURN)
    start_pindian(
        game.engine, player, target, reason="tianyi",
        on_complete=lambda result: _finish_tianyi(game, player, result))
    game.add_log(player.name + " 发动【天义】，与 " + target.name + " 拼点")
    return True


def _finish_tianyi(game, player, result):
    if result is None or result.cancelled:
        return
    if result.initiator_wins:
        player.skill_state.set("tianyi", "won", 1, ResetScope.TURN)
        game.add_log("%s 的【天义】获胜：攻击范围无限、可多出一张【杀】、"
                     "【杀】可多指定一个目标" % player.name)
        game.message = player.name + " 的【天义】获胜。"
    else:
        player.skill_state.set("tianyi", "lost", 1, ResetScope.TURN)
        game.add_log("%s 的【天义】没赢：本回合不能使用【杀】" % player.name)
        game.message = player.name + " 的【天义】没赢，本回合不能使用【杀】。"


# ==================================================
# 庞德 · 猛进
# ==================================================


class Mengjin(Skill):
    """你使用的【杀】被【闪】抵消时，可以弃掉对方的一张牌。"""

    id = "mengjin"
    name = "猛进"

    def bindings(self):
        return (SkillBinding(EventType.SHA_DODGED, priority=30),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        target = event.target
        if target is None or target is self.owner:
            return False
        return bool(MengjinFlow.candidates(target))

    def resolve(self, context, event):
        MengjinFlow(context.services["engine"], self.owner, event.target).start()


class MengjinFlow(Flow):
    @staticmethod
    def candidates(target):
        cards = list(getattr(target, "hand", ()) or ())
        for card in (getattr(target, "equipment", None) or {}).values():
            if card is not None:
                cards.append(card)
        cards.extend(list(getattr(target, "judgement_zone", ()) or ()))
        return cards

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.stage = "select"

    def begin(self):
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【猛进】：请选择弃置 %s 的一张牌" % self.target.name,
                  reason="mengjin", candidates=self.candidates(self.target),
                  min_cards=1, max_cards=1, zone="public_pool",
                  context={"zone_owner": self.target})
        return self.current_result()

    def advance(self, response=None):
        if self.stage != "select":
            raise RuntimeError("MengjinFlow cannot advance from stage " + self.stage)
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self.complete({"applied": False})
        card = cards[0]
        container = _card_container(self.game, self.target, card)
        if container is None:
            # 装备区里的牌：按槽位卸下（发出「失去装备」事件，枭姬一类照常响应）。
            from src.game.atoms_v2 import UnequipAtom

            for slot, equipped in (self.target.equipment or {}).items():
                if equipped is card:
                    self.context.apply(UnequipAtom(
                        self.target, slot, self.game.deck.discard_pile))
                    break
            else:
                return self.complete({"applied": False})
        else:
            self.context.apply(MoveCardAtom(
                card, source=container, destination=self.game.deck.discard_pile))
        self.game.add_log("%s 的【猛进】弃置 %s 的一张牌"
                          % (self.owner.name, self.target.name))
        return self.complete({"applied": True})


# ==================================================
# 庞统 · 连环 / 涅槃
# ==================================================


class Niepan(Skill):
    """限定技：濒死时弃掉所有牌、重置武将牌、摸三张牌且体力回复至 3 点。"""

    id = "niepan"
    name = "涅槃"

    def bindings(self):
        return (SkillBinding(EventType.DYING_ENTERED, priority=60),)

    def can_trigger(self, context, event):
        if event.target is not self.owner or not self.owner.alive:
            return False
        return not limited_used(self.owner, self.id)

    def resolve(self, context, event):
        NiepanFlow(context.services["engine"], self.owner).start()


class NiepanFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【涅槃】：是否发动？弃掉所有牌并恢复状态。",
                    reason="niepan")
        return self.current_result()

    def advance(self, response=None):
        if self.stage != "confirm":
            raise RuntimeError("NiepanFlow cannot advance from stage " + self.stage)
        if response is None or not response.confirmed:
            return self.complete({"applied": False})
        self.stage = "done"
        from src.game.atoms_v2 import UnequipAtom

        from ..mechanics import consume_limited

        game, owner = self.game, self.owner
        consume_limited(game, owner, "niepan", note="涅槃")
        for card in list(owner.hand):
            self.context.apply(MoveCardAtom(
                card, source=owner.hand, destination=game.deck.discard_pile))
        for slot in list(owner.equipment):
            if owner.get_equipment(slot) is not None:
                self.context.apply(UnequipAtom(owner, slot, game.deck.discard_pile))
        for card in list(owner.judgement_zone):
            self.context.apply(MoveCardAtom(
                card, source=owner.judgement_zone, destination=game.deck.discard_pile))
        # 「重置你的武将牌」= 翻回正面（并清掉不屈一类牌区外的负面状态）。
        owner.face_up = True
        target_hp = min(3, int(owner.max_hp))
        before = int(owner.hp)
        owner.hp = max(before, target_hp)
        healed = owner.hp - before
        self.context.apply(DrawCardsAtom(owner, 3))
        game.add_log("%s 发动限定技【涅槃】：重置武将牌、回复 %d 点体力并摸三张牌"
                     % (owner.name, healed))
        game.message = owner.name + " 发动【涅槃】，恢复了状态。"
        return self.complete({"applied": True})


# ==================================================
# 荀彧 · 驱虎 / 节命
# ==================================================


def _quhu_targets(game, player):
    return [other for other in other_alive_players(game, player)
            if int(other.hp) > int(player.hp)]


def _can_quhu(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("quhu", "used", 0):
        return False, "本回合已经发动过"
    if not player.hand:
        return False, "需要一张手牌拼点"
    if not _quhu_targets(game, player):
        return False, "没有体力比你多的角色"
    return True, ""


def _activate_quhu(game, player, target=None, cards=None):
    if not pindian_possible(player, target):
        return False
    player.skill_state.set("quhu", "used", 1, ResetScope.TURN)
    QuhuFlow(game.engine, player, target).start()
    return True


class QuhuFlow(Flow):
    """驱虎：拼点 → 赢了自己指定受伤者 / 没赢对方打你 1 点。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.stage = "victim"

    def begin(self):
        start_pindian(self.engine, self.owner, self.target, reason="quhu",
                      on_complete=self._after_pindian)
        return self.current_result()

    def advance(self, response=None):
        if self.stage != "victim":
            raise RuntimeError("QuhuFlow cannot advance from stage " + self.stage)
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        self._damage(self.target, targets[0])
        self.game.add_log("%s 的【驱虎】获胜，%s 对 %s 造成 1 点伤害"
                          % (self.owner.name, self.target.name, targets[0].name))
        return self.complete({"applied": True})

    def _damage(self, source, target):
        from src.game.flows.damage import DamageContext, DamageFlow

        DamageFlow(self.engine, DamageContext(source, target, 1)).start()

    def _after_pindian(self, result):
        if result is None or result.cancelled:
            return
        if not result.initiator_wins:
            self.game.add_log("%s 的【驱虎】没赢，%s 对他造成 1 点伤害"
                              % (self.owner.name, self.target.name))
            self._damage(self.target, self.owner)
            return
        candidates = [
            other for other in self.game.seats.alive_players_in_order(start_after=self.target)
            if other is not self.target and other is not self.owner
            and self._in_range(self.target, other)
        ]
        if not candidates:
            self.game.message = "【驱虎】没有可指定的受伤者。"
            return
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【驱虎】：请指定 %s 对其攻击范围内的一名角色造成伤害"
                           % self.target.name,
                    reason="quhu", candidates=candidates,
                    min_targets=1, max_targets=1)

    @staticmethod
    def _in_range(source, target):
        from src.game.rules import DistanceRule

        return DistanceRule.in_attack_range(None, source, target)


class Jieming(Skill):
    """你每受到 1 点伤害，可令一名角色将手牌补至其体力上限（至多五张）。"""

    id = "jieming"
    name = "节命"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=10),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        amount = int(event.payload.get("amount", 0) or 0)
        if damage is None or damage.target is not self.owner:
            return False
        return self.owner.alive and amount > 0

    def resolve(self, context, event):
        # 官方是"每受到 1 点伤害后"，所以一次 2 点伤害要问两次、补两次。
        # 伤害事件一次只发一条，触发次数只能按 amount 展开。
        amount = int(event.payload.get("amount", 0) or 0)
        JiemingFlow(context.services["engine"], self.owner, rounds=amount).start()


class JiemingFlow(Flow):
    def __init__(self, engine, owner, rounds=1):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.rounds = max(1, int(rounds))
        self.remaining = self.rounds
        self.stage = "target"

    def begin(self):
        return self._ask()

    def _ask(self):
        """剩下的每一次都独立问一次目标——可以补给自己，也可以补给同一个人。"""

        if self.remaining <= 0:
            return self.complete({"applied": True})
        self.stage = "target"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【节命】：请选择将手牌补至体力上限的角色"
                           + ("（%d/%d）" % (self.rounds - self.remaining + 1, self.rounds)
                              if self.rounds > 1 else ""),
                    reason="jieming",
                    candidates=list(self.game.get_alive_players()),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def advance(self, response=None):
        if self.stage != "target":
            raise RuntimeError("JiemingFlow cannot advance from stage " + self.stage)
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            # 放弃这一次，但已经攒下的剩余次数照样继续问（不是整条作废）。
            self.remaining = 0
            return self.complete({"applied": False})
        target = targets[0]
        limit = min(5, int(target.max_hp))
        lack = max(0, limit - len(target.hand))
        if lack:
            self.context.apply(DrawCardsAtom(target, lack))
        self.game.add_log("%s 的【节命】令 %s 将手牌补至 %d 张"
                          % (self.owner.name, target.name, limit))
        self.remaining -= 1
        if self.remaining > 0:
            return self._ask()
        return self.complete({"applied": True})


# ==================================================
# 袁绍 · 乱击 / 血裔
# ==================================================


def _luanji_same_suit_pair(cards):
    buckets = {}
    for card in cards:
        buckets.setdefault(getattr(card, "suit", None), []).append(card)
    for suit, group in buckets.items():
        if suit is not None and len(group) >= 2:
            return group[:2]
    return None


def _can_luanji(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if _luanji_same_suit_pair(hand_cards(player)) is None:
        return False, "需要两张相同花色的手牌"
    return True, ""


def _activate_luanji(game, player, target=None, cards=None):
    """乱击：把两张同花色的手牌当【万箭齐发】使用。

    哪两张由**玩家自己挑**（spec 的 cost_cards / keep_cards 声明的就是
    "选两张牌、但不由 activation 代付"）。这里只做最后一层复核：数量不对
    或花色不同就明确拒绝，**绝不替他找一对同花色的牌**——那样玩家会在
    什么都没点的情况下看到技能自己打出【万箭齐发】。
    """

    chosen = list(cards or ())
    if len(chosen) < 2:
        game.message = "【乱击】：请先选择两张手牌。"
        return False
    if getattr(chosen[0], "suit", None) != getattr(chosen[1], "suit", None):
        game.message = "【乱击】：两张牌的花色必须相同。"
        return False
    from src.game.rules import target_candidates

    probe = _probe_card("WANJIAN")
    effect = game.engine.card_effects.get(probe)
    targets = list(target_candidates(
        game, player, effect.target_rule if effect is not None else None, card=probe))
    use_virtual(game, player, "WANJIAN", sources=chosen[:2], targets=targets)
    game.add_log(player.name + " 发动【乱击】，将两张同花色手牌当【万箭齐发】使用")
    return True


def _xueyi_bonus(game, query):
    """血裔：每有一名其他群势力角色存活，手牌上限 +2。"""

    player = query.get("player")
    if player is None:
        return 0
    count = len([
        other for other in game.get_alive_players()
        if other is not player and getattr(other, "kingdom", None) == "qun"
    ])
    return count * 2


# ==================================================
# 颜良&文丑 · 双雄
#
# 一个技能 id 同时承担两件事：摸牌阶段的触发（放弃摸牌 + 判定），
# 以及出牌阶段把异色手牌当【决斗】使用的主动入口。
# ==================================================


class Shuangxiong(Skill):
    """摸牌阶段：可以放弃摸牌并判定，记下判定牌的颜色。"""

    id = "shuangxiong"
    name = "双雄"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=55),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.DRAW:
            return False
        if event.payload.get("skipped", False):
            return False
        return not self.owner.skill_state.get(self.id, "used", 0)

    def resolve(self, context, event):
        ShuangxiongFlow(context.services["engine"], self.owner,
                        event.payload.get("flow")).start()


class ShuangxiongFlow(Flow):
    def __init__(self, engine, owner, turn_flow):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.turn_flow = turn_flow
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【双雄】：是否放弃摸牌并进行判定？",
                    reason="shuangxiong")
        return self.current_result()

    def advance(self, response=None):
        if self.stage != "confirm":
            raise RuntimeError("ShuangxiongFlow cannot advance from stage " + self.stage)
        if response is None or not response.confirmed:
            return self.complete({"applied": False})
        self.owner.skill_state.set("shuangxiong", "used", 1, ResetScope.TURN)
        control = getattr(self.turn_flow, "phase_control", None)
        if control is not None:
            control.skip(TurnPhase.DRAW)
        # 「获得此判定牌」由判定流程自己完成：card_recipient 取走的是**最终
        # 生效**的那张判定牌，被改判换掉的旧牌早已离开处理区。
        flow, result = judge(self.engine, self.owner, "shuangxiong",
                             card_recipient=lambda _result: self.owner)
        if result is None:
            flow.on_complete = self._after_judge
            self.wait(flow)
            return self.current_result()
        return self._after_judge(result)

    def _after_judge(self, result):
        card = getattr(result, "card", None)
        if card is not None:
            # 判定牌此刻已经到手（判定流程收尾时移入）。这里只记颜色：
            # 本回合的出牌阶段可以把**颜色与它不同**的手牌当【决斗】使用。
            self.owner.skill_state.set(
                "shuangxiong", "judge_color", getattr(card, "card_color", None),
                ResetScope.TURN)
            self.game.add_log("%s 发动【双雄】，获得判定牌 %s（本回合可将异色手牌当【决斗】）"
                              % (self.owner.name,
                                 getattr(card, "identity_label", "") or "?"))
        return self.complete({"applied": True})


# ---- 双雄的转化：异色手牌当【决斗】 ----
#
# 规则原文是"本回合可以将与判定结果**颜色**不同的一张手牌当【决斗】使用"，
# 所以谓词只看 card_color（红 / 黑），不是花色。牌由**玩家自己**通过统一的
# 视为技通道挑选（点技能 → 点合法手牌 → 选目标 → 确认），没有任何
# "自动拿第一张"的兜底。

def _shuangxiong_judge_color(player):
    """本回合【双雄】判定结果的颜色；还没判定过就是 None。"""

    return player.skill_state.get("shuangxiong", "judge_color", None)


def _shuangxiong_card_is_color(card):
    """任何手牌都能进候选，颜色条件交给 owner_matches（它看得见判定结果）。"""

    return True


def _shuangxiong_conversion_available(game, player):
    """本回合已经用【双雄】判定过，颜色已经定下来。"""

    if game.game_over or not getattr(player, "alive", False):
        return False
    if not player.skill_state.get("shuangxiong", "used", 0):
        return False
    return _shuangxiong_judge_color(player) is not None


def _shuangxiong_owner_matches(game, player, card):
    """这张牌的颜色与本次判定结果**不同**才算合法素材。"""

    judge_color = _shuangxiong_judge_color(player)
    if judge_color is None:
        return False
    card_color = getattr(card, "card_color", None)
    # 没有颜色的牌（理论上不存在于手牌）不算异色，避免和 None 比较时误判。
    return bool(card_color) and card_color != judge_color


# ==================================================
# 技能表
# ==================================================

FIRE_SKILLS = (
    active(
        "qiangxi",
        "强袭",
        "出牌阶段限一次，你可以自减 1 点体力或弃置一张武器牌，"
        "然后对你攻击范围内的一名其他角色造成 1 点伤害。",
        can_activate=_can_qiangxi,
        activate=_activate_qiangxi,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=lambda game, player: attack_range_targets(game, player),
            target_prompt="【强袭】：请选择攻击范围内的一名角色",
        ),
        tags=("active",),
    ),
    SkillDef(
        id="bazhen",
        name="八阵",
        description="锁定技，若你没有装备防具，你始终视为装备着【八卦阵】。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.VIRTUAL_ARMOR, value="BAGUA", roles=("player",)),
        ),
    ),
    SkillDef(
        id="huoji",
        name="火计",
        description="你可以将一张红色手牌当【火攻】使用。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(skill_id="huoji", matches=_is_red, name="HUOGONG",
                           category="trick", contexts=(PLAY_CONTEXT,)),
        ),
        tags=("conversion",),
    ),
    SkillDef(
        id="kanpo",
        name="看破",
        description="你可以将一张黑色手牌当【无懈可击】使用。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(skill_id="kanpo", matches=_is_black, name="WUXIE",
                           category="trick", contexts=(RESPONSE_CONTEXT,)),
        ),
        tags=("conversion",),
    ),
    active(
        "tianyi",
        "天义",
        "出牌阶段限一次，你可以与一名角色拼点：若你赢，本回合你的攻击范围无限、"
        "可以额外使用一张【杀】、使用【杀】时可以额外指定一个目标；"
        "若你没赢，本回合你不能使用【杀】。",
        can_activate=_can_tianyi,
        activate=_activate_tianyi,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_tianyi_targets,
            target_prompt="【天义】：请选择拼点的角色",
            # 拼点牌**不能**走 cost 通道。那条路是"先支付再结算"：牌先被弃掉，
            # 拼点流程随后还要再收一张 → 一次拼点掉两张牌；只剩一张时更糟，
            # 费用先被扣光、拼点根本成立不了，技能没发动而牌已经没了。
            # 拼点牌由拼点流程自己收集（与【制霸】的写法一致）。
        ),
        modifiers=(
            ModifierSpec(kind=ModifierKind.ATTACK_RANGE, value=98, roles=("player",),
                         condition=lambda game, query: _tianyi_won(game, query)),
            ModifierSpec(kind=ModifierKind.SLASH_QUOTA, value=1, roles=("player",),
                         condition=lambda game, query: _tianyi_won(game, query)),
            ModifierSpec(kind=ModifierKind.SLASH_TARGETS, value=1, roles=("player",),
                         condition=lambda game, query: _tianyi_won(game, query)),
            ModifierSpec(kind=ModifierKind.SLASH_FORBIDDEN, value=True, roles=("player",),
                         condition=lambda game, query: _tianyi_lost(game, query)),
        ),
        tags=("active", "pindian"),
    ),
    triggered(
        "mengjin",
        "猛进",
        "当你使用的【杀】被【闪】抵消时，你可以弃置对方的一张牌。",
        factory=Mengjin,
    ),
    SkillDef(
        id="lianhuan",
        name="连环",
        description="出牌阶段，你可以将一张梅花手牌当【铁索连环】使用。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(skill_id="lianhuan", matches=_is_club, name="TIESUO",
                           category="trick", contexts=(PLAY_CONTEXT,)),
        ),
        tags=("conversion",),
    ),
    triggered(
        "niepan",
        "涅槃",
        "限定技，当你处于濒死状态时，你可以弃置你所有的牌与判定区里的牌，"
        "重置你的武将牌，然后摸三张牌且体力回复至 3 点。",
        factory=Niepan,
        tags=("limited",),
    ),
    active(
        "quhu",
        "驱虎",
        "出牌阶段限一次，你可以与一名体力比你多的角色拼点：若你赢，"
        "该角色对其攻击范围内、由你指定的一名角色造成 1 点伤害；"
        "若你没赢，该角色对你造成 1 点伤害。",
        can_activate=_can_quhu,
        activate=_activate_quhu,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_quhu_targets,
            target_prompt="【驱虎】：请选择体力比你多的角色",
            # 同【天义】：拼点牌交给拼点流程收，见上面那段说明。
        ),
        tags=("active", "pindian"),
    ),
    triggered(
        "jieming",
        "节命",
        "当你受到 1 点伤害后，你可以令一名角色将手牌补至其体力上限的张数（至多五张）。",
        factory=Jieming,
    ),
    active(
        "luanji",
        "乱击",
        "出牌阶段，你可以将两张相同花色的手牌当【万箭齐发】使用。",
        can_activate=_can_luanji,
        activate=_activate_luanji,
        spec=ActiveSkillSpec(
            # 两张牌是转化素材，不是费用：去向是"被当作【万箭齐发】使用"，
            # 所以按素材处理，由技能自己的结算负责移动。
            cost_cards=2,
            keep_cards=True,
            cost_prompt="【乱击】：请选择两张花色相同的手牌",
        ),
        tags=("active", "forced_use"),
    ),
    SkillDef(
        id="xueyi",
        name="血裔",
        description="主公技，锁定技，场上每有一名其他群势力角色存活，你的手牌上限便 +2。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.HAND_LIMIT, value=_xueyi_bonus,
                         roles=("player",)),
        ),
        is_lord_skill=True,
    ),
    SkillDef(
        id="shuangxiong",
        name="双雄",
        description="摸牌阶段，你可以放弃摸牌并进行一次判定：你获得此判定牌，"
        "且于此回合的出牌阶段，你可以将一张与此判定牌颜色不同的手牌当【决斗】使用。",
        # 摸牌阶段的"是否判定"由 factory 的触发技负责；出牌阶段的转化是
        # 视为技（点技能 → 自己挑合法手牌 → 选目标），两条路共用同一个
        # SkillDef，所以 kind 取 VIEW_AS：它决定玩家从哪里进得来。
        kind=SkillKind.VIEW_AS,
        factory=Shuangxiong,
        conversions=(
            CardConversion(
                skill_id="shuangxiong",
                matches=_shuangxiong_card_is_color,
                owner_matches=_shuangxiong_owner_matches,
                name="JUEDOU",
                category="trick",
                contexts=(PLAY_CONTEXT,),
                available=_shuangxiong_conversion_available,
            ),
        ),
        tags=("view_as", "conversion", "draw_phase"),
    ),
)
