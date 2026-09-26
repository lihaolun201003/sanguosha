"""远程真人控制器：决策发给他的客户端，答案由网络送回来。

这是唯一一个**异步**控制器：``present()`` / ``take_turn()`` 只是把请求登记
并发出，绝不会阻塞主线程——流程停在原地等，Pygame 主循环照常跑帧。

所有回答都要经过 ``DecisionRegistry`` 的纯数据校验，再由这里映射回真实的
Game 对象并提交**原本就存在**的 GameAction：远程玩家和本地真人走的是同一条
规则路径，区别只在"谁提供答案"。

Phase 11.3 追加的两件事：

* 可出牌 / 可响应列表带上 **View-As 选项**（龙胆把【闪】当【杀】、武圣把红牌
  当【杀】、急救把红牌当【桃】…）。客户端只选"用哪几张实体牌、走哪个技能"，
  虚拟牌由房主用 ``CardActionDiscovery`` 现算现造，客户端从不自己构造 VirtualCard。
* 每个请求都带 ``base_revision``：客户端手上的视图必须已经包含这次决策需要的
  信息，才能打开面板。
"""

from src.actions import CallbackAction, WaitAction

from src.game.available_actions import ActionType, AvailableActions, TargetMode
from src.game.conversion import PLAY_CONTEXT, RESCUE_CONTEXT, RESPONSE_CONTEXT
from src.game.engine import (
    ChooseOptionAction,
    ConfirmPendingAction,
    PassPendingAction,
    RespondCardAction,
    SelectCardsAction,
    SelectTargetsAction,
    UseCardAction,
)
from src.game.engine.events import Event, EventType
from src.game.engine.pending import PendingRequestType
from src.game.rules import TargetRule

from src.network.decisions import (
    ACTION_CANCEL,
    ACTION_END_PHASE,
    ACTION_PASS,
    ACTION_SKILL,
    ERR_BAD_CARD_COUNT,
    ERR_DECISION_CLOSED,
    ERR_ILLEGAL_CARD,
    ERR_ILLEGAL_SOURCE,
    ERR_OPERATION_FAILED,
    DecisionKind,
    DecisionRequest,
    card_entry,
    player_entry,
)

from .base import PlayerController

# 引擎的 PendingRequest 类型 → 线上决策类型（一一对应，不做技能特判）。
PENDING_KINDS = {
    PendingRequestType.RESPOND_CARD: DecisionKind.RESPOND_CARD,
    PendingRequestType.CONFIRM: DecisionKind.CONFIRM,
    PendingRequestType.CHOOSE_OPTION: DecisionKind.CHOOSE_OPTION,
    PendingRequestType.SELECT_CARDS: DecisionKind.SELECT_CARDS,
    PendingRequestType.SELECT_TARGETS: DecisionKind.SELECT_TARGETS,
}

# 桌面还在播动画时，出牌阶段的请求要等到空闲再发（最多等这么多轮）。
MAX_IDLE_RETRIES = 240

# "重铸"（置入弃牌堆并摸一张牌）在客户端是**与"使用"并列**的一种用法（例如
# 【铁索连环】的连环／重铸）。它不是 Card Action Discovery 的候选——重铸不
# 产生逻辑牌、也没有目标——所以由房主补一条同构条目下发；客户端点哪一条就回
# 哪一个 id，房主据此用规则层认得的 metadata 重建动作（见 ``_recast_action``）。
RECAST_ACTION_ID = "recast"

# 隐藏手牌的"不透明选择位"前缀（Phase 11.4 §9 / §19）。
#
# 顺手牵羊 / 过河拆桥 / 火攻这类"从别人手里拿牌"的选择：客户端必须能选，
# 但不能知道那是哪张牌。所以网络包里出现的不是真实 card_id，而是一个只在
# **本条请求内**有效的占位 token；真实 card_id → 牌的映射只留在房主内存里。
# token 里只有请求号与序号，不含任何牌面信息，也无法跨请求对应同一张牌。
OPAQUE_PREFIX = "hidden:"

# ==================================================
# 牌的区域语义（Phase 11.5 §15：客户端可见的"这张牌在哪里"）
#
# 以前这里靠"在不在手牌里"反推：不在手牌 = 装备牌。五谷丰登的公共牌因此
# 被标成"我的装备"，UI 只接受装备槽点击，游客点了公共牌没有任何提交。
# 现在改为**按真实容器判定**：公共池 / 处置区 / 桌面 / 判定区 / 弃牌堆 /
# 牌堆 / 手牌 / 装备槽各是一个明确的 zone，客户端据此决定画在哪、怎么点。
# ==================================================

ZONE_HAND = "hand"
ZONE_EQUIPMENT = "equipment"
ZONE_PUBLIC_POOL = "public_pool"
ZONE_TABLE = "table"
ZONE_JUDGE = "judge"
ZONE_DISCARD = "discard_pile"
ZONE_DRAW = "draw_pile"
ZONE_PROCESSING = "processing"

#: 这些区域里的牌**本来就是公开的**（谁都能看见牌面）。
PUBLIC_ZONES = frozenset({
    ZONE_EQUIPMENT, ZONE_PUBLIC_POOL, ZONE_TABLE, ZONE_JUDGE,
    ZONE_DISCARD, ZONE_PROCESSING,
})

#: 声明区域 → 客户端区域名（老请求里可能写成同义词）。
ZONE_ALIASES = {
    "hand": ZONE_HAND, "player_hand": ZONE_HAND, "hand_zone": ZONE_HAND,
    "equipment": ZONE_EQUIPMENT, "player_equipment": ZONE_EQUIPMENT,
    "public_pool": ZONE_PUBLIC_POOL, "selection_pool": ZONE_PUBLIC_POOL,
    "table": ZONE_TABLE, "processing": ZONE_PROCESSING,
    "judge": ZONE_JUDGE, "judgement": ZONE_JUDGE,
    "discard": ZONE_DISCARD, "discard_pile": ZONE_DISCARD,
    "draw": ZONE_DRAW, "draw_pile": ZONE_DRAW,
}


def normalize_zone(value, default=ZONE_HAND):
    text = str(value or "").strip()
    if not text:
        return default
    return ZONE_ALIASES.get(text, text)


def opaque_token(request_id, index):
    return "%s%d:%d" % (OPAQUE_PREFIX, int(request_id), int(index))


def is_opaque(token):
    return str(token or "").startswith(OPAQUE_PREFIX)


class TurnToken:
    """一次出牌阶段的上下文；结束整回合时调用 ``on_complete``。"""

    def __init__(self, on_complete, can_play=True):
        self.on_complete = on_complete
        self.can_play = bool(can_play)
        self.finished = False

    def finish(self):
        if self.finished:
            return False
        self.finished = True
        callback = self.on_complete
        self.on_complete = None
        if callback is not None:
            callback()
        return True


class RemoteHumanController(PlayerController):
    """远程真人（Host 侧）。"""

    asynchronous = True

    #: 进入出牌阶段后、发请求前的停顿（与 AI 每张牌之间的停顿同一套节奏）。
    TURN_PAUSE = 0.35
    #: 被别的交互挡住时的重试间隔。
    RETRY_PAUSE = 0.2
    #: 结束阶段被规则/动画挡下时的重试次数（超过就把决定权交回客户端）。
    MAX_END_RETRIES = 3

    def __init__(self, game, player, bridge=None):
        super().__init__(game, player)
        self.bridge = bridge
        self.registry = bridge.registry if bridge is not None else None
        self._turn = None
        self._idle_retries = 0
        self.last_local_error = ""
        #: 上一次回答映射失败的机器码（房主据此选拒绝原因，见 network.decisions）。
        self.last_local_code = ""
        #: 每条请求的"不透明 token → 真实牌"映射（只存在房主内存里）。
        self._opaque = {}

    # ==================================================
    # 状态
    # ==================================================

    @property
    def waiting(self):
        """是否有决策正等着这名玩家回答（卡死守卫据此停手）。"""

        if self.registry is None:
            return False
        return self.registry.current_for(self.player.player_id) is not None

    def _send(self, request):
        if self.bridge is None:
            raise RuntimeError("远程控制器没有网络桥，无法发起决策")
        return self.bridge.send_decision(self.player, request)

    def _cancel_decision(self, decision, reason):
        """作废一条等待中的决策，并告诉客户端别再答了。"""

        self.registry.cancel(decision.request_id, reason)
        if self.bridge is not None:
            self.bridge.session.send_to_player(
                self.player.player_id, "DECISION_CANCELLED",
                match_id=self._match_id(), request_id=decision.request_id,
                reason=reason)

    # ==================================================
    # PendingRequest（响应 / 确认 / 选牌 / 选目标 / 选选项）
    # ==================================================

    def present(self, request):
        kind = PENDING_KINDS.get(request.request_type)
        if kind is None:
            raise ValueError("远程控制器收到未知请求：" + str(request.request_type))
        if request.is_group:
            # 共享响应阶段（无懈）：一条请求同时问好几个人，每个人拿到的
            # 是**只属于自己的**候选，所以走单独的分发路径。
            self._present_group(request)
            return
        open_decision = self.registry.current_for(self.player.player_id)
        if open_decision is not None:
            if open_decision.request_id == request.request_id:
                # 同一条请求被重复驱动（_drive_pending_front 会这么做）：已经在
                # 客户端手上了，不重复发送，也不重置它的答案。
                return
            # 引擎换了要问的事：旧的那条客户端已经不可能答对，直接作废并
            # 通知客户端，然后把新请求发出去。出牌阶段面板同理（等这件事办完
            # 会重新发一次，见 resolve）。
            self._cancel_decision(
                open_decision,
                "被更高优先级的请求取代" if open_decision.local[0] == "turn"
                else "引擎改问了别的事情")
        wire = self._build_pending_request(kind, request)
        if wire is None:
            return                     # 已经在本地自动处理掉（例如无懈链没有牌可出）
        self.registry.open(wire, local=("pending", request))
        self._send(wire)
        self.registry.note("SEND", request_id=wire.request_id, kind=wire.kind,
                           player_id=wire.player_id)

    # ==================================================
    # 共享响应阶段（无懈）
    #
    # 一条引擎请求同时问好几个人，每人一条**独立的**网络决策（独立
    # request_id）：房主保留下全部候选，客户端只收到自己那一份。谁能响应该由
    # 引擎算（Card Action Discovery），这里只负责翻译与传输。
    # ==================================================

    def _present_group(self, request):
        if request.member_status(self.player) != "pending":
            return                     # 我没有资格 / 本轮已经答过：不打扰我
        round_id = int(request.context.get("round_id") or 0)
        open_decision = self.registry.current_for(self.player.player_id)
        if open_decision is not None:
            local = tuple(open_decision.local or ())
            if len(local) >= 3 and local[0] == "group" and local[1] is request \
                    and int(local[2]) == round_id:
                # 这一轮已经在客户端手上了：不重复发送，也不重置它的答案。
                return
            self._cancel_decision(open_decision, "引擎改问了别的事情")
        wire = self._build_group_request(request)
        if wire is None:
            return
        self.registry.open(wire, local=("group", request, round_id))
        self._send(wire)
        self.registry.note("SEND", request_id=wire.request_id, kind=wire.kind,
                           player_id=wire.player_id)

    def _build_group_request(self, request):
        """无懈阶段的**个人**请求：只带我此刻真的能打出的候选牌。"""

        cards, skills = self._response_cards(request, complete_only=True)
        if not cards:
            # 引擎只会问"有合法响应"的人；真出现（局面在这条请求建好之后
            # 又变了）就本地放弃，不白跑一次网络。
            self.submit(PassPendingAction(self.player, request.request_id))
            return None
        prompt = str(request.prompt or "")
        trick = request.context.get("card")
        return DecisionRequest(
            match_id=self._match_id(),
            request_id=self.registry.next_request_id(),
            player_id=self.player.player_id,
            kind=DecisionKind.RESPOND_CARD,
            prompt=prompt,
            context={
                "reason": "wuxie_chain",
                "source": getattr(request.source, "name", ""),
                "prompt": prompt,
                # 窗口号 / 轮次号：客户端与房主都靠它判断"这条回答属于哪一轮"。
                "window_id": int(request.context.get("window_id") or 0),
                "round_id": int(request.context.get("round_id") or 0),
                "card": (card_entry(trick) if trick is not None else None),
                # 无懈阶段没有"当前结算目标"，UI 不牵指向箭头（见 client_fx）。
                "sequential": False,
            },
            cards=cards,
            constraints={
                "min_cards": 1, "max_cards": 1,
                "min_targets": 0, "max_targets": 0,
                "allow_cancel": False, "allow_pass": True,
                "allowed_skills": list(skills),
            },
        )

    def withdraw(self, request, reason=""):
        """这一轮不用我回答了（有人抢先打出了无懈）：作废我的那条决策。"""

        decision = self.registry.current_for(self.player.player_id)
        if decision is None or not decision.open:
            return None
        local = tuple(decision.local or ())
        if len(local) < 3 or local[0] != "group" or local[1] is not request:
            return None
        self._cancel_decision(decision, reason or "本轮已经不需要你回答了")
        return None

    def _build_pending_request(self, kind, request):
        responder = self.player
        constraints = {
            "min_cards": 0, "max_cards": 0,
            "min_targets": 0, "max_targets": 0,
            "allow_cancel": bool(request.context.get("cancellable", False)),
            "allow_pass": False,
        }
        cards, targets, options = [], [], []
        context = {
            "reason": request.context.get("reason", ""),
            "source": getattr(request.source, "name", ""),
            "prompt": request.prompt,
        }

        if kind is DecisionKind.RESPOND_CARD:
            # 响应的"牌数"不是恒定的 1：丈八蛇矛一类多来源转化要两张实体牌
            # 才能凑出一个逻辑牌（两张手牌当【杀】）。约束由候选方式本身给出
            # ——房主仍然是唯一知道"这次最少几张、最多几张"的一方。
            cards, skills = self._response_cards(request)
            constraints["allow_pass"] = True
            minimum = min([int(item.get("min_sources") or 1) for item in cards] or [1])
            maximum = max([int(item.get("max_sources") or 1) for item in cards] or [1])
            constraints["min_cards"] = max(1, minimum)
            constraints["max_cards"] = max(constraints["min_cards"], maximum)
            constraints["allowed_skills"] = list(skills)

        elif kind is DecisionKind.CONFIRM:
            options = [
                {"value": True, "label": "发动"},
                {"value": False, "label": "不发动"},
            ]

        elif kind is DecisionKind.CHOOSE_OPTION:
            from src.game.skills.mechanics import option_label

            options = [
                {"value": value, "label": option_label(request, value)}
                for value in request.options
            ]

        elif kind is DecisionKind.SELECT_CARDS:
            owner = request.context.get("zone_owner", responder)
            constraints["min_cards"] = int(request.min_cards)
            constraints["max_cards"] = int(request.max_cards or request.min_cards)
            constraints["allow_cancel"] = bool(request.context.get("cancellable", False))
            cards = self._card_candidates(
                owner, request.context.get("candidates", ()),
                request.context.get("zone", "hand"),
                request_id=request.request_id)
            # 有上下文的选择（火攻）：把"已经公开亮出的那张牌"一起下发，
            # 客户端才能画出专门的界面（谁展示了什么、我要弃哪一张）。
            # 这张牌在规则上已经公开，不存在泄露；窗口一关就不再出现。
            revealed = request.context.get("revealed_card")
            if revealed is not None:
                context["revealed_card"] = card_entry(revealed)
                revealed_by = request.context.get("revealed_by")
                context["revealed_by"] = str(getattr(revealed_by, "player_id", "") or "")
            caster = request.context.get("caster")
            if caster is not None:
                context["caster"] = str(getattr(caster, "player_id", "") or "")

        elif kind is DecisionKind.SELECT_TARGETS:
            candidates = list(request.context.get("candidates", ()))
            constraints["min_targets"] = max(1, int(request.min_cards or 1))
            constraints["max_targets"] = max(
                constraints["min_targets"], int(request.max_cards or request.min_cards or 1))
            # 本地真人的选目标界面允许取消（映射为 PassPendingAction）。
            constraints["allow_cancel"] = True
            targets = [player_entry(player) for player in candidates]

        # 响应请求里一张合法牌都没有：和本地真人一样自动放弃，不用白跑一次网络。
        if kind is DecisionKind.RESPOND_CARD and not cards:
            self.submit(PassPendingAction(responder, request.request_id))
            return None

        return DecisionRequest(
            match_id=self._match_id(),
            request_id=request.request_id,
            player_id=responder.player_id,
            kind=kind,
            prompt=request.prompt,
            context=context,
            cards=cards,
            targets=targets,
            options=options,
            constraints=constraints,
        )

    def _card_candidates(self, owner, candidates, zone="hand", request_id=0):
        """候选牌：自己的牌发真实卡面，别人**手里**的牌退化成不透明占位 token。

        装备区、判定区、公共牌池、处置区、桌面、弃牌堆里的牌本来就是公开的
        （顺手牵羊可以拿装备、五谷的牌本来就是明牌），照常发真实 card_id；
        只有"从别人手里拿 / 弃"才换成 ``hidden:<请求号>:<序号>``。客户端因此
        只知道"对方有 N 张可选"，不知道是哪几张；真实映射留在房主内存里
        （``_opaque``），回答时再换回真牌。

        每条候选都带 ``owner_id`` / ``zone`` / ``slot``：客户端据此决定这张牌
        画在自己的手牌区、装备槽、桌面公共区还是判定区（Phase 11.4 §7 /
        Phase 11.5 §15）。``zone`` 一律按**真实容器**判定，绝不从"不在手牌
        里"反推——五谷丰登的公共牌被当成装备牌正是那个反推造成的。
        """

        index_of = self._zone_index()
        declared = normalize_zone(zone)
        entries = []
        owner_id = str(getattr(owner, "player_id", "") or "")
        for index, card in enumerate(list(candidates)):
            found_zone, slot = index_of.get(id(card), (None, None))
            card_zone = found_zone or declared
            visible = owner is self.player or card_zone != ZONE_HAND
            if visible:
                entry = card_entry(card)
                entry["zone"] = card_zone
                entry["slot"] = slot
            else:
                token = opaque_token(request_id, index)
                self._remember_opaque(request_id, token, card)
                entry = {
                    "card_id": token,
                    "name": "",
                    "label": "未知牌",
                    "suit": None,
                    "rank": None,
                    "category": "",
                    "face_down": True,
                    "zone": ZONE_HAND,
                    "slot": None,
                }
            entry["owner_id"] = owner_id
            entry["owner_name"] = str(getattr(owner, "name", "") or "")
            entries.append(entry)
        return entries

    def _zone_index(self):
        """``id(牌) → (区域名, 装备槽)``：一次调用建一次，不做逐张线性扫描。

        一张实体牌在任意时刻只可能待在一个容器里，所以按对象身份建反查表是
        安全的；找不到的牌（技能临时区一类）由调用方用请求声明的区域兜底。
        """

        game = self.game
        index = {}

        def note(container, zone, slot=None):
            for item in container or ():
                if item is not None and id(item) not in index:
                    index[id(item)] = (zone, slot)

        note(getattr(game, "public_card_pool", None), ZONE_PUBLIC_POOL)
        note(getattr(game, "processing_zone", None), ZONE_PROCESSING)
        note(getattr(getattr(game, "deck", None), "draw_pile", None), ZONE_DRAW)
        note(getattr(getattr(game, "deck", None), "discard_pile", None), ZONE_DISCARD)
        for player in game.players:
            note(getattr(player, "hand", None), ZONE_HAND)
            note(getattr(player, "judgement_zone", None), ZONE_JUDGE)
            for slot, card in (getattr(player, "equipment", None) or {}).items():
                if card is not None and id(card) not in index:
                    index[id(card)] = (ZONE_EQUIPMENT, slot)
        for card, slot in getattr(game, "table_cards", ()) or ():
            if card is not None and id(card) not in index:
                index[id(card)] = (ZONE_TABLE, slot)
        return index

    def _remember_opaque(self, request_id, token, card):
        mapping = self._opaque.setdefault(int(request_id), {})
        mapping[token] = card
        # 只保留最近几条请求的映射：回答过的请求不会再被引用。
        if len(self._opaque) > 8:
            for key in sorted(self._opaque)[:-8]:
                self._opaque.pop(key, None)

    def _resolve_card_token(self, request_id, token):
        """回答里的 card_id → 真实牌：不透明 token 先查映射表，再按 id 全局找。"""

        mapping = self._opaque.get(int(request_id)) or {}
        card = mapping.get(str(token))
        if card is not None:
            return card
        return self._card_by_id(token)

    # ==================================================
    # View-As：一张实体牌"还能当什么用"
    #
    # 这里只查询引擎的 Card Action Discovery（唯一规则来源），把它的结果翻译
    # 成"客户端能选的东西"。客户端选的永远只是"哪几张实体牌 + 哪个技能"，
    # 虚拟牌由房主用 effective_card 现造，客户端从不构造 VirtualCard。
    # ==================================================

    def _context_for_request(self, request):
        """这条引擎请求对应哪一种 Action Discovery 场合。"""

        context = dict(getattr(request, "context", None) or {})
        reason = str(context.get("reason") or "")
        allowed = tuple(getattr(request, "allowed_cards", ()) or ())
        if reason == "dying_rescue":
            return self.game.card_actions.rescue_context(
                self.player, request, context.get("dying_player"), allowed_names=allowed)
        return self.game.card_actions.response_context(
            self.player, request, allowed_names=allowed)

    def _actions(self):
        """本玩家视角的统一动作查询层（Phase 15A 的唯一合法性来源）。"""

        from src.game.available_actions import AvailableActions

        return AvailableActions(self.game)

    def _option_view(self, option, context=None):
        """一个候选动作 → 客户端可读的条目（含它自己的合法目标）。

        "现在能不能这样用"与"能打谁"都来自 ``AvailableActions``：目标规则 /
        数量走 ``CardEffect.target_rule_for`` / ``target_bounds_for`` 的**动态**
        查询（技能可以改写它们），素材与结果牌走 ``CardActionDiscovery``。
        这里只负责按玩家把描述编码成既有 Decision 载荷——不自己算规则。

        【闪】【无懈可击】这类由响应系统处理的牌没有 V2 的 CardEffect 对象，
        因此在出牌场合会被跳过、在响应场合照常下发。
        """

        play_context = context is None or getattr(context, "is_play", True)
        card = self.game.card_actions.effective_card(option)
        if card is None:
            return None
        if self.card_effect(card) is None and play_context:
            return None

        action = self._actions().action_from_option(
            self.player, option, context=context)
        if action is None:
            return None
        return self._action_entry(action)

    def _action_entry(self, action):
        """``AvailableAction`` → 客户端条目（载荷形状与 Phase 11 一致）。"""

        return {
            "action_id": action.action_id,
            "skill_id": action.source_skill_id,
            "skill_name": action.skill_name,
            "label": action.label,
            "detail": action.detail,
            "is_conversion": bool(action.is_conversion),
            "result_name": action.effective_card_name,
            "result_display": action.effective_display,
            "min_sources": int(action.min_sources),
            "max_sources": int(action.max_sources),
            "source_card_ids": list(action.source_ids),
            "targets": [item.entry() for item in action.target_candidates],
            "min_targets": int(action.min_targets),
            "max_targets": int(action.max_targets),
            "enabled": bool(action.enabled),
            "disabled_reason": str(action.disabled_reason or ""),
        }

    def _options_for_card(self, card, context):
        """这张实体牌当前的全部可用方式（普通使用在前，转换在后）。"""

        entries = []
        for option in self.game.card_actions.actions_for_card(self.player, card, context):
            view = self._option_view(option, context)
            if view is None:
                continue
            if not view["enabled"] and not getattr(option, "needs_more_sources", False):
                continue
            entries.append(view)
        return entries

    def _recast_view(self, card, context):
        """"重铸"这一条用法（能给就返回客户端可点的条目，否则返回 None）。

        重铸不是 discovery 的候选（不产生逻辑牌、也没有目标），但它同样是"这张
        牌现在的一种用法"，所以与 discovery 的候选并列下发。"能不能重铸"现在
        由共同查询回答（能力仍由牌自己的 ``CardEffect.can_recast`` 声明、
        ``can_use`` 判定），这里只把它编码成既有载荷。

        ``action_id`` 仍写成协议里约定的 ``RECAST_ACTION_ID``：客户端用它提交
        （见 ``_submit_recast``），内部查询用的 id 是 ``recast:<牌 id>``。
        """

        if not getattr(context, "is_play", True):
            return None
        action = self._actions().recast_action(self.player, card)
        if action is None:
            return None
        entry = self._action_entry(action)
        entry["action_id"] = RECAST_ACTION_ID
        return entry

    def _recast_action(self, card):
        """重铸动作：与本地真人 / AI / 共同查询用的是同一份 metadata 约定。"""

        from src.game.available_actions import RECAST_METADATA

        return UseCardAction(
            self.player, card, [], metadata=dict(RECAST_METADATA))

    def _response_cards(self, request, *, complete_only=False):
        """响应窗口里"能打出的牌 + 它们的全部转换方式"。

        除了牌名直接匹配的牌（例如手上就有【闪】），还包含能被技能转化成本次
        允许牌名的实体牌（龙胆把【闪】当【杀】、武圣把红牌当【杀】、急救把红牌
        当【桃】），以及多 source 转化的候选（丈八蛇矛：两张手牌当【杀】，
        ``min_sources`` = 2）。

        同一张实体牌可能有多种方式（【闪】直接打出，或被龙胆当【杀】打出），
        所以**按牌聚合**：一条候选带上它的全部方式，客户端才能像单机一样弹出
        "选择操作"面板，而不是被默认成第一种。

        响应窗口不需要选目标（响应由引擎的 PendingRequest 决定打给谁），
        因此这里把方式里的目标信息清空，避免客户端误以为要选人。

        ``complete_only`` 只给共享无懈阶段用：那里"有资格"必须等于"现在真的
        支付得出"，所以先用 ``respondable_options`` 过滤一遍（凑不齐 source 的
        多来源转化既不下发、也不算数），与"谁会收到询问"用的是同一条判据。
        """

        context = self._context_for_request(request)
        actions = self.game.card_actions
        options = (actions.respondable_options(self.player, context) if complete_only
                   else actions.usable_options(self.player, context))
        grouped = {}
        order = []
        for option in options:
            if len(option.source_cards) != 1:
                continue
            card = option.source_cards[0]
            view = self._option_view(option, context)
            if view is None or not view["enabled"]:
                continue
            view["targets"] = []
            view["min_targets"] = 0
            view["max_targets"] = 0
            key = id(card)
            if key not in grouped:
                entry = card_entry(card)
                entry["options"] = []
                entry["min_sources"] = int(view["min_sources"] or 1)
                entry["max_sources"] = int(view["max_sources"] or 1)
                entry["skill_ids"] = []
                grouped[key] = entry
                order.append(key)
            entry = grouped[key]
            entry["options"].append(view)
            entry["min_sources"] = min(entry["min_sources"], int(view["min_sources"] or 1))
            entry["max_sources"] = max(entry["max_sources"], int(view["max_sources"] or 1))
            if view["skill_id"] and view["skill_id"] not in entry["skill_ids"]:
                entry["skill_ids"].append(view["skill_id"])

        entries = [grouped[key] for key in order]
        skills = set()
        for entry in entries:
            skills.update(entry["skill_ids"])
        return entries, sorted(skills)

    def _resolve_option(self, context, source_cards, skill_id="", action_id="",
                        result_name=""):
        """客户端选回来的"实体牌 + 方式" → 引擎认可的 CardActionOption。

        客户端只回三样东西：**哪几张实体牌**、**哪个技能的转化**、**想用出
        哪张逻辑牌**。合法性一律在这里用引擎自己的规则重算：

        * ``actions_for_sources`` 会按 conversion 的 source 数量区间重新解释
          这组实体牌（两张牌凑出的【杀】就是在这里成形的）；
        * 多来源方式的 action_id 里带着具体 source，客户端选"方式"时拿到的是
          **候选态**的 id（还没有第二张牌），所以先按 id 精确匹配，匹配不上再
          退到"技能 + 结果牌名"（同一技能同一结果的不同 source 组合是等价的）；
        * 最后交给 ``card_actions.validate`` 复核一次。
        """

        wanted_skill = str(skill_id or "")
        wanted_action = str(action_id or "")
        wanted_result = str(result_name or "")
        candidates = []
        for option in self.game.card_actions.actions_for_sources(
                self.player, source_cards, context):
            if not option.enabled or option.needs_more_sources:
                continue
            if not context.allows(option.result_name):
                continue
            candidates.append(option)
        if not candidates:
            return None

        if wanted_action:
            for option in candidates:
                if option.action_id == wanted_action:
                    return self._validated_option(option, context)
        matches = [
            option for option in candidates
            if str(option.skill_id or "") == wanted_skill
            and (not wanted_result or str(option.result_name) == wanted_result)
        ]
        if not matches:
            # 老客户端只回 skill_id（不带结果牌名）：同一技能下的候选等价。
            matches = [
                option for option in candidates
                if str(option.skill_id or "") == wanted_skill
            ]
        if len(matches) > 1 and wanted_result:
            matches = [item for item in matches
                       if str(item.result_name) == wanted_result]
        if not matches:
            return None
        return self._validated_option(matches[0], context)

    def _validated_option(self, option, context):
        """来源区域 + 引擎校验：房主不接受客户端自称的"这是我手上的牌"。"""

        if option is None:
            return None
        if not self._sources_allowed(option):
            self.last_local_error = "来源牌不在允许的区域"
            self.last_local_code = ERR_ILLEGAL_SOURCE
            return None
        ok, reason = self.game.card_actions.validate(
            option, sources=option.source_cards, context=context)
        if not ok:
            self.last_local_error = str(reason or "")
            self.last_local_code = ERR_ILLEGAL_SOURCE
            return None
        return option

    def _sources_allowed(self, option):
        """这组实体牌真的能当这次动作的来源吗（区域 + 不重复）。

        普通使用必须来自手牌；转化按 conversion 自己声明的 ``source_zones``
        （武圣只看手牌、卸甲一类可以吃装备区的牌）。同一张实体牌不能算两次。
        """

        cards = list(getattr(option, "source_cards", ()) or ())
        if not cards:
            return False
        if len({id(card) for card in cards}) != len(cards):
            return False
        api = self.game.card_actions
        if not option.is_conversion:
            return all(api.zone_of(self.player, card) == "hand" for card in cards)
        conversion = api.conversion_of(option)
        zones = tuple(getattr(conversion, "source_zones", ()) or ("hand",))
        for card in cards:
            zone = api.zone_of(self.player, card)
            if zone is None or zone not in zones:
                return False
        return True

    def _announce_conversion(self, option):
        """转换真正提交后记录战报与技能浮字（与本地真人同一条路径）。"""

        if option is None or not option.is_conversion:
            return
        log = option.log_text
        if log:
            self.game.add_log(log)
        self.game.context.emit(Event(
            EventType.SKILL_TRIGGERED,
            source=self.player,
            payload={
                "skill_id": option.skill_id,
                "skill_name": option.skill_name,
                "card_action": option,
            },
        ))

    # ==================================================
    # 出牌阶段
    # ==================================================

    def take_turn(self, on_complete, can_play=True):
        self._turn = TurnToken(on_complete, can_play)
        self._idle_retries = 0
        if not can_play:
            # 出牌阶段被跳过：与 AI 一样直接收尾（不进入交互式弃牌）。
            self._turn.finish()
            return
        # 先让桌面静一下（和 AI 出牌之间留停顿是同一套节奏手段），
        # 再发请求：此时队列里的前一段结算已经播完。
        self.game.actions.add(WaitAction(self.TURN_PAUSE))
        self.game.actions.add(CallbackAction(self._request_play_phase))

    def _request_play_phase(self):
        game = self.game
        token = self._turn
        if token is None or token.finished:
            return
        if game.game_over or not self.player.is_alive:
            token.finish()
            return
        if game.current_turn_player is not self.player or game.phase != "play":
            return
        # 注意：这里不能看 game.busy——本方法就是被动作队列回调调用的，
        # 执行期间 busy 恒为真。要判断的是"还有没有别的交互在等答案"。
        if self.waiting or game.engine.pending.active or game.response.active or game.choice.active:
            # 共享无懈阶段、别人的响应窗口、房主结算期间都不发面板：那时这名
            # 玩家什么也做不了。等待本身可能很久（真人思考没有上限），所以
            # 这里**只做有限次重试**，别让动作队列一直显示"忙"；真的等超时了
            # 就交给房主那层补发（见 ``maybe_reprompt_turn``）。
            if self._idle_retries >= MAX_IDLE_RETRIES:
                return
            self._idle_retries += 1
            game.actions.add(WaitAction(self.RETRY_PAUSE))
            game.actions.add(CallbackAction(self._request_play_phase))
            return
        self._idle_retries = 0

        cards = self.playable_cards()
        # 目标数量是**逐张牌**不同的（杀 1 个、铁索最多 2 个、群体牌 0 个），
        # 所以这里只给出一个上界用于粗校验；精确数量在 _build_play_action
        # 里按该牌自己的 target_limits 复核。
        max_targets = max([int(card.get("max_targets") or 0) for card in cards] or [0])
        max_sources = max([int(card.get("max_sources") or 1) for card in cards] or [1])
        # 顶层 target 列表是所有牌、所有方式的合法目标**并集**：只用于粗校验
        # （"这个 id 是不是本请求发出去的"）；每张牌各自能打谁由
        # _build_play_action 用引擎复核。
        allowed_targets = []
        allowed_skills = []
        seen_targets = set()
        for card in cards:
            for item in card.get("options", ()):
                if item.get("skill_id") and item["skill_id"] not in allowed_skills:
                    allowed_skills.append(item["skill_id"])
            for item in list(card.get("targets", ())) + [
                    target for option in card.get("options", ())
                    for target in option.get("targets", ())]:
                if item.get("player_id") in seen_targets:
                    continue
                seen_targets.add(item.get("player_id"))
                allowed_targets.append(item)
        wire = DecisionRequest(
            match_id=self._match_id(),
            request_id=self.registry.next_request_id(),
            player_id=self.player.player_id,
            kind=DecisionKind.PLAY_PHASE,
            prompt="出牌阶段：选择一张牌使用，或结束回合",
            context={"phase": game.phase, "turn_player": self.player.player_id},
            cards=cards,
            targets=allowed_targets,
            constraints={
                "min_cards": 0, "max_cards": max_sources,
                "min_targets": 0, "max_targets": max_targets,
                "allow_cancel": True, "allow_pass": True, "allow_end_phase": True,
                "allowed_skills": allowed_skills,
                # 主动技与出牌是同一层决策的两种答案：一起下发，客户端才有
                # "在出牌阶段发动技能"的入口（Phase 11.4 §14）。
                "activatable": self._activatable_skills(),
            },
        )
        self.registry.open(wire, local=("turn", token))
        self._send(wire)
        self.registry.note("SEND", request_id=wire.request_id, kind=wire.kind,
                           player_id=wire.player_id)

    def _activatable_skills(self):
        """出牌阶段可以发动的主动技（含它们的目标候选与费用牌）。

        规则来源是**共同查询**：``SkillManager.activatable_skills`` 决定"能不能
        发动"，``skills.activation.activation_inputs`` 决定"要选谁 / 弃几张"。
        这里只把它编码成既有载荷，不再自己拼一份目标候选。
        """

        entries = []
        for action in self._actions().active_skills(self.player):
            definition = self.game.skill_registry.get(action.source_skill_id)
            if definition is None:
                continue
            entries.append({
                "skill_id": action.source_skill_id,
                "name": action.skill_name,
                "needs_target": action.min_targets > 0,
                "target_prompt": action.target_prompt,
                "cost_cards": int(action.min_sources),
                "cost_prompt": action.cost_prompt,
                "variable_cost": bool(action.variable_cost),
                "transfer_cards": bool(action.transfer_cards),
                "targets": [item.entry() for item in action.target_candidates],
                "cost_candidates": [dict(card_id=item.card_id, name=item.name,
                                         label=item.label)
                                    for item in action.source_candidates],
                "enabled": bool(action.enabled),
                "disabled_reason": str(action.disabled_reason or ""),
            })
        return entries

    def playable_cards(self):
        """出牌阶段可用的牌：每张牌附带它**全部**可用方式与合法目标。

        判定完全来自共同查询（Phase 15A）：``CardActionDiscovery`` 提供素材与
        转换候选，``CardEffect.target_rule_for/target_bounds_for`` 提供**动态**
        目标规则，重铸由 ``can_recast`` + ``can_use`` 判定。客户端只负责"挑"。

        每张牌的 ``options`` 里既有"直接使用"（``is_conversion=False``），也有
        技能转化（例如【龙胆】把【闪】当【杀】）；多 source 转化（两张手牌当
        【杀】一类）会带上 ``min_sources`` > 1，由客户端多选几张再确认。

        候选来源不限于手牌：转换可以声明"吃装备区的牌"（``source_zones``），
        所以自己装备区里可操作的牌也在候选里，并带上 ``zone`` / ``slot``，
        客户端才知道要画在装备槽上。
        """

        query = self._actions()
        context = self.game.card_actions.play_context(self.player)
        entries = []
        for card, zone, slot in self._play_candidates():
            actions = query.card_actions(self.player, card, context=context)
            options = []
            for action in actions:
                if action.kind not in (ActionType.PLAY, ActionType.VIEW_AS,
                                       ActionType.RECAST):
                    continue
                if not action.enabled and not action.needs_more_sources:
                    continue
                entry = self._action_entry(action)
                if action.kind == ActionType.RECAST:
                    # 协议里重铸固定用这个 id 提交（见 _submit_recast）。
                    entry["action_id"] = RECAST_ACTION_ID
                options.append(entry)
            if not options:
                continue
            entry = card_entry(card)
            entry["zone"] = zone
            entry["slot"] = slot
            entry["owner_id"] = self.player.player_id
            entry["owner_name"] = str(self.player.name or "")
            entry["options"] = options
            entry["min_sources"] = min(item["min_sources"] for item in options)
            entry["max_sources"] = max(item["max_sources"] for item in options)
            # 兼容 Phase 11.2 的字段：顶层给"直接使用"那一项的目标与数量。
            primary = next(
                (item for item in options if not item["is_conversion"] and item["enabled"]),
                next((item for item in options if item["enabled"]), options[0]),
            )
            entry["targets"] = primary["targets"]
            entry["min_targets"] = primary["min_targets"]
            entry["max_targets"] = primary["max_targets"]
            entry["skill_ids"] = sorted(
                {item["skill_id"] for item in options if item["skill_id"]})
            entries.append(entry)
        return entries

    def _play_candidates(self):
        """出牌阶段可能作为素材的实体牌：手牌 + 自己装备区。"""

        candidates = [(card, ZONE_HAND, None) for card in list(self.player.hand)]
        for slot, card in (self.player.equipment or {}).items():
            if card is not None:
                candidates.append((card, ZONE_EQUIPMENT, slot))
        return candidates

    def maybe_reprompt_turn(self):
        """兜底：轮到这名玩家、引擎空闲、他手里却没有等待中的面板 → 补一次。

        共享无懈阶段或别人的响应窗口会把出牌阶段面板挤掉；窗口结束后"把面板
        补回来"这件事不该靠动作队列里的重试循环（那会让桌面一直显示"忙"，
        也会拖慢整局）。房主每帧调用本方法（调用方负责节流）。

        返回是否真的补发了一次。
        """

        token = self._turn
        game = self.game
        if token is None or token.finished:
            return False
        if game.game_over or not self.player.is_alive:
            return False
        if game.current_turn_player is not self.player or game.phase != "play":
            return False
        if self.waiting or game.busy:
            return False
        if game.engine.pending.active or game.response.active or game.choice.active:
            return False
        self._idle_retries = 0
        self._request_play_phase()
        return True

    def _request_discard(self):
        """弃牌阶段：把手牌里要弃的张数交给客户端决定。"""

        game = self.game
        token = self._turn
        need = len(self.player.hand) - game.hand_limit(self.player)
        if need <= 0:
            self._finish_after_discard()
            return
        request_id = self.registry.next_request_id()
        wire = DecisionRequest(
            match_id=self._match_id(),
            request_id=request_id,
            player_id=self.player.player_id,
            kind=DecisionKind.SELECT_CARDS,
            prompt="请弃置 %d 张手牌（体力上限 %d）" % (need, game.hand_limit(self.player)),
            context={"phase": "discard", "turn_player": self.player.player_id},
            # 弃的是**自己的手牌**：候选带 owner_id / zone，客户端才会把它们
            # 画在手牌区（而不是桌面公共区）。
            cards=self._card_candidates(self.player, self.player.hand, "hand",
                                        request_id=request_id),
            constraints={"min_cards": need, "max_cards": need,
                         "allow_cancel": False, "allow_pass": False},
        )
        self.registry.open(wire, local=("discard", token))
        self._send(wire)

    def _finish_after_discard(self):
        token = self._turn
        if token is None:
            return
        # on_complete = 引擎给出的收尾（AI 同款）：弃到上限 → 结束交互流程 →
        # 下一个回合。此刻手牌已经合规，所以额外的弃牌是空操作。
        token.finish()

    # ==================================================
    # 回答落地（由网络桥在收到 DECISION_RESPONSE 时调用）
    # ==================================================

    def resolve(self, pending, result):
        """把校验通过的答案映射回真实 GameAction 并提交。

        返回 ``True`` 表示"房主已经把这个答案提交给引擎、并且引擎接受了"，
        返回 ``False`` 表示**没能落地**（局面变了 / 牌不对 / 规则拒绝）。
        房主据此决定是 ACK 还是 REJECT——False 绝不能被当成成功，否则
        客户端会以为自己在等结算，而引擎还在等输入（Phase 11.5 §7）。
        """

        self.last_local_error = ""
        self.last_local_code = ""
        kind = pending.request.kind
        if kind == DecisionKind.PLAY_PHASE:
            return self._resolve_play_phase(pending, result)
        if kind == DecisionKind.SELECT_CARDS and pending.local[0] == "discard":
            return self._resolve_discard(pending, result)
        if pending.local and pending.local[0] == "group":
            return self._resolve_group(pending, result)

        local_kind, request = pending.local
        if local_kind != "pending":
            self.last_local_error = "这条决策已经不在等待中"
            self.last_local_code = ERR_OPERATION_FAILED
            return False
        # "这条回答还算不算数"按 request_id 判断，和引擎自己的 require() 一致：
        # 不能拿对象身份比较——同一条请求可能被重新驱动过，对象换了但 id 没变。
        current = self.game.engine.pending.current
        if current is None or current.request_id != request.request_id:
            # 等待期间流程已经收尾（目标阵亡 / 效果取消）：这条回答作废。
            self.last_local_error = "请求已经不在等待中（stale）"
            self.last_local_code = ERR_OPERATION_FAILED
            return False
        request_id = request.request_id

        if kind == DecisionKind.RESPOND_CARD:
            if result.action in (ACTION_PASS, ACTION_CANCEL):
                return self._submitted(
                    self.submit(PassPendingAction(self.player, request_id)))
            sources = self._response_sources(result, request_id)
            if sources is None:
                return False
            context = self._context_for_request(request)
            option = self._resolve_option(
                context, sources, result.skill_id, result.action_id,
                result.result_name)
            if option is None:
                # 客户端选的牌 / 方式在这条响应窗口里不成立：拒绝，面板保留。
                if not self.last_local_code:
                    self.last_local_code = ERR_ILLEGAL_SOURCE
                    self.last_local_error = "这组牌不能这样响应"
                return False
            card = self.game.card_actions.effective_card(option)
            if card is None:
                self.last_local_code = ERR_ILLEGAL_SOURCE
                return False
            try:
                submitted = self.submit(RespondCardAction(
                    self.player, request_id, card, None))
            except ValueError as error:
                self.last_local_error = str(error)
                self.last_local_code = ERR_OPERATION_FAILED
                return False
            if not self._submitted(submitted):
                return False
            self._announce_conversion(option)

        elif kind == DecisionKind.CONFIRM:
            if result.confirm is None:
                self.last_local_code = ERR_INVALID_OPTION
                return False
            if not self._submitted(self.submit(ConfirmPendingAction(
                    self.player, request_id, bool(result.confirm)))):
                return False

        elif kind == DecisionKind.CHOOSE_OPTION:
            # 客户端原样回传**房主下发的那个值**（见 remote_table._submit_option），
            # 所以这里按值解析——按索引解析会与"下发的就是值"对不上，任何选项
            # 都会以"选项不是数字"被拒。数字索引仍然兼容，老客户端不必同步升级。
            values = list(request.options)
            chosen = result.option
            if chosen not in values:
                try:
                    index = int(chosen)
                except (TypeError, ValueError):
                    index = -1
                if 0 <= index < len(values):
                    chosen = values[index]
            if chosen not in values:
                self.last_local_code = ERR_INVALID_OPTION
                self.last_local_error = "选项不在可选范围内"
                return False
            if not self._submitted(self.submit(ChooseOptionAction(
                    self.player, request_id, chosen))):
                return False


        elif kind == DecisionKind.SELECT_CARDS:
            cards = [self._resolve_card_token(request_id, card_id)
                     for card_id in result.card_ids]
            if any(card is None for card in cards):
                self.last_local_code = ERR_ILLEGAL_CARD
                self.last_local_error = "选中的牌找不到"
                return False
            if len({id(card) for card in cards}) != len(cards):
                self.last_local_code = ERR_ILLEGAL_CARD
                self.last_local_error = "同一张牌被选了两次"
                return False
            if not self._submitted(self.submit(
                    SelectCardsAction(self.player, request_id, cards))):
                return False

        elif kind == DecisionKind.SELECT_TARGETS:
            targets = [self._player_by_id(player_id) for player_id in result.target_ids]
            if any(target is None for target in targets):
                self.last_local_code = ERR_ILLEGAL_TARGET
                self.last_local_error = "目标不存在"
                return False
            if not self._submitted(self.submit(
                    SelectTargetsAction(self.player, request_id, targets))):
                return False

        else:
            self.last_local_code = ERR_OPERATION_FAILED
            self.last_local_error = "未知决策类型：" + str(kind)
            return False

        # 这条响应可能顶掉了一张出牌阶段的面板：办完事之后把它补回来。
        if self._turn is not None and not self._turn.finished:
            self.game.actions.add(CallbackAction(self._request_play_phase))
        return True

    def _resolve_group(self, pending, result):
        """共享无懈阶段的一条回答：只对**它所属的那一轮**生效。

        本轮可能已经被别人抢先锁定（甚至已经开到下一轮），所以"这条回答还算
        不算数"要按 引擎请求身份 + 轮次号 + 我自己的成员状态 一起判断：
        算不算数由这里说了算，而**不是**由"手里少了一张牌"倒推。作废的回答
        不扣牌、不结算、也不会被算进下一轮。
        """

        _local_kind, request, round_id = tuple(pending.local)[:3]
        current = self.game.engine.pending.current
        if (current is not request
                or request.status != "pending"
                or int(request.context.get("round_id") or 0) != int(round_id)
                or request.member_status(self.player) != "pending"):
            self.last_local_code = ERR_DECISION_CLOSED
            self.last_local_error = "这一轮已经结束"
            return False

        if result.action in (ACTION_PASS, ACTION_CANCEL):
            # 个人放弃：只更新我的状态，别人还能继续响应。
            return self._submitted(
                self.submit(PassPendingAction(self.player, request.request_id)))

        sources = self._response_sources(result, pending.request.request_id)
        if sources is None:
            return False
        context = self._context_for_request(request)
        option = self._resolve_option(
            context, sources, result.skill_id, result.action_id,
            result.result_name)
        if option is None:
            if not self.last_local_code:
                self.last_local_code = ERR_ILLEGAL_SOURCE
                self.last_local_error = "这组牌不能这样响应"
            return False
        card = self.game.card_actions.effective_card(option)
        if card is None:
            self.last_local_code = ERR_ILLEGAL_SOURCE
            return False
        try:
            submitted = self.submit(RespondCardAction(
                self.player, request.request_id, card, None))
        except ValueError as error:
            # 引擎在提交瞬间判定这一轮已经被锁定 / 我已经答过：整条作废。
            self.last_local_error = str(error)
            self.last_local_code = ERR_DECISION_CLOSED
            return False
        if not self._submitted(submitted):
            return False
        self._announce_conversion(option)
        return True

    def _response_sources(self, result, request_id):
        """响应里的实体牌：查得到、属于自己、且没有重复。

        响应可以是一张牌（闪）也可以是两张（两张手牌当【杀】），所以这里只看
        "这批牌是不是自己的"，张数是否合法交给方式本身（``_resolve_option``
        会用 conversion 的 min/max sources 复核）。
        """

        sources = [self._resolve_card_token(request_id, card_id)
                   for card_id in result.card_ids]
        if not sources:
            self.last_local_code = ERR_BAD_CARD_COUNT
            self.last_local_error = "响应没有给出牌"
            return None
        api = self.game.card_actions
        for card in sources:
            if card is None:
                self.last_local_code = ERR_ILLEGAL_CARD
                self.last_local_error = "响应牌找不到"
                return None
            if api.zone_of(self.player, card) is None:
                self.last_local_code = ERR_ILLEGAL_SOURCE
                self.last_local_error = "响应牌不在自己手里"
                return None
        if len({id(card) for card in sources}) != len(sources):
            self.last_local_code = ERR_ILLEGAL_SOURCE
            self.last_local_error = "同一张牌被选了两次"
            return None
        return sources

    def _submitted(self, status):
        """GameAction 的提交结果：被取消（规则在提交瞬间变了）不算成功。"""

        value = getattr(getattr(status, "status", None), "value", None)
        if value == "cancelled":
            if not self.last_local_code:
                self.last_local_code = ERR_OPERATION_FAILED
                self.last_local_error = "引擎取消了这次操作"
            return False
        return True

    def _resolve_play_phase(self, pending, result):
        """出牌阶段的一条回答：结束回合 / 发动技能 / 用一张牌。

        返回 True 只代表"房主已经处理了这一条回答"——包括"局面变了、我把面板
        重新发一次"这种情况（那条回答本身被丢弃了，但流程继续推进，所以对
        客户端算已接受）。
        """

        token = pending.local[1]
        if token is not self._turn or token.finished:
            self.last_local_code = ERR_OPERATION_FAILED
            self.last_local_error = "这一回合已经结束"
            return False

        if result.action == ACTION_END_PHASE:
            return self._submit_end_phase()

        if result.action == ACTION_SKILL:
            return self._activate_skill(pending, result)

        sources = [self._card_by_id(card_id) for card_id in result.card_ids]
        if not sources or any(card is None for card in sources):
            self.last_local_code = ERR_ILLEGAL_CARD
            self.last_local_error = "选中的牌找不到"
            return False
        if len({id(card) for card in sources}) != len(sources):
            self.last_local_code = ERR_ILLEGAL_SOURCE
            self.last_local_error = "同一张牌被选了两次"
            return False

        if str(result.action_id or "") == RECAST_ACTION_ID:
            return self._submit_recast(sources)

        context = self.game.card_actions.play_context(self.player)
        option = self._resolve_option(
            context, sources, result.skill_id, result.action_id,
            result.result_name)
        if option is None:
            # 局面变了（例如那张牌已经不能这样用了）：不执行，重新问一次。
            # 这条回答仍然算"已处理"：客户端会拿到 ACK，然后收到新面板。
            self.last_local_error = self.last_local_error or "这张牌现在不能这样使用"
            self.game.actions.add(CallbackAction(self._request_play_phase))
            return True

        action = self._build_play_action(option, result.target_ids)
        if action is None:
            # 局面变了（例如目标已阵亡）：不执行，重新问一次。
            self.game.actions.add(CallbackAction(self._request_play_phase))
            return True

        action.on_complete = lambda _result: self.game.actions.add(
            CallbackAction(self._request_play_phase))
        result_status = self.submit(action)
        if not self._submitted(result_status):
            # 被取消（规则在提交瞬间又变了）：重新问一次，别停在原地。
            self.game.actions.add(CallbackAction(self._request_play_phase))
            return True
        self._announce_conversion(option)
        return True

    def _activate_skill(self, pending, result):
        """远程玩家按下技能键：走与 AI / 本地真人**同一个**发动入口。

        客户端只回"哪个技能 + 选中的目标 id + 费用牌 id"，合法性由
        ``resolve_activation`` 在执行前重新校验（状态可能已经变了）。
        """

        token = pending.local[1]
        if token is not self._turn or token.finished:
            self.last_local_code = ERR_OPERATION_FAILED
            self.last_local_error = "这一回合已经结束"
            return False
        entry = pending.request.skill_entry(result.skill_id)
        if entry is None:
            self.last_local_code = ERR_UNKNOWN_SKILL
            self.last_local_error = "这个技能不在本次可用范围"
            return False

        skill_id = str(entry.get("skill_id") or "")
        definition = self.game.skill_registry.get(skill_id)
        if definition is None or definition.activate is None:
            self.last_local_code = ERR_UNKNOWN_SKILL
            self.last_local_error = "这个技能没有可发动的实现"
            return False

        targets = [self._player_by_id(player_id) for player_id in result.target_ids]
        if any(target is None for target in targets):
            return False
        cards = [self._card_by_id(card_id) for card_id in result.card_ids]
        if any(card is None or card not in self.player.hand for card in cards):
            return False

        ok, message = self.game.skills.activate(
            self.player, skill_id,
            target=targets[0] if targets else None,
            cards=cards,
        )
        if not ok:
            # 局面变了（体力 / 手牌 / 标记已不满足）：不执行，重新问一次。
            self.last_local_error = str(message or "")
        # 无论成功与否都重新发一次出牌阶段面板：成功时接着出牌，失败时
        # 让客户端看到新状态（而不是停在一个已经作废的面板上）。
        self.game.actions.add(CallbackAction(self._request_play_phase))
        return True

    def _submit_recast(self, sources):
        """客户端点了"重铸"：用同一条规则路径重建动作并提交。

        面板发出去之后局面可能已经变了（牌被拿走、游戏结束），所以"现在还能不能
        重铸"在这里再算一遍：能力由 ``CardEffect.can_recast`` 声明，合法性由牌
        自己的 ``can_use`` 判定——房主从不接受客户端自称的重铸。
        """

        if len(sources) != 1:
            self.last_local_code = ERR_BAD_CARD_COUNT
            self.last_local_error = "重铸只针对一张牌"
            return False
        action = self._recast_action(sources[0])
        if not self.can_use(sources[0], action):
            # 局面变了：不执行，但要重新发一次面板（与"这张牌现在不能这样使用"
            # 同一条处理路径），否则游客会停在一个已经作废的面板上。
            self.last_local_error = "这张牌现在不能重铸"
            self.game.actions.add(CallbackAction(self._request_play_phase))
            return True
        # 重铸瞬间结算（摸一张牌），办完之后把出牌阶段面板补回来：这名玩家
        # 还在出牌阶段，必须能继续出牌或结束回合。
        action.on_complete = lambda _result: self.game.actions.add(
            CallbackAction(self._request_play_phase))
        if not self._submitted(self.submit(action)):
            return False
        return True

    def _build_play_action(self, option, target_ids):
        """按**房主自己的规则判定**重建出牌动作。

        客户端只提供"想用哪几张实体牌、走哪个技能、想打谁"；用出来的牌由
        房主用 ``effective_card`` 现造（View-As 时就是虚拟牌），目标是否仍然
        合法也在这里重新算——目标规则与数量来自共同查询的动态接口，规则决定
        目标的牌（桃 / 酒 / 群体锦囊）直接用规则结果，需要挑目标的牌则复核
        客户端给的那几个 id。
        """

        from src.game.available_actions import TargetMode

        card = self.game.card_actions.effective_card(option)
        if card is None:
            return None
        if self.card_effect(card) is None:
            return None
        profile = self._actions().target_profile(self.player, option=option)
        if profile.mode == TargetMode.NONE:
            targets = []
        elif profile.mode in (TargetMode.SELF, TargetMode.ALL):
            targets = list(profile.players)
        else:
            legal = list(profile.players)
            targets = []
            for player_id in target_ids:
                player = self._player_by_id(player_id)
                if player is None or not any(player is item for item in legal):
                    return None
                targets.append(player)
            if not profile.count_within_bounds(len(targets)):
                return None

        metadata = {"card_action": option}
        if option.is_conversion:
            metadata["conversion"] = {
                "skill_id": option.skill_id,
                "skill_name": option.skill_name,
                "source_cards": option.source_cards,
                "result_name": option.result_name,
                "log": option.log_text,
            }
        action = UseCardAction(self.player, card, targets, metadata=metadata)
        if not self.can_use(card, action):
            return None
        return action

    def _submit_end_phase(self, attempt=0):
        """结束出牌阶段：走与本地真人完全相同的 end_player_turn 路径。"""

        game = self.game
        token = self._turn
        if token is None or token.finished:
            self.last_local_code = ERR_OPERATION_FAILED
            self.last_local_error = "这一回合已经结束"
            return False
        if game.game_over:
            token.finish()
            self.last_local_code = ERR_OPERATION_FAILED
            self.last_local_error = "对局已经结束"
            return False
        if game.current_turn_player is not self.player or game.phase != "play":
            self.last_local_code = ERR_OPERATION_FAILED
            self.last_local_error = "现在不是你的出牌阶段"
            return False

        game.end_player_turn(self.player)

        if game.phase == "discard":
            self._request_discard()
            return True
        if game.engine.pending.active or game.response.active or game.choice.active:
            # 桌上还有窗口在等答案（共享无懈阶段 / 别人的响应 / 结算中）：
            # 现在结束回合会把结算切断，所以明确拒绝把面板还给客户端，
            # 而不是静默重试到超时。
            self.last_local_code = ERR_OPERATION_FAILED
            self.last_local_error = "桌上还有正在结算的窗口，请稍候再结束回合"
            return False
        if (game.phase == "play" and game.current_turn_player is self.player
                and not game.game_over):
            # 没能结束：桌面还在播动画，或规则暂时不允许（酒必须先出杀）。
            # 前者等一拍再来，后者把决定权交回客户端。
            if attempt < self.MAX_END_RETRIES:
                game.actions.add(WaitAction(self.RETRY_PAUSE))
                game.actions.add(CallbackAction(
                    lambda: self._submit_end_phase(attempt + 1)))
            else:
                self.game.actions.add(CallbackAction(self._request_play_phase))
                self._push_view()
            return True

        # end_player_turn 已经结束了这一回合（含下一个回合的推进）。
        token.finished = True
        token.on_complete = None
        return True

    def _push_view(self):
        if self.bridge is not None:
            self.bridge.push_views(force=True)

    def _resolve_discard(self, pending, result):
        token = pending.local[1]
        if token is not self._turn or token.finished:
            self.last_local_code = ERR_OPERATION_FAILED
            self.last_local_error = "这一回合已经结束"
            return False
        cards = [self._card_by_id(card_id) for card_id in result.card_ids]
        if any(card is None or card not in self.player.hand for card in cards):
            self.last_local_code = ERR_ILLEGAL_SOURCE
            self.last_local_error = "要弃的牌不在自己手里"
            return False
        if len({id(card) for card in cards}) != len(cards):
            self.last_local_code = ERR_ILLEGAL_SOURCE
            self.last_local_error = "同一张牌被选了两次"
            return False
        moved = self.discard_cards(cards)
        if len(moved) != len(cards):
            self.last_local_code = ERR_OPERATION_FAILED
            self.last_local_error = "弃牌没能全部生效"
            return False
        self.game.actions.add(CallbackAction(self._finish_after_discard))
        return True

    # ==================================================
    # 工具
    # ==================================================

    def _match_id(self):
        return getattr(self.bridge, "match_id", "") if self.bridge is not None else ""

    def _card_by_id(self, card_id):
        """按稳定 card_id 找一张实体牌——**所有**可能的区域都查。

        远程决策可能针对任何区域：自己的手牌（出牌 / 响应）、别人的手牌与
        装备（顺手牵羊 / 过河拆桥）、公共牌池（五谷丰登）、牌堆、弃牌堆、
        结算区。只查"自己的手牌 + 弃牌堆"会让跨区域的回答静默失败——规则层
        已经校验过 id 是否在请求允许集合内，所以按 id 在全局找是安全的。
        """

        text = str(card_id)
        containers = [
            self.player.hand,
            self.player.judgement_zone,
            list((self.player.equipment or {}).values()),
            self.game.deck.draw_pile,
            self.game.deck.discard_pile,
            self.game.processing_zone,
            self.game.public_card_pool,
        ]
        for player in self.game.players:
            if player is self.player:
                continue
            containers.append(player.hand)
            containers.append(player.judgement_zone)
            containers.append(list((player.equipment or {}).values()))
        for card, _slot in getattr(self.game, "table_cards", ()) or ():
            containers.append([card])
        for container in containers:
            for card in container:
                if card is not None and str(getattr(card, "id", "")) == text:
                    return card
        return None

    def _player_by_id(self, player_id):
        for player in self.game.players:
            if player.player_id == player_id:
                return player
        return None

    # ==================================================
    # 掉线 / 终止
    # ==================================================

    def abort(self):
        """对局终止：把等待中的决策全部作废，别再让流程干等。"""

        if self.registry is not None:
            self.registry.cancel_player(self.player.player_id, "match_aborted")
        if self._turn is not None:
            self._turn.finish()
            self._turn = None
