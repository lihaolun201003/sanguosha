"""网络线程与 Pygame 主线程之间**唯一**的通信形式。

规则（Phase 11.1 的硬约束）：

* 网络线程只会 ``events.put(...)``，不碰 Game / UI / players / Renderer；
* Pygame 主线程每帧 ``poll`` 一次队列，再决定怎么改界面。

``NetworkEvent`` 是纯数据（可自由打印、可断言），所以两侧都不需要持锁。
"""

from dataclasses import dataclass, field


class EventKind:
    """事件种类。"""

    # 房主 accept 到一个新 TCP 连接（还没握手，还不知道他是谁）。
    ACCEPTED = "accepted"
    # 客户端成功连上房主。
    CONNECTED = "connected"
    # 收到一条完整消息（``message`` 字段是消息字典）。
    MESSAGE = "message"
    # 连接断开（对端关闭、心跳超时、socket 出错）。
    DISCONNECTED = "disconnected"
    # 传输层错误（连不上、协议错误等）。
    ERROR = "error"


@dataclass
class NetworkEvent:
    """一个待主线程处理的网络事实。

    ``detail`` 放线程间传递的对象（例如房主侧的连接对象）；它不参与相等
    比较，也不参与任何界面逻辑。
    """

    kind: str
    player_id: str = ""
    message: dict = field(default_factory=dict)
    error: str = ""
    detail: object = None

    # ---- 构造快捷方式（网络线程侧使用） ----

    @classmethod
    def accepted(cls, connection):
        return cls(EventKind.ACCEPTED, detail=connection)

    @classmethod
    def connected(cls, detail=None):
        return cls(EventKind.CONNECTED, detail=detail)

    @classmethod
    def incoming(cls, player_id, message, detail=None):
        return cls(EventKind.MESSAGE, player_id=player_id, message=message, detail=detail)

    @classmethod
    def disconnected(cls, player_id="", error="", detail=None):
        return cls(EventKind.DISCONNECTED, player_id=player_id, error=error, detail=detail)

    @classmethod
    def failure(cls, error, detail=None):
        return cls(EventKind.ERROR, error=error, detail=detail)

    def __repr__(self):  # pragma: no cover - 仅调试输出
        return "NetworkEvent(%s, player_id=%r, error=%r)" % (
            self.kind, self.player_id, self.error)
