"""局域网协议：带长度前缀的 JSON 消息（Phase 11.1）。

帧格式::

    +----------------+----------------------------------+
    | 4 字节大端长度  |  UTF-8 编码的 JSON 负载（N 字节）  |
    +----------------+----------------------------------+

TCP 是**字节流**：一次 ``recv`` 可能只拿到半个帧，也可能一次拿到三帧半
（粘包）。所以接收侧必须自己缓冲与切分（``FrameDecoder``），任何
「一次 recv == 一条消息」的假设都会在真实网络里偶发丢消息。

每条消息自带协议版本 ``v``，字段固定为 ``v / type / id / payload``：以后
加字段只动 payload，改协议则靠 ``v`` 在握手阶段拒绝不兼容的对端。
"""

import json
import struct

# ==================================================
# 版本与默认配置
# ==================================================

PROTOCOL_VERSION = 1

# 默认端口：程序不写死只能用 9527，UI 与本模块都允许改。
DEFAULT_PORT = 9527
MIN_PORT = 1
MAX_PORT = 65535

# 单帧上限：超过直接判为协议错误并断开该连接（正常大厅消息只有几百字节）。
MAX_FRAME_BYTES = 64 * 1024

# 单次 recv 的缓冲大小。
RECV_SIZE = 4096

HEADER = struct.Struct(">I")


# ==================================================
# 连接运行参数（集中配置，避免散落的魔法数字）
# ==================================================

# 心跳：房主每 HEARTBEAT_INTERVAL 秒发一次 PING，对方立即回 PONG；
# 任何一方超过 HEARTBEAT_TIMEOUT 秒没收到任何字节就判掉线。
HEARTBEAT_INTERVAL = 3.0
HEARTBEAT_TIMEOUT = 10.0

# 客户端异步连接房主的超时（连接在后台线程里做，UI 不会卡住）。
CONNECT_TIMEOUT = 6.0
# 连上之后必须在 HELLO_TIMEOUT 秒内完成握手，否则房主断开它。
HELLO_TIMEOUT = 6.0

# socket 读写超时：让读写线程能定期醒来检查关闭标志，从而干净退出。
READ_TIMEOUT = 0.4
WRITE_TIMEOUT = 0.2

# 关闭连接时等待线程回收的上限（毫秒级的实际值，只是兜底）。
JOIN_TIMEOUT = 0.6


class ProtocolError(Exception):
    """对端发来的字节流无法解析（长度越界 / 非 JSON / 缺字段）。"""


# ==================================================
# 消息类型
# ==================================================

class MessageType:
    """本阶段用到的全部消息类型（客户端只能发请求，不能发状态）。"""

    # 握手
    HELLO = "HELLO"                    # Client → Host：协议版本 + 昵称
    WELCOME = "WELCOME"                # Host → Client：分配 player_id + 权威大厅
    # 大厅
    LOBBY_STATE = "LOBBY_STATE"        # Host → Client：权威大厅全量快照
    PLAYER_JOINED = "PLAYER_JOINED"    # Host → Client：有人加入（提示用）
    PLAYER_LEFT = "PLAYER_LEFT"        # Host → Client：有人离开 / 掉线（提示用）
    SET_READY = "SET_READY"            # Client → Host：请求改自己的准备状态
    START_GAME = "START_GAME"          # Host → Client：房主开始对局
    # 对局（Phase 11.2）
    GAME_SETUP = "GAME_SETUP"          # Host → Client：开局（名单 + 我的手牌）
    GAME_VIEW = "GAME_VIEW"            # Host → Client：权威状态的最小只读视图
    DECISION_REQUEST = "DECISION_REQUEST"      # Host → Client：该你做决定了
    DECISION_RESPONSE = "DECISION_RESPONSE"    # Client → Host：我选好了
    DECISION_CANCELLED = "DECISION_CANCELLED"  # Host → Client：这条请求作废
    DECISION_ACCEPTED = "DECISION_ACCEPTED"    # Host → Client：房主已接受并生效
    DECISION_REJECTED = "DECISION_REJECTED"    # Host → Client：这条回答被拒绝（可重试）
    GAME_ABORTED = "GAME_ABORTED"      # Host → Client：联网对局终止
    # 对局（Phase 11.5：结算之后的重开协商，仍然由房主统一决定）
    RESTART_REQUEST = "RESTART_REQUEST"    # Client → Host：我希望再来一局
    MATCH_RESTART = "MATCH_RESTART"        # Host → Client：房主重新开局，回大厅等新局
    # 对局（Phase 11.3：逐人只读视图 + 权威表现事件）
    GAME_VIEW_SNAPSHOT = "GAME_VIEW_SNAPSHOT"  # Host → Client：带 revision 的逐人全量视图
    GAME_EVENT = "GAME_EVENT"                  # Host → Client：已按可见性过滤的表现事件
    STATE_RESYNC_REQUEST = "STATE_RESYNC_REQUEST"  # Client → Host：我漏了状态，请重发
    GAME_RESULT = "GAME_RESULT"                # Host → Client：对局结果（由房主判定）
    # 开局流程（Phase 11.4.4：与单机同一套"先看身份，再选将"）
    IDENTITY_ASSIGN = "IDENTITY_ASSIGN"        # Host → Client：**你自己的**身份（别人的不发）
    IDENTITY_READY = "IDENTITY_READY"          # Client → Host：我看过自己的身份了
    # Client → Host：「我的武将池」——**这名玩家自己的**偏好，房主据此为他抽候选。
    # 它只服务于抽签，房主不会转发给其他玩家（别人只需要知道最终选了谁）。
    FAVORITE_POOL = "FAVORITE_POOL"
    GENERAL_CANDIDATES = "GENERAL_CANDIDATES"  # Host → Client：给你的候选武将，选一个
    GENERAL_PICKED = "GENERAL_PICKED"          # Client → Host：我选的武将
    # 通用
    ERROR = "ERROR"                    # Host → Client：拒绝 / 出错（带 code）
    PING = "PING"
    PONG = "PONG"
    DISCONNECT = "DISCONNECT"          # 双向：礼貌告别


# HostServer / LanClient 在大厅阶段就认识的消息；其余一律交给网络桥。
LOBBY_MESSAGE_TYPES = frozenset({
    MessageType.HELLO,
    MessageType.WELCOME,
    MessageType.LOBBY_STATE,
    MessageType.PLAYER_JOINED,
    MessageType.PLAYER_LEFT,
    MessageType.SET_READY,
    MessageType.START_GAME,
    MessageType.ERROR,
    MessageType.PING,
    MessageType.PONG,
    MessageType.DISCONNECT,
})


# 错误码 → 玩家看得懂的中文说明。UI 直接显示这些文案，绝不把 traceback
# 丢给玩家。
ERROR_TEXTS = {
    "match_aborted": "联网对局已终止",
    "player_gone": "玩家掉线，联网对局终止",
    "room_full": "房间已满",
    "version_mismatch": "协议版本不兼容，请双方使用同一版本的游戏",
    "already_started": "游戏已经开始，无法加入",
    "bad_message": "收到了无法解析的消息",
    "bad_hello": "握手消息不完整",
    "host_closing": "房主已关闭房间",
    "host_lost": "房主已断开连接",
    "connect_failed": "无法连接到房主",
    "connect_timeout": "连接房主超时",
    "kicked": "已被房主移出房间",
    "stale_revision": "客户端状态落后，已重新同步",
    "resync_unavailable": "房主已不在对局中，无法重新同步",
    "match_restart": "房主重新开局了，请回大厅等待",
}


def error_text(code, message=""):
    """错误码 → 中文文案；未知错误码退回原始文本。"""

    return message or ERROR_TEXTS.get(code, "联机错误（" + str(code) + "）")


# ==================================================
# 消息构造与编码
# ==================================================

def make_message(message_type, *, message_id=0, **payload):
    """构造一条消息字典（payload 直接展开成关键字参数）。"""

    return {
        "v": PROTOCOL_VERSION,
        "type": message_type,
        "id": int(message_id),
        "payload": dict(payload),
    }


def message_type(message):
    if isinstance(message, dict):
        return str(message.get("type") or "")
    return ""


def message_payload(message):
    payload = message.get("payload") if isinstance(message, dict) else None
    return payload if isinstance(payload, dict) else {}


def message_version(message):
    try:
        return int(message.get("v"))
    except (TypeError, ValueError):
        return 0


def encode_message(message):
    """消息字典 → 一个完整帧的字节串。"""

    payload = json.dumps(
        message, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError("消息过大：" + str(len(payload)) + " 字节")
    return HEADER.pack(len(payload)) + payload


def decode_message(payload_bytes):
    """一帧的负载字节 → 消息字典；结构不合法则抛 ProtocolError。"""

    try:
        message = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("不是合法的 JSON 帧") from exc
    if not isinstance(message, dict):
        raise ProtocolError("帧内容不是对象")
    if not isinstance(message.get("type"), str) or not message["type"]:
        raise ProtocolError("帧缺少 type 字段")
    if "v" not in message:
        raise ProtocolError("帧缺少协议版本")
    return message


class FrameDecoder:
    """把一个 socket 的字节流切成消息（接收侧每读一段就喂一次）。

    只做缓冲与切分，不做业务判断：粘包拆包在这里被消化掉，上层拿到的
    永远是一条条完整的消息字典。
    """

    def __init__(self, max_frame_bytes=MAX_FRAME_BYTES):
        self.max_frame_bytes = int(max_frame_bytes)
        self._buffer = bytearray()
        self._queue = []
        self.received_bytes = 0
        self.dropped_frames = 0

    @property
    def buffered(self):
        return len(self._buffer)

    def feed(self, chunk):
        """喂入原始字节，切出所有已完整的帧（结果放入内部队列）。"""

        if chunk:
            self._buffer.extend(chunk)
            self.received_bytes += len(chunk)
        while True:
            if len(self._buffer) < HEADER.size:
                return
            (length,) = HEADER.unpack_from(self._buffer, 0)
            if length <= 0 or length > self.max_frame_bytes:
                # 长度头本身就是垃圾：无法再对齐到下一帧，只能放弃这条连接。
                raise ProtocolError("帧长度非法：" + str(length))
            total = HEADER.size + length
            if len(self._buffer) < total:
                return
            payload = bytes(self._buffer[HEADER.size:total])
            del self._buffer[:total]
            try:
                self._queue.append(decode_message(payload))
            except ProtocolError:
                # 单帧内容坏掉不影响后面的帧：丢掉这一帧，继续切分。
                self.dropped_frames += 1

    def pop(self):
        """取出下一条完整消息；没有则返回 None。"""

        if self._queue:
            return self._queue.pop(0)
        return None

    def __len__(self):
        return len(self._queue)
