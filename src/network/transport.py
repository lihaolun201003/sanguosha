"""TCP 传输层：一条连接的读线程 / 写线程 / 心跳应答。

线程模型（Phase 11.1 的核心约束）::

    读线程   recv → FrameDecoder → events.put(NetworkEvent)
    写线程   outbox.get() → sendall          （主线程只往 outbox 里放）

* 网络线程**只**做 socket I/O 与事件投递，不碰 Game / Lobby / UI；
* 主线程调用 ``send()`` 只是把一个字节串放进队列，绝不阻塞 Pygame；
* PING / PONG 在连接内部消化，不进入上层事件流，UI 不需要认识心跳。

房主与客户端共用这一个类，所以心跳、粘包、半包、断线检测只有一份实现。
"""

import queue
import socket
import threading
import time

from .events import NetworkEvent
from .protocol import (
    JOIN_TIMEOUT,
    READ_TIMEOUT,
    RECV_SIZE,
    WRITE_TIMEOUT,
    FrameDecoder,
    MessageType,
    ProtocolError,
    encode_message,
    make_message,
    message_type,
)


# 本机地址要显示在界面上，但 ``getaddrinfo`` 是系统调用：主循环每帧都问一次
# 会明显拖慢帧率，所以结果做几秒钟的缓存（换网络后也能自动刷新）。
IP_CACHE_SECONDS = 5.0
_ip_cache = {"value": (), "at": 0.0}


def local_ip_addresses(refresh=False):
    """本机的局域网 IPv4 列表（房主需要念给同宿舍的同学听）。

    优先用「连一下路由」拿到默认出口网卡地址（UDP connect 不发包）；
    离线时退回解析主机名。全过程不抛异常，拿不到就返回空元组。
    """

    now = time.monotonic()
    if (not refresh and _ip_cache["value"]
            and now - _ip_cache["at"] < IP_CACHE_SECONDS):
        return _ip_cache["value"]

    addresses = []

    def push(value):
        if value and value not in addresses and not value.startswith("127."):
            addresses.append(value)

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 53))
            push(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            push(info[4][0])
    except OSError:
        pass

    _ip_cache["value"] = tuple(addresses)
    _ip_cache["at"] = now
    return _ip_cache["value"]


def describe_address(address):
    """socket 地址 → "ip:port" 文本。"""

    if isinstance(address, tuple) and len(address) >= 2:
        return str(address[0]) + ":" + str(address[1])
    return str(address or "")


class PeerConnection:
    """一条已建立（已 connect / 已 accept）的 TCP 连接。

    ``events`` 是主线程与网络线程共享的 ``queue.Queue``：读线程往里放事件，
    主线程每帧取。除此之外两侧不共享任何可变状态。
    """

    def __init__(self, sock, events, *, address="", read_timeout=READ_TIMEOUT):
        self.sock = sock
        self.events = events
        self.address = address if isinstance(address, str) else describe_address(address)
        self.read_timeout = float(read_timeout)

        # 握手后才由上层写入（房主分配 player_id / 客户端记住房主）。
        self.player_id = ""
        self.nickname = ""
        self.handshaked = False
        # 房主侧：这条连接必须在 hello 截止前完成握手。
        self.hello_deadline = time.monotonic() + 1e9

        self.last_seen = time.monotonic()
        self.outbox = queue.Queue()
        self._closed = threading.Event()
        self._writer = None
        self._reader = None

    # ==================================================
    # 生命周期
    # ==================================================

    @property
    def alive(self):
        return not self._closed.is_set()

    def start(self):
        """启动读 / 写线程（都是 daemon：进程退出不会因为它们挂住）。"""

        self._writer = threading.Thread(
            target=self._write_loop, name="lan-writer", daemon=True)
        self._reader = threading.Thread(
            target=self._read_loop, name="lan-reader", daemon=True)
        self._writer.start()
        self._reader.start()
        return self

    def close(self, *, notify=True, reason="", error=""):
        """关闭连接：先礼貌告别，再回收线程与 socket（可重复调用）。"""

        if self._closed.is_set():
            return
        if notify:
            self.send(make_message(MessageType.DISCONNECT, reason=reason))
        self._closed.set()

        # 让读线程立刻从 recv 里醒来（Windows 上 SHUT_RD 会让 recv 立即返回）。
        self._shutdown_socket(socket.SHUT_RD)
        self.outbox.put(None)
        self._join(self._reader)
        self._join(self._writer)
        try:
            self.sock.close()
        except OSError:
            pass
        self.events.put(NetworkEvent.disconnected(
            self.player_id, error=error, detail=self))

    def _join(self, thread):
        if thread is None or thread is threading.current_thread():
            return
        if thread.is_alive():
            thread.join(JOIN_TIMEOUT)

    def _shutdown_socket(self, how):
        try:
            self.sock.shutdown(how)
        except OSError:
            pass

    # ==================================================
    # 发送（任何线程都可以调用；只入队，不阻塞）
    # ==================================================

    def send(self, message):
        if self._closed.is_set():
            return False
        try:
            self.outbox.put_nowait(encode_message(message))
        except (ProtocolError, queue.Full):
            return False
        return True

    def _write_loop(self):
        while True:
            try:
                item = self.outbox.get(timeout=WRITE_TIMEOUT)
            except queue.Empty:
                if self._closed.is_set():
                    break
                continue
            if item is None:
                # 关闭信号：队列里排在它前面的消息已经在上一轮发完了。
                break
            try:
                self.sock.sendall(item)
            except OSError:
                break
        self._closed.set()

    # ==================================================
    # 接收
    # ==================================================

    def _read_loop(self):
        decoder = FrameDecoder()
        try:
            self.sock.settimeout(self.read_timeout)
        except OSError:
            pass

        error = ""
        try:
            while not self._closed.is_set():
                try:
                    chunk = self.sock.recv(RECV_SIZE)
                except socket.timeout:
                    continue
                except OSError as exc:
                    error = "连接中断（" + str(exc) + "）"
                    break
                if not chunk:
                    error = "对方关闭了连接"
                    break

                self.last_seen = time.monotonic()
                try:
                    decoder.feed(chunk)
                except ProtocolError as exc:
                    # 无法对齐的字节流只能断开这一条连接，房间不受影响。
                    error = "协议错误：" + str(exc)
                    break

                while True:
                    message = decoder.pop()
                    if message is None:
                        break
                    self._dispatch(message)
        finally:
            self._closed.set()
            self.events.put(NetworkEvent.disconnected(
                self.player_id, error=error, detail=self))

    def _dispatch(self, message):
        """连接层自家处理心跳，其余消息交给主线程。"""

        kind = message_type(message)
        if kind == MessageType.PING:
            self.send(make_message(MessageType.PONG))
            return
        if kind == MessageType.PONG:
            return
        self.events.put(NetworkEvent.incoming(self.player_id, message, self))
