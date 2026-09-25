"""大厅数据模型：只有房主持有权威实例，客户端持有只读镜像。

设计要点（Phase 11.1）：

* **身份不用 socket**：每名玩家拿到稳定的 ``player_id``（UUID 片段），
  socket 只是传输通道，断开重进不会沿用同一个身份。
* **座位按加入顺序分配**：房主固定 seat 0（与 ``Player.seat`` 同一套 0 基
  座次），后来者取最小的空位。换座 / 观战 / 踢人留给后续阶段。
* 这一层不认识任何具体武将、牌或流程：它只知道房间、玩家、准备状态。
"""

import re
import uuid

# 人数下限与上限：与自由混战的 2～8 人一致。
MIN_PLAYERS = 2
MAX_PLAYERS_LIMIT = 8

MAX_NICKNAME = 12
DEFAULT_NICKNAME = "玩家"

# 昵称里不允许出现控制字符与换行（它们会破坏 UI 排版）。
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def clean_nickname(text, fallback=DEFAULT_NICKNAME):
    """昵称清洗：去控制字符、压缩空白、限长，空则退回默认名。"""

    if text is None:
        return fallback
    cleaned = _CONTROL_CHARS.sub("", str(text)).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned[:MAX_NICKNAME].strip()
    return cleaned or fallback


def new_player_id():
    """稳定的玩家身份：UUID 片段（不用 socket 当身份）。"""

    return uuid.uuid4().hex[:12]


def new_room_id():
    return uuid.uuid4().hex[:6].upper()


def planned_battle_size(mode, human_count):
    """给定模式与真人数，本局总人数（真人 + AI 补位）。

    规则只有一条：**模式允许人数中 ≧ 真人数的那个最小值**。身份局只有 5～8
    人（5 = 主公/忠臣/反贼/反贼/内奸），所以 2 名真人进房就是 5 人局、补 3 个
    AI，而不是退化成 2 人无身份对局；5 名真人则一人一座、不补 AI。自由混战
    的允许人数是 2～8 人，于是"有几名真人就是几人"，与联机旧行为一致。

    大厅界面与房主开局（``HostMatch``）都调用这里，保证"显示的人数"就是
    "真正开局的人数"。
    """

    count = max(1, int(human_count or 0))
    # ``allowed_player_counts`` 是类属性（实例也读得到），所以模式对象传类或
    # 实例都可以：大厅里拿到的可能只是注册表里那个类。
    counts = tuple(getattr(mode, "allowed_player_counts", ()) or ())
    allowed = sorted(counts) if counts else list(range(2, 9))
    for size in allowed:
        if int(size) >= count:
            return int(size)
    return int(allowed[-1]) if allowed else count


class LobbyPlayer:
    """大厅里的一个玩家条目。"""

    __slots__ = ("player_id", "nickname", "seat", "ready", "is_host",
                 "connected", "address")

    def __init__(self, player_id, nickname, seat, *, ready=False, is_host=False,
                 connected=True, address=""):
        self.player_id = str(player_id)
        self.nickname = clean_nickname(nickname)
        self.seat = int(seat)
        self.ready = bool(ready)
        self.is_host = bool(is_host)
        self.connected = bool(connected)
        self.address = str(address or "")

    # ---- 展示用的短标签（UI 只读这个） ----

    @property
    def status_label(self):
        if not self.connected:
            return "已掉线"
        if self.is_host:
            return "房主"
        return "已准备" if self.ready else "未准备"

    def to_dict(self):
        return {
            "player_id": self.player_id,
            "nickname": self.nickname,
            "seat": self.seat,
            "ready": self.ready,
            "is_host": self.is_host,
            "connected": self.connected,
            "address": self.address,
        }

    @classmethod
    def from_dict(cls, data):
        data = data if isinstance(data, dict) else {}
        return cls(
            data.get("player_id") or new_player_id(),
            data.get("nickname"),
            int(data.get("seat") or 0),
            ready=bool(data.get("ready")),
            is_host=bool(data.get("is_host")),
            connected=bool(data.get("connected", True)),
            address=data.get("address") or "",
        )


class LobbyState:
    """权威大厅状态：房间 + 玩家列表 + 准备状态 + 开局条件。

    只有房主进程会改它；客户端把收到的 LOBBY_STATE 反序列化成同样的对象
    只用于显示，绝不用它做本地决定。
    """

    def __init__(self, *, room_id=None, host_player_id, host_address="",
                 max_players=MAX_PLAYERS_LIMIT, game_mode="ffa", started=False,
                 players=()):
        self.room_id = str(room_id or new_room_id())
        self.host_player_id = str(host_player_id)
        self.host_address = str(host_address or "")
        self.max_players = self.clamp_max_players(max_players)
        self.game_mode = str(game_mode or "ffa")
        self.started = bool(started)
        self.players = list(players)

    # ---- 人数配置 ----

    @staticmethod
    def clamp_max_players(value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = MAX_PLAYERS_LIMIT
        return max(MIN_PLAYERS, min(MAX_PLAYERS_LIMIT, value))

    def set_max_players(self, value):
        """房主改人数上限；不把已在房里的玩家踢出去，只限制后来者。"""

        self.max_players = self.clamp_max_players(value)
        return self.max_players

    # ---- 查询 ----

    def player(self, player_id):
        for player in self.players:
            if player.player_id == player_id:
                return player
        return None

    @property
    def host(self):
        return self.player(self.host_player_id)

    @property
    def count(self):
        return len(self.players)

    @property
    def full(self):
        return len(self.players) >= self.max_players

    def free_seat(self):
        used = {player.seat for player in self.players}
        seat = 0
        while seat in used:
            seat += 1
        return seat

    def ordered(self):
        """按座位号排序的玩家列表（UI 直接遍历它）。"""

        return sorted(self.players, key=lambda player: player.seat)

    def ready_count(self):
        return len([player for player in self.players if player.ready and not player.is_host])

    def waiting_for(self):
        """还没准备的普通玩家昵称（房主自己不需要准备）。"""

        return [player.nickname for player in self.ordered()
                if not player.is_host and not player.ready]

    # ---- 开局条件 ----

    def start_blocker(self):
        """不能开局的原因；返回空串表示可以开局。"""

        if self.started:
            return "对局已经开始"
        if len(self.players) < MIN_PLAYERS:
            return "至少需要 2 名玩家（当前 " + str(len(self.players)) + " 人）"
        waiting = self.waiting_for()
        if waiting:
            return "等待 " + "、".join(waiting) + " 准备"
        return ""

    def can_start(self):
        return not self.start_blocker()

    # ---- 修改（仅房主调用） ----

    def add_player(self, nickname, *, player_id=None, address="", is_host=False):
        """加入一名玩家并分配座位；房间满则返回 None。"""

        if not is_host and self.full:
            return None
        player = LobbyPlayer(
            player_id or new_player_id(),
            clean_nickname(nickname),
            0 if is_host else self.free_seat(),
            ready=False,
            is_host=bool(is_host),
            connected=True,
            address=address,
        )
        self.players.append(player)
        return player

    def remove_player(self, player_id):
        player = self.player(player_id)
        if player is None:
            return None
        self.players.remove(player)
        return player

    def set_ready(self, player_id, ready):
        player = self.player(player_id)
        if player is None:
            return None
        player.ready = bool(ready)
        return player

    def set_connected(self, player_id, connected):
        player = self.player(player_id)
        if player is None:
            return None
        player.connected = bool(connected)
        return player

    # ---- 序列化 ----

    def to_dict(self):
        return {
            "room_id": self.room_id,
            "host_player_id": self.host_player_id,
            "host_address": self.host_address,
            "max_players": self.max_players,
            "game_mode": self.game_mode,
            "started": self.started,
            "players": [player.to_dict() for player in self.ordered()],
        }

    @classmethod
    def from_dict(cls, data):
        data = data if isinstance(data, dict) else {}
        players = [LobbyPlayer.from_dict(item) for item in (data.get("players") or ())]
        return cls(
            room_id=data.get("room_id"),
            host_player_id=data.get("host_player_id") or "",
            host_address=data.get("host_address") or "",
            max_players=data.get("max_players", MAX_PLAYERS_LIMIT),
            game_mode=data.get("game_mode") or "ffa",
            started=bool(data.get("started")),
            players=players,
        )
