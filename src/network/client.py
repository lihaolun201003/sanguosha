"""局域网客户端：连上房主，只提交「意图」，权威状态永远来自房主。

客户端做三件事：

1. 后台线程里完成 TCP 连接（**绝不阻塞 Pygame 主线程**）；
2. 连接成功后所有收发都交给 ``PeerConnection`` 的读写线程；
3. 主线程每帧 ``poll``：把房主广播的 ``LOBBY_STATE`` 当作唯一真相刷新本地镜像。

客户端不会自己改座位、不会自己改别人的准备状态、不会自己开始游戏：
``SET_READY`` 只是请求，结果由房主广播回来。
"""

import queue
import socket
import threading
import time

from .events import EventKind, NetworkEvent
from .lobby import DEFAULT_NICKNAME, LobbyState, clean_nickname
from .protocol import (
    CONNECT_TIMEOUT,
    DEFAULT_PORT,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_TIMEOUT,
    LOBBY_MESSAGE_TYPES,
    PROTOCOL_VERSION,
    READ_TIMEOUT,
    MessageType,
    error_text,
    make_message,
    message_payload,
    message_type,
)
from .transport import PeerConnection

MAX_NOTICES = 4


def describe_connect_error(exc, address):
    """把 socket 异常翻译成玩家看得懂的一句话。"""

    errno = getattr(exc, "errno", None)
    if isinstance(exc, socket.gaierror) or errno in (11001,):
        return "找不到这台电脑（IP 地址不正确）：" + address
    if errno in (10061, 61):
        return "房主没有开房（连接被拒绝）：" + address
    if errno in (10065, 65, 10051, 101, 113):
        return "无法到达该地址（检查是否在同一个局域网）：" + address
    if isinstance(exc, socket.timeout) or errno in (10060, 110):
        return "连接房主超时：没有响应（" + address + "）"
    return "无法连接到房主：" + address + "（" + str(exc) + "）"


class LanClient:
    """一台加入房间的电脑。"""

    def __init__(self, *, host_ip, port=DEFAULT_PORT, nickname=DEFAULT_NICKNAME):
        self.events = queue.Queue()
        self.host_ip = str(host_ip or "").strip()
        self.port = int(port)
        self.nickname = clean_nickname(nickname)

        self.conn = None
        self.lobby = None
        self.local_player_id = ""
        self.connected = False
        self.handshaked = False
        self.error = ""
        self.start_payload = None

        self._closing = threading.Event()
        self._left = False
        self._thread = None
        self._notices = []
        self._last_ping = 0.0
        # 大厅之外的消息（对局视图 / 决策请求）由网络桥在主线程消费。
        self.game_messages = []

    # ==================================================
    # 对外信息（UI 只读这些）
    # ==================================================

    @property
    def address_text(self):
        return self.host_ip + ":" + str(self.port)

    @property
    def notices(self):
        return tuple(self._notices)

    def _notice(self, text):
        if not text:
            return
        self._notices.append(str(text))
        del self._notices[:-MAX_NOTICES]

    # ==================================================
    # 连接
    # ==================================================

    def start(self):
        """开始异步连接（立刻返回，UI 不会卡住）。"""

        self._thread = threading.Thread(
            target=self._connect, name="lan-connect", daemon=True)
        self._thread.start()
        return self

    def _connect(self):
        address = self.address_text
        try:
            sock = socket.create_connection(
                (self.host_ip, self.port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            self.events.put(NetworkEvent.failure(
                describe_connect_error(exc, address)))
            return
        if self._closing.is_set():
            try:
                sock.close()
            except OSError:
                pass
            return

        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

        conn = PeerConnection(sock, self.events, address=address,
                              read_timeout=READ_TIMEOUT)
        # 先挂好连接对象，再投递事件：主线程看到事件时一定能读到它。
        self.conn = conn
        conn.start()
        conn.send(make_message(
            MessageType.HELLO, protocol=PROTOCOL_VERSION, nickname=self.nickname))
        self.events.put(NetworkEvent.connected(detail=conn))

    # ==================================================
    # 主动操作（主线程调用）
    # ==================================================

    def set_ready(self, ready):
        """把「准备 / 取消准备」的**请求**发给房主（等房主广播结果）。"""

        if self.conn is None or not self.handshaked:
            return False
        return self.conn.send(make_message(MessageType.SET_READY, ready=bool(ready)))

    def send(self, message_type_, **payload):
        """给房主发一条消息（断线或未握手返回 False）。"""

        if self.conn is None or not self.conn.alive:
            return False
        return self.conn.send(make_message(message_type_, **payload))

    def drain_game_messages(self):
        """把对局阶段的消息交给网络桥。"""

        if not self.game_messages:
            return []
        messages = self.game_messages
        self.game_messages = []
        return messages

    def leave(self, *, reason="client_left"):
        """主动退出：礼貌告别，然后关闭连接（可重复调用）。"""

        self._left = True
        conn = self.conn
        if conn is not None and conn.alive:
            if self.handshaked:
                conn.send(make_message(MessageType.DISCONNECT, reason=reason))
            conn.close(notify=False)
        self.connected = False
        self.handshaked = False
        self._closing.set()

    def close(self):
        self.leave()

    # ==================================================
    # 每帧轮询（主线程调用）
    # ==================================================

    def poll(self):
        """处理网络事件；返回提示文本列表（致命错误写在 ``self.error``）。"""

        self._notices = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        self._check_heartbeat()
        notices = list(self._notices)
        self._notices = []
        return notices

    def _handle_event(self, event):
        if event.kind == EventKind.CONNECTED:
            self.connected = True
            return
        if event.kind == EventKind.MESSAGE:
            self._handle_message(event.message)
            return
        if event.kind == EventKind.ERROR:
            self._fail(event.error)
            return
        if event.kind == EventKind.DISCONNECTED:
            self.connected = False
            if not self._left and not self.error:
                # 底层错误（WinError / ConnectionReset…）对玩家没有意义，
                # 统一说人话；真正的原因在开发时看日志即可。
                self._fail("房主已断开连接")
            return

    def _handle_message(self, message):
        kind = message_type(message)
        payload = message_payload(message)

        if kind not in LOBBY_MESSAGE_TYPES:
            # 对局阶段的消息（视图 / 决策请求）交给网络桥处理。
            self.game_messages.append(message)
            return

        if kind == MessageType.WELCOME:
            self.local_player_id = str(payload.get("player_id") or "")
            self.lobby = LobbyState.from_dict(payload.get("lobby"))
            self.handshaked = True
            self._notice("已加入房间 " + (self.lobby.room_id or ""))
            return

        if kind == MessageType.LOBBY_STATE:
            # 权威快照：直接替换本地镜像，不做任何合并或猜测。
            self.lobby = LobbyState.from_dict(payload.get("lobby"))
            if self.lobby.started:
                self._notice("房主已开始游戏")
            return

        if kind == MessageType.PLAYER_JOINED:
            self._notice(str(payload.get("nickname") or "玩家") + " 加入了房间")
            return
        if kind == MessageType.PLAYER_LEFT:
            self._notice(str(payload.get("nickname") or "玩家") + " 离开了房间")
            return

        if kind == MessageType.START_GAME:
            self.start_payload = dict(payload)
            if self.lobby is not None:
                self.lobby.started = True
            self._notice("房主已开始游戏")
            return

        if kind == MessageType.ERROR:
            self._fail(error_text(payload.get("code"), str(payload.get("message") or "")))
            return

        if kind == MessageType.DISCONNECT:
            if not self._left:
                self._fail(error_text(payload.get("reason"), "房主已断开连接"))
            return

    def _fail(self, text):
        """记录致命错误（一次即可）并收尾连接。"""

        if not self.error:
            self.error = text or "联机中断"
        self.connected = False
        self.handshaked = False
        self._closing.set()
        conn = self.conn
        if conn is not None and conn.alive:
            conn.close(notify=False)

    def _check_heartbeat(self):
        conn = self.conn
        if conn is None or not conn.alive:
            return
        now = time.monotonic()
        if now - self._last_ping >= HEARTBEAT_INTERVAL:
            self._last_ping = now
            conn.send(make_message(MessageType.PING))
        if self.handshaked and now - conn.last_seen > HEARTBEAT_TIMEOUT:
            self._fail("房主已断开连接（心跳超时）")
