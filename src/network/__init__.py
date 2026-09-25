"""局域网联机（Phase 11.1）：内嵌房主 + 大厅。

这一层只负责四件事：

* TCP 传输（``protocol`` / ``transport``）
* 线程安全的事件队列（``events``）
* 权威大厅（``lobby`` / ``host`` / ``client``）
* 主线程门面（``session``）

它**不认识**武将、技能、牌与回合流程——规则仍在引擎里，网络层不写任何
``if card.name == ...`` 之类的特判。联机对局的正式同步留到 Phase 11.2。

本包不导入 pygame，可以在无显示环境下单独测试。
"""

from .client import LanClient
from .events import EventKind, NetworkEvent
from .host import HostServer
from .lobby import (
    MAX_NICKNAME,
    MAX_PLAYERS_LIMIT,
    MIN_PLAYERS,
    LobbyPlayer,
    LobbyState,
    clean_nickname,
)
from .protocol import (
    DEFAULT_PORT,
    MAX_PORT,
    MIN_PORT,
    PROTOCOL_VERSION,
    MessageType,
    ProtocolError,
    decode_message,
    encode_message,
    make_message,
)
from .session import LanSession, parse_host_address, parse_port
from .transport import local_ip_addresses

__all__ = [
    "DEFAULT_PORT",
    "EventKind",
    "HostServer",
    "LanClient",
    "LanSession",
    "LobbyPlayer",
    "LobbyState",
    "MAX_NICKNAME",
    "MAX_PLAYERS_LIMIT",
    "MAX_PORT",
    "MIN_PLAYERS",
    "MIN_PORT",
    "MessageType",
    "NetworkEvent",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "clean_nickname",
    "decode_message",
    "encode_message",
    "local_ip_addresses",
    "make_message",
    "parse_host_address",
    "parse_port",
]
