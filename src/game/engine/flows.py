"""Resumable flow convention compatible with a synchronous Pygame loop."""

import contextlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .events import Event, EventType


#: "没有延迟完成值"的哨兵：``None`` 是合法的完成值，不能拿它当默认。
_NO_VALUE = object()


class FlowStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class FlowResult:
    status: FlowStatus
    value: Any = None


class Flow(ABC):
    """A rules process that may pause for a UI or AI response.

    事件回调里也会**另起一条**流程：天香在伤害发生前开一个选目标窗口、悲歌在
    伤害结算后开一个选牌窗口、涅槃在濒死时开一个确认窗口、颂威在判定结束后
    开一个确认窗口。事件分发是同步的，``Skill.resolve`` 起的流程没有人接，
    于是"答案还没拿到，父流程已经接着扣血 / 进入下一阶段 / 交给下一个人了"。

    这里给每条流程一张**子流程**名单：

    * 引擎在 Flow 执行期间压栈（``Flow.start`` / ``Flow.resume``），执行期间
      新启动的 Flow 自动挂到栈顶那条流程上（``adopt_child``）；
    * 父流程在**改状态之前**调用 ``guard_child_flows()``：有子流程没跑完就挂起
      自己，等它结束再继续；
    * ``complete()`` 还会兜一次底：即使某个结算点忘了显式检查，也不会在子流程
      没结束时就宣告完成——只是顺序保证不如显式检查点强。
    """

    def __init__(self, context: "GameContext") -> None:
        self.context = context
        self.status = FlowStatus.READY
        self.pending_request: Any = None
        self.result: Any = None
        #: 事件回调里启动、还没有结束的子流程（见 guard_child_flows）。
        self.parent: Any = None
        self._children: list = []
        #: 被子流程挡住时"原本要返回的完成值"：与 _NO_VALUE 不同就表示
        #: 恢复时直接完成，不要再走一遍 advance（那会重复结算）。
        self._deferred: Any = _NO_VALUE
        #: 正在**守卫等待**的子流程（guard_child_flows / complete 被拦截）。
        #: 只有它才由 _child_finished 唤醒：显式 ``wait(child_flow)`` 的调用方
        #: 自己用 on_complete 接回结算，不能再被唤醒一次。
        self._awaiting_child: Any = None

    def start(self) -> FlowResult:
        if self.status is not FlowStatus.READY:
            raise RuntimeError("flow has already started")
        self.status = FlowStatus.RUNNING
        self._emit(EventType.FLOW_STARTED)
        with self._entered():
            opened = self.begin()
            if opened is not None:
                return opened
            return self.advance()

    def begin(self):
        """开场：发出本流程的第一条请求。

        默认返回 None，表示"什么都不用问，直接 advance()"——既有的流程
        （结算 / 判定 / 伤害）全部保持原样。需要立刻向玩家提问的流程
        （技能流程：先确认、先选牌、先选目标）重写它并在里面 ``wait``，
        返回 ``self.current_result()``；这样 ``advance`` 收到的就**总是
        一条真实回答**，不会把"开场时的 None"误当成"玩家放弃"。
        """

        return None

    def resume(self, response: Any) -> FlowResult:
        if self.status is not FlowStatus.WAITING:
            raise RuntimeError("only a waiting flow can be resumed")
        self.pending_request = None
        self.status = FlowStatus.RUNNING
        self._emit(EventType.FLOW_RESUMED, response=response)
        with self._entered():
            return self.advance(response)

    def wait(self, request: Any) -> FlowResult:
        self.pending_request = request
        self.status = FlowStatus.WAITING
        self._emit(EventType.FLOW_WAITING, request=request)
        return FlowResult(self.status, request)

    def current_result(self) -> FlowResult:
        return FlowResult(self.status, self.result)

    def complete(self, value: Any = None) -> FlowResult:
        child = self._unfinished_child()
        if child is not None:
            # 事件回调里起的子流程还没结束：记住"本来要完成的值"，挂起自己。
            self._deferred = value
            return self.guard_child_flows(child)
        self.pending_request = None
        self.result = value
        self.status = FlowStatus.COMPLETED
        self._emit(EventType.FLOW_FINISHED, result=value)
        result = FlowResult(self.status, value)
        # 先做自己的收尾（on_complete 回调通常会推进调用方的状态），再唤醒
        # 等在子流程上的父流程——反过来会让父流程从一个还没收尾的阶段恢复。
        self.on_settled(result)
        self._notify_parent()
        return result

    def cancel(self, reason: Any = None) -> FlowResult:
        self.pending_request = None
        self.result = reason
        self.status = FlowStatus.CANCELLED
        self._emit(EventType.FLOW_CANCELLED, reason=reason)
        result = FlowResult(self.status, reason)
        # 取消 = 这条流程不再产出任何东西；等在它上面的流程必须醒过来，
        # 否则父流程会永远停在"等一个已经取消的子流程"。
        self._notify_parent()
        return result

    def on_settled(self, result: FlowResult) -> None:
        """真正完成（或被取消之外的终态）之后的收尾。

        子类用它接回自己的 ``on_complete`` 回调。**不能**写在 ``complete()``
        调用点后面：被子流程挡住时那次 ``complete`` 只是挂起，收尾必须等到
        真正完成时再做一次、且只做一次。
        """

    # ==================================================
    # 子流程（事件回调里启动的技能流程）
    # ==================================================

    def adopt_child(self, child: "Flow") -> None:
        """登记一条本流程不知道的子流程（由引擎在 Flow 执行期间自动调用）。"""

        if child is None or child is self or child in self._children:
            return
        child.parent = self
        self._children.append(child)

    def _unfinished_child(self):
        for child in self._children:
            if child.status in (FlowStatus.READY, FlowStatus.RUNNING, FlowStatus.WAITING):
                return child
        return None

    def guard_child_flows(self, child=None):
        """有未完成的子流程就挂起自己；返回 None 表示可以继续。

        在"事件发完、马上要改状态"的位置调用：天香要先选完转移目标再扣血、
        啖酪要先决定是否生效再结算锦囊、涅槃要先处理决定再完成濒死。
        """

        child = child if child is not None else self._unfinished_child()
        if child is None:
            return None
        if child.status is FlowStatus.READY:
            child.start()
        if child.status in (FlowStatus.RUNNING, FlowStatus.WAITING):
            self._awaiting_child = child
            self.wait(child)
            return self.current_result()
        return None

    def _child_finished(self, child: "Flow", result: FlowResult) -> None:
        """子流程结束：如果本流程正**守卫等待**它，就在这里精确恢复一次。"""

        if child.parent is self:
            child.parent = None
        if self._awaiting_child is not child:
            # 不是守卫等待（例如显式 wait(child_flow)）：由它自己的
            # on_complete 回调接回结算，这里绝不能替它推进。
            return
        self._awaiting_child = None
        deferred = self._deferred
        self._deferred = _NO_VALUE
        self.pending_request = None
        self.status = FlowStatus.RUNNING
        self._emit(EventType.FLOW_RESUMED, response=result)
        with self._entered():
            if deferred is not _NO_VALUE:
                # 挂起前就已经决定要完成了：恢复时直接完成，不重跑 advance。
                self.complete(deferred)
            else:
                self.resume_from_child(result)

    def resume_from_child(self, result: FlowResult) -> FlowResult:
        """子流程结束后继续自己：默认就是再走一遍 ``advance``。

        自己的恢复路径不在 ``advance`` 上的流程（回合流程的交互模式）重写它。
        """

        return self.advance(result)

    def _notify_parent(self) -> None:
        parent = self.parent
        self.parent = None
        if parent is not None:
            parent._child_finished(self, FlowResult(self.status, self.result))

    @contextlib.contextmanager
    def _entered(self):
        """执行期间把自己标成"当前流程"，让事件回调里起的流程能认父。"""

        services = getattr(self.context, "services", None) or {}
        engine = services.get("engine")
        if engine is None:
            yield
            return
        engine.enter_flow(self)
        try:
            yield
        finally:
            engine.leave_flow(self)

    def _emit(self, event_type: EventType, **payload: Any) -> None:
        self.context.emit(Event(event_type, source=self, payload=payload))

    @abstractmethod
    def advance(self, response: Any = None) -> FlowResult:
        raise NotImplementedError


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import GameContext
