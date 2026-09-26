"""统一交互契约（Phase 18）。

规则层只回答一件事：**现在需要某个玩家做什么**。本包把这件事编译成一份
与视角无关的 ``InteractionSchema``；演什么由 ``presentation`` 模块负责，两者
互不越界：

    InteractionSchema   玩家必须做决定 → 规则流程停在原地等回答
    PresentationSchema  玩家只看       → 规则流程照常结算，画面按队列演

# 为什么要多这一层

在它之前，"这条请求该显示什么界面"有两份互不相通的实现：

    房主 → 网络 → RemoteHumanController._build_pending_request → DecisionRequest
    本机 →            HumanController.present                  → 直接摆面板

两份实现只要有一处不一致，同一局在单机与联机下就是两个界面（判定改判窗口
就是活例子：客户端有"跳过"按钮、单机没有）。现在两边都只读同一份 schema，
"谁能点、能点哪些牌、能选几个人、能不能放弃"只有一处判据。

# 与既有抽象的关系（不是第二套系统）

* ``DecisionKind`` 的值域就是 ``InteractionKind``——网络协议一个字节没改。
* ``PendingRequest`` 仍然是引擎唯一的"等回答"对象；schema 是它的**投影**，
  不是替代品。
* ``Payload`` 里的 ``candidates`` 就是引擎给出的权威候选，UI 只消费。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


# ==================================================
# 交互种类
#
# 值域与 ``PendingRequestType`` / ``DecisionKind`` 完全一致（网络协议不变）。
# 种类只表达"交互的形状"，不表达"哪张牌 / 哪个技能"——具体约束一律进
# ``payload``。所以新增一张锦囊、一个技能都不需要新加枚举。
# ==================================================

NONE = "none"
RESPOND_CARD = "respond_card"
CONFIRM = "confirm"
SELECT_OPTION = "choose_option"
SELECT_CARDS = "select_cards"
SELECT_TARGETS = "select_targets"
PLAY_PHASE = "play_phase"


class InteractionKind:
    """交互种类。

    成员的值就是协议里用的字符串，因此 ``kind is InteractionKind.CONFIRM``
    与 ``kind == "confirm"`` 同时成立——网络协议一个字节没改，而"有哪几种
    交互"从此只有这一处定义（``network.decisions.DecisionKind`` 是它的别名）。
    """

    NONE = NONE
    RESPOND_CARD = RESPOND_CARD
    CONFIRM = CONFIRM
    SELECT_OPTION = SELECT_OPTION
    SELECT_CARDS = SELECT_CARDS
    SELECT_TARGETS = SELECT_TARGETS
    PLAY_PHASE = PLAY_PHASE

    @classmethod
    def values(cls):
        return (cls.NONE, cls.RESPOND_CARD, cls.CONFIRM, cls.SELECT_OPTION,
                cls.SELECT_CARDS, cls.SELECT_TARGETS, cls.PLAY_PHASE)

#: 引擎 ``PendingRequestType`` → 交互种类。``PLAY_PHASE`` 不是 PendingRequest
#: （出牌阶段由回合流程驱动），由 ``build_play_interaction`` 单独构造。
KIND_BY_REQUEST_TYPE = {
    "respond_card": RESPOND_CARD,
    "confirm": CONFIRM,
    "choose_option": SELECT_OPTION,
    "select_cards": SELECT_CARDS,
    "select_targets": SELECT_TARGETS,
}

# ==================================================
# 语义名（给人看，不进协议）
#
# 需求里那张"通用语义表"（SELECT_CARD / SELECT_TARGET / RESPOND_SKILL…）由
# ``InteractionSchema.semantic`` 从 kind + 数量约束**推导**出来。刻意不做成
# 独立枚举：单张与多张是同一套交互，分成两个 kind 只会让两侧各写一份分派。
# ==================================================

SEM_NONE = "NONE"
SEM_SELECT_CARD = "SELECT_CARD"
SEM_SELECT_CARDS = "SELECT_CARDS"
SEM_SELECT_TARGET = "SELECT_TARGET"
SEM_SELECT_TARGETS = "SELECT_TARGETS"
SEM_SELECT_OPTION = "SELECT_OPTION"
SEM_CONFIRM = "CONFIRM"
SEM_RESPOND_CARD = "RESPOND_CARD"
SEM_RESPOND_SKILL = "RESPOND_SKILL"
SEM_PLAY_PHASE = "PLAY_PHASE"

# ==================================================
# 回答动作词（与 network.decisions 的 ACTION_* 同源）
# ==================================================

ACTION_SUBMIT = "submit"
ACTION_PASS = "pass"
ACTION_CANCEL = "cancel"
ACTION_END_PHASE = "end_phase"
ACTION_SKILL = "use_skill"


@dataclass(frozen=True)
class InteractionSchema:
    """一条与视角无关的交互契约。

    字段分三层：

    * **身份**：``id``（= 引擎 request_id）/ ``kind`` / ``actor_id`` /
      ``responders`` / ``source_id``——"这条请求问的是谁"。
    * **文案**：``title`` / ``prompt`` / ``note``——由规则层给出，UI 不许自己写。
    * **约束（payload）**：``candidates``（权威候选实体）/ ``card_ids`` /
      ``min_count`` / ``max_count`` / ``zone`` / ``options`` /
      ``required_suit`` / ``revealed_card`` / ``panel`` / ``purpose`` …
    """

    id: int = 0
    kind: str = NONE
    actor_id: str = ""
    source_id: str = ""
    responders: Tuple[str, ...] = ()
    title: str = ""
    prompt: str = ""
    note: str = ""
    cancellable: bool = False
    allowed_actions: Tuple[str, ...] = ()
    payload: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

    # ---- 派生：身份 ----

    @property
    def is_group(self) -> bool:
        return bool(self.responders)

    @property
    def purpose(self) -> str:
        """规则声明的请求意图（``judge_replacement`` / ``huogong_discard``…）。"""

        return str(self.payload.get("purpose") or self.context.get("reason") or "")

    # ---- 派生：约束 ----

    @property
    def min_count(self) -> int:
        return int(self.payload.get("min_count") or 0)

    @property
    def max_count(self) -> int:
        return int(self.payload.get("max_count") or 0)

    @property
    def candidates(self) -> Tuple[Any, ...]:
        """权威候选实体（规则层的真实对象；网络侧只发 id）。"""

        return tuple(self.payload.get("candidates") or ())

    @property
    def card_ids(self) -> Tuple[str, ...]:
        return tuple(str(item) for item in (self.payload.get("card_ids") or ()))

    @property
    def target_ids(self) -> Tuple[str, ...]:
        return tuple(str(item) for item in (self.payload.get("target_ids") or ()))

    @property
    def options(self) -> Tuple[Any, ...]:
        return tuple(self.payload.get("options") or ())

    @property
    def panel(self) -> str:
        """规则声明的专用面板名（空串 = 用通用界面）。"""

        return str(self.payload.get("panel") or "")

    # ---- 派生：对外的语义名 ----

    @property
    def semantic(self) -> str:
        if self.kind == SELECT_CARDS:
            return SEM_SELECT_CARD if self.max_count == 1 else SEM_SELECT_CARDS
        if self.kind == SELECT_TARGETS:
            return SEM_SELECT_TARGET if self.max_count == 1 else SEM_SELECT_TARGETS
        if self.kind == SELECT_OPTION:
            return SEM_SELECT_OPTION
        if self.kind == CONFIRM:
            return SEM_CONFIRM
        if self.kind == RESPOND_CARD:
            # 技能转化出来的响应（武圣把红牌当杀）与"打出一张实体牌"是同一层
            # 交互，靠 payload 区分；这里只在语义名上区分，便于日志与报告。
            return SEM_RESPOND_SKILL if self.payload.get("skill_only") else SEM_RESPOND_CARD
        if self.kind == PLAY_PHASE:
            return SEM_PLAY_PHASE
        return SEM_NONE

    def with_payload(self, **values) -> "InteractionSchema":
        merged = dict(self.payload)
        merged.update(values)
        return InteractionSchema(
            id=self.id, kind=self.kind, actor_id=self.actor_id,
            source_id=self.source_id, responders=self.responders,
            title=self.title, prompt=self.prompt, note=self.note,
            cancellable=self.cancellable, allowed_actions=self.allowed_actions,
            payload=merged, context=self.context,
        )

    def describe(self) -> str:
        return "interaction#%s %s/%s → %s" % (
            self.id, self.kind, self.semantic, self.actor_id or "-")


def _player_id(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(getattr(value, "player_id", "") or "")


def _entries(candidates) -> Tuple[Any, ...]:
    """候选规范化：``context["candidates"]`` 既可能是牌，也可能是 ``(牌, 键)``。"""

    result = []
    for item in candidates or ():
        if isinstance(item, (tuple, list)) and item:
            result.append(item[0])
        else:
            result.append(item)
    return tuple(result)


def _card_id(card) -> str:
    return str(getattr(card, "card_id", None) or getattr(card, "id", "") or "")


def _resolve(reason: str):
    """取规则层声明的展示语义（``interaction_presentation``）；没有就是空。"""

    from src.game.interaction_presentation import interaction_source

    return interaction_source(reason)


def build_interaction(game, request, *, title: str = "") -> InteractionSchema:
    """``PendingRequest`` → ``InteractionSchema``（唯一的一处翻译）。

    这是房主与本机共用的构造口：网络侧拿它生成 ``DecisionRequest``，本地
    控制器拿它摆面板。任何"这条请求能点什么"的判断都必须写在这里，UI 与
    客户端一律不许自己算。
    """

    from src.game.engine.pending import PendingRequestType

    request_type = getattr(request, "request_type", None)
    kind = KIND_BY_REQUEST_TYPE.get(
        getattr(request_type, "value", "") or str(request_type or ""), NONE)
    context = dict(getattr(request, "context", None) or {})
    reason = str(context.get("reason") or "")
    spec = _resolve(reason)

    candidates = _entries(context.get("candidates"))
    owner = context.get("zone_owner") or getattr(request, "target", None)
    min_cards = int(getattr(request, "min_cards", 0) or 0)
    max_cards = int(getattr(request, "max_cards", 0) or 0)

    # 取牌区域：请求自己声明优先，否则由规则层唯一的那条查询决定
    # （"自己的牌从手牌拿，别人的牌摆在公共区"）。界面不再自己算。
    zone = str(context.get("zone") or "")
    if not zone and kind == SELECT_CARDS:
        engine = getattr(game, "engine", None)
        namer = getattr(engine, "zone_name", None)
        if callable(namer):
            zone = str(namer(owner, request) or "")

    payload: Dict[str, Any] = {
        "purpose": reason,
        "zone": zone,
        "zone_owner_id": _player_id(owner),
    }

    if kind == SELECT_TARGETS:
        payload["candidates_objects"] = candidates
        payload["target_ids"] = tuple(_player_id(item) for item in candidates)
        payload["min_count"] = min_cards
        payload["max_count"] = max_cards
    elif kind == SELECT_CARDS:
        payload["candidates"] = candidates
        payload["card_ids"] = tuple(_card_id(card) for card in candidates)
        payload["min_count"] = min_cards
        payload["max_count"] = max_cards
    elif kind == SELECT_OPTION:
        from src.game.skills.mechanics import option_label

        payload["options"] = tuple(
            (value, option_label(request, value)) for value in request.options)
        payload["min_count"] = 1
        payload["max_count"] = 1

    # ---- 展示语义：由规则层声明，UI 不许自己写文案 ----
    if spec is not None:
        payload["panel"] = spec.panel
        payload["panel_stage"] = spec.stage
        payload["note"] = spec.render(context)
        if not title:
            title = spec.title

    # ---- 上下文型选牌的通用约束（火攻一类的"有依据地挑牌"）----
    if context.get("required_suit"):
        payload["required_suit"] = str(context.get("required_suit"))
        payload["required_suit_label"] = str(
            context.get("required_suit_label") or context.get("required_suit"))
    if context.get("revealed_card") is not None:
        payload["revealed_card"] = context.get("revealed_card")
    if context.get("revealed_by") is not None:
        payload["revealed_by"] = _player_id(context.get("revealed_by"))
    if context.get("caster") is not None:
        payload["caster_id"] = _player_id(context.get("caster"))
    if context.get("judge_card") is not None:
        payload["judge_card"] = context.get("judge_card")
    if context.get("skill_id"):
        payload["skill_id"] = str(context.get("skill_id"))

    # ---- 能不能放弃 ----
    # 判据只有一条：规则上"至少选 0 张"才允许空手交卷。UI 不再各写一份
    # （原来客户端恒显示"跳过"、单机没有——同一个窗口两种界面）。
    cancellable = False
    if kind == SELECT_CARDS:
        cancellable = min_cards == 0
    elif kind == SELECT_TARGETS:
        cancellable = True
    elif kind == RESPOND_CARD:
        cancellable = bool(context.get("allow_pass", True)) and not bool(
            context.get("must_respond", False))

    # 显式声明优先（技能可以要求"这次必须给答复"）。
    if context.get("cancellable") is not None:
        cancellable = bool(context.get("cancellable"))

    allowed = [ACTION_SUBMIT]
    if kind == RESPOND_CARD:
        allowed.append(ACTION_PASS)
    elif cancellable:
        allowed.append(ACTION_CANCEL)
    if kind in (SELECT_CARDS, SELECT_TARGETS, RESPOND_CARD) and not cancellable:
        allowed = [ACTION_SUBMIT] + [item for item in allowed if item != ACTION_CANCEL]

    return InteractionSchema(
        id=int(getattr(request, "request_id", 0) or 0),
        kind=kind,
        actor_id=_player_id(getattr(request, "target", None)),
        source_id=_player_id(getattr(request, "source", None)),
        responders=tuple(_player_id(item) for item in
                         (getattr(request, "responders", None) or ())),
        title=str(title or ""),
        prompt=str(getattr(request, "prompt", "") or ""),
        note=str(payload.get("note") or ""),
        cancellable=bool(cancellable),
        allowed_actions=tuple(dict.fromkeys(allowed)),
        payload=payload,
        context=context,
    )


def group_interaction(game, request, member_id: str, *, title: str = "") -> InteractionSchema:
    """群体请求（共享无懈阶段）里**某一成员那一份**的契约。

    群体请求的候选/约束对每个人都一样，但"问谁"不同；网络侧按成员各发一份，
    本地侧只为自己那份建面板。
    """

    schema = build_interaction(game, request, title=title)
    return InteractionSchema(
        id=schema.id, kind=schema.kind, actor_id=str(member_id),
        source_id=schema.source_id, responders=schema.responders,
        title=schema.title, prompt=schema.prompt, note=schema.note,
        cancellable=schema.cancellable, allowed_actions=schema.allowed_actions,
        payload=schema.payload, context=schema.context,
    )


def build_play_interaction(game, player, *, request_id: int, prompt: str = "",
                           title: str = "出牌阶段") -> InteractionSchema:
    """出牌阶段（不是 PendingRequest）：形状是"选一张牌用 + 选目标"。

    与请求型交互共用同一个 schema，所以界面层只需要一套分派。
    """

    return InteractionSchema(
        id=int(request_id),
        kind=PLAY_PHASE,
        actor_id=_player_id(player),
        source_id=_player_id(player),
        title=str(title or ""),
        prompt=str(prompt or ""),
        cancellable=True,
        allowed_actions=(ACTION_SUBMIT, ACTION_CANCEL, ACTION_END_PHASE, ACTION_SKILL),
        payload={"purpose": "play_phase", "zone": "hand"},
        context={"phase": "play"},
    )


def pending_member_id(request) -> Optional[str]:
    """单人请求下"谁该回答"；群体请求返回 None（要按成员分别构造）。"""

    if getattr(request, "is_group", False):
        return None
    return _player_id(getattr(request, "target", None))
