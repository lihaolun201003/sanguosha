"""Single authoritative pending request for resumable Engine V2 flows."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, FrozenSet, Optional, Sequence

from .events import Event, EventType


class PendingRequestType(str, Enum):
    RESPOND_CARD = "respond_card"
    CONFIRM = "confirm"
    SELECT_CARDS = "select_cards"
    CHOOSE_OPTION = "choose_option"
    # 选择角色目标（突袭一类需要玩家挑目标的技能）。候选放在
    # context["candidates"] 里，数量约束复用 min_cards / max_cards。
    SELECT_TARGETS = "select_targets"


@dataclass
class PendingRequest:
    request_id: int
    request_type: PendingRequestType
    source: Any
    target: Any
    prompt: str
    allowed_cards: FrozenSet[str] = frozenset()
    min_cards: int = 0
    max_cards: int = 0
    options: Sequence[Any] = ()
    context: dict = field(default_factory=dict)
    owner_flow: Any = field(default=None, repr=False)
    status: str = "pending"
    #: 群体等待（无懈阶段）：这些人**同时**获得回答机会，而不是按座次逐个问。
    #: 空元组 = 单人请求，只有 ``target`` 能回答（杀 / 闪、救援、选牌等一切旧流程）。
    responders: tuple = ()
    #: 群体成员的本轮状态：player_id → "pending" / "passed" / "used" / "withdrawn"。
    #: 个人放弃只改这里的一个条目，**不**结束整条请求。
    member_state: dict = field(default_factory=dict)

    @property
    def owner_id(self):
        return getattr(self.target, "player_id", None)

    @property
    def source_id(self):
        return getattr(self.source, "player_id", None)

    @property
    def target_ids(self):
        targets = self.context.get("targets", ())
        return tuple(getattr(target, "player_id", target) for target in targets)

    @property
    def allowed_card_ids(self):
        return tuple(getattr(card, "card_id", getattr(card, "id", None)) for card in self.context.get("candidates", ()))

    # ==================================================
    # 群体等待（同时响应）
    #
    # 只有一个回答者的请求（普通杀 / 闪、救援、选牌、判定）走的仍然是
    # ``target`` 那一套；``responders`` 非空时才启用这一组语义。
    # ==================================================

    @property
    def is_group(self):
        return bool(self.responders)

    @property
    def group_members(self):
        """可以回答这条请求的所有人（单人请求就是 target 一个人）。"""

        if self.responders:
            return tuple(self.responders)
        return (self.target,) if self.target is not None else ()

    @staticmethod
    def member_key(player):
        """成员身份的稳定键：优先 player_id（网络两侧都有它）。"""

        player_id = getattr(player, "player_id", None)
        return str(player_id) if player_id not in (None, "") else "id:%d" % id(player)

    def is_member(self, player):
        if player is None:
            return False
        if not self.is_group:
            return player is self.target
        return any(member is player for member in self.responders)

    def member_status(self, player):
        return self.member_state.get(self.member_key(player), "")

    def status_of_player_id(self, player_id):
        """按 player_id 查成员状态（网络 / 工具侧只有 id）。"""

        return self.member_state.get(str(player_id), "")

    def set_member_status(self, player, status):
        self.member_state[self.member_key(player)] = str(status)
        return status

    @property
    def pending_members(self):
        """本轮还没有给出回答的人（"已放弃 / 已使用 / 已撤回"都不算）。"""

        return tuple(
            member for member in self.group_members
            if self.member_status(member) == "pending"
        )

    @property
    def group_answered(self):
        """本轮是否已经有人打出了牌（锁定本轮）。"""

        return any(status == "used" for status in self.member_state.values())


@dataclass
class PendingResolution:
    request: PendingRequest
    actor: Any
    card: Any = None
    cards: Sequence[Any] = ()
    targets: Sequence[Any] = ()
    option: Any = None
    confirmed: Optional[bool] = None
    passed: bool = False
    source_rect: Any = None


class PendingManager:
    def __init__(self, context):
        self.context = context
        self._stack = []
        self._next_request_id = 1

    @property
    def active(self):
        return self.current is not None

    @property
    def current(self):
        return self._stack[-1] if self._stack else None

    @property
    def stack(self):
        return tuple(self._stack)

    def clear(self):
        for request in self._stack:
            request.status = "cancelled"
        self._stack.clear()

    def create(
        self,
        request_type,
        *,
        source,
        target,
        prompt,
        owner_flow,
        allowed_cards=(),
        min_cards=0,
        max_cards=0,
        options=(),
        request_context=None,
        responders=(),
    ):
        responders = tuple(responders or ())
        request = PendingRequest(
            request_id=self._next_request_id,
            request_type=request_type,
            source=source,
            target=target,
            prompt=prompt,
            allowed_cards=frozenset(allowed_cards),
            min_cards=int(min_cards),
            max_cards=int(max_cards),
            options=tuple(options),
            context=dict(request_context or {}),
            owner_flow=owner_flow,
            responders=responders,
            member_state={PendingRequest.member_key(member): "pending"
                          for member in responders},
        )
        self._next_request_id += 1
        self._stack.append(request)
        self.context.emit(
            Event(
                EventType.PENDING_CREATED,
                source=source,
                target=target,
                payload={"request": request},
            )
        )
        return request

    def require(self, request_id):
        request = self.current
        if request is None or request.request_id != request_id:
            raise ValueError("PendingRequest is missing or no longer current")
        return request

    def take(self, request_id):
        request = self.require(request_id)
        self._stack.pop()
        request.status = "resolved"
        return request

    def emit_resolved(self, resolution):
        self.context.emit(
            Event(
                EventType.PENDING_RESOLVED,
                source=resolution.request.source,
                target=resolution.actor,
                payload={"resolution": resolution},
            )
        )
