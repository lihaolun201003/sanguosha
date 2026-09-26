"""神将技能：关羽 / 司马懿 / 吕布 / 吕蒙 / 周瑜 / 曹操 / 赵云。

神诸葛亮的【七星】需要"游戏开始时共发十一张牌并从中选四张"的开局钩子，
当前引擎没有这个时机（发牌与绑定武将的先后顺序无法在技能层安全插入），
因此神诸葛亮在 ``generals/expansions.py`` 里标为不可开局，而不是拿一个
"第一回合开始时再补"的近似实现冒充它。
"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom, UnequipAtom
from src.game.conversion import (
    PLAY_CONTEXT,
    RESPONSE_CONTEXT,
    CardConversion,
)
from src.game.engine import EventType, Flow
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..definitions import (
    ActiveSkillSpec,
    ModifierSpec,
    PhaseReplacement,
    SkillDef,
    SkillKind,
    active,
    triggered,
)
from ..mechanics import (
    add_mark,
    ask_cards,
    ask_confirm,
    ask_option,
    ask_targets,
    awaken,
    flip_player,
    gain_skill,
    hand_cards,
    judge,
    limited_used,
    lose_hp,
    mark_count,
    other_alive_players,
    remove_mark,
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


def _is_heart(card):
    return not getattr(card, "is_virtual", False) and getattr(card, "suit", None) == "heart"


def _is_diamond(card):
    return not getattr(card, "is_virtual", False) and getattr(card, "suit", None) == "diamond"


def _is_club(card):
    return not getattr(card, "is_virtual", False) and getattr(card, "suit", None) == "club"


def _is_spade(card):
    return not getattr(card, "is_virtual", False) and getattr(card, "suit", None) == "spade"


def _is_non_delay_trick(card):
    return (getattr(card, "category", None) == "trick"
            and getattr(card, "name", None) not in ("LEBU", "BINGLIANG", "SHANDIAN"))


# ==================================================
# 神关羽 · 武神 / 武魂
# ==================================================


def _wushen_ignores_distance(game, query):
    """武神：用**红桃**【杀】无距离限制。

    官方是"你使用红桃【杀】无距离限制"——所以判据是"这张杀是不是红桃"，
    而不是"它是不是武神转化出来的"：牌堆里真实存在的红桃【杀】（一副牌 6 张）
    走普通出牌路径时同样该享受，之前它们被距离卡住。
    """

    card = query.get("card")
    if card is None:
        return False
    if getattr(card, "skill_id", "") == "wushen":
        return True
    if str(getattr(card, "name", "") or "") != "SHA":
        return False
    return getattr(card, "suit", None) == "heart"


class Wuhun(Skill):
    """锁定技：每对你造成 1 点伤害的角色获得一个梦魇标记；
    你死亡时，持有最多梦魇标记的角色判定，不为【桃】或【桃园结义】则其立即死亡。"""

    id = "wuhun"
    name = "武魂"

    def bindings(self):
        return (
            SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=5),
            SkillBinding(EventType.DEATH, priority=-40),
        )

    def can_trigger(self, context, event):
        if event.name is EventType.DAMAGE_TARGET_AFTER:
            damage = event.payload.get("damage")
            if damage is None or damage.target is not self.owner:
                return False
            source = getattr(damage, "source", None)
            return (source is not None and source is not self.owner
                    and int(event.payload.get("amount", 0) or 0) > 0)
        return event.target is self.owner

    def resolve(self, context, event):
        game = context.state
        if event.name is EventType.DAMAGE_TARGET_AFTER:
            damage = event.payload["damage"]
            amount = int(event.payload.get("amount", 0) or 0)
            add_mark(game, damage.source, self.id, amount, "nightmare", log_name="梦魇")
            return
        WuhunFlow(context.services["engine"], self.owner).start()


class WuhunFlow(Flow):
    """武魂：你死亡时，梦魇标记最多的角色判定，非【桃】/【桃园结义】则立即死亡。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "judge"

    def is_noop(self):
        return self._worst() is None

    def _worst(self):
        candidates = [
            player for player in self.game.players
            if player is not self.owner and mark_count(player, "wuhun", "nightmare") > 0
        ]
        if not candidates:
            return None
        best = max(mark_count(player, "wuhun", "nightmare") for player in candidates)
        return next(player for player in candidates
                    if mark_count(player, "wuhun", "nightmare") == best)

    def begin(self):
        if self.is_noop():
            return self.complete({"applied": False})
        return self._begin_judge()

    def _begin_judge(self):
        target = self._worst()
        flow, result = judge(self.engine, target, "wuhun")
        if result is None:
            flow.on_complete = self._after_judge
            self.wait(flow)
            return self.current_result()
        return self._after_judge(result)

    def advance(self, response=None):
        return self.complete({"applied": True})

    def _after_judge(self, result):
        game = self.game
        target = self._worst()
        if target is None or result is None:
            return self.complete({"applied": False})
        card = getattr(result, "card", None)
        name = getattr(card, "name", None) if card is not None else None
        label = getattr(result, "identity_label", "") or "?"
        if name in ("TAO", "TAOYUAN"):
            game.add_log("【武魂】判定为【%s】，%s 免于死亡"
                         % (getattr(card, "display_name", "?"), target.name))
            return self.complete({"applied": False})
        game.add_log("【武魂】判定为 %s，%s 立即死亡" % (label, target.name))
        target.hp = 0
        self.engine.run_death(target, source=self.owner, cause=None)
        return self.complete({"applied": True})


# ==================================================
# 神司马懿 · 忍戒 / 拜印 / 连破 / 极略
# ==================================================


class Renjie(Skill):
    """锁定技：受到伤害后或于弃牌阶段弃牌后，获得等量的「忍」标记。"""

    id = "renjie"
    name = "忍戒"

    def bindings(self):
        return (
            SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=5),
            SkillBinding(EventType.CARD_DISCARDED, priority=5),
        )

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        if event.name is EventType.DAMAGE_TARGET_AFTER:
            damage = event.payload.get("damage")
            return (damage is not None and damage.target is self.owner
                    and int(event.payload.get("amount", 0) or 0) > 0)
        if event.payload.get("owner") is not self.owner:
            return False
        return (context.state.current_turn_player is self.owner
                and context.state.phase == "discard")

    def resolve(self, context, event):
        game = context.state
        if event.name is EventType.DAMAGE_TARGET_AFTER:
            amount = int(event.payload.get("amount", 0) or 0)
        else:
            amount = 1
        add_mark(game, self.owner, self.id, amount, "ren", log_name="忍")


class Baiyin(Skill):
    """觉醒技：准备阶段若你有 4 枚或更多「忍」标记，减 1 点体力上限并获得【极略】。"""

    id = "baiyin"
    name = "拜印"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=55),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.PREPARE:
            return False
        if limited_used(self.owner, self.id):
            return False
        return mark_count(self.owner, "renjie", "ren") >= 4

    def resolve(self, context, event):
        awaken(context.state, self.owner, self.id, max_hp_delta=-1,
               gain=("jilue",), name="拜印")


#: 极略可以发动的技能（卡面明列的五个）。
JILUE_SKILLS = (
    ("guicai", "鬼才（改判）"),
    ("fangzhu", "放逐"),
    ("wansha", "完杀"),
    ("jizhi", "集智"),
    ("zhiheng", "制衡"),
)


def _can_jilue(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if mark_count(player, "renjie", "ren") < 1:
        return False, "没有「忍」标记"
    if not game.skills.has(player, "jilue"):
        return False, "还没有获得【极略】"
    return True, ""


def _activate_jilue(game, player, target=None, cards=None):
    """极略：弃一枚「忍」标记，发动下列一项技能（临时获得直到回合结束）。"""

    if mark_count(player, "renjie", "ren") < 1:
        return False
    remove_mark(game, player, "renjie", 1, "ren")
    JilueFlow(game.engine, player).start()
    return True


class JilueFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "choose"

    def begin(self):
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【极略】：请选择要发动的技能", reason="jilue",
                   options=JILUE_SKILLS)
        return self.current_result()

    def advance(self, response=None):
        option = str(getattr(response, "option", "") or "")
        if option not in [value for value, _label in JILUE_SKILLS]:
            return self.complete({"applied": False})
        # 「发动下列一项技能」= 本回合内拥有它：获得用通用绑定入口，
        # 回合结束时由 TurnEndCleanup 统一撤掉，不留常驻监听。
        gain_skill(self.game, self.owner, option)
        temporary = self.owner.skill_state.get("jilue", "temporary", None)
        temporary = list(temporary or [])
        if option not in temporary:
            temporary.append(option)
        self.owner.skill_state.set("jilue", "temporary", tuple(temporary),
                                   ResetScope.TURN)
        self.game.add_log("%s 的【极略】发动了【%s】"
                          % (self.owner.name,
                             self.game.skill_registry.get(option).name))
        return self.complete({"applied": True})


class JilueCleanup(Skill):
    """回合结束时撤掉【极略】临时获得的技能。

    ``id`` 与 SkillDef 的 ``jilue`` 一致——``SkillManager.unbind`` 按实例的
    ``id`` 匹配，不一致就卸载不掉。
    """

    id = "jilue"
    name = "极略"

    def bindings(self):
        return (SkillBinding(EventType.TURN_END, priority=-80),)

    def can_trigger(self, context, event):
        return event.source is self.owner and bool(
            self.owner.skill_state.get("jilue", "temporary", None))

    def resolve(self, context, event):
        game = context.state
        for skill_id in list(self.owner.skill_state.get("jilue", "temporary", ()) or ()):
            if skill_id == "jilue":
                continue
            if game.skills.has(self.owner, skill_id):
                game.skills.unbind(self.owner, skill_id)
        self.owner.skill_state.clear("jilue", "temporary")


class Lianpo(Skill):
    """一名角色的回合结束后，若你于此回合内杀死过至少一名角色，
    你可以进行一个额外的回合。"""

    id = "lianpo"
    name = "连破"

    def bindings(self):
        return (
            SkillBinding(EventType.DEATH, priority=-70),
            SkillBinding(EventType.TURN_END, priority=-70),
        )

    def can_trigger(self, context, event):
        if event.name is EventType.DEATH:
            return event.source is self.owner and self.owner.alive
        if not self.owner.alive:
            return False
        return bool(self.owner.skill_state.get(self.id, "killed", 0))

    def resolve(self, context, event):
        game = context.state
        if event.name is EventType.DEATH:
            self.owner.skill_state.set(self.id, "killed", 1, ResetScope.ROUND)
            return
        self.owner.skill_state.set(self.id, "killed", 0, ResetScope.ROUND)
        # 官方：「一名角色的回合结束时，若你本回合杀死过角色，你可以执行一个
        # 额外回合」——**自己的回合结束时同样成立**（FAQ：本回合内击杀 → 该回合
        # 结束后立刻再来一个）。这里曾经把"自己回合内击杀"整条 return 掉，
        # 等于把连破的滚雪球核心砍掉，只剩回合外击杀能用。
        if not self.owner.alive or game.game_over:
            return
        game.queue_extra_turn(self.owner)
        game.add_log("%s 的【连破】获得一个额外回合" % self.owner.name)


# ==================================================
# 神吕布 · 狂暴 / 无谋 / 无前 / 神愤
# ==================================================

RAGE = "rage"


class Kuangbao(Skill):
    """锁定技：游戏开始时获得 2 个暴怒标记；**造成或受到** 1 点伤害各得 1 个。"""

    id = "kuangbao"
    name = "狂暴"

    def bindings(self):
        return (
            SkillBinding(EventType.PHASE_START, priority=65),
            SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=5),
            # 官方是"造成**或**受到 1 点伤害后"：造成伤害那条挂在来源侧。
            SkillBinding(EventType.DAMAGE_SOURCE_AFTER, priority=5),
        )

    def can_trigger(self, context, event):
        if event.name is EventType.PHASE_START:
            if event.source is not self.owner:
                return False
            if event.payload.get("phase") is not TurnPhase.PREPARE:
                return False
            return not self.owner.skill_state.get(self.id, "started", 0)
        damage = event.payload.get("damage")
        if damage is None or not self.owner.alive:
            return False
        if event.name is EventType.DAMAGE_SOURCE_AFTER:
            if damage.source is not self.owner:
                return False
        elif damage.target is not self.owner:
            return False
        return int(event.payload.get("amount", 0) or 0) > 0

    def resolve(self, context, event):
        if event.name is EventType.PHASE_START:
            # 「游戏开始时获得 2 个暴怒标记」：写在**绑定技能的那一刻**会
            # 让"重开后技能状态为空"这条不变量失去意义（绑定本身就是开局的
            # 一部分），因此改在持有者的第一个回合开始阶段发放。暴怒标记
            # 只在神吕布自己的出牌阶段被消费，两种时机的实际效果完全一致，
            # 而这样也保证技能状态不会在 bind 阶段就被写入。
            self.owner.skill_state.set(self.id, "started", 1)
            add_mark(context.state, self.owner, self.id, 2, RAGE, log_name="暴怒")
            return
        amount = int(event.payload.get("amount", 0) or 0)
        add_mark(context.state, self.owner, self.id, amount, RAGE, log_name="暴怒")


class Wumou(Skill):
    """锁定技：每使用一张非延时锦囊（结算前），弃 1 个暴怒标记或失去 1 点体力。"""

    id = "wumou"
    name = "无谋"

    def bindings(self):
        return (SkillBinding(EventType.CARD_USE_BEFORE, priority=40),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        return _is_non_delay_trick(event.payload.get("card"))

    def resolve(self, context, event):
        WumouFlow(context.services["engine"], self.owner).start()


class WumouFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "choose"

    def begin(self):
        options = []
        if mark_count(self.owner, "kuangbao", RAGE) >= 1:
            options.append(("mark", "弃 1 个暴怒标记"))
        # 官方是"弃 1 个暴怒标记，**或**失去 1 点体力"：没有标记时必须失去
        # 体力（1 体力也要失去，会进濒死），不能因为"会死"就整个跳过。
        options.append(("hp", "失去 1 点体力"))
        if not options:                                   # 理论上到不了
            return self.complete({"applied": False})
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【无谋】：请选择支付方式", reason="wumou",
                   options=tuple(options))
        return self.current_result()

    def advance(self, response=None):
        option = str(getattr(response, "option", "") or "")
        if option == "hp":
            lose_hp(self.game, self.owner, 1, reason="无谋")
        elif mark_count(self.owner, "kuangbao", RAGE) >= 1:
            remove_mark(self.game, self.owner, "kuangbao", 1, RAGE)
        else:
            lose_hp(self.game, self.owner, 1, reason="无谋")
        return self.complete({"applied": True})


def _can_wuqian(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if mark_count(player, "kuangbao", RAGE) < 2:
        return False, "需要 2 个暴怒标记"
    if not other_alive_players(game, player):
        return False, "没有其他角色"
    return True, ""


def _activate_wuqian(game, player, target=None, cards=None):
    if target is None:
        return False
    remove_mark(game, player, "kuangbao", 2, RAGE)
    player.skill_state.set("wuqian", "target_id", id(target), ResetScope.TURN)
    gain_skill(game, player, "wushuang")
    game.add_log("%s 发动【无前】→ %s：其防具无效，且获得【无双】直到回合结束"
                 % (player.name, target.name))
    return True


class Wuqian(Skill):
    """无前：两件事绑在同一个技能实例上——临时【无双】的回收，以及
    "指定角色防具无效"的伤害修正。

    工厂类的 ``id`` 必须与 SkillDef 的 ``id`` 一致：``SkillManager.unbind``
    按 ``instance.id`` 匹配，对不上就永远卸载不掉（监听会留在事件总线上）。
    """

    id = "wuqian"
    name = "无前"

    def bindings(self):
        return (
            SkillBinding(EventType.TURN_END, priority=-80),
            SkillBinding(EventType.DAMAGE_MODIFY, priority=60),
        )

    def can_trigger(self, context, event):
        if event.name is EventType.TURN_END:
            return (event.source is self.owner
                    and self.owner.skill_state.get("wuqian", "target_id", None) is not None)
        damage = event.payload.get("damage")
        if damage is None or damage.source is not self.owner:
            return False
        if self.owner.skill_state.get("wuqian", "target_id", 0) != id(damage.target):
            return False
        return context.state.armor_card(damage.target) is not None

    def resolve(self, context, event):
        game = context.state
        if event.name is EventType.DAMAGE_MODIFY:
            damage = event.payload["damage"]
            damage.ignore_armor = True
            damage.effects.append("【无前】防具无效")
            return
        # 回合结束：撤掉【无前】临时给的【无双】。原本就会【无双】的形态
        # （SP008 吕布）不能被误删，按"是不是本体的武将技能"判断。
        general = game.generals.get(getattr(self.owner, "general_id", None))
        if (game.skills.has(self.owner, "wushuang")
                and "wushuang" not in (getattr(general, "skill_ids", ()) or ())):
            game.skills.unbind(self.owner, "wushuang")
        self.owner.skill_state.clear("wuqian", "target_id")


def _can_shenfen(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("shenfen", "used", 0):
        return False, "本回合已经发动过"
    if mark_count(player, "kuangbao", RAGE) < 6:
        return False, "需要 6 个暴怒标记"
    return True, ""


def _activate_shenfen(game, player, target=None, cards=None):
    """神愤：弃 6 个暴怒标记，对每名其他角色各造成 1 点伤害，
    其他角色先弃置装备区所有牌、再各弃四张手牌，然后将你翻面。"""

    remove_mark(game, player, "kuangbao", 6, RAGE)
    player.skill_state.set("shenfen", "used", 1, ResetScope.TURN)
    targets = [other for other in game.seats.alive_players_in_order(start_after=player)
               if other is not player]
    ShenfenFlow(game.engine, player, targets).start()
    return True


class ShenfenFlow(Flow):
    """神愤的结算：伤害（连同一切受伤触发技）走完 → 弃装备 → 弃四张手牌 → 翻面。

    伤害是**异步**的：受伤方可能触发【遗计】【刚烈】【反馈】，这些技能自己
    也会摸牌、造成伤害。以前是"发起伤害之后立刻弃牌翻面"，那些技能刚拿到
    的牌转眼就被弃掉，连结算顺序都乱了。这里用子流程守卫，等伤害彻底结束
    （含它引发的全部技能）再往下走。
    """

    def __init__(self, engine, player, targets):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.player = player
        self.targets = list(targets)

    def begin(self):
        from src.game.flows.chain_damage import ChainDamageFlow

        if self.targets:
            ChainDamageFlow(
                self.engine, source=self.player, card=None, nature="normal",
                amount=1, targets=self.targets).start()
        return self.advance()

    def advance(self, response=None):
        guard = self.guard_child_flows()
        if guard is not None:
            return guard
        return self._discard_and_flip()

    def _discard_and_flip(self):
        game = self.game
        for other in self.targets:
            for slot in list(other.equipment):
                if other.get_equipment(slot) is not None:
                    self.context.apply(UnequipAtom(
                        other, slot, game.deck.discard_pile))
            for _ in range(4):
                hand = list(getattr(other, "hand", ()) or ())
                if not hand:
                    break
                self.context.apply(MoveCardAtom(
                    hand[-1], source=other.hand, destination=game.deck.discard_pile))
        flip_player(game, self.player, reason="神愤")
        game.add_log("%s 的【神愤】结算完毕：对 %d 名角色造成伤害、弃牌后翻面"
                     % (self.player.name, len(self.targets)))
        return self.complete({"applied": True})


# ==================================================
# 神吕蒙 · 涉猎 / 攻心
# ==================================================


def _can_shelie(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    return len(game.deck.draw_pile) >= 1


def _apply_shelie(game, player):
    """涉猎：放弃摸牌，亮出牌堆顶五张，拿走不同花色的各一张，其余弃掉。"""

    count = min(5, len(game.deck.draw_pile))
    revealed = []
    for _ in range(count):
        card = game.deck.draw()
        if card is None:
            break
        revealed.append(card)
    taken, seen_suits = [], set()
    for card in revealed:
        suit = getattr(card, "suit", None)
        if suit in seen_suits:
            continue
        seen_suits.add(suit)
        taken.append(card)
    for card in revealed:
        if any(card is item for item in taken):
            player.hand.append(card)
        else:
            game.deck.discard(card)
    names = "、".join((getattr(card, "identity_label", "") or "?") for card in revealed)
    game.add_log("%s 发动【涉猎】，亮出 %s，取走 %d 张"
                 % (player.name, names, len(taken)))
    game.message = "%s 的【涉猎】拿走了 %d 张不同花色的牌。" % (player.name, len(taken))
    return True


def _can_gongxin(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("gongxin", "used", 0):
        return False, "本阶段已经发动过"        # 官方：出牌阶段限一次
    if not [other for other in other_alive_players(game, player)
            if getattr(other, "hand", ())]:
        return False, "没有手牌不为空的其他角色"
    return True, ""


def _activate_gongxin(game, player, target=None, cards=None):
    if target is None or not target.hand:
        return False
    player.skill_state.set("gongxin", "used", 1, ResetScope.PHASE)
    GongxinFlow(game.engine, player, target).start()
    return True


class GongxinFlow(Flow):
    """攻心：观看目标手牌，展示其中一张红桃牌，弃掉它或将它置于牌堆顶。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.stage = "reveal"

    def begin(self):
        self.game.add_log("%s 发动【攻心】，观看 %s 的手牌"
                          % (self.owner.name, self.target.name))
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【攻心】：请选择展示 %s 的一张红桃牌" % self.target.name,
                  reason="gongxin",
                  candidates=hand_cards(self.target, _is_heart),
                  min_cards=0, max_cards=1, zone="public_pool",
                  context={"zone_owner": self.target})
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "reveal":
            return self._after_reveal(response)
        return self._after_action(response)

    def _after_reveal(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self.complete({"applied": True})
        self.card = cards[0]
        self.stage = "action"
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【攻心】：请选择处理这张红桃牌的方式",
                   reason="gongxin",
                   options=(("discard", "弃置它"), ("top", "置于牌堆顶")))
        return self.current_result()

    def _after_action(self, response):
        option = str(getattr(response, "option", "") or "discard")
        if any(item is self.card for item in self.target.hand):
            if option == "top":
                self.context.apply(MoveCardAtom(
                    self.card, source=self.target.hand,
                    destination=self.game.deck.draw_pile))
                self.game.add_log("【攻心】将这张牌置于牌堆顶")
            else:
                self.context.apply(MoveCardAtom(
                    self.card, source=self.target.hand,
                    destination=self.game.deck.discard_pile))
                self.game.add_log("【攻心】弃置了这张牌")
        return self.complete({"applied": True})


# ==================================================
# 神周瑜 · 琴音 / 业炎
# ==================================================


class Qinyin(Skill):
    """弃牌阶段，当你弃置两张或更多手牌时，可以令所有角色各回复 1 点体力
    或各失去 1 点体力。"""

    id = "qinyin"
    name = "琴音"

    def bindings(self):
        return (
            SkillBinding(EventType.CARD_DISCARDED, priority=5),
            SkillBinding(EventType.PHASE_END, priority=-15),
        )

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        if event.name is EventType.CARD_DISCARDED:
            if event.payload.get("owner") is not self.owner:
                return False
            return (context.state.current_turn_player is self.owner
                    and context.state.phase == "discard")
        if event.payload.get("phase") is not TurnPhase.DISCARD:
            return False
        return event.source is self.owner and int(
            self.owner.skill_state.get(self.id, "discarded", 0) or 0) >= 2

    def resolve(self, context, event):
        if event.name is EventType.CARD_DISCARDED:
            self.owner.skill_state.add(self.id, "discarded", 1, ResetScope.TURN)
            return
        QinyinFlow(context.services["engine"], self.owner).start()


class QinyinFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "choose"

    def begin(self):
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【琴音】：请选择一项", reason="qinyin",
                   options=(("heal", "所有角色各回复 1 点体力"),
                            ("lose", "所有角色各失去 1 点体力"),
                            ("none", "不发动")))
        return self.current_result()

    def advance(self, response=None):
        option = str(getattr(response, "option", "") or "none")
        if option == "none":
            return self.complete({"applied": False})
        for player in list(self.game.get_alive_players()):
            if not player.alive:
                continue
            if option == "heal":
                self.context.apply(RecoverHpAtom(player, 1))
            else:
                lose_hp(self.game, player, 1, source=self.owner, reason="琴音")
        self.game.add_log("%s 的【琴音】令所有角色各%s 1 点体力"
                          % (self.owner.name, "回复" if option == "heal" else "失去"))
        return self.complete({"applied": True})


def _can_yeyan(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if limited_used(player, "yeyan"):
        return False, "限定技已经发动过"
    if not other_alive_players(game, player):
        return False, "没有其他角色"
    return True, ""


def _activate_yeyan(game, player, target=None, cards=None):
    YeyanFlow(game.engine, player).start()
    return True


class YeyanFlow(Flow):
    """业炎：选 1~3 名角色并分配合计至多 3 点火焰伤害。

    对同一角色分配 2 点或更多时，须先弃置四张不同花色的手牌并失去 3 点体力。
    分配逐点进行：每次询问"这 1 点给谁"，分配完 3 点或主动结束后结算。
    """

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.allocation = {}
        self.remaining = 3
        self.stage = "pick"

    def begin(self):
        return self._ask()

    def _ask(self):
        candidates = [
            other for other in other_alive_players(self.game, self.owner)
            if self.allocation.get(id(other), 0) < 3
        ]
        if not candidates or self.remaining <= 0:
            return self._resolve()
        self.stage = "pick"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【业炎】：还可以分配 %d 点火焰伤害，请选择目标"
                           % self.remaining,
                    reason="yeyan", candidates=candidates,
                    min_targets=1, max_targets=1,
                    # 已经分配出去的点数：AI 靠它把伤害摊到不同角色身上
                    # （全压在一个人身上要弃四张不同花色的手牌并失去 3 点体力）。
                    context={
                        "remaining": self.remaining,
                        "allocation": {
                            str(player.player_id): self.allocation.get(id(player), 0)
                            for player in other_alive_players(self.game, self.owner)
                        },
                    })
        return self.current_result()

    def advance(self, response=None):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self._resolve()
        target = targets[0]
        self.allocation[id(target)] = self.allocation.get(id(target), 0) + 1
        self.remaining -= 1
        return self._ask()

    def _resolve(self):
        game = self.game
        if not self.allocation:
            return self.complete({"applied": False})
        heavy = [player for player in other_alive_players(game, self.owner)
                 if self.allocation.get(id(player), 0) >= 2]
        if heavy:
            hearts = {getattr(card, "suit", None) for card in hand_cards(self.owner)}
            needed = 4
            # 官方只要求"弃四张不同花色的手牌并失去 3 点体力"，**没有**"必须
            # 有 4 点以上体力"这一条：1 体力时也可以烧到濒死。
            if len(hearts) < needed:
                game.message = "【业炎】：无法支付重额分配的代价（需要四张不同花色的手牌）。"
                return self.complete({"applied": False})
            paid = []
            used_suits = set()
            for card in hand_cards(self.owner):
                suit = getattr(card, "suit", None)
                if suit in used_suits:
                    continue
                used_suits.add(suit)
                paid.append(card)
                if len(paid) == needed:
                    break
            for card in paid:
                self.context.apply(MoveCardAtom(
                    card, source=self.owner.hand,
                    destination=game.deck.discard_pile))
            lose_hp(game, self.owner, 3, reason="业炎")
        from ..mechanics import consume_limited
        from src.game.flows.damage import DamageContext, DamageFlow

        consume_limited(game, self.owner, "yeyan", note="业炎")
        for player in other_alive_players(game, self.owner):
            amount = self.allocation.get(id(player), 0)
            if amount <= 0:
                continue
            DamageFlow(self.engine, DamageContext(
                self.owner, player, amount, nature="fire")).start()
        game.add_log("%s 发动限定技【业炎】" % self.owner.name)
        return self.complete({"applied": True})


# ==================================================
# 神曹操 · 归心 / 飞影
# ==================================================


class Guixin(Skill):
    """每受到 1 点伤害，可以分别从每名其他角色的手牌、装备区与判定区
    各获得一张牌；若如此做，将你的武将牌翻面。"""

    id = "guixin"
    name = "归心"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=15),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        if int(event.payload.get("amount", 0) or 0) <= 0 or not self.owner.alive:
            return False
        return any(_cards_of(other) for other in other_alive_players(context.state, self.owner))

    def repeat_times(self, context, event):
        """官方：每受到 **1 点**伤害就可以发动一次（2 点伤害 = 发动 2 次）。

        引擎的技能回调一次事件只调用一次 resolve，所以这里把"点数"换算成
        需要重复的次数，由 resolve 自己循环——否则 2 点伤害只能拿一半的牌，
        而且必然翻面（跳过自己的下个回合），两个方向都吃亏。
        """

        return max(1, int(event.payload.get("amount", 0) or 0))

    def resolve(self, context, event):
        engine = context.services["engine"]
        game = context.state
        # 每 1 点伤害各发动一次（2 点 = 拿两轮牌、翻两次面 = 回到正面）。
        for _ in range(self.repeat_times(context, event)):
            if not self.owner.alive or game.game_over:
                break
            GuixinFlow(engine, self.owner).start()


class GuixinFlow(Flow):
    """归心：逐名角色各获一张牌（装备区 / 判定区的牌由规则指定，手牌由对方给）。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.order = []
        self.index = 0
        self.stage = "run"

    def begin(self):
        self.order = other_alive_players(self.game, self.owner)
        self.index = 0
        seen = False
        for player in self.order:
            if self._take_one(player):
                seen = True
        if seen:
            flip_player(self.game, self.owner, reason="归心")
            self.game.add_log("%s 发动【归心】，获得若干牌并翻面" % self.owner.name)
        return self.complete({"applied": seen})

    def advance(self, response=None):
        return self.complete({"applied": True})

    def _take_one(self, player):
        """从这名角色的公开区域拿一张牌（手牌内容不可读，按最后一张取）。"""

        for slot, equipped in list((player.equipment or {}).items()):
            if equipped is not None:
                self.context.apply(UnequipAtom(player, slot))
                self.owner.hand.append(equipped)
                return True
        if getattr(player, "judgement_zone", None):
            card = player.judgement_zone[-1]
            self.context.apply(MoveCardAtom(
                card, source=player.judgement_zone, destination=self.owner.hand))
            return True
        if getattr(player, "hand", None):
            card = player.hand[-1]
            self.context.apply(MoveCardAtom(
                card, source=player.hand, destination=self.owner.hand))
            return True
        return False


# ==================================================
# 神赵云 · 绝境 / 龙魂
# ==================================================


def lost_hp_safe(player):
    return max(0, int(getattr(player, "max_hp", 0)) - int(getattr(player, "hp", 0)))


def _longhun_count(game, owner):
    """龙魂：X = 当前体力值，且至少为 1。"""

    return max(1, int(getattr(owner, "hp", 1)))


# ==================================================
# 技能表
# ==================================================

GOD_SKILLS = (
    SkillDef(
        id="wushen",
        name="武神",
        description="锁定技，你的红桃手牌均视为【杀】；你使用红桃【杀】无距离限制。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(skill_id="wushen", matches=_is_heart, name="SHA",
                           contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT)),
        ),
        modifiers=(
            ModifierSpec(kind=ModifierKind.SLASH_DISTANCE_IGNORE, value=True,
                         roles=("player",), condition=_wushen_ignores_distance),
        ),
        tags=("conversion",),
    ),
    triggered(
        "wuhun",
        "武魂",
        "锁定技，每名角色每对你造成 1 点伤害就获得一个梦魇标记；"
        "你死亡时，持有最多梦魇标记的角色进行判定，"
        "若结果不为【桃】或【桃园结义】，该角色立即死亡。",
        factory=Wuhun,
        kind=SkillKind.LOCKED,
    ),
    triggered(
        "renjie",
        "忍戒",
        "锁定技，当你受到伤害后，或于弃牌阶段弃牌后，"
        "你获得等同于受到伤害或弃置牌数量的「忍」标记。",
        factory=Renjie,
        kind=SkillKind.LOCKED,
    ),
    triggered(
        "baiyin",
        "拜印",
        "觉醒技，准备阶段若你拥有 4 枚或更多的「忍」标记，"
        "你减 1 点体力上限，然后获得技能【极略】。",
        factory=Baiyin,
        tags=("awakening",),
    ),
    SkillDef(
        id="jilue",
        name="极略",
        description="你可以弃一枚「忍」标记，发动下列一项技能："
        "【鬼才】【放逐】【完杀】【集智】【制衡】。",
        kind=SkillKind.ACTIVE,
        factory=JilueCleanup,
        can_activate=_can_jilue,
        activate=_activate_jilue,
        active_spec=ActiveSkillSpec(),
        tags=("active", "granted"),
    ),
    triggered(
        "lianpo",
        "连破",
        "一名角色的回合结束后，若你于此回合内杀死过至少一名角色，"
        "你可以于此回合结束后进行一个额外的回合。",
        factory=Lianpo,
    ),
    triggered(
        "kuangbao",
        "狂暴",
        "锁定技，游戏开始时，你获得 2 个暴怒标记；你每受到 1 点伤害，获得 1 个暴怒标记。",
        factory=Kuangbao,
        kind=SkillKind.LOCKED,
    ),
    triggered(
        "wumou",
        "无谋",
        "锁定技，你每使用一张非延时类锦囊（在它结算前），"
        "弃掉 1 个暴怒标记或失去 1 点体力。",
        factory=Wumou,
        kind=SkillKind.LOCKED,
    ),
    SkillDef(
        id="wuqian",
        name="无前",
        description="出牌阶段，你可以弃 2 个暴怒标记并选择一名角色："
        "该角色防具无效，且你获得【无双】直到回合结束。",
        kind=SkillKind.ACTIVE,
        factory=Wuqian,
        can_activate=_can_wuqian,
        activate=_activate_wuqian,
        active_spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=lambda game, player: other_alive_players(game, player),
            target_prompt="【无前】：请选择防具无效的角色",
        ),
        modifiers=(
            ModifierSpec(kind=ModifierKind.RESERVED_NOOP, value=0),
        ) if False else (),
        tags=("active",),
    ),
    active(
        "shenfen",
        "神愤",
        "出牌阶段限一次，弃 6 个暴怒标记：你对每名其他角色各造成 1 点伤害，"
        "其他角色先弃掉各自装备区里所有的牌、再各弃四张手牌，"
        "然后将你的武将牌翻面。",
        can_activate=_can_shenfen,
        activate=_activate_shenfen,
        spec=ActiveSkillSpec(),
        # big_play：一次性消耗大、收益面的主动技。AI 会优先考虑它——否则
        # 神吕布会把标记零散花在无前上，永远攒不到 6 枚放神愤。
        tags=("active", "big_play"),
    ),
    SkillDef(
        id="shelie",
        name="涉猎",
        description="摸牌阶段，你可以选择采取以下行动来取代摸牌："
        "从牌堆顶亮出五张牌，拿走不同花色的各一张，弃掉其余的。",
        kind=SkillKind.PASSIVE,
        phase_replacement=PhaseReplacement(
            phase=TurnPhase.DRAW,
            prompt="【涉猎】：是否放弃摸牌，改为亮出牌堆顶五张牌？",
            can_offer=_can_shelie,
            apply=_apply_shelie,
        ),
    ),
    active(
        "gongxin",
        "攻心",
        "出牌阶段，你可以观看一名其他角色的手牌，"
        "并可以展示其中一张红桃牌，然后弃掉它或将它置于牌堆顶。",
        can_activate=_can_gongxin,
        activate=_activate_gongxin,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=lambda game, player: [
                other for other in other_alive_players(game, player)
                if getattr(other, "hand", ())],
            target_prompt="【攻心】：请选择观看手牌的角色",
        ),
        # 攻心的交互（选一张红桃牌 → 二选一处理）全部走 PendingRequest
        # （ask_cards / ask_option），单机真人、远程真人与 AI 都能回答，
        # 因此**不**标 needs_local_ui——标了会让后两者直接发不动。
        tags=("active",),
    ),
    triggered(
        "qinyin",
        "琴音",
        "弃牌阶段，当你弃掉了两张或更多的手牌后，"
        "你可以令所有角色各回复 1 点体力或各失去 1 点体力。",
        factory=Qinyin,
    ),
    active(
        "yeyan",
        "业炎",
        "限定技，出牌阶段，你可以选择一至三名角色，"
        "对他们分别造成最多共 3 点火焰伤害（你可以任意分配）；"
        "若你将对一名角色分配 2 点或更多的火焰伤害，"
        "你须先弃置四张不同花色的手牌并失去 3 点体力。",
        can_activate=_can_yeyan,
        activate=_activate_yeyan,
        spec=ActiveSkillSpec(),
        tags=("active", "limited"),
    ),
    triggered(
        "guixin",
        "归心",
        "你每受到 1 点伤害，可以分别从每名其他角色的手牌、装备区和判定区"
        "各获得一张牌；若如此做，将你的武将牌翻面。",
        factory=Guixin,
    ),
    SkillDef(
        id="feiying",
        name="飞影",
        description="锁定技，当其他角色计算与你的距离时，始终 +1。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.DISTANCE_INCOMING, value=1,
                         roles=("target",)),
        ),
    ),
    SkillDef(
        id="juejing",
        name="绝境",
        description="锁定技，摸牌阶段，你摸牌的数量为你已损失的体力值 +2；"
        "你的手牌上限 +2。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.DRAW_COUNT,
                         value=lambda game, query: lost_hp_safe(query.get("player"))
                         if query.get("player") is not None else 0,
                         roles=("player",)),
            ModifierSpec(kind=ModifierKind.HAND_LIMIT, value=2, roles=("player",)),
        ),
    ),
    SkillDef(
        id="longhun",
        name="龙魂",
        description="你可以将同花色的 X 张牌按下列规则使用或打出："
        "红桃当【桃】，方块当火【杀】，梅花当【闪】，黑桃当【无懈可击】；"
        "X 为你当前的体力值且至少为 1。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(skill_id="longhun", matches=_is_heart, name="TAO",
                           contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT, "rescue"),
                           count_for=_longhun_count),
            CardConversion(skill_id="longhun", matches=_is_diamond, name="SHA",
                           nature="fire",
                           contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT),
                           count_for=_longhun_count),
            CardConversion(skill_id="longhun", matches=_is_club, name="SHAN",
                           contexts=(RESPONSE_CONTEXT,),
                           count_for=_longhun_count),
            CardConversion(skill_id="longhun", matches=_is_spade, name="WUXIE",
                           category="trick", contexts=(RESPONSE_CONTEXT,),
                           count_for=_longhun_count),
        ),
        tags=("conversion",),
    ),
)
