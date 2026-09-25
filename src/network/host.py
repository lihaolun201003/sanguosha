"""局域网房主：内嵌在游戏进程里，同时是 Pygame 客户端与唯一权威。

职责边界（Phase 11.1）：

* 监听 TCP（默认 ``0.0.0.0:9527``），维护权威 ``LobbyState``；
* 所有大厅状态变更（加入 / 离开 / 准备 / 开局）都由这里决定，然后广播；
* 心跳：定期 PING，超时未响应就把该玩家移出房间；
* **不认识任何武将、牌、回合流程**——网络层只处理连接与大厅。

线程划分：accept 线程、每条连接一个读线程 + 写线程；它们的产物全部通过
``queue.Queue`` 交回主线程。改大厅状态的只有主线程（``poll``）。
"""

import queue
import socket
import threading
import time

from .events import EventKind, NetworkEvent
from .lobby import (
    DEFAULT_NICKNAME,
    MAX_PLAYERS_LIMIT,
    LobbyState,
    clean_nickname,
    new_player_id,
)
from .protocol import (
    DEFAULT_PORT,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_TIMEOUT,
    HELLO_TIMEOUT,
    LOBBY_MESSAGE_TYPES,
    PROTOCOL_VERSION,
    MessageType,
    error_text,
    make_message,
    message_payload,
    message_type,
    message_version,
)
from .transport import PeerConnection, local_ip_addresses

# 房主默认监听所有网卡：同宿舍 / 同校园网的同学都能连进来。
HOST_BIND = "0.0.0.0"
# 大厅提示最多保留几条（UI 只显示最近几条）。
MAX_NOTICES = 4


class HostServer:
    """一个内嵌的局域网房主。"""

    def __init__(self, *, nickname=DEFAULT_NICKNAME, max_players=MAX_PLAYERS_LIMIT,
                 port=DEFAULT_PORT, bind=HOST_BIND, game_mode="ffa"):
        self.events = queue.Queue()
        self.nickname = clean_nickname(nickname)
        self.requested_port = int(port)
        self.max_players = LobbyState.clamp_max_players(max_players)
        self.bind = bind
        self.game_mode = str(game_mode or "ffa")

        self.listener = None
        self.bound_port = 0
        self.lobby = None
        self.local_player_id = ""

        # 只有主线程会读写下面这两个容器；网络线程只往 events 里放东西。
        self.connections = []
        self.by_player = {}

        self._closing = threading.Event()
        self._accept_thread = None
        self._last_ping = 0.0
        self._notices = []
        self._running = False
        # 大厅之外的消息（对局决策响应等）由网络桥主线程消费。
        self.game_messages = []

    # ==================================================
    # 生命周期
    # ==================================================

    def start(self):
        """绑定端口并开始接受连接；返回 ``(ok, 中文说明)``。"""

        if self._running:
            return True, ""
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._prepare_listener(listener)
            listener.settimeout(0.4)
            listener.bind((self.bind, self.requested_port))
            listener.listen(MAX_PLAYERS_LIMIT)
        except OSError as exc:
            return False, self._bind_failure_text(exc)

        self.listener = listener
        self.bound_port = listener.getsockname()[1]
        self.local_player_id = new_player_id()
        self.lobby = LobbyState(
            host_player_id=self.local_player_id,
            host_address=self.address_text,
            max_players=self.max_players,
            game_mode=self.game_mode,
        )
        self.lobby.add_player(self.nickname, player_id=self.local_player_id,
                              address="本机", is_host=True)

        self._running = True
        self._last_ping = time.monotonic()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="lan-accept", daemon=True)
        self._accept_thread.start()
        self._notice("房间已创建：" + self.address_text)
        return True, ""

    @staticmethod
    def _prepare_listener(listener):
        """端口占用语义：宁可明确报错，也不要两个监听套接字抢同一个端口。

        Windows 上 ``SO_REUSEADDR`` 会让第二个房主"绑定成功"，然后两个进程
        随机抢夺连接——那是最难排查的一类问题。``SO_EXCLUSIVEADDRUSE`` 让
        第二次绑定直接失败，界面就能给出「端口已被占用」。
        """

        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:  # POSIX：靠 REUSEADDR 避开 TIME_WAIT
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def _bind_failure_text(self, exc):
        errno = getattr(exc, "errno", None)
        if errno in (10048, 48, 98):  # WSAEADDRINUSE / EADDRINUSE
            return "端口 " + str(self.requested_port) + " 已被占用，请换一个端口"
        if errno in (10013, 13, 1):   # 权限不足
            return "端口 " + str(self.requested_port) + " 无法监听（权限不足）"
        return "创建房间失败：" + str(exc)

    def close(self, *, reason="host_closing"):
        """关闭房间：通知所有人后回收全部线程与 socket。"""

        if not self._running:
            return
        self._running = False
        self._closing.set()

        for conn in list(self.connections):
            if conn.handshaked:
                conn.send(make_message(
                    MessageType.ERROR, code=reason, message=error_text(reason)))
            conn.close(notify=True, reason=reason)
        self.connections = []
        self.by_player = {}

        if self._accept_thread is not None:
            if self._accept_thread.is_alive():
                self._accept_thread.join(0.6)
            self._accept_thread = None
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass
            self.listener = None

    # ==================================================
    # 对外信息（UI 只读这些）
    # ==================================================

    @property
    def address_text(self):
        hosts = local_ip_addresses()
        host = hosts[0] if hosts else "127.0.0.1"
        return host + ":" + str(self.bound_port or self.requested_port)

    @property
    def player_count(self):
        return self.lobby.count if self.lobby is not None else 0

    @property
    def full(self):
        return self.lobby.full if self.lobby is not None else True

    @property
    def notices(self):
        return tuple(self._notices)

    def _notice(self, text):
        if not text:
            return
        self._notices.append(str(text))
        del self._notices[:-MAX_NOTICES]

    # ==================================================
    # 房主自己的操作（主线程调用）
    # ==================================================

    def set_max_players(self, value):
        self.max_players = LobbyState.clamp_max_players(value)
        if self.lobby is None:
            return self.max_players
        self.lobby.set_max_players(self.max_players)
        self._broadcast(self._lobby_message())
        return self.max_players

    def start_match(self):
        """开始对局；返回 ``(ok, 原因)``。只有满足条件时才广播 START_GAME。"""

        if self.lobby is None:
            return False, "房间还未创建"
        blocker = self.lobby.start_blocker()
        if blocker:
            return False, blocker
        self.lobby.started = True
        self._broadcast(make_message(
            MessageType.START_GAME,
            mode=self.lobby.game_mode,
            room_id=self.lobby.room_id,
            player_count=self.lobby.count,
            players=[player.nickname for player in self.lobby.ordered()],
        ))
        self._broadcast(self._lobby_message())
        self._notice("房主已开始游戏")
        return True, ""

    # ==================================================
    # 每帧轮询（主线程调用）
    # ==================================================

    def poll(self):
        """处理所有网络事件与心跳；返回本次产生的提示文本列表。"""

        if not self._running:
            return []
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
        if event.kind == EventKind.ACCEPTED:
            self._register(event.detail)
            return
        if event.kind == EventKind.MESSAGE:
            conn = event.detail
            if conn is None:
                conn = self.by_player.get(event.player_id)
            if conn is not None:
                self._handle_message(conn, event.message)
            return
        if event.kind == EventKind.DISCONNECTED:
            self._handle_disconnected(event)
            return
        if event.kind == EventKind.ERROR:
            self._notice(event.error)
            return

    def _register(self, conn):
        """accept 到的新连接：先等着它握手。"""

        if conn is None or self._closing.is_set():
            return
        conn.hello_deadline = time.monotonic() + HELLO_TIMEOUT
        self.connections.append(conn)

    def _handle_disconnected(self, event):
        conn = event.detail
        if conn is None or conn not in self.connections:
            return
        self.connections.remove(conn)
        if conn.player_id:
            self.by_player.pop(conn.player_id, None)
        # 读线程可能是自己撞上 EOF / 协议错误退出的，socket 还没关：
        # 统一由主线程收尾（close 是幂等的，重复的 DISCONNECTED 会被忽略）。
        conn.close(notify=False)

        player_id = conn.player_id
        if not player_id or self.lobby is None:
            return
        player = self.lobby.remove_player(player_id)
        if player is None:
            return
        # 单人异常退出绝不能影响房间：这里只改大厅，然后广播。
        self._notice(player.nickname + (" 掉线了" if event.error else " 离开了房间"))
        self._broadcast(make_message(
            MessageType.PLAYER_LEFT, player_id=player_id, nickname=player.nickname))
        self._broadcast(self._lobby_message())

    # ==================================================
    # 消息处理（主线程）
    # ==================================================

    def _handle_message(self, conn, message):
        kind = message_type(message)
        payload = message_payload(message)

        if kind == MessageType.HELLO:
            self._handle_hello(conn, message)
            return
        if not conn.handshaked:
            # 没握手就说别的话：不理会（等 hello 超时自然断开）。
            return

        if kind == MessageType.SET_READY:
            self._handle_set_ready(conn, payload)
        elif kind == MessageType.PING:
            conn.send(make_message(MessageType.PONG))
        elif kind == MessageType.DISCONNECT:
            conn.close(notify=False)
        elif kind in LOBBY_MESSAGE_TYPES:
            pass                       # 大厅阶段已经处理过 / 不该由客户端发
        else:
            # 对局阶段的消息（决策响应等）交给网络桥，HostServer 不认识它们。
            self.game_messages.append((conn.player_id, message))

    def _handle_hello(self, conn, message):
        if conn.handshaked:
            return
        payload = message_payload(message)
        if message_version(message) != PROTOCOL_VERSION:
            self._reject(conn, "version_mismatch")
            return
        if self.lobby.started:
            self._reject(conn, "already_started")
            return
        if self.lobby.full:
            self._reject(conn, "room_full")
            return

        player = self.lobby.add_player(
            payload.get("nickname"), address=conn.address)
        if player is None:
            self._reject(conn, "room_full")
            return

        conn.player_id = player.player_id
        conn.nickname = player.nickname
        conn.handshaked = True
        self.by_player[player.player_id] = conn

        conn.send(make_message(
            MessageType.WELCOME,
            player_id=player.player_id,
            room_id=self.lobby.room_id,
            lobby=self.lobby.to_dict(),
        ))
        self._broadcast(make_message(
            MessageType.PLAYER_JOINED,
            player_id=player.player_id,
            nickname=player.nickname,
            seat=player.seat,
        ), exclude=conn)
        self._broadcast(self._lobby_message())
        self._notice(player.nickname + " 加入了房间")

    def _handle_set_ready(self, conn, payload):
        player = self.lobby.set_ready(conn.player_id, bool(payload.get("ready")))
        if player is None:
            return
        # 客户端只说"我想怎样"，改完的权威状态一定由这里广播回去。
        self._broadcast(self._lobby_message())

    def _reject(self, conn, code):
        conn.send(make_message(
            MessageType.ERROR, code=code, message=error_text(code)))
        conn.close(notify=True, reason=code)
        self._notice("拒绝了一个连接：" + error_text(code))

    def drain_game_messages(self):
        """把对局阶段的消息交给调用方（网络桥）处理。"""

        if not self.game_messages:
            return []
        messages = self.game_messages
        self.game_messages = []
        return messages

    def send_to(self, player_id, message_type_, **payload):
        """给指定玩家发一条消息（不在线返回 False）。"""

        conn = self.by_player.get(str(player_id))
        if conn is None or not conn.alive:
            return False
        return conn.send(make_message(message_type_, **payload))

    def send_to_all(self, message_type_, **payload):
        message = make_message(message_type_, **payload)
        self._broadcast(message)
        return True

    def _lobby_message(self):
        return make_message(MessageType.LOBBY_STATE, lobby=self.lobby.to_dict())

    def _broadcast(self, message, *, exclude=None):
        for conn in list(self.connections):
            if conn is exclude or not conn.handshaked:
                continue
            conn.send(message)

    # ==================================================
    # 心跳
    # ==================================================

    def _check_heartbeat(self):
        now = time.monotonic()
        if now - self._last_ping >= HEARTBEAT_INTERVAL:
            self._last_ping = now
            for conn in list(self.connections):
                if conn.handshaked:
                    conn.send(make_message(MessageType.PING))

        for conn in list(self.connections):
            # last_seen 由读线程更新，主线程只读：个别帧读到略旧的值无影响。
            if conn.handshaked:
                if now - conn.last_seen > HEARTBEAT_TIMEOUT:
                    self._notice((conn.nickname or "玩家") + " 掉线了")
                    conn.close(notify=False, error="心跳超时")
            elif now > conn.hello_deadline:
                conn.close(notify=False, error="握手超时")

    # ==================================================
    # 接受连接（accept 线程：只做 accept 与投递）
    # ==================================================

    def _accept_loop(self):
        while not self._closing.is_set():
            listener = self.listener
            if listener is None:
                break
            try:
                sock, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            conn = PeerConnection(sock, self.events, address=address)
            conn.start()
            self.events.put(NetworkEvent.accepted(conn))
