"""蜀势力武将技能：张飞 / 黄月英 / 关羽 / 赵云。"""

from src.card import mark_card_flag
from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom
from src.game.conversion import (
    PLAY_CONTEXT,
    RESPONSE_CONTEXT,
    CardConversion,
)
from src.game.engine import EventType, Flow, FlowStatus
from src.game.engine.skills import Skill, SkillBinding
from src.game.flows.judge import JudgeFlow
from src.game.rules import TurnPhase

from ..definitions import (
    ActiveSkillSpec,
    ModifierSpec,
    SkillDef,
    SkillKind,
    active,
    triggered,
)
from ..modifiers import ModifierKind
from ..mechanics import (
    ask_cards,
    ask_confirm,
    ask_option,
    ask_targets,
    is_non_delay_trick,
    optional_trigger,
)
from ..state import ResetScope


# ==================================================
# 张飞 · 咆哮
# ==================================================


class Paoxiao(Skill):
    """锁定技，出牌阶段使用【杀】无次数限制（纯 modifier，无事件监听）。"""

    id = "paoxiao"
    name = "咆哮"

    def bindings(self):
        return ()

    def resolve(self, context, event):  # pragma: no cover - 锁定技不响应事件
        return


# ==================================================
# 黄月英 · 集智 / 奇才
# ==================================================


class Jizhi(Skill):
    """使用**非延时**类锦囊牌时，**可以**摸一张牌。

    官方经典版文本明确是"非延时类锦囊"：以前只判 ``category == "trick"``，
    而卡牌目录里【乐不思蜀】【闪电】【兵粮寸断】的类别同样是锦囊（那是为了
    【帷幕】"黑色锦囊"要覆盖它们），于是使用延时锦囊也会白摸一张。
    描述里的"可以"以前也没落地——``resolve`` 直接摸牌，玩家无法拒绝。
    """

    id = "jizhi"
    name = "集智"

    def bindings(self):
        return (SkillBinding(EventType.CARD_USED),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        return is_non_delay_trick(event.payload.get("card"))

    def resolve(self, context, event):
        optional_trigger(
            context, self.owner,
            prompt="【集智】：是否摸一张牌？", reason="jizhi", label="集智",
            effect=self._draw).start()

    def _draw(self, flow):
        flow.context.apply(DrawCardsAtom(self.owner, 1))
        self.owner.skill_state.add(self.id, "drawn", 1, ResetScope.TURN)
        flow.game.add_log(self.owner.name + " 发动【集智】，摸一张牌")
        return True


# ==================================================
# 关羽 · 武圣
# ==================================================


def _is_red(card):
    """红色实体牌（虚拟牌不能作为转换源，避免递归）。"""

    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "card_color", None) == "red"


# ==================================================
# 赵云 · 龙胆
# ==================================================


def _is_sha(card):
    return not getattr(card, "is_virtual", False) and getattr(card, "name", None) == "SHA"


def _is_shan(card):
    return not getattr(card, "is_virtual", False) and getattr(card, "name", None) == "SHAN"


SHU_SKILLS = (
    SkillDef(
        id="paoxiao",
        name="咆哮",
        description="锁定技，出牌阶段你使用【杀】无次数限制。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.SLASH_QUOTA, value=99, roles=("player",)),
        ),
    ),
    SkillDef(
        id="jizhi",
        name="集智",
        description="当你使用一张非延时类锦囊牌时，你可以摸一张牌。",
        kind=SkillKind.PASSIVE,
        factory=Jizhi,
    ),
    SkillDef(
        id="qicai",
        name="奇才",
        description="锁定技，你使用锦囊牌无距离限制。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.TRICK_RANGE_IGNORE, value=1, roles=("player",)),
        ),
    ),
    SkillDef(
        id="wusheng",
        name="武圣",
        description="你可以将一张红色牌当【杀】使用或打出。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(
                skill_id="wusheng",
                matches=_is_red,
                name="SHA",
                contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT),
            ),
        ),
        tags=("conversion",),
    ),
    SkillDef(
        id="longdan",
        name="龙胆",
        description="你可以将【杀】当【闪】、【闪】当【杀】使用或打出。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(
                skill_id="longdan",
                matches=_is_sha,
                name="SHAN",
                contexts=(RESPONSE_CONTEXT,),
            ),
            CardConversion(
                skill_id="longdan",
                matches=_is_shan,
                name="SHA",
                contexts=(PLAY_CONTEXT, RESPONSE_CONTEXT),
            ),
        ),
        tags=("conversion",),
    ),
)


# ==================================================
# 刘备 · 仁德 / 激将
# ==================================================


def lord_skills_enabled(game, player):
    """主公技的唯一启用条件：身份模式下该角色是主公。

    FFA 没有主公，因此即使选了刘备 / 孙权也不会获得主公技。
    """

    mode = getattr(game, "mode", None)
    if mode is None or not getattr(mode, "uses_identities", False):
        return False
    from src.game.identity import Identity

    return getattr(player, "identity", None) is Identity.LORD


def _can_rende(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if not player.hand:
        return False, "没有可以交给他人的手牌"
    if not _rende_targets(game, player):
        return False, "没有其他存活角色"
    return True, ""


def _rende_targets(game, player):
    return [
        other for other in game.get_alive_players()
        if other is not player and getattr(other, "alive", True)
    ]


def _activate_rende(game, player, target=None, cards=None):
    """牌已经由引擎转交给目标（transfer_cards），这里只记录。"""

    if target is None or not cards:
        return False
    game.add_log(
        player.name + " 发动【仁德】→ " + target.name
        + "（交给 " + str(len(cards)) + " 张牌）")
    return True


def _jijiang_pending_request(game, player):
    """现在是不是"刘备被要求打出一张【杀】"的那个响应窗口。

    官方时机是"当你**需要使用或打出**一张【杀】时"：出牌阶段主动使用是一半，
    另一半是【决斗】【南蛮入侵】一类要求他打出【杀】的响应窗口（引擎里就是
    一条 ``RESPOND_CARD``、允许牌名含【杀】、目标是他的待回答请求）。
    """

    from src.game.engine.pending import PendingRequestType

    engine = getattr(game, "engine", None)
    pending = getattr(engine, "pending", None)
    request = pending.current if pending is not None else None
    if request is None or request.status != "pending":
        return None
    if request.request_type is not PendingRequestType.RESPOND_CARD:
        return None
    if request.target is not player:
        return None
    if "SHA" not in tuple(request.allowed_cards or ()):
        return None
    return request


def _jijiang_options(game, helper):
    """这名蜀势力角色现在能**打出**的【杀】有哪些方式（含技能转化）。

    判据不按牌名自己判断：实体【杀】、【武圣】把红牌当【杀】、【龙胆】把
    【闪】当【杀】都在规则层的"可打出牌"查询里。只接受素材全在**手牌**里的
    方式——那张牌要先交到刘备手里，再由他使用 / 打出。
    """

    actions = getattr(game, "card_actions", None)
    if actions is None:                                   # pragma: no cover
        return []
    context = actions.response_context(helper, allowed_names=("SHA",))
    options = []
    for option in actions.respondable_options(helper, context):
        if option.result_name != "SHA" or not option.source_cards:
            continue
        if any(not any(item is card for item in helper.hand)
               for card in option.source_cards):
            continue
        options.append(option)
    return options


def _jijiang_helpers(game, player):
    """**其他存活的蜀势力角色**（按座次）。

    判据只有"阵营 + 存活"，**与手牌完全无关**。以前这里用 ``_jijiang_options``
    过滤掉"手里没有真能打出的【杀】"的人，而那份结果又被 ``_can_jijiang`` 拿
    去决定技能按钮亮不亮——刘备只看一眼按钮，就知道某个蜀将手里有没有
    【杀】，等于把别人的暗手牌读了出来（看得到按钮 = 队友有杀）。

    谁能提供、提供哪一张，由被问的人**自己**在被问到的那一刻用规则层查询
    回答（见 ``JijiangFlow``）：查不出可用的【杀】就等同于拒绝。
    """

    return [
        other for other in game.seats.alive_players_in_order(
            start_after=player, include_start=False)
        if getattr(other, "kingdom", None) == "shu"
    ]


def jijiang_ready(game, player):
    """【激将】的发动资格（**不看任何人的手牌**）。

    官方文本里唯一的条件就是"有其他蜀势力角色"：主公技启用 + 存在其他存活的
    蜀势力角色。别的流程（【借刀杀人】问"这次使用【杀】能不能交给激将"）读的
    也是这一份，不各自再写一套判据。
    """

    if not lord_skills_enabled(game, player):
        return False
    if game.game_over or not player.alive:
        return False
    return bool(_jijiang_helpers(game, player))


def _jijiang_sha_usable(game, player, target):
    """刘备现在能不能对 ``target`` 使用一张【杀】（规则层判据）。"""

    from src.game.available_actions import AvailableActions
    from ..mechanics import virtual_card

    probe = virtual_card("SHA", player, (), category="basic")
    profile = AvailableActions(game).target_profile(player, card=probe)
    return any(item is target for item in profile.players)


def _jijiang_targets(game, player):
    """这张【杀】的合法目标：全部来自规则层的目标候选查询，不自算距离。"""

    from src.game.available_actions import AvailableActions
    from ..mechanics import virtual_card

    probe = virtual_card("SHA", player, (), category="basic")
    profile = AvailableActions(game).target_profile(player, card=probe)
    return [target for target in profile.players if getattr(target, "alive", True)]


def _can_jijiang(game, player):
    if not lord_skills_enabled(game, player):
        return False, "只有主公可以发动"
    if game.game_over or not player.alive:
        return False, "无法发动"
    if not _jijiang_helpers(game, player):
        return False, "没有其他蜀势力角色"
    if _jijiang_pending_request(game, player) is not None:
        # 被要求打出【杀】的响应窗口：无论是不是自己的回合都成立。
        return True, ""
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段，或被要求打出【杀】时发动"
    if player.sha_used and not game.can_use_unlimited_sha(player):
        return False, "本回合已经使用过【杀】"
    if not _jijiang_targets(game, player):
        return False, "没有【杀】的合法目标"
    return True, ""


def _activate_jijiang(game, player, target=None, cards=None):
    """激将：请其他蜀势力角色替你打出一张【杀】。

    目标与素材都在流程里问：出牌阶段先由刘备选这张【杀】的合法目标（候选来自
    规则层），然后按座次逐个询问**每一个**存活蜀势力角色——每人自己决定提供
    不提供、以及提供哪一张。流程走完之前不改动任何牌。
    """

    request = _jijiang_pending_request(game, player)
    JijiangFlow(game.engine, player, request).start()
    return True


def jijiang_use_flow(engine, owner, target, *, validator=None, on_used=None):
    """在**别的流程**里借着【激将】完成"对 ``target`` 使用一张【杀】"。

    与出牌阶段用的是同一条 ``JijiangFlow``（逐个询问蜀势力角色、每人自己
    决定是否提供 / 提供哪一张）。区别只有两处：目标是调用方定好的（不再问
    刘备），以及这次使用忽略出牌次数限制、不要求是刘备自己的出牌阶段——
    【借刀杀人】要求的这次使用本来就不算"出牌阶段使用【杀】"。

    ``validator`` 是目标合法性的规则层查询（``callable(game, owner, target)
    -> bool``），``on_used`` 在【杀】的结算真正走完之后接回调用方。返回已经
    启动的流程对象（可能当场就跑完了，调用方按 ``status`` 判断）。
    """

    flow = JijiangFlow(
        engine, owner, target=target, ignore_usage_limit=True,
        validator=validator, use_on_complete=on_used)
    flow.start()
    return flow


class JijiangFlow(Flow):
    """激将：刘备需要使用 / 打出一张【杀】时，逐个询问蜀势力角色。

    * 出牌阶段：先选【杀】的合法目标（候选只来自规则层的目标查询），
      再问同伴；
    * 响应窗口（【决斗】【南蛮入侵】…）：目标由那条请求决定，不需要选；
    * 外部流程（【借刀杀人】）：目标由调用方给定，合法性由调用方给的规则层
      查询裁决（见 ``jijiang_use_flow``）；
    * **每一个存活的蜀势力角色都会被问到**——"谁手里有【杀】"不是刘备能查
      的信息（那会泄露别人的暗手牌）。能不能提供，由被问的人自己在回答的
      那一刻用规则层查询决定：查不出可用的【杀】就等同于拒绝。
    * 所有人都不提供时这次什么都不发生（一张牌都不动）。
    """

    PROVIDE = "provide"
    PASS = "pass"

    def __init__(self, engine, owner, request=None, *, target=None,
                 ignore_usage_limit=False, validator=None, use_on_complete=None):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.request = request
        self.target = target
        #: "这次使用由别的流程发起"（目标由调用方给定）：它决定了结算走哪条路。
        self.external = request is None and target is not None
        #: 外部流程发起的这次使用忽略出牌次数限制（借刀杀人）。
        self.ignore_usage_limit = bool(ignore_usage_limit)
        #: 目标合法性的规则层查询（外部流程给的那一份）。
        self.validator = validator
        #: 【杀】的结算走完之后接回调用方（借刀杀人的收尾）。
        self.use_on_complete = use_on_complete
        self.helpers = []
        self.index = 0
        self.helper = None
        self.options = []
        self.stage = "target" if (request is None and target is None) else "helper"

    def begin(self):
        self.helpers = _jijiang_helpers(self.game, self.owner)
        if not self.helpers:
            return self.complete({"applied": False})
        if self.stage == "target":
            candidates = _jijiang_targets(self.game, self.owner)
            if not candidates:
                return self.complete({"applied": False})
            ask_targets(self.engine, self, source=self.owner, target=self.owner,
                        prompt="【激将】：请选择这张【杀】的目标",
                        reason="jijiang", candidates=candidates,
                        min_targets=1, max_targets=1,
                        context={"cancellable": True})
            return self.current_result()
        if self.external and not self._target_legal():
            # 调用方给定的目标已经不合法：不再往下问同伴（一张牌都不动）。
            return self.complete({"applied": False})
        return self._ask_next()

    def advance(self, response=None):
        if self.stage == "target":
            return self._after_target(response)
        if self.stage == "card":
            return self._after_card(response)
        if self.stage == "asking":
            return self._after_choice(response)
        # 外部流程（借刀杀人）拿到的子流程：等它结束由 ``resume_from_child``
        # 接着走，这里不会收到别的阶段。
        return self.current_result()

    # ---- 1. 出牌阶段：这张【杀】打谁 ----

    def _after_target(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            self.game.add_log("%s 取消发动【激将】" % self.owner.name)
            return self.complete({"applied": False})
        target = targets[0]
        if not _jijiang_sha_usable(self.game, self.owner, target):
            self.game.message = "【激将】：这个目标已经不再合法。"
            return self.complete({"applied": False})
        self.target = target
        self.stage = "helper"
        return self._ask_next()

    # ---- 2. 逐个询问同伴（按座次）----

    def _ask_next(self):
        """按座次问下一位蜀势力角色。

        这里**不做任何手牌过滤**：过滤会把"谁手里有【杀】"变成刘备能观察到的
        信号（谁被问了 / 问完是谁），那正是这次要修掉的泄露。每个人都照问，
        他能不能提供由他自己回答（``_after_choice`` 里的规则层查询）。
        """

        if self.game.game_over:
            return self.complete({"applied": False})
        while self.index < len(self.helpers):
            helper = self.helpers[self.index]
            self.index += 1
            if not getattr(helper, "alive", True):
                continue
            if helper is self.owner or getattr(helper, "kingdom", None) != "shu":
                continue
            self.helper = helper
            self.options = _jijiang_options(self.game, helper)
            self.stage = "asking"
            ask_option(
                self.engine, self, source=self.owner, target=helper,
                prompt="【激将】：%s 需要使用一张【杀】，是否为他打出一张？"
                       % self.owner.name,
                reason="jijiang",
                options=((self.PROVIDE, "打出一张【杀】"),
                         (self.PASS, "不打出")))
            return self.current_result()
        self.game.add_log("【激将】：没有蜀势力角色打出【杀】")
        return self.complete({"applied": False})

    def _after_choice(self, response):
        if str(getattr(response, "option", "") or "") != self.PROVIDE:
            return self._ask_next()
        # 被问的人回答"提供"的**这一刻**才去查规则层：他现在能拿出哪一张
        # 【杀】（实体杀 / 武圣 / 龙胆一类转化）。查不出可用的就等同于拒绝，
        # 继续问下一位——不替他挑、也不回头改别人的答案。
        self.options = _jijiang_options(self.game, self.helper)
        if not self.options:
            return self._ask_next()
        cards = self._source_cards()
        if len(self.options) == 1 or len(cards) <= 1:
            return self._deliver(self.options[0])
        self.stage = "card"
        ask_cards(self.engine, self, source=self.owner, target=self.helper,
                  prompt="【激将】：请选择要打出的一张【杀】",
                  reason="jijiang", candidates=cards, min_cards=1, max_cards=1)
        return self.current_result()

    def _source_cards(self):
        cards = []
        for option in self.options:
            for card in option.source_cards:
                if not any(card is other for other in cards):
                    cards.append(card)
        return cards

    def _after_card(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self._ask_next()
        chosen = cards[0]
        option = next((item for item in self.options
                       if any(card is chosen for card in item.source_cards)), None)
        if option is None:
            # 素材在询问期间被移走：这次提供不成立，问下一位。
            return self._ask_next()
        return self._deliver(option)

    # ---- 3. 交牌 + 由刘备使用 / 打出 ----

    def _target_legal(self):
        """外部流程给定的目标现在还合法吗——判据来自规则层查询，不自算距离。"""

        if self.target is None or not getattr(self.target, "alive", True):
            return False
        if self.target is self.owner or not getattr(self.owner, "alive", True):
            return False
        if self.validator is not None:
            return bool(self.validator(self.game, self.owner, self.target))
        return _jijiang_sha_usable(self.game, self.owner, self.target)

    def _deliver(self, option):
        from src.game.engine import RespondCardAction, UseCardAction

        helper = self.helper
        materials = list(option.source_cards)
        if not materials or any(
                not any(item is card for item in helper.hand) for card in materials):
            return self._ask_next()
        virtual = self.game.card_actions.effective_card(option)
        if virtual is None:
            return self._ask_next()

        if self.request is not None:
            # 响应窗口：这张牌由刘备"打出"，所以要先由他打出它。
            pending = _jijiang_pending_request(self.game, self.owner)
            if pending is None or pending is not self.request:
                self.game.add_log("【激将】：响应窗口已经结束，本次作废")
                return self.complete({"applied": False})
            self._move_to_owner(materials)
            self.game.add_log("%s 发动【激将】，%s 替他打出一张【杀】"
                              % (self.owner.name, helper.name))
            self.engine.submit(RespondCardAction(
                self.owner, self.request.request_id, virtual))
            return self.complete({"applied": True})

        if self.external:
            # 外部流程（借刀杀人）要求的这次使用：时机判据不是"刘备的出牌
            # 阶段"，而是调用方给的那份规则层查询（它同时表达"忽略出杀次数
            # 限制"的口径）。结算走完之后由 ``use_on_complete`` 接回调用方。
            if not self._target_legal():
                self.game.message = "【激将】：目标已经不再合法。"
                return self.complete({"applied": False})
            self._move_to_owner(materials)
            self.game.add_log("%s 发动【激将】，%s 替他打出一张【杀】→ %s"
                              % (self.owner.name, helper.name, self.target.name))
            self.engine.submit(UseCardAction(
                self.owner, virtual, [self.target],
                ignore_usage_limit=self.ignore_usage_limit,
                on_complete=self.use_on_complete))
            return self.complete({"applied": True})

        if self.target is None or not _jijiang_sha_usable(
                self.game, self.owner, self.target):
            self.game.message = "【激将】：目标已经不再合法。"
            return self.complete({"applied": False})
        if (self.game.current_turn_player is not self.owner
                or self.game.phase != "play"
                or _jijiang_pending_request(self.game, self.owner) is not None):
            # 询问期间局面已经变了（远程同伴隔了几帧才回答、回合已经翻页）：
            # 这张牌一张都不动，不能把同伴的牌塞进刘备手里用不出去。
            self.game.message = "【激将】：现在不能使用【杀】了。"
            return self.complete({"applied": False})
        self._move_to_owner(materials)
        self.game.add_log("%s 发动【激将】，%s 替他打出一张【杀】→ %s"
                          % (self.owner.name, helper.name, self.target.name))
        self.engine.submit(UseCardAction(
            self.owner, virtual, [self.target], ignore_usage_limit=False))
        return self.complete({"applied": True})

    def _move_to_owner(self, materials):
        """同伴的牌交到刘备手里：他随后要用 / 打出的是**这张实体牌**。

        虚拟牌（【武圣】把红牌当【杀】）的实体素材必须先在刘备的区域里，
        使用 / 响应流程才能把它移进处理区——合成一张"凭空出现的牌"会让
        素材留在同伴手里白用一次。
        """

        for card in materials:
            self.context.apply(MoveCardAtom(
                card, source=self.helper.hand, destination=self.owner.hand))



# ==================================================
# 诸葛亮 · 观星 / 空城
# ==================================================


def _empty_hand(game, query):
    """空城：没有手牌时不能成为【杀】或【决斗】的目标。"""

    target = query.get("target")
    card = query.get("card")
    if target is None or card is None:
        return 0
    if getattr(target, "hand", None):
        return 0
    return 1 if getattr(card, "name", None) in ("SHA", "JUEDOU") else 0


class Guanxing(Skill):
    """准备阶段打开可选窗口，在排好牌堆顶前暂停回合。"""

    id = "guanxing"
    name = "观星"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START),)

    def can_trigger(self, context, event):
        game = context.services["engine"].game
        return (event.source is self.owner and self.owner.alive
                and event.payload.get("phase") is TurnPhase.PREPARE
                and not event.payload.get("skipped")
                and not self.owner.skill_state.get(self.id, "used", 0)
                and bool(game.deck.draw_pile))

    def resolve(self, context, event):
        GuanxingFlow(context.services["engine"], self.owner).start()


class GuanxingFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.viewed = []
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【观星】：是否查看并排列牌堆顶的牌？", reason="guanxing")
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "confirm":
            if response is None or not response.confirmed:
                return self.complete({"applied": False})
            pile = self.game.deck.draw_pile
            count = min(5, len(self.game.get_alive_players()), len(pile))
            if count == 0:
                return self.complete({"applied": False})
            self.viewed = list(pile[-count:])
            self.owner.skill_state.set("guanxing", "used", 1, ResetScope.TURN)
            self.game.add_log("%s 发动【观星】，查看牌堆顶 %d 张" %
                              (self.owner.name, count))
            self.stage = "order"
            ask_cards(
                self.engine, self, source=self.owner, target=self.owner,
                prompt="【观星】：请按放回牌堆顶的顺序依次选择（先选的在上）",
                reason="guanxing_order", candidates=self.viewed,
                min_cards=count, max_cards=count, zone="public_pool",
                context={"cancellable": True})
            return self.current_result()
        ordered = () if response is None or response.passed else response.cards
        _apply_guanxing(self.game, self.viewed, ordered)
        return self.complete({"applied": True})


def _apply_guanxing(game, viewed, ordered):
    """把查看过的牌按玩家选择顺序放回牌堆顶。

    ``draw_pile[-1]`` 是下一个被摸到的牌，所以"第一个选的"要落在尾部：
    ``pile[-n:] = reversed(ordered)``。``ordered`` 为空（玩家放弃排序）
    表示保持原序，不做任何移动。
    """

    chosen = list(ordered or ())
    pile = game.deck.draw_pile
    count = len(viewed)
    if not chosen or count == 0 or len(pile) < count:
        game.message = "【观星】：保持原顺序。"
        game.add_log(game.current_turn_player.name + " 的【观星】保持原顺序")
        return
    if len(chosen) != count:
        return
    # 牌堆在这一轮里被改动过（理论上不会发生）：保持原样，不冒风险重排。
    if any(not any(card is item for item in pile) for card in viewed):
        return
    pile[-count:] = list(reversed(chosen))
    game.message = "【观星】：已按选择的顺序放回牌堆顶。"
    game.add_log(game.current_turn_player.name + " 的【观星】调整了牌堆顶顺序")


# ==================================================
# 马超 · 马术 / 铁骑
# ==================================================


class Tieji(Skill):
    """使用【杀】指定目标后判定：红色则该目标不能使用【闪】。"""

    id = "tieji"
    name = "铁骑"

    def bindings(self):
        return (SkillBinding(EventType.CARD_USED),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        card = event.payload.get("card")
        return bool(
            card is not None
            and getattr(card, "name", None) == "SHA"
            and event.payload.get("targets")
        )

    def resolve(self, context, event):
        engine = context.services["engine"]
        card = event.payload["card"]
        judge = JudgeFlow(engine, self.owner, "tieji")
        outcome = judge.start()
        if outcome.status is FlowStatus.WAITING:
            judge.on_complete = lambda result: self._after_judge(card, result)
            return
        self._after_judge(card, outcome.value)

    def _after_judge(self, card, result):
        if result is None:
            return
        # 判定结果的颜色读 JudgeResult 的真实字段（card_color 是实体牌的字段名，
        # 判定结果上叫 color）。
        if getattr(result, "color", None) != "red":
            return
        # 此【杀】不可被响应：标记写在实体牌上，由杀的结算统一读取
        # （Game.cannot_respond_to），这里不复制响应流程。
        mark_card_flag(card, "_cannot_respond", True)
        self.owner.skill_state.add(self.id, "hit", 1, ResetScope.TURN)


SHU_EXTRA_SKILLS = (
    active(
        "rende",
        "仁德",
        "出牌阶段，你可以将任意数量的手牌交给一名其他角色。",
        can_activate=_can_rende,
        activate=_activate_rende,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_rende_targets,
            target_prompt="【仁德】：请选择一名其他角色",
            variable_cost=True,
            transfer_cards=True,
            cost_prompt="【仁德】：请选择要交给他人的手牌",
        ),
        tags=("active", "card_transfer"),
    ),
    active(
        "jijiang",
        "激将",
        "主公技，当你需要使用或打出一张【杀】时，你可以令其他蜀势力角色"
        "选择是否打出一张【杀】（视为由你使用或打出）。",
        can_activate=_can_jijiang,
        activate=_activate_jijiang,
        spec=ActiveSkillSpec(
            # 目标（这张【杀】打谁）与素材都在技能自己的流程里问：出牌阶段
            # 先选目标，再按座次逐个询问蜀势力角色；响应窗口里目标由那条
            # 请求决定。界面不需要预先收集任何输入。
            needs_target=False,
        ),
        tags=("active", "lord"),
        is_lord_skill=True,
    ),
    triggered(
        "guanxing",
        "观星",
        "准备阶段开始时，你可以查看牌堆顶的若干张牌（数量为存活角色数，至多五张），"
        "并将它们以任意顺序放回牌堆顶。",
        factory=Guanxing,
    ),
    SkillDef(
        id="kongcheng",
        name="空城",
        description="锁定技，若你没有手牌，你不能成为【杀】或【决斗】的目标。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(
                kind=ModifierKind.TARGET_FORBIDDEN,
                value=_empty_hand,
                roles=("target",),
            ),
        ),
    ),
    SkillDef(
        id="mashu",
        name="马术",
        description="锁定技，你计算与其他角色的距离时减一。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(
                kind=ModifierKind.DISTANCE_OUTGOING, value=-1, roles=("source",)),
        ),
    ),
    triggered(
        "tieji",
        "铁骑",
        "当你使用【杀】指定目标后，你可以进行判定：若结果为红色，该角色不能使用【闪】。",
        factory=Tieji,
    ),
)

SHU_SKILLS = SHU_SKILLS + SHU_EXTRA_SKILLS
