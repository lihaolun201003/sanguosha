"""风包武将技能：于吉 / 周泰 / 夏侯渊 / 小乔 / 张角 / 曹仁 / 魏延 / 黄忠。

版本说明
--------
张角与曹仁各有两个规则版本，卡面正文不同、结算也不同，因此这里同时实现：

* 【雷击】2008 初版 = 令目标**失去 2 点体力**；2010 修订版 = 造成 2 点雷电伤害。
  「失去体力」不走伤害流程（不触发受伤类技能、不吃防具与伤害加成），
  两者不能共用一个实现。
* 【据守】2008 初版 = 跳过你下个回合；2010 修订版 = 将你的武将牌翻面。
"""

from src.card import mark_card_flag
from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom
from src.game.engine import EventType, Flow, FlowResult, FlowStatus
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..definitions import (
    ActiveSkillSpec,
    JudgeReplacement,
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
    effective_suit,
    flip_player,
    hand_cards,
    judge,
    lose_hp,
    other_alive_players,
    use_virtual,
)
from ..modifiers import ModifierKind
from ..state import ResetScope


# ==================================================
# 于吉 · 蛊惑
# ==================================================

#: 蛊惑可以说出的牌名：基本牌 + 非延时类锦囊（延时锦囊不能蛊惑）。
GUHUO_NAMES = (
    ("SHA", "杀"), ("SHAN", "闪"), ("TAO", "桃"), ("JIU", "酒"),
    ("WUZHONG", "无中生有"), ("GUOHE", "过河拆桥"), ("SHUNSHOU", "顺手牵羊"),
    ("JUEDOU", "决斗"), ("NANMAN", "南蛮入侵"), ("WANJIAN", "万箭齐发"),
    ("TAOYUAN", "桃园结义"), ("WUGU", "五谷丰登"), ("WUXIE", "无懈可击"),
    ("JIEDAO", "借刀杀人"), ("HUOGONG", "火攻"), ("TIESUO", "铁索连环"),
)
#: 这些牌名不需要指定目标（自己受益 / 无目标）。
GUHUO_UNTARGETED = frozenset(
    ("SHAN", "TAO", "WUXIE", "WUZHONG", "TAOYUAN", "WUGU", "TIESUO", "SHANDIAN"))


def _can_guhuo(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if not hand_cards(player):
        return False, "没有可以蛊惑的手牌"
    return True, ""


class GuhuoFlow(Flow):
    """蛊惑：说牌名 → 选牌 → 选目标 → 全场依次质疑 → 按真伪结算。

    「质疑」按座次逐个问，而不是同时开一块大面板：本地只有一个鼠标，
    远程真人也只有一条决策通道，逐个问在三种控制器下语义一致。
    """

    def __init__(self, engine, player):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.player = player
        self.declared = ""
        self.card = None
        self.chosen_targets = []
        self.challengers = []
        self.ask_index = 0
        self.stage = "declare"

    def begin(self):
        ask_option(self.engine, self, source=self.player, target=self.player,
                   prompt="【蛊惑】：请说出这张牌是什么",
                   reason="guhuo", options=GUHUO_NAMES)
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "declare":
            return self._after_declare(response)
        if self.stage == "pick_card":
            return self._after_pick(response)
        if self.stage == "pick_targets":
            return self._after_targets(response)
        if self.stage == "challenge":
            return self._after_challenge(response)
        raise RuntimeError("GuhuoFlow cannot advance from stage " + self.stage)

    def _after_declare(self, response):
        option = getattr(response, "option", None)
        if option is None:
            return self.cancel(None)
        self.declared = str(option)
        self.stage = "pick_card"
        ask_cards(self.engine, self, source=self.player, target=self.player,
                  prompt="【蛊惑】：请选择一张手牌正面朝下打出（声明为【%s】）"
                         % self._declare_label(),
                  reason="guhuo", candidates=hand_cards(self.player),
                  min_cards=1, max_cards=1)
        return self.current_result()

    def _after_pick(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self.cancel(None)
        self.card = cards[0]
        candidates = self._target_candidates()
        if candidates:
            self.stage = "pick_targets"
            ask_targets(self.engine, self, source=self.player, target=self.player,
                        prompt="【蛊惑】：请为声明为【%s】的牌选择目标" % self._declare_label(),
                        reason="guhuo", candidates=candidates,
                        min_targets=1, max_targets=len(candidates))
            return self.current_result()
        return self._start_challenge()

    def _after_targets(self, response):
        self.chosen_targets = list(getattr(response, "targets", ()) or ())
        if self._target_candidates() and not self.chosen_targets:
            return self.cancel(None)
        return self._start_challenge()

    # ---- 质疑窗口 ----

    def _start_challenge(self):
        self.stage = "challenge"
        self.ask_index = 0
        return self._ask_next_challenger()

    def _ask_next_challenger(self):
        members = other_alive_players(self.game, self.player)
        while self.ask_index < len(members):
            member = members[self.ask_index]
            self.ask_index += 1
            ask_confirm(self.engine, self, source=self.player, target=member,
                        prompt="%s 声称打出了【%s】，是否质疑？"
                               % (self.player.name, self._declare_label()),
                        reason="guhuo", context={"declared": self.declared})
            return self.current_result()
        return self._resolve()

    def _after_challenge(self, response):
        if response is not None and response.confirmed:
            self.challengers.append(response.actor)
        return self._ask_next_challenger()

    # ---- 结算 ----

    def _resolve(self):
        game = self.game
        truth = self._is_true()
        game.add_log("%s 发动【蛊惑】声称【%s】，质疑者 %d 人"
                     % (self.player.name, self._declare_label(), len(self.challengers)))

        if self.challengers:
            if truth:
                for challenger in self.challengers:
                    lose_hp(game, challenger, 1, source=self.player,
                            reason="质疑【蛊惑】失败")
            else:
                for challenger in self.challengers:
                    self.context.apply(DrawCardsAtom(challenger, 1))
                game.message = "【蛊惑】被识破，质疑者各摸一张牌。"

            # 被质疑的牌：只有"为真且为红桃"才照常生效，否则弃置且无效。
            keeps_effect = (
                truth and effective_suit(game, self.card, self.player) == "heart")
            if not keeps_effect:
                self._discard_card()
                game.message = ("【蛊惑】被揭穿，该牌无效。" if not truth
                                else "【蛊惑】为真但不是红桃，该牌无效。")
                return self.complete({"declared": self.declared, "effective": False})

        return self._use_declared()

    def _use_declared(self):
        game = self.game
        targets = list(self.chosen_targets)
        if self._is_true():
            from src.game.engine import UseCardAction

            game.add_log("%s 的【蛊惑】为真，【%s】照常结算"
                         % (self.player.name, self._declare_label()))
            game.engine.submit(UseCardAction(
                self.player, self.card, targets, ignore_usage_limit=True))
            return self.complete({"declared": self.declared, "effective": True})
        # 假牌：用虚拟牌走通用结算，实体牌作为它的 source 一起进入处理区。
        use_virtual(game, self.player, self.declared, sources=(self.card,),
                    targets=targets)
        return self.complete({"declared": self.declared, "effective": True})

    def _discard_card(self):
        if self.card is None:
            return
        if any(item is self.card for item in self.player.hand):
            self.context.apply(MoveCardAtom(
                self.card, source=self.player.hand,
                destination=self.game.deck.discard_pile))

    def _is_true(self):
        return getattr(self.card, "name", None) == self.declared

    def _declare_label(self):
        for name, label in GUHUO_NAMES:
            if name == self.declared:
                return label
        return str(self.declared)

    def _target_candidates(self):
        """声明的牌若需要目标，给出合法目标（走通用效果查询）。"""

        if self.declared in GUHUO_UNTARGETED:
            return []
        from src.card import Card

        probe = Card(name=self.declared, category="basic", color=(0, 0, 0))
        effect = self.game.engine.card_effects.get(probe)
        if effect is None:
            return []
        if not self.game.card_actions.effect_requires_targets(probe):
            return []
        return list(self.game.card_actions.target_candidates(
            self.player, effect.target_rule, probe))

    def cancel(self, reason=None):
        if self.card is not None and any(item is self.card for item in self.player.hand):
            # 取消发生在选牌之后：牌还在手上，什么都不用还。
            pass
        self.game.message = "取消蛊惑。"
        return super().cancel(reason)


def _activate_guhuo(game, player, target=None, cards=None):
    GuhuoFlow(game.engine, player).start()
    game.add_log(player.name + " 发动【蛊惑】")
    return True


# ==================================================
# 周泰 · 不屈
# ==================================================

#: 不屈牌存放的牌区名（在 ``player.placed_cards`` 里）。
BUQU_ZONE = "buqu"


class Buqu(Skill):
    """濒死求桃失败后阻止死亡；体力回到 0 以上时清掉「不屈」牌。

    「不死」的检查点是引擎里通用的一步（``DYING_BEFORE_DEATH``）：
    任何角色只要满足条件都能阻止自己的死亡，规则层不认识周泰。
    """

    id = "buqu"
    name = "不屈"

    def bindings(self):
        return (
            SkillBinding(EventType.DYING_BEFORE_DEATH, priority=50),
            SkillBinding(EventType.PHASE_END, priority=-20),
        )

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        if event.name is EventType.DYING_BEFORE_DEATH:
            return event.target is self.owner
        return self.owner.hp > 0 and bool(self.owner.placed_zone(BUQU_ZONE))

    def resolve(self, context, event):
        if event.name is EventType.DYING_BEFORE_DEATH:
            return self._prevent_death(context, event)
        return self._clear_buqu(context)

    # ---- 不死 ----

    def _prevent_death(self, context, event):
        game = context.state
        while self.owner.hp <= 0 and len(self.owner.placed_zone(BUQU_ZONE)) < 32:
            if self._flip(game) is None:
                break
        labels = "、".join(
            (getattr(card, "identity_label", "") or "?")
            for card in self.owner.placed_zone(BUQU_ZONE))
        if self._protected():
            event.payload["prevented"] = True
            game.add_log("%s 的【不屈】牌点数互不相同（%s），不会死去"
                         % (self.owner.name, labels))
            game.message = self.owner.name + " 的【不屈】生效，不会死去。"
        else:
            game.add_log("%s 的【不屈】出现重复点数（%s），仍会死亡"
                         % (self.owner.name, labels))

    def _flip(self, game):
        card = game.deck.draw()
        if card is None:
            return None
        if any(item is card for item in game.processing_zone):
            game.processing_zone.remove(card)
        self.owner.place_card(BUQU_ZONE, card)
        game.remove_table_card(card)
        return card

    def _protected(self):
        ranks = [str(getattr(card, "rank", ""))
                 for card in self.owner.placed_zone(BUQU_ZONE)]
        return bool(ranks) and len(ranks) == len(set(ranks))

    def _clear_buqu(self, context):
        game = context.state
        pile = list(self.owner.placed_zone(BUQU_ZONE))
        if not pile:
            return
        for card in pile:
            self.owner.take_placed_card(BUQU_ZONE, card)
            game.deck.discard(card)
        game.add_log("%s 回复到 0 点体力以上，弃置全部「不屈」牌" % self.owner.name)


# ==================================================
# 夏侯渊 · 神速
# ==================================================


class Shensu(Skill):
    """准备阶段开始时选一至两项，直接改写本回合的阶段进程。"""

    id = "shensu"
    name = "神速"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=60),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.PREPARE:
            return False
        if self.owner.skill_state.get(self.id, "used", 0):
            return False
        if event.payload.get("flow") is None:
            return False
        return bool(self._options())

    def _options(self):
        options = []
        if not self.owner.skill_state.get(self.id, "used", 0):
            options.append(("skip_draw", "跳过判定与摸牌阶段"))
            if hand_cards(self.owner, _is_equipment):
                options.append(("skip_play", "跳过出牌阶段并弃一张装备牌"))
        if not options:
            return []
        if len(options) == 2:
            options.append(("both", "两项都选"))
        options.append(("none", "都不选"))
        return options

    def resolve(self, context, event):
        game = context.state
        options = self._options()
        if not options:
            return
        self.owner.skill_state.set(self.id, "used", 1, ResetScope.TURN)
        flow = ShensuFlow(
            context.services["engine"], self.owner, event.payload["flow"], options)
        flow.start()


def _is_equipment(card):
    return getattr(card, "category", None) == "equipment"


class ShensuFlow(Flow):
    """神速：选项 → 为每一项选一个【杀】的目标。"""

    def __init__(self, engine, owner, turn_flow, options):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.turn_flow = turn_flow
        self.options = options
        self.steps = []
        self.step_index = 0
        self.stage = "choose"

    def begin(self):
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【神速】：请选择要执行的项",
                   reason="shensu", options=tuple(self.options))
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "choose":
            return self._after_choice(response)
        return self._after_target(response)

    def _after_choice(self, response):
        option = str(getattr(response, "option", "") or "")
        if option == "both":
            self.steps = ["skip_draw", "skip_play"]
        elif option in ("skip_draw", "skip_play"):
            self.steps = [option]
        else:
            return self.complete({"applied": False})
        self._apply_skips()
        self.stage = "target"
        return self._ask_target()

    def _apply_skips(self):
        control = getattr(self.turn_flow, "phase_control", None)
        if control is None:
            return
        if "skip_draw" in self.steps:
            control.skip(TurnPhase.JUDGE)
            control.skip(TurnPhase.DRAW)
        if "skip_play" in self.steps:
            control.skip(TurnPhase.PLAY)
            self._pay_equipment()

    def _pay_equipment(self):
        candidates = hand_cards(self.owner, _is_equipment)
        if not candidates:
            return
        candidates.sort(key=lambda card: int(getattr(card, "attack_range", 0) or 0))
        card = candidates[0]
        self.context.apply(MoveCardAtom(
            card, source=self.owner.hand, destination=self.game.deck.discard_pile))
        self.game.add_log("%s 的【神速】弃置装备牌【%s】"
                          % (self.owner.name, getattr(card, "display_name", "?")))

    def _ask_target(self):
        candidates = [
            other for other in self.game.seats.alive_players_in_order(start_after=self.owner)
            if other is not self.owner
        ]
        if not candidates:
            return self.complete({"applied": True})
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【神速】：请选择视为出【杀】的目标（第 %d / %d 项）"
                           % (self.step_index + 1, len(self.steps)),
                    reason="shensu", candidates=candidates,
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_target(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if targets:
            use_virtual(self.game, self.owner, "SHA", targets=targets)
        self.step_index += 1
        if self.step_index < len(self.steps):
            return self._ask_target()
        return self.complete({"applied": True})


# ==================================================
# 小乔 · 天香 / 红颜
# ==================================================


def _hongyan_suit(game, card, owner):
    """红颜：自己手上的黑桃牌视为红桃（虚拟牌不改，避免递归）。"""

    if getattr(card, "is_virtual", False):
        return None
    if getattr(card, "suit", None) == "spade":
        return "heart"
    return None


class Tianxiang(Skill):
    """受到伤害时，弃一张红桃手牌把这次伤害转移给其他角色。"""

    id = "tianxiang"
    name = "天香"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_TARGET_BEFORE, priority=40),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        if getattr(damage, "cancelled", False) or int(damage.amount) <= 0:
            return False
        if not self.owner.alive:
            return False
        game = context.state
        return bool(self._heart_cards(game)) and bool(other_alive_players(game, self.owner))

    def _heart_cards(self, game):
        return hand_cards(
            self.owner,
            lambda card: effective_suit(game, card, self.owner) == "heart")

    def resolve(self, context, event):
        flow = TianxiangFlow(context.services["engine"], self.owner,
                             event.payload["damage"])
        flow.start()


class TianxiangFlow(Flow):
    """天香：选红桃手牌 → 选目标 → 改伤害目标并让其摸牌。"""

    def __init__(self, engine, owner, damage):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.damage = damage
        self.card = None
        self.stage = "card"

    @property
    def owner_hearts(self):
        """可弃的"红桃"手牌：走有效花色查询，红颜改写的黑桃也算在内。"""

        return hand_cards(
            self.owner,
            lambda card: effective_suit(self.game, card, self.owner) == "heart")

    def begin(self):
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【天香】：请选择一张红桃手牌弃置",
                  reason="tianxiang", candidates=self.owner_hearts,
                  min_cards=1, max_cards=1)
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "card":
            return self._after_card(response)
        return self._after_target(response)

    def _after_card(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self.complete({"applied": False})
        self.card = cards[0]
        self.stage = "target"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【天香】：请选择承受此伤害的角色",
                    reason="tianxiang",
                    candidates=other_alive_players(self.game, self.owner),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_target(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        target = targets[0]
        self.context.apply(MoveCardAtom(
            self.card, source=self.owner.hand, destination=self.game.deck.discard_pile))
        # 伤害转移改的是**同一个** DamageContext 的目标：濒死、连锁、伤害来源
        # 全部按新目标继续走，不复制伤害流程。
        lost = max(0, int(target.max_hp) - int(target.hp))
        self.damage.target = target
        self.damage.effects.append("【天香】将伤害转移给了 " + target.name)
        if lost:
            self.context.apply(DrawCardsAtom(target, lost))
        self.game.add_log(
            "%s 发动【天香】，弃置【%s】将此伤害转移给 %s（其摸 %d 张牌）"
            % (self.owner.name, getattr(self.card, "display_name", "?"),
               target.name, lost))
        return self.complete({"applied": True})


# ==================================================
# 张角 · 雷击 / 鬼道 / 黄天
# ==================================================


class LeijiBase(Skill):
    """使用或打出一张【闪】时令一名角色判定，黑桃则发动。

    ``uses_damage`` 决定黑桃之后是"造成 2 点雷电伤害"（2010 修订版）
    还是"令其失去 2 点体力"（2008 初版）——两者在规则上不是一回事。
    """

    id = "leiji"
    name = "雷击"
    uses_damage = True

    def bindings(self):
        return (SkillBinding(EventType.CARD_RESPONDED, priority=20),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        card = event.payload.get("card")
        if card is None or getattr(card, "name", None) != "SHAN":
            return False
        return bool(other_alive_players(context.state, self.owner))

    def resolve(self, context, event):
        flow = LeijiFlow(context.services["engine"], self, context.state)
        flow.start()


class LeijiOld(LeijiBase):
    """2008 初版：打出一张【闪】时令一名角色失去 2 点体力。"""

    id = "leiji_old"
    name = "雷击"
    uses_damage = False


class LeijiFlow(Flow):
    """雷击：选目标 → 判定 → 黑桃则按版本结算。"""

    def __init__(self, engine, skill, game):
        super().__init__(engine.context)
        self.engine = engine
        self.game = game
        self.skill = skill
        self.owner = skill.owner
        self.target = None
        self.stage = "target"

    def begin(self):
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【雷击】：请选择判定后受影响的目标",
                    reason="leiji",
                    candidates=other_alive_players(self.game, self.owner),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "target":
            return self._after_target(response)
        return self._after_judge(response)

    def _after_target(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        self.target = targets[0]
        self.stage = "judge"
        flow, result = judge(self.engine, self.owner, "leiji")
        if result is None:
            flow.on_complete = lambda value: self._finished(value)
            self.wait(flow)
            return self.current_result()
        return self._finished(result)

    def _after_judge(self, response):
        return self.complete({"applied": True})

    def _finished(self, result):
        if result is None or getattr(result, "suit", None) != "spade":
            self.game.message = "【雷击】判定不是黑桃，无效。"
            self.game.add_log("%s 的【雷击】判定不是黑桃" % self.owner.name)
            return self.complete({"applied": False})
        if self.skill.uses_damage:
            from src.game.flows.damage import DamageContext, DamageFlow

            DamageFlow(self.engine, DamageContext(
                self.owner, self.target, 2, nature="thunder")).start()
            self.game.add_log("%s 的【雷击】对 %s 造成 2 点雷电伤害"
                              % (self.owner.name, self.target.name))
        else:
            lose_hp(self.game, self.target, 2, source=self.owner, reason="雷击")
            self.game.add_log("%s 的【雷击】令 %s 失去 2 点体力"
                              % (self.owner.name, self.target.name))
        return self.complete({"applied": True})


def _guidao_candidates(game, player, judge_context):
    """鬼道：任意角色的判定牌生效前，用自己的一张黑桃 / 梅花牌替换。"""

    return hand_cards(
        player, lambda card: getattr(card, "suit", None) in ("spade", "club"))


def _can_huangtian(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if not _huangtian_donors(game, player):
        return False, "没有持有【闪】或【闪电】的群势力角色"
    return True, ""


def _huangtian_donors(game, player):
    """可以给张角【闪】或【闪电】的其他群势力角色（按座次）。"""

    result = []
    for other in game.seats.alive_players_in_order(start_after=player):
        if other is player or getattr(other, "kingdom", None) != "qun":
            continue
        if any(getattr(card, "name", None) in ("SHAN", "SHANDIAN")
               for card in other.hand):
            result.append(other)
    return result


def _activate_huangtian(game, player, target=None, cards=None):
    """黄天：群雄同伴在各自的出牌阶段把一张【闪】或【闪电】交给张角。

    同伴是 AI 时不会自己"主动给"，因此由张角点名一位持有者，由他交出牌——
    这与卡面"群雄角色可在他们各自的出牌阶段给你"的结算结果一致。
    """

    donors = _huangtian_donors(game, player)
    if not donors:
        return False
    donor = target if target in donors else donors[0]
    card = next((item for item in donor.hand
                 if getattr(item, "name", None) in ("SHAN", "SHANDIAN")), None)
    if card is None:
        return False
    game.engine.context.apply(MoveCardAtom(
        card, source=donor.hand, destination=player.hand))
    game.add_log("%s 发动【黄天】，%s 交给他一张【%s】"
                 % (player.name, donor.name, getattr(card, "display_name", "?")))
    return True


# ==================================================
# 曹仁 · 据守（两个版本）
# ==================================================


class JushouBase(Skill):
    """结束阶段摸三张牌，代价按版本不同（翻面 / 跳过下个回合）。"""

    id = "jushou"
    name = "据守"
    flips = True

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=20),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.FINISH:
            return False
        return not self.owner.skill_state.get(self.id, "used", 0)

    def resolve(self, context, event):
        flow = JushouFlow(context.services["engine"], self, context.state)
        flow.start()


class Jushou(JushouBase):
    """2010 修订版：将你的武将牌翻面。"""

    id = "jushou"
    flips = True


class JushouOld(JushouBase):
    """2008 初版：跳过你下个回合。"""

    id = "jushou_old"
    flips = False


class JushouFlow(Flow):
    def __init__(self, engine, skill, game):
        super().__init__(engine.context)
        self.engine = engine
        self.game = game
        self.skill = skill
        self.owner = skill.owner
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【据守】：是否摸三张牌？",
                    reason="jushou")
        return self.current_result()

    def advance(self, response=None):
        if self.stage != "confirm":
            raise RuntimeError("JushouFlow cannot advance from stage " + self.stage)
        if response is None or not response.confirmed:
            return self.complete({"applied": False})
        self.owner.skill_state.set(self.skill.id, "used", 1, ResetScope.TURN)
        self.context.apply(DrawCardsAtom(self.owner, 3))
        self.game.add_log("%s 发动【%s】，摸三张牌"
                          % (self.owner.name, self.skill.name))
        if self.skill.flips:
            flip_player(self.game, self.owner, reason=self.skill.name)
        else:
            # 2008 初版：把"跳过下一个回合"记在标记里，由 TurnMixin.start_turn
            # 在回合开始时消费——它是一条通用规则，任何角色都可能被跳过回合。
            self.owner.add_mark(self.skill.id, "skip_turn", 1)
            self.game.add_log("%s 将跳过自己的下一个回合" % self.owner.name)
        return self.complete({"applied": True})


# ==================================================
# 魏延 · 狂骨
# ==================================================


class Kuanggu(Skill):
    """锁定技：对距离 1 以内的角色每造成 1 点伤害，回复 1 点体力。"""

    id = "kuanggu"
    name = "狂骨"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED, priority=-10),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        damage = event.payload.get("damage")
        amount = int(event.payload.get("amount", 0) or 0)
        if damage is None or amount <= 0:
            return False
        target = getattr(damage, "target", None)
        if target is None or target is self.owner:
            return False
        return self._within_one(context.state, target)

    def _within_one(self, game, target):
        """狂骨只看**伤害来源自己**到目标的距离，不能拿别人当起点。"""

        from src.game.rules import DistanceRule

        try:
            return DistanceRule.distance(game, self.owner, target) <= 1
        except (TypeError, ValueError, AttributeError):
            return False

    def resolve(self, context, event):
        game = context.state
        amount = int(event.payload.get("amount", 0) or 0)
        before = self.owner.hp
        context.apply(RecoverHpAtom(self.owner, amount))
        healed = self.owner.hp - before
        if healed:
            game.add_log("%s 的【狂骨】回复 %d 点体力" % (self.owner.name, healed))


class Liegong(Skill):
    """使用【杀】指定目标后，把满足条件的目标标记为"不可响应"。"""

    id = "liegong"
    name = "烈弓"

    def bindings(self):
        return (SkillBinding(EventType.CARD_USED, priority=40),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        card = event.payload.get("card")
        if card is None or getattr(card, "name", None) != "SHA":
            return False
        targets = event.payload.get("targets") or ()
        return any(self._qualifies(context.state, target) for target in targets)

    def _qualifies(self, game, target):
        if target is None:
            return False
        if game.current_turn_player is not self.owner or game.phase != "play":
            return False
        hand = len(getattr(target, "hand", ()) or ())
        return (hand >= int(getattr(self.owner, "hp", 0))
                or hand <= int(getattr(self.owner, "attack_range", 1)))

    def resolve(self, context, event):
        game = context.state
        card = event.payload["card"]
        marked = []
        for target in event.payload.get("targets") or ():
            if not self._qualifies(game, target):
                continue
            targets = getattr(card, "_cannot_respond_targets", None)
            if targets is None:
                targets = set()
                mark_card_flag(card, "_cannot_respond_targets", targets)
            targets.add(id(target))
            marked.append(target.name)
        if marked:
            self.owner.skill_state.add(self.id, "used", 1, ResetScope.TURN)
            game.add_log("%s 的【烈弓】使此【杀】对 %s 不可被响应"
                         % (self.owner.name, "、".join(marked)))


# ==================================================
# 技能表
# ==================================================

WIND_SKILLS = (
    active(
        "guhuo",
        "蛊惑",
        "出牌阶段，你可以说出一种基本牌或非延时锦囊的牌名，并将一张手牌正面朝下打出。"
        "其他角色可以依次质疑：若为真，质疑者各失去 1 点体力，且该牌只有为红桃时才照常生效；"
        "若为假，质疑者各摸一张牌，且该牌弃置无效。无人质疑时该牌按其声明的牌名结算。",
        can_activate=_can_guhuo,
        activate=_activate_guhuo,
        spec=ActiveSkillSpec(),
        tags=("active",),
    ),
    triggered(
        "buqu",
        "不屈",
        "锁定技，当你体力降到 0 或更低时，每扣减 1 点体力就从牌堆翻开一张牌置于你的武将牌上；"
        "只要这些牌的点数互不相同，你就不会死去。体力回到 0 以上后弃置全部「不屈」牌。",
        factory=Buqu,
        kind=SkillKind.LOCKED,
    ),
    triggered(
        "shensu",
        "神速",
        "回合开始前，你可以选择一至两项：1. 跳过该回合的判定阶段与摸牌阶段；"
        "2. 跳过该回合的出牌阶段并弃一张装备牌。每选择一项，视为对一名角色使用一张【杀】。",
        factory=Shensu,
    ),
    triggered(
        "tianxiang",
        "天香",
        "每当你受到伤害时，你可以弃一张红桃手牌，将此伤害转移给一名其他角色，"
        "然后该角色摸 X 张牌（X 为其已损失的体力值）。",
        factory=Tianxiang,
    ),
    SkillDef(
        id="hongyan",
        name="红颜",
        description="锁定技，你的黑桃牌均视为红桃。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.SUIT_AS, value=_hongyan_suit, roles=("player",)),
        ),
    ),
    triggered(
        "leiji",
        "雷击",
        "当你使用或打出一张【闪】时，你可以令一名角色进行判定："
        "若结果为黑桃，你对其造成 2 点雷电伤害。",
        factory=LeijiBase,
    ),
    triggered(
        "leiji_old",
        "雷击（2008 版）",
        "当你打出一张【闪】时，你可以令一名角色进行判定："
        "若结果为黑桃，该角色失去 2 点体力。",
        factory=LeijiOld,
    ),
    SkillDef(
        id="guidao",
        name="鬼道",
        description="任意角色的判定牌生效前，你可以打出一张黑桃或梅花牌替换之。",
        kind=SkillKind.PASSIVE,
        judge_replacement=JudgeReplacement(
            candidates=_guidao_candidates,
            prompt="【鬼道】：是否用一张黑桃 / 梅花牌替换判定牌？",
        ),
    ),
    active(
        "huangtian",
        "黄天",
        "主公技，出牌阶段你可以指定一名其他群势力角色，将其一张【闪】或【闪电】交给你。",
        can_activate=_can_huangtian,
        activate=_activate_huangtian,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_huangtian_donors,
            target_prompt="【黄天】：请选择给你【闪】或【闪电】的群势力角色",
        ),
        tags=("active", "lord"),
        is_lord_skill=True,
    ),
    triggered(
        "jushou",
        "据守",
        "结束阶段，你可以摸三张牌。若如此做，将你的武将牌翻面。",
        factory=Jushou,
    ),
    triggered(
        "jushou_old",
        "据守（2008 版）",
        "结束阶段，你可以摸三张牌。若如此做，跳过你下个回合。",
        factory=JushouOld,
    ),
    triggered(
        "kuanggu",
        "狂骨",
        "锁定技，你对距离 1 以内的角色每造成 1 点伤害，你回复 1 点体力。",
        factory=Kuanggu,
        kind=SkillKind.LOCKED,
    ),
    triggered(
        "liegong",
        "烈弓",
        "出牌阶段，当你使用【杀】指定目标后，若其手牌数不小于你的体力值、"
        "或不大于你的攻击范围，则此【杀】对其不可被响应。",
        factory=Liegong,
    ),
)
