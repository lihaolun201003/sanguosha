"""魏势力武将技能：曹操 / 司马懿 / 郭嘉 / 张辽 / 夏侯惇。"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, UnequipAtom
from src.game.engine import EventType, Flow, FlowStatus
from src.game.engine.pending import PendingRequestType
from src.game.engine.skills import Skill, SkillBinding
from src.game.flows.damage import DamageContext, DamageFlow
from src.game.flows.judge import JudgeFlow
from src.game.rules import TurnPhase

from ..mechanics import ask_cards, ask_confirm, ask_option, judge
from ..definitions import (
    JudgeReplacement,
    PhaseReplacement,
    SkillDef,
    SkillKind,
    triggered,
)
from ..state import ResetScope

# ==================================================
# 夏侯惇 · 刚烈
# ==================================================


class Ganglie(Skill):
    """受到伤害后**可以**判定（"你可以"由夏侯惇本人决定）。

    这里只做两件事：判断能不能触发、把整条流程交给 ``GanglieFlow``。
    判定本身仍然走统一的 ``JudgeFlow``——鬼才 / 鬼道 / JudgeGate / 最终
    判定结果全部由它负责，这里不自己写一遍判定。
    """

    id = "ganglie"
    name = "刚烈"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        if event.payload.get("amount", 0) <= 0:
            return False
        source = damage.source
        if source is None or source is self.owner:
            return False
        return source.alive and self.owner.alive

    def resolve(self, context, event):
        # 一次受伤 = 一条独立流程。判定来源、选项、两个窗口的次序全部是流程
        # 自己的状态，不靠 Skill 实例上的临时字段拼——那种字段在"取消"或
        # "同一回合连续两次受伤"时会互相污染。
        GanglieFlow(context.services["engine"], self.owner,
                    event.payload["damage"].source).start()


class GanglieFlow(Flow):
    """刚烈全过程：是否发动 → 判定 → 伤害来源选择一项 →（弃牌则再选牌）。

    官方标准版：

        当你受到伤害后，你可以进行判定，若结果不为红桃，
        伤害来源选择一项：1.弃置两张手牌；2.受到你造成的 1 点伤害。

    四处不能省：

    * **"你可以"由夏侯惇本人决定**：刚烈不是锁定技，系统不能替他发动；
      拒绝之后不写状态、不抽判定牌、不产生 JudgeGate、不留日志。
    * **判定走统一 ``JudgeFlow``**：鬼才 / 鬼道参与，判定优先级由 JudgeGate
      维持，判据是**最终生效判定牌**的花色（改判之后以后者为准）。
    * **选择权在伤害来源手里**：不是刚烈拥有者替他选。
    * **弃哪两张也由他自己挑**：真正的 ``SELECT_CARDS`` 窗口
      （min = max = 2，候选就是他的手牌），不是程序取前两张。
    """

    DISCARD = "discard"
    DAMAGE = "damage"

    def __init__(self, engine, owner, source):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.source = source
        self.stage = "confirm"

    # ---- 1. 是否发动（夏侯惇本人决定）----

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【刚烈】：是否进行判定？", reason="ganglie")
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "confirm":
            if response is None or not response.confirmed:
                return self.complete({"applied": False})
            return self._begin_judge()
        if self.stage == "option":
            return self._after_option(response)
        return self._after_cards(response)

    # ---- 2. 判定（统一 JudgeFlow）----

    def _begin_judge(self):
        self.stage = "judge"
        flow, result = judge(self.engine, self.owner, "ganglie")
        if result is None:
            flow.on_complete = self._after_judge
            self.wait(flow)
            return self.current_result()
        return self._after_judge(result)

    def _after_judge(self, result):
        # 判据是**最终生效判定牌**的花色：司马懿改成红桃就不反击，
        # 改成非红桃就要反击——以后者为准。
        if result is None or getattr(result, "suit", None) == "heart":
            self.game.add_log(self.owner.name + " 的【刚烈】：判定为红桃，结束")
            return self.complete({"applied": True, "mode": "heart"})
        self.game.add_log(self.owner.name + " 发动【刚烈】")
        return self._ask_option()

    # ---- 3. 伤害来源选择一项 ----

    def _ask_option(self):
        if not self._can_discard():
            # 手牌不足两张：规则上没有"弃两张"这个选项，不摆一个假的二选一
            # 给玩家点（官方 FAQ：此时只能受到伤害）。
            self.game.message = ("%s 手牌不足两张，只能承受【刚烈】的伤害"
                                 % self.source.name)
            return self._hurt()
        self.stage = "option"
        ask_option(self.engine, self, source=self.owner, target=self.source,
                   prompt="【刚烈】：请选择一项",
                   reason="ganglie",
                   options=((self.DISCARD, "弃置两张手牌"),
                            (self.DAMAGE,
                             "受到 %s 造成的 1 点伤害" % self.owner.name)))
        return self.current_result()

    def _after_option(self, response):
        option = str(getattr(response, "option", "") or "")
        if option == self.DISCARD and self._can_discard():
            return self._ask_cards()
        return self._hurt()

    # ---- 4. 弃哪两张也由他自己挑 ----

    def _ask_cards(self):
        candidates = list(getattr(self.source, "hand", ()) or ())
        if len(candidates) < 2:
            # 选牌之前手牌变少了：退回"只能受伤"，绝不替他补牌。
            return self._hurt()
        self.stage = "cards"
        ask_cards(self.engine, self, source=self.owner, target=self.source,
                  prompt="【刚烈】：请选择要弃置的两张手牌",
                  reason="ganglie", candidates=candidates,
                  min_cards=2, max_cards=2)
        return self.current_result()

    def _after_cards(self, response):
        """**选满两张之后**才一起进弃牌堆——在此之前一张都不动。"""

        chosen = []
        for card in list(getattr(response, "cards", ()) or ()):
            if not any(item is card for item in self.source.hand):
                continue
            if any(item is card for item in chosen):
                continue
            chosen.append(card)
        if len(chosen) != 2:
            # 引擎已经校验过数量与候选；这里只兜"选牌期间牌被移走"的极端
            # 情况——兜不住就按规则退化为承受伤害，绝不替玩家补牌。
            return self._hurt()
        for card in chosen:
            self.context.apply(MoveCardAtom(
                card, source=self.source.hand,
                destination=self.game.deck.discard_pile))
        self.game.add_log("%s 弃置 %s 以免受【刚烈】" % (
            self.source.name,
            "、".join(getattr(card, "display_name", "?") for card in chosen)))
        return self.complete({"applied": True, "mode": self.DISCARD,
                              "cards": tuple(chosen)})

    # ---- 5. 承受伤害 ----

    def _can_discard(self):
        return len(getattr(self.source, "hand", ()) or ()) >= 2

    def _hurt(self):
        if self.source.alive and self.owner.alive:
            DamageFlow(self.engine, DamageContext(
                self.owner, self.source, 1)).start()
        return self.complete({"applied": True, "mode": self.DAMAGE})


# ==================================================
# 司马懿 · 反馈 / 鬼才
# ==================================================


class Fankui(Skill):
    """受到伤害后获得伤害来源的一张牌（手牌优先，否则装备）。"""

    id = "fankui"
    name = "反馈"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        if event.payload.get("amount", 0) <= 0:
            return False
        source = damage.source
        if source is None or source is self.owner or not source.alive:
            return False
        return bool(source.hand) or any(card is not None for card in source.equipment.values())

    def resolve(self, context, event):
        source = event.payload["damage"].source
        card = None
        if source.hand:
            # 手牌是**暗牌**：规则上玩家不指定具体哪一张，所以在真实牌堆里
            # 随机取一张。固定取第一张会让双方都能靠记牌预测，等于泄露暗牌。
            card = _random_hand_card(context.state, source)
            context.apply(MoveCardAtom(card, source=source.hand, destination=self.owner.hand))
        else:
            for slot, equipped in source.equipment.items():
                if equipped is None:
                    continue
                # 装备离场走统一入口：失去装备事件照常发出。
                card = equipped
                context.apply(UnequipAtom(source, slot, self.owner.hand))
                break
        if card is not None:
            # 拿走的是别人的牌：亮在桌面停一下，让对手看清是哪张。
            engine = context.services.get("engine")
            if engine is not None:
                engine.show_taken_card(card, source, self.owner, to_hand=True)
            context.state.add_log(self.owner.name + " 发动【反馈】，获得 " + source.name + " 一张牌")


def _random_hand_card(game, player):
    """从一名角色的手牌里随机取一张（暗牌取牌的统一样式）。

    ``Player.hand`` 是列表，取首元素会让"获得一张手牌"变成可预测的行为——
    对手能记住自己手牌的顺序，等于提前知道自己会丢哪张。真实对局里这张牌
    是未知的，所以用对局的随机源抽。
    """

    hand = list(getattr(player, "hand", ()) or ())
    if not hand:
        return None
    rng = getattr(game, "rng", None)
    if rng is None:                                      # pragma: no cover - 防御
        return hand[0]
    return hand[rng.randrange(len(hand))]


def guicai_candidates(game, player, judge_context):
    """鬼才可以打出的替换牌：手上任意一张手牌。"""

    if judge_context is None or judge_context.locked:
        return []
    return list(player.hand)


# ==================================================
# 郭嘉 · 天妒 / 遗计
# ==================================================


class Tiandu(Skill):
    """判定牌生效后获得它（在鬼才替换之后取最终生效的那张）。"""

    id = "tiandu"
    name = "天妒"

    def bindings(self):
        return (SkillBinding(EventType.JUDGE_FINISHED),)

    def can_trigger(self, context, event):
        result = event.payload.get("result")
        if result is None or result.target is not self.owner:
            return False
        card = result.card
        return self.owner.alive and any(item is card for item in context.state.processing_zone)

    def resolve(self, context, event):
        card = event.payload["result"].card
        context.apply(MoveCardAtom(
            card,
            source=context.state.processing_zone,
            destination=self.owner.hand,
        ))
        context.state.add_log(self.owner.name + " 发动【天妒】，获得判定牌")


class Yiji(Skill):
    """每受到 1 点伤害后摸两张牌。"""

    id = "yiji"
    name = "遗计"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        return (
            damage is not None
            and damage.target is self.owner
            and event.payload.get("amount", 0) > 0
            and self.owner.alive
        )

    def resolve(self, context, event):
        amount = int(event.payload.get("amount", 0))
        context.apply(DrawCardsAtom(self.owner, amount * 2))
        self.owner.skill_state.add(self.id, "drawn", amount * 2, ResetScope.TURN)
        context.state.add_log(self.owner.name + " 发动【遗计】，摸 " + str(amount * 2) + " 张牌")


# ==================================================
# 曹操 · 奸雄
# ==================================================


def _jianxiong_cards(damage):
    """造成这次伤害的实体牌（虚拟牌取它的 source_cards）。"""

    card = damage.card
    if card is None:
        return []
    sources = getattr(card, "source_cards", None)
    if sources:
        return list(sources)
    return [card]


class Jianxiong(Skill):
    """受到伤害后获得造成此伤害的牌（拿真实实体牌，不按牌名重造）。"""

    id = "jianxiong"
    name = "奸雄"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        if event.payload.get("amount", 0) <= 0 or not self.owner.alive:
            return False
        game = context.state
        for card in _jianxiong_cards(damage):
            if _card_in_pile(game, card):
                return True
        return False

    def resolve(self, context, event):
        game = context.state
        gained = 0
        for card in _jianxiong_cards(event.payload["damage"]):
            source = _pile_containing(game, card)
            if source is None:
                continue
            context.apply(MoveCardAtom(card, source=source, destination=self.owner.hand))
            gained += 1
        if gained:
            game.add_log(self.owner.name + " 发动【奸雄】，获得造成伤害的牌")


def _pile_containing(game, card):
    for zone in (game.processing_zone, game.deck.discard_pile):
        if any(item is card for item in zone):
            return zone
    return None


def _card_in_pile(game, card):
    return _pile_containing(game, card) is not None


# ==================================================
# 张辽 · 突袭
# ==================================================


def tuxi_targets(game, player):
    """突袭目标：其他存活且手牌不为空角色，按座次从自己下家开始。"""

    ordered = game.seats.alive_players_in_order(start_after=player, include_start=False)
    return [other for other in ordered if other.hand]


def tuxi_can_offer(game, player):
    """摸牌阶段能否发动：本回合没用过，且至少有一个有手牌的其他角色。"""

    if game.game_over or not player.alive:
        return False
    if player.skill_state.get("tuxi", "used", 0):
        return False
    return bool(tuxi_targets(game, player))


class TuxiFlow(Flow):
    """【突袭】：放弃摸牌，改为获得至多两名其他角色各一张手牌。

    TurnFlow 在摸牌阶段开始前询问是否发动；发动后本回合挂起，等这里选完
    目标、取完牌再继续。取消选择 = 不发动，摸牌阶段照常结算。
    """

    MAX_TARGETS = 2

    def __init__(self, engine, player, on_complete=None):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.player = player
        self.on_complete = on_complete
        self.stage = "select_targets"

    def advance(self, response=None):
        if self.stage != "select_targets":
            raise RuntimeError("TuxiFlow cannot advance from stage " + self.stage)
        if response is None:
            return self._request_targets()
        targets = list(getattr(response, "targets", ()) or ())
        if getattr(response, "passed", False) or not targets:
            self.game.add_log(self.player.name + " 放弃发动【突袭】")
            return self._finish(False)
        return self._take_cards(targets)

    def _request_targets(self):
        candidates = tuxi_targets(self.game, self.player)
        request = self.engine.pending.create(
            PendingRequestType.SELECT_TARGETS,
            source=self.player,
            target=self.player,
            prompt="【突袭】：请选择至多 " + str(self.MAX_TARGETS) + " 名有手牌的角色",
            owner_flow=self,
            min_cards=1,
            max_cards=min(self.MAX_TARGETS, len(candidates)),
            request_context={"reason": "tuxi", "candidates": candidates},
        )
        self.wait(request)
        self.engine.present_or_auto_resolve(request)
        return self.current_result()

    def _take_cards(self, targets):
        taken = []
        for target in targets:
            # 引擎侧复核：目标必须仍然合法（存活、不是自己、仍有手牌）。
            if target is self.player or not target.alive or not target.hand:
                continue
            # 同上：突袭拿的也是暗牌，随机取一张。
            card = _random_hand_card(self.game, target)
            if card is None:
                continue
            self.context.apply(
                MoveCardAtom(card, source=target.hand, destination=self.player.hand)
            )
            # 逐张亮出来再飞走：一次拿两张时也能看清。
            self.engine.show_taken_card(card, target, self.player, to_hand=True)
            taken.append((target, card))
        self.player.skill_state.set("tuxi", "used", 1, ResetScope.TURN)
        if taken:
            self.game.add_log(
                self.player.name + " 发动【突袭】，获得 "
                + "、".join(target.name for target, _card in taken) + " 各一张手牌"
            )
        return self._finish(True)

    def _finish(self, applied):
        return self.complete({"applied": applied})

    def on_settled(self, result):
        # 同 WuxieResponseChain：调用方要的是 {"applied": bool} 这个业务值，
        # 不是包着它的 FlowResult。在这里声明一次，基类那次被幂等挡掉。
        self.notify_on_complete({"applied": bool(self.result and
                                                 self.result.get("applied"))})


def tuxi_flow(game, player):
    return TuxiFlow(game.engine, player)


# ==================================================
# 注册
# ==================================================

WEI_SKILLS = (
    triggered(
        "ganglie",
        "刚烈",
        "当你受到伤害后，你可以进行判定，若结果不为红桃，伤害来源选择一项：1.弃置两张手牌；2.受到你造成的 1 点伤害。",
        factory=Ganglie,
    ),
    triggered(
        "fankui",
        "反馈",
        "当你受到伤害后，你可以获得伤害来源的一张牌。",
        factory=Fankui,
    ),
    SkillDef(
        id="guicai",
        name="鬼才",
        description="在任意角色的判定牌生效前，你可以打出一张手牌代替之。",
        kind=SkillKind.PASSIVE,
        judge_replacement=JudgeReplacement(candidates=guicai_candidates),
        tags=("judge_replacement",),
    ),
    triggered(
        "tiandu",
        "天妒",
        "当你的判定牌生效后，你可以获得此牌。",
        factory=Tiandu,
    ),
    triggered(
        "yiji",
        "遗计",
        "当你受到 1 点伤害后，你可以摸两张牌。",
        factory=Yiji,
    ),
    triggered(
        "jianxiong",
        "奸雄",
        "当你受到伤害后，你可以获得造成此伤害的牌。",
        factory=Jianxiong,
    ),
    SkillDef(
        id="tuxi",
        name="突袭",
        description="摸牌阶段，你可以放弃摸牌，改为获得至多两名其他角色各一张手牌。",
        kind=SkillKind.PASSIVE,
        phase_replacement=PhaseReplacement(
            phase=TurnPhase.DRAW,
            prompt="【突袭】：是否放弃摸牌，改为获得至多两名角色各一张手牌？",
            can_offer=tuxi_can_offer,
            flow=tuxi_flow,
        ),
        tags=("phase_replacement",),
    ),
)
