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
    JudgeReplacement,
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
    add_mark,
    ask_cards,
    ask_confirm,
    ask_option,
    ask_targets,
    awaken,
    flip_player,
    gain_skill,
    grant_extra_turn,
    hand_cards,
    judge,
    limited_used,
    lose_hp,
    lost_hp,
    mark_count,
    other_alive_players,
    remove_mark,
    take_card_from_zone,
    use_virtual,
    zone_take_options,
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
    """武魂：你死亡时，梦魇标记最多的角色判定，非【桃】/【桃园结义】则立即死亡。

    并列最多时**由你指定**一名（以前固定取列表第一人）：这是锁定技，判定
    一定会发生，所以窗口不可取消——玩家取消时退回并列中的第一位，绝不跳过判定。
    """

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "judge"
        self.target = None

    def is_noop(self):
        return not self._candidates()

    def _candidates(self):
        """梦魇标记最多且大于 0 的其他角色（可能并列）。"""

        others = [
            player for player in self.game.players
            if player is not self.owner
            and mark_count(player, "wuhun", "nightmare") > 0
        ]
        if not others:
            return []
        best = max(mark_count(player, "wuhun", "nightmare") for player in others)
        return [player for player in others
                if mark_count(player, "wuhun", "nightmare") == best]

    def begin(self):
        tied = self._candidates()
        if not tied:
            return self.complete({"applied": False})
        if len(tied) == 1:
            self.target = tied[0]
            return self._begin_judge()
        self.stage = "pick"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【武魂】：请指定一名拥有最多梦魇标记的角色进行判定",
                    reason="wuhun", candidates=tied,
                    min_targets=1, max_targets=1,
                    context={"cancellable": False})
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "pick":
            tied = self._candidates()
            chosen = [target for target in (getattr(response, "targets", None) or ())
                      if any(target is item for item in tied)]
            # 锁定技：判定一定要发生。窗口被取消（或选的人已经不在并列里）
            # 时退回并列中的第一位，绝不跳过判定。
            self.target = chosen[0] if chosen else (tied[0] if tied else None)
            if self.target is None:
                return self.complete({"applied": False})
            return self._begin_judge()
        return self.complete({"applied": True})

    def _begin_judge(self):
        target = self.target
        flow, result = judge(self.engine, target, "wuhun")
        if result is None:
            flow.on_complete = self._after_judge
            self.wait(flow)
            return self.current_result()
        return self._after_judge(result)

    def _after_judge(self, result):
        game = self.game
        target = self.target
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


def _jilue_can_pay(player):
    """极略的代价：一枚「忍」标记。"""

    return mark_count(player, "renjie", "ren") >= 1


def _jilue_guicai_candidates(game, player, judge_context):
    """极略·鬼才：判定牌生效前，用一张手牌替换它（代价见 on_use）。"""

    if not _jilue_can_pay(player):
        return []
    return hand_cards(player)


def _jilue_guicai_cost(game, player, card):
    """极略·鬼才的代价：弃一枚「忍」标记（付不出就不能发动）。"""

    if not _jilue_can_pay(player):
        return False
    remove_mark(game, player, "renjie", 1, "ren")
    game.add_log("%s 的【极略·鬼才】弃一枚「忍」标记改判" % player.name)
    return True


#: 【极略·制衡】的输入契约：与孙权【制衡】同一条规则——弃**任意张牌**
#: （手牌 + 装备区），然后摸等量的牌。区域只在这里声明一次，候选、界面、
#: 引擎校验、远程下发全部由它派生。
JILUE_ZHI_HENG_SPEC = ActiveSkillSpec(
    variable_cost=True,
    cost_prompt="【极略·制衡】：请选择要弃置的牌",
    allowed_zones=(CostZone.HAND, CostZone.EQUIPMENT),
)


def _jilue_cost_candidates(game, player):
    """这次能支付的牌（与引擎校验、界面高亮同一份判断）。"""

    from src.game.skills.activation import cost_candidates

    return cost_candidates(game, player, JILUE_ZHI_HENG_SPEC)


def _can_jilue(game, player):
    """极略·制衡的主动技入口（出牌阶段）。"""

    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if not game.skills.has(player, "jilue"):
        return False, "还没有获得【极略】"
    if not _jilue_can_pay(player):
        return False, "没有「忍」标记"
    if player.skill_state.get("jilue", "zhiheng_used", 0):
        return False, "本阶段已经制衡过"
    if not _jilue_cost_candidates(game, player):
        return False, "没有可以弃置的牌"
    return True, ""


def _activate_jilue(game, player, target=None, cards=None):
    """极略·制衡：弃一枚「忍」标记，弃任意张牌，然后摸等量的牌。

    费用牌（``cards``）已由引擎按 ``variable_cost`` 弃置，这里只负责扣标记
    与摸牌——与孙权【制衡】的结算完全一致。
    """

    chosen = list(cards or ())
    if not chosen:
        return False
    if not _jilue_can_pay(player):
        return False
    remove_mark(game, player, "renjie", 1, "ren")
    player.skill_state.set("jilue", "zhiheng_used", 1, ResetScope.PHASE)
    game.engine.context.apply(DrawCardsAtom(player, len(chosen)))
    game.add_log("%s 的【极略·制衡】：弃置 %d 张牌并摸 %d 张牌"
                 % (player.name, len(chosen), len(chosen)))
    return True


class Jilue(Skill):
    """极略：五个选项**各自在自己的规则时机**开放，每次弃一枚「忍」结算一次。

    卡面：你可以弃一枚「忍」标记，发动下列一项技能：【鬼才】【放逐】【完杀】
    【集智】【制衡】。

    五个能力的时机完全不同，所以不能只做成"出牌阶段点一下"——那样鬼才
    （判定牌生效前）与放逐（受伤后）在别人的回合里永远发不出来：

        鬼才  判定牌生效前（改判窗口，见 SkillDef 的 ``judge_replacement``）
        放逐  你受到伤害后
        完杀  你的出牌阶段开始时（直到回合结束生效）
        集智  你使用非延时锦囊牌时
        制衡  出牌阶段（主动技入口，见 ``_activate_jilue``）

    本类同时承担"临时能力到回合结束就收回"的收尾。``id`` 必须与 SkillDef
    一致——``SkillManager.unbind`` 按实例 id 匹配。
    """

    id = "jilue"
    name = "极略"

    def bindings(self):
        return (
            SkillBinding(EventType.CARD_USED, priority=10),
            SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=25),
            SkillBinding(EventType.PHASE_START, priority=35),
            SkillBinding(EventType.TURN_END, priority=-80),
        )

    def can_trigger(self, context, event):
        game = context.state
        if event.name is EventType.TURN_END:
            # 回收临时能力不花标记：没标记也要把上回合临时获得的技能收回去。
            return bool(self.owner.skill_state.get("jilue", "temporary", None))
        if not self.owner.alive or not game.skills.has(self.owner, self.id):
            return False
        if not _jilue_can_pay(self.owner):
            return False
        if event.name is EventType.CARD_USED:
            return (event.source is self.owner
                    and _is_non_delay_trick(event.payload.get("card")))
        if event.name is EventType.DAMAGE_TARGET_AFTER:
            damage = event.payload.get("damage")
            if damage is None or damage.target is not self.owner:
                return False
            if int(event.payload.get("amount", 0) or 0) <= 0:
                return False
            return bool(other_alive_players(game, self.owner))
        if event.source is not self.owner:
            return False
        if event.payload.get("phase") is not TurnPhase.PLAY:
            return False
        if event.payload.get("skipped"):
            return False
        return not self.owner.skill_state.get("jilue", "wansha_used", 0)

    def resolve(self, context, event):
        game = context.state
        if event.name is EventType.TURN_END:
            for skill_id in list(
                    self.owner.skill_state.get("jilue", "temporary", None) or ()):
                if skill_id == self.id:
                    continue
                if game.skills.has(self.owner, skill_id):
                    game.skills.unbind(self.owner, skill_id)
            self.owner.skill_state.clear("jilue", "temporary")
            return
        engine = context.services["engine"]
        if event.name is EventType.CARD_USED:
            JilueJizhiFlow(engine, self.owner).start()
            return
        if event.name is EventType.DAMAGE_TARGET_AFTER:
            JilueFangzhuFlow(engine, self.owner).start()
            return
        JilueWanshaFlow(engine, self.owner).start()


def _gain_until_turn_end(game, owner, skill_id):
    """临时获得一个技能直到回合结束（由 Jilue 的 TURN_END 分支统一收回）。"""

    gain_skill(game, owner, skill_id)
    temporary = list(owner.skill_state.get("jilue", "temporary", None) or ())
    if skill_id not in temporary:
        temporary.append(skill_id)
    owner.skill_state.set("jilue", "temporary", tuple(temporary),
                          ResetScope.TURN)


class JilueJizhiFlow(Flow):
    """极略·集智：使用非延时锦囊时，弃一枚「忍」摸一张牌（可以放弃）。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【极略·集智】：是否弃一枚「忍」标记摸一张牌？",
                    reason="jilue")
        return self.current_result()

    def advance(self, response=None):
        if (response is None or not response.confirmed
                or not _jilue_can_pay(self.owner)):
            return self.complete({"applied": False})
        remove_mark(self.game, self.owner, "renjie", 1, "ren")
        self.context.apply(DrawCardsAtom(self.owner, 1))
        self.game.add_log("%s 的【极略·集智】弃一枚「忍」标记摸一张牌"
                          % self.owner.name)
        return self.complete({"applied": True})


class JilueFangzhuFlow(Flow):
    """极略·放逐：受到伤害后弃一枚「忍」，令一名其他角色摸 X 张牌并翻面。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【极略·放逐】：是否弃一枚「忍」标记发动【放逐】？",
                    reason="jilue")
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "confirm":
            if (response is None or not response.confirmed
                    or not _jilue_can_pay(self.owner)):
                return self.complete({"applied": False})
            remove_mark(self.game, self.owner, "renjie", 1, "ren")
            self.stage = "target"
            ask_targets(self.engine, self, source=self.owner, target=self.owner,
                        prompt="【极略·放逐】：请选择摸牌并翻面的角色",
                        reason="jilue",
                        candidates=other_alive_players(self.game, self.owner),
                        min_targets=1, max_targets=1)
            return self.current_result()
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        target = targets[0]
        x = max(1, lost_hp(self.owner))
        self.context.apply(DrawCardsAtom(target, x))
        flip_player(self.game, target, reason="放逐")
        self.game.add_log("%s 的【极略·放逐】令 %s 摸 %d 张牌并翻面"
                          % (self.owner.name, target.name, x))
        return self.complete({"applied": True})


class JilueWanshaFlow(Flow):
    """极略·完杀：出牌阶段开始时弃一枚「忍」，直到回合结束获得【完杀】。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【极略·完杀】：是否弃一枚「忍」标记，"
                           "直到回合结束获得【完杀】？",
                    reason="jilue")
        return self.current_result()

    def advance(self, response=None):
        if (response is None or not response.confirmed
                or not _jilue_can_pay(self.owner)):
            return self.complete({"applied": False})
        remove_mark(self.game, self.owner, "renjie", 1, "ren")
        self.owner.skill_state.set("jilue", "wansha_used", 1, ResetScope.TURN)
        _gain_until_turn_end(self.game, self.owner, "wansha")
        self.game.add_log("%s 的【极略·完杀】生效直到回合结束" % self.owner.name)
        return self.complete({"applied": True})


class LianpoFlow(Flow):
    """连破：额外回合**由玩家决定**（可以拒绝）。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【连破】：是否进行一个额外的回合？",
                    reason="lianpo")
        return self.current_result()

    def advance(self, response=None):
        if (response is None or not response.confirmed
                or not self.owner.alive or self.game.game_over):
            self.game.add_log("%s 放弃【连破】" % self.owner.name)
            return self.complete({"applied": False})
        grant_extra_turn(self.game, self.owner)
        self.game.add_log("%s 的【连破】获得一个额外回合" % self.owner.name)
        return self.complete({"applied": True})


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
        # 本回合的击杀标记先清掉再问：同一次击杀只问一次，拒绝之后也不会
        # 在别的时机被再问一遍。
        self.owner.skill_state.set(self.id, "killed", 0, ResetScope.ROUND)
        # 官方：「一名角色的回合结束时，若你本回合杀死过角色，你可以执行一个
        # 额外回合」——**自己的回合结束时同样成立**（FAQ：本回合内击杀 → 该回合
        # 结束后立刻再来一个）。这里曾经把"自己回合内击杀"整条 return 掉，
        # 等于把连破的滚雪球核心砍掉，只剩回合外击杀能用。
        if not self.owner.alive or game.game_over:
            return
        LianpoFlow(context.services["engine"], self.owner).start()


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
        self.index = 0
        self.stage = "run"

    def begin(self):
        from src.game.flows.chain_damage import ChainDamageFlow

        if self.targets:
            ChainDamageFlow(
                self.engine, source=self.player, card=None, nature="normal",
                amount=1, targets=self.targets).start()
        return self.advance()

    def advance(self, response=None):
        if self.stage == "discard":
            return self._after_discard(response)
        guard = self.guard_child_flows()
        if guard is not None:
            return guard
        return self._discard_equipment()

    def _discard_equipment(self):
        """先弃置装备区（规则本身没有选择），再逐名角色问手牌。"""

        game = self.game
        for other in self.targets:
            for slot in list(getattr(other, "equipment", {})):
                if other.get_equipment(slot) is not None:
                    self.context.apply(UnequipAtom(
                        other, slot, game.deck.discard_pile))
        self.stage = "discard"
        self.index = 0
        return self._next_discard()

    def _next_discard(self):
        """逐名角色各问一次"弃哪四张"——弃哪几张必须由他自己挑。"""

        while self.index < len(self.targets):
            player = self.targets[self.index]
            self.index += 1
            if not getattr(player, "alive", True) or int(player.hp) <= 0:
                continue
            hand = list(getattr(player, "hand", ()) or ())
            if not hand:
                continue
            count = min(4, len(hand))
            ask_cards(self.engine, self, source=self.player, target=player,
                      prompt="【神愤】：请选择要弃置的 %d 张手牌" % count,
                      reason="shenfen", candidates=hand,
                      min_cards=count, max_cards=count)
            return self.current_result()
        return self._flip()

    def _after_discard(self, response):
        player = self.targets[self.index - 1] if self.index else None
        for card in list(getattr(response, "cards", ()) or ()):
            if player is None:
                break
            if any(item is card for item in getattr(player, "hand", ())):
                self.context.apply(MoveCardAtom(
                    card, source=player.hand,
                    destination=self.game.deck.discard_pile))
        return self._next_discard()

    def _flip(self):
        game = self.game
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


def _shelie_flow(game, player):
    return ShelieFlow(game.engine, player)


def _suit_label(suit):
    from src.card import suit_name

    return suit_name(suit) or "这个花色的"


class ShelieFlow(Flow):
    """涉猎：放弃摸牌，亮出牌堆顶五张，每种花色各取一张。

    同花色不止一张时，"取哪一张"由拥有者决定——官方把"摸到的牌质量可控、
    按场上局势取舍"当作涉猎的玩法；以前是代码按"每花色第一次出现"固定留
    第一张，玩家的取舍整条被吃掉。
    """

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.revealed = []
        self.groups = {}
        self.taken = []
        self.queue = []
        self.index = 0
        self.stage = "begin"

    # ---- 1. 亮牌（亮出的五张进公共牌池，结算完再各自归位）----

    def begin(self):
        count = min(5, len(self.game.deck.draw_pile))
        if count <= 0:
            return self.complete({"applied": False})
        pool = self.game.public_card_pool
        for _ in range(count):
            card = self.game.deck.draw()
            if card is None:
                break
            self.revealed.append(card)
            pool.append(card)
        if not self.revealed:
            return self.complete({"applied": False})
        names = "、".join(
            (getattr(card, "identity_label", "") or "?") for card in self.revealed)
        self.game.add_log("%s 的【涉猎】亮出 %d 张：%s"
                          % (self.owner.name, len(self.revealed), names))
        for card in self.revealed:
            self.groups.setdefault(getattr(card, "suit", None), []).append(card)
        # 唯一的那张必须拿（没有选择）；同花色多张才需要问。
        self.taken = [cards[0] for cards in self.groups.values()
                      if len(cards) == 1]
        self.queue = [suit for suit, cards in self.groups.items()
                      if len(cards) > 1]
        self.index = 0
        return self._next()

    def advance(self, response=None):
        return self._after_choice(response)

    # ---- 2. 逐花色问"要哪一张" ----

    def _next(self):
        while self.index < len(self.queue):
            suit = self.queue[self.index]
            self.index += 1
            cards = [card for card in self.groups.get(suit, ())
                     if self._in_pool(card)]
            if len(cards) <= 1:
                if cards:
                    self.taken.append(cards[0])
                continue
            self.stage = "choose:%s" % suit
            ask_cards(self.engine, self, source=self.owner, target=self.owner,
                      prompt="【涉猎】：请选择要获得的%s牌（同花色只取一张）"
                             % _suit_label(suit),
                      reason="shelie", candidates=cards,
                      min_cards=1, max_cards=1, zone="public_pool")
            return self.current_result()
        return self._settle()

    def _after_choice(self, response):
        suit = str(self.stage).split(":", 1)[-1]
        cards = [card for card in self.groups.get(suit, ()) if self._in_pool(card)]
        chosen = [card for card in (getattr(response, "cards", ()) or ())
                  if any(card is item for item in cards)]
        if chosen:
            self.taken.append(chosen[0])
        elif cards:
            # 回答里没有合法牌（牌在这期间被移走一类）：保住这个花色的一张，
            # 绝不让"每种花色各一张"少给一张。
            self.taken.append(cards[0])
        return self._next()

    # ---- 3. 所选入手，其余进弃牌堆 ----

    def _settle(self):
        pool = self.game.public_card_pool
        taken_ids = {id(card) for card in self.taken}
        for card in list(self.revealed):
            if not self._in_pool(card):
                continue
            if id(card) in taken_ids:
                self.context.apply(MoveCardAtom(
                    card, source=pool, destination=self.owner.hand))
            else:
                self.context.apply(MoveCardAtom(
                    card, source=pool,
                    destination=self.game.deck.discard_pile,
                    # 这类牌是"置入弃牌堆"，不是**弃置**：不能触发【落英】
                    # 一类只认"因弃置或判定进入弃牌堆"的技能。
                    reason="shelie", owner=self.owner))
        labels = "、".join(
            (getattr(card, "display_name", "") or "?") for card in self.taken)
        self.game.add_log("%s 的【涉猎】取走 %s，其余 %d 张置入弃牌堆"
                          % (self.owner.name, labels,
                             len(self.revealed) - len(self.taken)))
        self.game.message = "%s 的【涉猎】拿走了 %d 张不同花色的牌。" % (
            self.owner.name, len(self.taken))
        return self.complete({"applied": True})

    def _in_pool(self, card):
        return any(item is card for item in self.game.public_card_pool)


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
        # 候选是目标的**全部手牌**：官方的攻心是"先观看全部手牌，再展示其中
        # 一张红桃牌"。以前候选被过滤成"只有红桃"，拥有者看不到其他花色，
        # 目标手里没红桃时还会看到一个空列表——那不是观看手牌，是看答案。
        #
        # 展示仍然只能是红桃：选到非红桃 = 这次不展示（不处理任何牌）。
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【攻心】：观看 %s 的手牌，选择其中一张红桃牌展示"
                         "（选择其他花色 = 不展示）" % self.target.name,
                  reason="gongxin",
                  candidates=hand_cards(self.target),
                  min_cards=0, max_cards=1, zone="public_pool",
                  context={"zone_owner": self.target, "cancellable": True})
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "reveal":
            return self._after_reveal(response)
        return self._after_action(response)

    def _after_reveal(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            self.game.add_log("%s 没有展示 %s 的牌" % (self.owner.name, self.target.name))
            return self.complete({"applied": True})
        card = cards[0]
        if not any(card is item for item in self.target.hand):
            # 选牌期间这张牌已经离开他的手牌：什么都不处理。
            return self.complete({"applied": True})
        if not _is_heart(card):
            # 非红桃只能被"看到"，不能被展示 / 处理。
            self.game.message = "【攻心】：只能展示红桃牌，本次不展示。"
            self.game.add_log("【攻心】：%s 选择了非红桃牌，本次不展示"
                              % self.owner.name)
            return self.complete({"applied": True})
        self.card = card
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
    """每受到 1 点伤害后，**可以**分别从每名其他角色区域里各获得一张牌，
    然后将你的武将牌翻面。"""

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
        return any(_cards_of(other)
                   for other in other_alive_players(context.state, self.owner))

    def points(self, context, event):
        """这次伤害有几个"1 点"要分别问（官方：每受 1 点伤害可发动一次）。"""

        return max(1, int(event.payload.get("amount", 0) or 0))

    def resolve(self, context, event):
        GuixinFlow(context.services["engine"], self.owner,
                   points=self.points(context, event)).start()


class GuixinFlow(Flow):
    """归心：**逐点**询问（可以拒绝），每一轮逐名角色各选一张牌，最后翻面。

    三件事必须在玩家手里：

    * 每 1 点伤害**独立**问一次"要不要发动"（可以拒绝）——翻面会跳过自己的
      下个回合，这是必须由玩家自己承担代价的决定，不是系统替他发动；
    * 对每名角色选**哪个区域、哪一张牌**（手牌是暗牌，按隐藏信息规则随机取）；
    * 一轮拿完、翻面结算完成之后才问下一点伤害。
    """

    def __init__(self, engine, owner, points=1):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.points = max(1, int(points))
        self.point = 0
        self.order = []
        self.index = 0
        self.current = None
        self.applied = False
        self.stage = "confirm"

    def begin(self):
        return self._ask_point()

    # ---- 每一点伤害一次 ----

    def _ask_point(self):
        if (self.point >= self.points or not self.owner.alive
                or self.game.game_over):
            return self.complete({"applied": self.applied})
        self.order = [other for other in other_alive_players(self.game, self.owner)
                      if _cards_of(other)]
        if not self.order:
            # 没人有牌可取：没有可拿的东西，不翻面，也不问。
            return self.complete({"applied": self.applied})
        self.stage = "confirm"
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【归心】：是否发动？（第 %d / %d 点伤害；"
                           "发动后你的武将牌翻面）" % (self.point + 1, self.points),
                    reason="guixin")
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "confirm":
            if response is None or not response.confirmed:
                # 拒绝：不拿牌、**不翻面**，直接问下一点。
                self.point += 1
                return self._ask_point()
            self.applied = True
            self.index = 0
            return self._take_next()
        return self._after_take(response)

    # ---- 逐名角色选一张 ----

    def _take_next(self):
        while self.index < len(self.order):
            player = self.order[self.index]
            self.index += 1
            if not player.alive or not _cards_of(player):
                continue
            options = zone_take_options(player)
            if not options:
                continue
            self.current = player
            self.stage = "take"
            ask_option(self.engine, self, source=self.owner, target=self.owner,
                       prompt="【归心】：获得 %s 区域里的一张牌" % player.name,
                       reason="guixin", options=tuple(options),
                       context={"zone_holder_id": getattr(player, "player_id", None)})
            return self.current_result()
        return self._end_round()

    def _after_take(self, response):
        player = self.current
        self.current = None
        option = str(getattr(response, "option", "") or "")
        card = self._take_card(player, option)
        if card is not None:
            engine = self.engine
            shower = getattr(engine, "show_taken_card", None)
            if callable(shower):
                # 拿走的是别人的牌：亮在桌面停一下，让对手看清是哪张。
                shower(card, player, self.owner, to_hand=True)
            self.game.add_log("【归心】：%s 获得 %s 的一张牌"
                              % (self.owner.name, player.name))
        return self._take_next()

    def _take_card(self, player, option):
        """按选择的区域拿牌（手牌是暗牌，按隐藏信息规则随机取）。"""

        return take_card_from_zone(
            self.context, self.game, self.owner, player, option)

    # ---- 一轮结束：翻面，然后问下一点 ----

    def _end_round(self):
        flip_player(self.game, self.owner, reason="归心")
        self.game.add_log("%s 发动【归心】，获得若干牌并翻面" % self.owner.name)
        self.point += 1
        return self._ask_point()


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
        # 锁定技：这不是"可以点技能选择转化"，而是**替换**——手里的红桃牌
        # 就是【杀】，因此 ① 技能栏不该给一个可选的发动入口，② 原牌名那条路
        # （红桃【桃】救人、红桃【闪】当闪）必须彻底关掉。
        kind=SkillKind.LOCKED,
        conversions=(
            CardConversion(skill_id="wushen", matches=_is_heart, name="SHA",
                           contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT),
                           locks_source=True),
        ),
        modifiers=(
            ModifierSpec(kind=ModifierKind.SLASH_DISTANCE_IGNORE, value=True,
                         roles=("player",), condition=_wushen_ignores_distance),
        ),
        tags=("conversion", "locked"),
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
        factory=Jilue,
        can_activate=_can_jilue,
        activate=_activate_jilue,
        active_spec=JILUE_ZHI_HENG_SPEC,
        judge_replacement=JudgeReplacement(
            candidates=_jilue_guicai_candidates,
            prompt="【极略·鬼才】：是否弃一枚「忍」标记改判？",
            on_use=_jilue_guicai_cost,
        ),
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
            # 同花色取哪一张要玩家自己挑：用**流程**替代纯数据替代，
            # 阶段结算会挂起等选择完成，期间摸牌阶段绝不悄悄推进。
            flow=_shelie_flow,
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
