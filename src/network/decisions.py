"""远程决策的传输结构与权威校验。

房主把「需要某个远程真人做的决定」翻译成 ``DecisionRequest`` 发出去；客户端
只能回一个 ``result``（选了哪些 card_id / 谁的 player_id / 哪个选项），
**永远不能自己改游戏状态**。所有回答都回到这里做一次纯数据校验，再由房主
用真实的 GameAction 提交给引擎。

这一层不认识任何武将 / 牌 / 技能：它只知道"请求—回答—校验"。

校验规则（Phase 11.2 §7 / §21）：

1. ``match_id`` 必须属于当前对局（上一局迟到的响应直接丢弃）
2. ``request_id`` 必须存在，且属于这名玩家
3. 请求必须仍是待回答状态（重复提交只算一次）
4. 回来的类型必须与该请求的 kind 匹配
5. ``card_ids`` / ``target_ids`` 必须落在请求给出的允许集合内
6. 数量必须满足 min/max 约束
7. 不允许取消（或不允许放弃）的请求不能靠 cancel/pass 结束
8. 集合内不允许重复 id

Phase 11.5 追加的两件事（都是"提交 ≠ 接受"的配套）：

* **拒绝原因体系**：所有拒绝都带一个稳定的机器码（``ERR_*``），日志 / 报告
  里能区分"牌不对"与"局面已经变了"，而不是只有一句 bad_payload；
* **两级成功**：``validate`` 只做纯数据校验，``resolve`` / ``ack`` 把决策真正
  标记为完成——房主必须**先**把答案落到 Game 上（``controller.resolve`` 返回
  真），再调用 ``ack``。任何中间步骤失败时决策保持 OPEN，客户端还能重来。
"""

import os
import time

# ==================================================
# 拒绝原因（机器码）：debug / 日志 / 报告用它，UI 用中文文案
# ==================================================

ERR_MALFORMED_PAYLOAD = "malformed_payload"   # 结构坏掉（字段类型不对）
ERR_UNKNOWN_DECISION = "unknown_decision"     # 没有这条请求
ERR_WRONG_PLAYER = "wrong_player"             # 这条请求不属于你
ERR_STALE_DECISION = "stale_decision"         # 上一局 / 已被替换的旧请求
ERR_DECISION_CLOSED = "decision_closed"       # 请求已经作废（被取消 / 顶掉）
ERR_DUPLICATE_ANSWER = "duplicate_answer"     # 同一条请求重复回答
ERR_BAD_CARD_COUNT = "bad_card_count"         # 选牌张数不合法
ERR_ILLEGAL_CARD = "illegal_card"             # 选了不在候选里的牌
ERR_ILLEGAL_SOURCE = "illegal_source"         # 来源牌不属于你 / 不能这样转化
ERR_ILLEGAL_TARGET = "illegal_target"         # 目标不在允许范围内
ERR_INVALID_OPTION = "invalid_option"         # 选项 / 是-否答案不合法
ERR_CANCEL_NOT_ALLOWED = "cancel_not_allowed" # 这条请求不允许取消 / 放弃
ERR_UNKNOWN_SKILL = "unknown_skill"           # 技能不在本次可用范围
ERR_SKILL_DISABLED = "skill_disabled"         # 技能存在但现在不能发动
ERR_BAD_ACTION = "bad_action"                 # 动作词与请求类型不匹配
ERR_OPERATION_FAILED = "operation_failed"     # 校验过了，但房主没能把它落到游戏上

#: 拒绝码 → 玩家看得懂的中文（客户端只显示这一句，不看技术名）。
REJECT_TEXTS = {
    ERR_MALFORMED_PAYLOAD: "这次操作没能被房主识别，请重新选择",
    ERR_UNKNOWN_DECISION: "这一步已经不需要你操作了",
    ERR_WRONG_PLAYER: "这一步不是你的操作",
    ERR_STALE_DECISION: "这一步属于上一局，已经作废",
    ERR_DECISION_CLOSED: "这一步已经结束，请等房主刷新",
    ERR_DUPLICATE_ANSWER: "这一步你刚刚已经提交过了",
    ERR_BAD_CARD_COUNT: "选择的牌数不对，请重新选择",
    ERR_ILLEGAL_CARD: "选择的牌现在不能选，请重新选择",
    ERR_ILLEGAL_SOURCE: "这些牌不能这样转化，请重新选择",
    ERR_ILLEGAL_TARGET: "选择的目标现在不合法，请重新选择",
    ERR_INVALID_OPTION: "这个选项现在不能选，请重新选择",
    ERR_CANCEL_NOT_ALLOWED: "这一步不能取消，必须做出选择",
    ERR_UNKNOWN_SKILL: "这个技能现在不能发动",
    ERR_SKILL_DISABLED: "这个技能现在不能发动",
    ERR_BAD_ACTION: "服务器不认识这个操作",
    ERR_OPERATION_FAILED: "房主没能执行这次操作（局面可能已经变了），请重试",
}


def reject_text(code, message=""):
    """拒绝码 → 一行中文提示（**给玩家看的**一句话）。

    已知拒绝码一律用规范文案（各处的细节说明只进日志，不直接丢给玩家）；
    未知码才退回房主给的说明。这样客户端显示的永远是"能看懂的一句话 + 该
    怎么办"，而 debug / 日志里仍然有具体原因。
    """

    text = REJECT_TEXTS.get(str(code or ""))
    if text:
        return text
    return message or "房主拒绝了这次操作，请重新选择"


class DecisionKind:
    """通用决策类型（按项目真实交互归纳，不按具体技能命名）。"""

    # 打出一张响应牌（闪 / 桃 / 无懈 / 杀）：对应 PendingRequestType.RESPOND_CARD
    RESPOND_CARD = "respond_card"
    # 是 / 否（装备技能、以及将来任何"是否发动"）：对应 CONFIRM
    CONFIRM = "confirm"
    # 多选一：对应 CHOOSE_OPTION
    CHOOSE_OPTION = "choose_option"
    # 选若干张牌（弃牌 / 制衡 / 五谷…）：对应 SELECT_CARDS
    SELECT_CARDS = "select_cards"
    # 选若干角色目标：对应 SELECT_TARGETS
    SELECT_TARGETS = "select_targets"
    # 出牌阶段：出一张牌（带目标）或结束阶段（回合流程，不是 PendingRequest）
    PLAY_PHASE = "play_phase"


ALL_KINDS = (
    DecisionKind.RESPOND_CARD,
    DecisionKind.CONFIRM,
    DecisionKind.CHOOSE_OPTION,
    DecisionKind.SELECT_CARDS,
    DecisionKind.SELECT_TARGETS,
    DecisionKind.PLAY_PHASE,
)

# 回答的动作词：提交 / 放弃（不出、不选）/ 取消（不允许时拒绝）/ 结束阶段 /
# 发动主动技（Phase 11.4：出牌阶段里"主动技按下技能键"与"打出一张牌"是同一层
# 决策的两种答案，所以复用同一条请求，不新开一套协议）
ACTION_SUBMIT = "submit"
ACTION_PASS = "pass"
ACTION_CANCEL = "cancel"
ACTION_END_PHASE = "end_phase"
ACTION_SKILL = "use_skill"


class DecisionError(Exception):
    """非法回答（房主拒绝这一条响应，但房间与对局都不受影响）。"""

    def __init__(self, code, message="", detail=None):
        super().__init__(message or code)
        self.code = str(code or ERR_MALFORMED_PAYLOAD)
        self.message = message or code
        #: 结构化的补充信息（哪张牌 / 哪个目标）：只进日志，不给玩家看。
        self.detail = dict(detail or {})

    def text(self):
        """给玩家看的一行中文。"""

        return reject_text(self.code, self.message)

    def __repr__(self):                             # pragma: no cover - 调试用
        return "DecisionError(%s: %s)" % (self.code, self.message)


#: 有"明确业务含义"的拒绝码；其它一律算 malformed（结构坏掉）。
KNOWN_ERROR_CODES = frozenset({
    ERR_MALFORMED_PAYLOAD, ERR_UNKNOWN_DECISION, ERR_WRONG_PLAYER,
    ERR_STALE_DECISION, ERR_DECISION_CLOSED, ERR_DUPLICATE_ANSWER,
    ERR_BAD_CARD_COUNT, ERR_ILLEGAL_CARD, ERR_ILLEGAL_SOURCE,
    ERR_ILLEGAL_TARGET, ERR_INVALID_OPTION, ERR_CANCEL_NOT_ALLOWED,
    ERR_UNKNOWN_SKILL, ERR_SKILL_DISABLED, ERR_BAD_ACTION,
    ERR_OPERATION_FAILED,
})


class DecisionStatus:
    OPEN = "open"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"


def _ids_in(values):
    """把玩家侧传来的 id 列表规范化：去重、保持顺序、忽略非字符串。"""

    result = []
    for value in values or ():
        text = str(value)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def card_entry(card):
    """一张牌在客户端眼里长什么样（只发给有权知道它的玩家）。"""

    return {
        "card_id": card.id,
        "name": card.name,
        "label": getattr(card, "display_name", card.name),
        "suit": card.suit,
        "rank": card.rank,
        "category": card.category,
    }


def player_entry(player, *, hand_count=None):
    """一名角色的**公开**信息（手牌只给张数，不给内容）。"""

    return {
        "player_id": player.player_id,
        "nickname": player.name,
        "seat": player.seat,
        "hp": player.hp,
        "max_hp": player.max_hp,
        "alive": bool(player.alive and player.hp > 0),
        "hand_count": (len(player.hand) if hand_count is None else int(hand_count)),
        "general_id": getattr(player, "general_id", None),
        "equipment": sorted(
            slot for slot, card in (player.equipment or {}).items() if card is not None),
        "judge_count": len(getattr(player, "judgement_zone", ()) or ()),
        "chained": bool(getattr(player, "chained", False)),
    }


class DecisionRequest:
    """一条发给远程玩家的决策请求（纯 JSON 数据）。"""

    __slots__ = ("match_id", "request_id", "player_id", "kind", "prompt",
                 "context", "cards", "targets", "options", "constraints")

    def __init__(self, *, match_id, request_id, player_id, kind, prompt,
                 context=None, cards=(), targets=(), options=(), constraints=None):
        self.match_id = str(match_id)
        self.request_id = int(request_id)
        self.player_id = str(player_id)
        self.kind = str(kind)
        self.prompt = str(prompt or "")
        self.context = dict(context or {})
        self.cards = [dict(item) for item in cards]
        self.targets = [dict(item) for item in targets]
        self.options = [dict(item) for item in options]
        self.constraints = dict(constraints or {})

    # ---- 约束的默认值 ----

    @property
    def min_cards(self):
        return int(self.constraints.get("min_cards", 0))

    @property
    def max_cards(self):
        return int(self.constraints.get("max_cards", 0))

    @property
    def min_targets(self):
        return int(self.constraints.get("min_targets", 0))

    @property
    def max_targets(self):
        return int(self.constraints.get("max_targets", 0))

    @property
    def allow_cancel(self):
        return bool(self.constraints.get("allow_cancel", False))

    @property
    def allow_pass(self):
        return bool(self.constraints.get("allow_pass", False))

    @property
    def card_ids(self):
        return tuple(item.get("card_id") for item in self.cards)

    @property
    def target_ids(self):
        return tuple(item.get("player_id") for item in self.targets)

    def card_entry(self, card_id):
        for item in self.cards:
            if item.get("card_id") == card_id:
                return item
        return None

    def options_for_card(self, card_id):
        """这张候选牌在当前请求里的全部可用方式（普通使用 / 技能转化）。"""

        entry = self.card_entry(card_id)
        if entry is None:
            return ()
        return tuple(
            item for item in (entry.get("options") or ()) if isinstance(item, dict))

    def find_option(self, action_id):
        """按方式 id 找一条候选方式（出牌阶段与响应窗口共用同一套字段）。"""

        wanted = str(action_id or "")
        if not wanted:
            return None
        for entry in self.cards:
            for item in entry.get("options") or ():
                if isinstance(item, dict) and str(item.get("action_id") or "") == wanted:
                    return item
        return None

    @property
    def option_ids(self):
        return tuple(
            str(item.get("action_id") or "")
            for entry in self.cards
            for item in entry.get("options") or ()
            if isinstance(item, dict) and item.get("action_id")
        )

    @property
    def activatable(self):
        """本条请求允许发动的主动技（出牌阶段才有）。"""

        return tuple(self.constraints.get("activatable") or ())

    def skill_entry(self, skill_id):
        for item in self.activatable:
            if str(item.get("skill_id") or "") == str(skill_id or ""):
                return item
        return None

    # ---- 序列化 ----

    def to_payload(self):
        return {
            "match_id": self.match_id,
            "request_id": self.request_id,
            "player_id": self.player_id,
            "kind": self.kind,
            "prompt": self.prompt,
            "context": dict(self.context),
            "cards": [dict(item) for item in self.cards],
            "targets": [dict(item) for item in self.targets],
            "options": [dict(item) for item in self.options],
            "constraints": dict(self.constraints),
        }

    @classmethod
    def from_payload(cls, payload):
        payload = payload if isinstance(payload, dict) else {}
        return cls(
            match_id=payload.get("match_id") or "",
            request_id=payload.get("request_id") or 0,
            player_id=payload.get("player_id") or "",
            kind=payload.get("kind") or "",
            prompt=payload.get("prompt") or "",
            context=payload.get("context") or {},
            cards=payload.get("cards") or (),
            targets=payload.get("targets") or (),
            options=payload.get("options") or (),
            constraints=payload.get("constraints") or {},
        )

    def __repr__(self):  # pragma: no cover - 调试用
        return "DecisionRequest(#%s %s → %s)" % (
            self.request_id, self.kind, self.player_id)


class DecisionResult:
    """客户端回来的原始答案（尚未与 Game 对象挂钩）。"""

    __slots__ = ("action", "card_ids", "target_ids", "option", "confirm",
                 "skill_id", "action_id", "result_name")

    def __init__(self, *, action=ACTION_SUBMIT, card_ids=(), target_ids=(),
                 option=None, confirm=None, skill_id="", action_id="",
                 result_name=""):
        self.action = str(action or ACTION_SUBMIT)
        self.card_ids = _ids_in(card_ids)
        self.target_ids = _ids_in(target_ids)
        self.option = option
        self.confirm = confirm
        #: View-As：这次用的是哪个技能的转化。空串表示"直接使用 / 打出"。
        self.skill_id = str(skill_id or "")
        #: 用牌 / 响应的**具体方式**（引擎的 ``CardActionOption.action_id``）。
        #: 同一张牌可以有好几种用法，skill_id 不足以区分（龙胆把【闪】当
        #: 【杀】与把【杀】当【闪】是同一个技能），所以方式本身也要回传。
        self.action_id = str(action_id or "")
        #: 这次想用出来的**逻辑牌**名（"SHA" / "SHAN"）。多来源转化凑齐后
        #: 引擎给出的 action_id 里会带上具体 source，光靠 action_id 对不上，
        #: 所以再带一个"我打算用出什么"的语义提示——房主仍然会重新校验它。
        self.result_name = str(result_name or "")

    @property
    def passed(self):
        return self.action in (ACTION_PASS, ACTION_CANCEL)

    def to_payload(self):
        return {
            "action": self.action,
            "card_ids": list(self.card_ids),
            "target_ids": list(self.target_ids),
            "option": self.option,
            "confirm": self.confirm,
            "skill_id": self.skill_id,
            "action_id": self.action_id,
            "result_name": self.result_name,
        }

    @classmethod
    def from_payload(cls, payload):
        payload = payload if isinstance(payload, dict) else {}
        return cls(
            action=payload.get("action") or ACTION_SUBMIT,
            card_ids=payload.get("card_ids") or (),
            target_ids=payload.get("target_ids") or (),
            option=payload.get("option"),
            confirm=payload.get("confirm"),
            skill_id=payload.get("skill_id") or "",
            action_id=payload.get("action_id") or "",
            result_name=payload.get("result_name") or "",
        )


class PendingDecision:
    """房主侧一条等待回答的决策。"""

    __slots__ = ("request", "local", "status", "created_at", "reason")

    def __init__(self, request, local):
        self.request = request
        self.local = local                 # 本地语义：("pending", PendingRequest) / ("turn", token)
        self.status = DecisionStatus.OPEN
        self.created_at = time.monotonic()
        self.reason = ""

    @property
    def open(self):
        return self.status == DecisionStatus.OPEN

    @property
    def request_id(self):
        return self.request.request_id


class DecisionRegistry:
    """房主侧"等待中的远程决策"登记表 + 回答校验。

    一名远程玩家同时只允许一条前台决策（``current_for``），这既符合真人
    一次只做一个决定的事实，也把"协议串台"的可能性降到最低。引擎内部仍然
    允许嵌套 Pending（无懈套无懈）：嵌套的内层请求解决后，外层会被重新驱动。

    决策生命周期（Phase 11.5）：

        open(OPEN) → SEND → 收到答案 → validate → 落到 Game 上 → ack(RESOLVED)

    ``validate`` 与 ``ack`` 是**两步**：只有房主真的把答案提交给引擎并确认
    生效之后才 ack。中间任何一步失败，决策保持 OPEN，客户端手里的面板不会
    被撤掉，玩家可以直接重试——这正是"提交 ≠ 接受"的实现。
    """

    # 出牌阶段这类"不是 PendingRequest"的决策，id 从 100000 起，和引擎的
    # PendingRequest id（从 1 开始）不会撞车。request_id 只需在当前对局内唯一。
    TURN_REQUEST_BASE = 100000

    def __init__(self, match_id="", debug=None):
        self.match_id = str(match_id)
        self._by_id = {}
        self._by_player = {}
        self._next_turn_id = self.TURN_REQUEST_BASE
        self.rejected = []          # 被拒绝的响应（简短原因，供日志/报告）
        #: 结构化 debug 日志（OPEN/SEND/ANSWER/ACCEPT/REJECT/RESOLVE）。
        #: 默认只在显式打开时记录，正常运行不会刷屏。
        self.debug = bool(os.environ.get("SANGGUOSHA_DECISION_DEBUG")) \
            if debug is None else bool(debug)
        self.journal = []
        self.stats = {"open": 0, "answer": 0, "accepted": 0, "rejected": 0,
                      "resolved": 0}

    def note(self, stage, **fields):
        """记一条决策生命周期日志（默认关闭；打开后每帧也不会刷屏）。"""

        item = {"stage": str(stage), "at": time.monotonic()}
        item.update(fields)
        self.journal.append(item)
        del self.journal[:-200]
        if self.debug:                              # pragma: no cover - 调试用
            print("[decision] %s %s" % (
                stage, " ".join("%s=%s" % kv for kv in sorted(fields.items()))))
        return item

    def next_request_id(self):
        self._next_turn_id += 1
        return self._next_turn_id

    # ---- 登记 ----

    def open(self, request, local=None):
        pending = PendingDecision(request, local)
        self._by_id[request.request_id] = pending
        self._by_player[request.player_id] = pending
        self.stats["open"] = self.stats.get("open", 0) + 1
        self.note("OPEN", request_id=request.request_id, kind=request.kind,
                  player_id=request.player_id)
        return pending

    def get(self, request_id):
        return self._by_id.get(int(request_id))

    def current_for(self, player_id):
        pending = self._by_player.get(str(player_id))
        return pending if pending is not None and pending.open else None

    @property
    def waiting(self):
        return any(pending.open for pending in self._by_id.values())

    def open_requests(self):
        return tuple(p for p in self._by_id.values() if p.open)

    # ---- 作废 ----

    def cancel(self, request_id, reason="", status=DecisionStatus.CANCELLED):
        pending = self._by_id.get(int(request_id))
        if pending is None or not pending.open:
            return None
        pending.status = status
        pending.reason = reason
        if self._by_player.get(pending.request.player_id) is pending:
            self._by_player.pop(pending.request.player_id, None)
        return pending

    def cancel_player(self, player_id, reason="player_gone"):
        pending = self._by_player.get(str(player_id))
        if pending is None:
            return None
        return self.cancel(pending.request_id, reason,
                           status=DecisionStatus.INVALIDATED)

    def cancel_all(self, reason="match_over"):
        for pending in list(self._by_id.values()):
            self.cancel(pending.request_id, reason,
                        status=DecisionStatus.INVALIDATED)

    # ---- 校验 ----

    def validate(self, player_id, request_id, match_id, result):
        """校验一条回答；不合法就抛 ``DecisionError``（绝不改任何状态）。"""

        player_id = str(player_id)
        try:
            request_id = int(request_id)
        except (TypeError, ValueError):
            raise DecisionError(ERR_MALFORMED_PAYLOAD, "缺少或非法的 request_id")

        if self.match_id and str(match_id) != self.match_id:
            raise DecisionError(ERR_STALE_DECISION, "响应属于上一局比赛")

        pending = self._by_id.get(request_id)
        if pending is None:
            raise DecisionError(ERR_UNKNOWN_DECISION, "没有这条决策请求")
        if pending.request.player_id != player_id:
            raise DecisionError(ERR_WRONG_PLAYER, "这条决策请求不属于该玩家")
        if pending.status == DecisionStatus.RESOLVED:
            raise DecisionError(ERR_DUPLICATE_ANSWER, "这条决策请求已经回答过了")
        if not pending.open:
            raise DecisionError(ERR_DECISION_CLOSED, "这条决策请求已经作废")

        request = pending.request
        if result.action not in (ACTION_SUBMIT, ACTION_PASS, ACTION_CANCEL,
                                 ACTION_END_PHASE, ACTION_SKILL):
            raise DecisionError(ERR_BAD_ACTION, "未知的回答动作")

        if result.action in (ACTION_CANCEL,) and not request.allow_cancel:
            raise DecisionError(ERR_CANCEL_NOT_ALLOWED, "这条决策不允许取消")
        if result.action == ACTION_PASS and not (request.allow_pass or request.allow_cancel):
            raise DecisionError(ERR_CANCEL_NOT_ALLOWED, "这条决策必须给出选择")
        if result.action == ACTION_END_PHASE and request.kind != DecisionKind.PLAY_PHASE:
            raise DecisionError(ERR_BAD_ACTION, "只有出牌阶段可以结束阶段")

        if result.action == ACTION_SKILL:
            self._validate_skill(request, result)
            return pending, result

        if result.action != ACTION_SUBMIT:
            return pending, result

        allowed_cards = set(request.card_ids)
        allowed_targets = set(request.target_ids)

        unknown_cards = [item for item in result.card_ids if item not in allowed_cards]
        if unknown_cards:
            raise DecisionError(
                ERR_ILLEGAL_CARD, "选择的牌不在允许范围内",
                {"cards": unknown_cards})
        unknown_targets = [item for item in result.target_ids if item not in allowed_targets]
        if unknown_targets:
            raise DecisionError(
                ERR_ILLEGAL_TARGET, "选择的目标不在允许范围内",
                {"targets": unknown_targets})

        # View-As：客户端只能选房主在请求里列出的技能与方式，不能自创转化。
        allowed_skills = set(request.constraints.get("allowed_skills") or ())
        if result.skill_id and result.skill_id not in allowed_skills:
            raise DecisionError(ERR_UNKNOWN_SKILL, "这个技能不在本次可用范围内")
        if result.action_id:
            known = set(request.option_ids)
            # 方式 id 由房主生成：只在"这张牌的候选方式"里出现。允许它缺省
            # （老客户端 / 单一方式），但给了就必须是本次请求下发过的之一。
            if known and result.action_id not in known \
                    and not self._option_id_known(request, result.action_id):
                raise DecisionError(
                    ERR_INVALID_OPTION, "这个使用方式不在本次可用范围内",
                    {"action_id": result.action_id})

        cards = len(result.card_ids)
        if cards < request.min_cards or cards > request.max_cards:
            raise DecisionError(
                ERR_BAD_CARD_COUNT,
                "选牌数量不合法（需要 %d～%d，收到 %d）" % (
                    request.min_cards, request.max_cards, cards),
                {"min_cards": request.min_cards, "max_cards": request.max_cards,
                 "got": cards})

        targets = len(result.target_ids)
        if targets < request.min_targets or targets > request.max_targets:
            raise DecisionError(
                ERR_ILLEGAL_TARGET,
                "目标数量不合法（需要 %d～%d，收到 %d）" % (
                    request.min_targets, request.max_targets, targets),
                {"min_targets": request.min_targets,
                 "max_targets": request.max_targets, "got": targets})

        if request.kind == DecisionKind.CONFIRM and result.confirm is None:
            raise DecisionError(ERR_INVALID_OPTION, "缺少是/否答案")
        if request.kind == DecisionKind.CHOOSE_OPTION:
            values = [item.get("value") for item in request.options]
            if result.option not in values:
                raise DecisionError(
                    ERR_INVALID_OPTION, "选项不在允许范围内",
                    {"option": result.option, "allowed": values})

        return pending, result

    @staticmethod
    def _option_id_known(request, action_id):
        """方式 id 是否属于本次请求（含只发给本人的隐藏候选）。"""

        wanted = str(action_id or "")
        for entry in request.cards:
            if str(entry.get("action_id") or "") == wanted:
                return True
        return False

    @staticmethod
    def _validate_skill(request, result):
        """主动技：只接受房主在**本条请求**里列出的技能与它的费用 / 目标。"""

        if request.kind != DecisionKind.PLAY_PHASE:
            raise DecisionError(ERR_BAD_ACTION, "只有出牌阶段可以发动技能")
        entry = request.skill_entry(result.skill_id)
        if entry is None:
            raise DecisionError(ERR_UNKNOWN_SKILL, "这个技能不在本次可用范围内")
        if not entry.get("enabled", True):
            raise DecisionError(
                ERR_SKILL_DISABLED,
                str(entry.get("disabled_reason") or "现在不能发动这个技能"))

        allowed_cards = {item.get("card_id") for item in entry.get("cost_candidates") or ()}
        unknown_cards = [item for item in result.card_ids if item not in allowed_cards]
        if unknown_cards:
            raise DecisionError(
                ERR_ILLEGAL_SOURCE, "费用牌不在允许范围内", {"cards": unknown_cards})
        allowed_targets = {item.get("player_id") for item in entry.get("targets") or ()}
        unknown_targets = [item for item in result.target_ids if item not in allowed_targets]
        if unknown_targets:
            raise DecisionError(
                ERR_ILLEGAL_TARGET, "技能目标不在允许范围内",
                {"targets": unknown_targets})

        chosen = len(result.card_ids)
        if entry.get("variable_cost"):
            # 可变费用（制衡 / 仁德）：至少一张，上限由房主自己复核。
            if chosen < 1:
                raise DecisionError(ERR_BAD_CARD_COUNT, "至少要选择一张牌")
        elif chosen != int(entry.get("cost_cards") or 0):
            raise DecisionError(
                ERR_BAD_CARD_COUNT,
                "费用牌数量不合法（需要 %d，收到 %d）" % (
                    int(entry.get("cost_cards") or 0), chosen))

        targets = len(result.target_ids)
        if entry.get("needs_target"):
            if targets != 1:
                raise DecisionError(ERR_ILLEGAL_TARGET, "这个技能需要选择 1 个目标")
        elif targets:
            raise DecisionError(ERR_ILLEGAL_TARGET, "这个技能不需要目标")

    def resolve(self, player_id, request_id, match_id, result):
        """校验通过后把请求标记为已解决并返回它。

        **注意**：这只代表"这条回答在协议层合法"，不代表房主已经把它落到
        Game 上。真正的两步提交用 ``ack``（见类文档）。
        """

        pending, result = self.validate(player_id, request_id, match_id, result)
        self.ack(pending, answer=result)
        return pending, result

    def ack(self, pending, answer=None):
        """把一条决策标记为已完成（房主已经把它提交给引擎且确认生效）。"""

        if pending is None or not pending.open:
            return None
        pending.status = DecisionStatus.RESOLVED
        if self._by_player.get(pending.request.player_id) is pending:
            self._by_player.pop(pending.request.player_id, None)
        self.stats["resolved"] = self.stats.get("resolved", 0) + 1
        self.note("RESOLVE", request_id=pending.request_id,
                  kind=pending.request.kind,
                  action=getattr(answer, "action", ""))
        return pending

    def reject(self, player_id, request_id, error):
        """记录一条被拒绝的响应（只做简短记录，不做反作弊系统）。

        决策**不会**被关闭：客户端手里的面板仍然有效，玩家可以直接重试。
        """

        code = getattr(error, "code", ERR_MALFORMED_PAYLOAD)
        if code not in KNOWN_ERROR_CODES:
            code = ERR_MALFORMED_PAYLOAD
        item = {
            "player_id": str(player_id),
            "request_id": request_id,
            "code": code,
            "message": str(error),
            "detail": dict(getattr(error, "detail", None) or {}),
            "at": time.monotonic(),
        }
        self.rejected.append(item)
        del self.rejected[:-20]
        self.stats["rejected"] = self.stats.get("rejected", 0) + 1
        self.note("REJECT", request_id=request_id, code=code)
        return item

    def note_answer(self, player_id, request_id):
        """收到一条回答（在协议校验之前记一笔，便于排查"到底收到了没有"）。"""

        self.stats["answer"] = self.stats.get("answer", 0) + 1
        return self.note("ANSWER", request_id=request_id, player_id=player_id)

    def note_accept(self, pending):
        self.stats["accepted"] = self.stats.get("accepted", 0) + 1
        return self.note("ACCEPT", request_id=getattr(pending, "request_id", None))
