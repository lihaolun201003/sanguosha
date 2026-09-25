"""主线程侧的联机会话门面：房主会话与客户端会话在这里长得一模一样。

UI 只需要认识 ``LanSession``：它对外暴露 ``lobby / is_host / local_player_id /
set_ready / start_match / poll / leave``，至于「我是房主还是客户端」由内部
的 ``HostServer`` / ``LanClient`` 承担。这样 Lobby 界面不必写
``if is_host: ... else: ...`` 的两套逻辑，也不会有人误以为客户端能改权威状态。
"""

import time

from .client import LanClient
from .host import HostServer
from .lobby import (
    DEFAULT_NICKNAME,
    MAX_PLAYERS_LIMIT,
    clean_nickname,
    planned_battle_size,
)
from .protocol import DEFAULT_PORT, MAX_PORT, MIN_PORT

MAX_NOTICES = 6

#: 联机默认模式：**标准身份局**。
#
# 联机不是一种新模式，它就是"身份局里某些座位的控制器换成远程真人"。
# 默认落在自由混战上会让玩家一进房就得到一局没有身份、没有武将的对局，
# 而那从来不是"局域网三国杀"的意思。房主可以在多人菜单里改。
DEFAULT_GAME_MODE = "identity"

_DEFAULT_MODES = None


def default_mode_registry():
    """内置模式注册表（只读，用于没有权威 Game 的客户端进程）。"""

    global _DEFAULT_MODES
    if _DEFAULT_MODES is None:
        from src.game.modes import create_default_mode_registry

        _DEFAULT_MODES = create_default_mode_registry()
    return _DEFAULT_MODES


def parse_port(text, default=DEFAULT_PORT):
    """把玩家输入的端口解析成整数；返回 ``(端口, 错误说明)``。

    ``0`` 表示由系统分配一个空闲端口（大厅会显示真实端口，工具与测试用它）。
    """

    text = "" if text is None else str(text).strip()
    if not text:
        return int(default), ""
    if not text.isdigit():
        return 0, "端口必须是数字（默认 " + str(DEFAULT_PORT) + "）"
    value = int(text)
    if value != 0 and (value < MIN_PORT or value > MAX_PORT):
        return 0, "端口范围是 " + str(MIN_PORT) + "～" + str(MAX_PORT)
    return value, ""


def parse_host_address(text, default_port=DEFAULT_PORT):
    """把玩家输入的地址解析成 ``(ip, 端口, 错误说明)``。

    支持 ``192.168.1.23`` 与 ``192.168.1.23:9527`` 两种写法。
    """

    text = str(text or "").strip()
    if not text:
        return "", 0, "请输入房主的局域网 IP"
    port = int(default_port)
    if ":" in text:
        head, _, tail = text.partition(":")
        text = head.strip()
        port, error = parse_port(tail, default_port)
        if error:
            return "", 0, error
    if not text or " " in text:
        return "", 0, "IP 地址格式不正确，例如 192.168.1.23"
    return text, port, ""


class LanSession:
    """一次局域网会话（创建房间 或 加入房间，二选一）。"""

    def __init__(self):
        self.host = None
        self.client = None
        self.notices = []
        self.error = ""
        self._exit_notice = ""
        self.max_players = MAX_PLAYERS_LIMIT
        self.nickname = DEFAULT_NICKNAME
        self.mode_label = ""
        #: 房主选的联机模式（多人菜单里可改）；开房时写进权威大厅。
        self.game_mode = DEFAULT_GAME_MODE
        # 联网对局：房主侧是 HostMatch（权威桥），客户端侧是 ClientMatch（只读视图）。
        self.match = None
        # 房主的权威 Game 由 main.py 注入（网络层不自己 new Game）。
        self.game = None

    # ==================================================
    # 状态（UI 只读）
    # ==================================================

    @property
    def active(self):
        return self.host is not None or self.client is not None

    @property
    def is_host(self):
        return self.host is not None

    @property
    def lobby(self):
        if self.host is not None:
            return self.host.lobby
        if self.client is not None:
            return self.client.lobby
        return None

    @property
    def local_player_id(self):
        if self.host is not None:
            return self.host.local_player_id
        if self.client is not None:
            return self.client.local_player_id
        return ""

    @property
    def local_player(self):
        lobby = self.lobby
        if lobby is None:
            return None
        return lobby.player(self.local_player_id)

    @property
    def connected(self):
        """客户端：与房主握手完成即为已连接；房主：房间在跑就是已连接。"""

        if self.host is not None:
            return True
        if self.client is not None:
            return self.client.handshaked
        return False

    @property
    def connecting(self):
        """客户端正在连接（还没握手也没报错）。"""

        if self.client is None:
            return False
        return not self.client.handshaked and not self.client.error

    @property
    def started(self):
        lobby = self.lobby
        return bool(lobby is not None and lobby.started)

    @property
    def address_text(self):
        if self.host is not None:
            return self.host.address_text
        if self.client is not None:
            return self.client.address_text
        return ""

    @property
    def local_ip_hint(self):
        """本机可以告诉别人的地址（房主在大厅里展示它）。"""

        if self.host is not None:
            return self.host.address_text
        return ""

    # ==================================================
    # 建立会话
    # ==================================================

    def create_room(self, nickname, max_players, port, game_mode=None):
        """创建房间（同步绑定端口）；返回 ``(ok, 中文说明)``。"""

        self.leave()
        value, error = parse_port(port)
        if error:
            return False, error

        self.game_mode = str(game_mode or self.game_mode or DEFAULT_GAME_MODE)
        self.nickname = clean_nickname(nickname)
        self.max_players = max_players
        host = HostServer(
            nickname=self.nickname,
            max_players=max_players,
            port=value,
            game_mode=self.game_mode,
        )
        ok, message = host.start()
        if not ok:
            return False, message
        self.host = host
        self.mode_label = "房主"
        self.error = ""
        self.notices = []
        return True, ""

    # ==================================================
    # 本局人数（大厅与开局共用同一份推算）
    # ==================================================

    @property
    def mode_of_lobby(self):
        """当前大厅模式的模式对象。

        房主用自己那个 Game 的注册表；客户端进程里没有权威 Game（也不该有），
        所以退回内置的模式注册表——它只需要 ``allowed_player_counts`` 这个
        类属性就能算出"这一局几个人"。
        """

        lobby = self.lobby
        mode_id = str(getattr(lobby, "game_mode", "") or "") if lobby is not None else ""
        if self.game is not None and mode_id:
            mode = self.game.modes.get(mode_id)
            if mode is not None:
                return mode
        if self.game is not None and not mode_id:
            return getattr(self.game, "mode", None)
        return default_mode_registry().get(mode_id) if mode_id else None

    @property
    def planned_battle_size(self):
        """本局总人数（真人 + AI 补位）；大厅在开局前就显示它。"""

        lobby = self.lobby
        if lobby is None:
            return 0
        return planned_battle_size(self.mode_of_lobby, lobby.count)

    @property
    def planned_ai_count(self):
        lobby = self.lobby
        if lobby is None:
            return 0
        return max(0, self.planned_battle_size - lobby.count)

    def join_room(self, nickname, host_ip, port):
        """开始异步连接房主；返回 ``(ok, 中文说明)``（真正结果看 ``poll``）。"""

        self.leave()
        value, error = parse_port(port)
        if error:
            return False, error
        # 地址栏里允许直接写 "192.168.1.23:9527"，此时以地址栏里的端口为准。
        address, parsed_port, error = parse_host_address(host_ip, value)
        if error:
            return False, error
        value = parsed_port

        self.nickname = clean_nickname(nickname)
        client = LanClient(host_ip=address, port=value, nickname=self.nickname)
        self.client = client
        self.mode_label = "客户端"
        self.error = ""
        self.notices = []
        client.start()
        return True, ""

    # ==================================================
    # 会话中的操作
    # ==================================================

    def set_ready(self, ready):
        """准备 / 取消准备；房主自己不需要准备，返回 False。"""

        if self.client is None:
            return False
        return self.client.set_ready(ready)

    def set_max_players(self, value):
        """改人数上限：房主改的是权威大厅（并广播），客户端只记本地选择。"""

        self.max_players = value
        if self.host is not None:
            return self.host.set_max_players(value)
        return value

    def start_match(self):
        """房主开始游戏：建立权威对局并把开局信息发给每个客户端。

        返回 ``(ok, 原因)``；客户端调用一律失败。
        """

        if self.host is None:
            return False, "只有房主可以开始游戏"
        if self.match is not None and self.match.started:
            return False, "对局已经开始了"
        # 先确认本地有权威 Game：否则广播了 START_GAME 却建不起对局，
        # 客户端会进到一个房主根本没准备好的"对局"里。
        if self.game is None:
            return False, "本地缺少 Game 实例，无法开始联网对局"
        ok, reason = self.host.start_match()
        if not ok:
            return False, reason

        from .match import HostMatch

        match = HostMatch(self, self.game, self.host.lobby)
        try:
            match.start()
        except Exception as error:                  # pragma: no cover - 兜底
            self.error = "建立联网对局失败：" + str(error)
            return False, self.error
        self.match = match
        return True, ""

    def set_game(self, game):
        """注入房主的权威 Game（main.py 启动时调用一次）。"""

        self.game = game
        return self

    def note(self, text):
        """记一条给玩家看的提示（大厅/对局终止原因）。"""

        if not text:
            return
        self.notices = (self.notices + [str(text)])[-MAX_NOTICES:]

    # ---- 发送（对局阶段） ----

    def send_to_player(self, player_id, message_type_, **payload):
        if self.host is None:
            return False
        return self.host.send_to(player_id, message_type_, **payload)

    def send_to_all(self, message_type_, **payload):
        if self.host is None:
            return False
        return self.host.send_to_all(message_type_, **payload)

    def send_to_host(self, message_type_, **payload):
        if self.client is None:
            return False
        return self.client.send(message_type_, **payload)

    # ---- 视图 / 决策（客户端侧） ----

    @property
    def view(self):
        """客户端看到的只读视图（房主侧没有这个对象）。"""

        return getattr(self.match, "view", None) if self.match is not None else None

    def poll(self):
        """每帧调用：排空网络事件；返回本次的提示文本。"""

        notices = []
        if self.host is not None:
            notices.extend(self.host.poll())
            for player_id, message in self.host.drain_game_messages():
                if self.match is not None:
                    self.match.handle_host_message(player_id, message)
            if self.match is not None:
                self.match.poll()
        if self.client is not None:
            notices.extend(self.client.poll())
            if self.client.error and not self.error:
                self.error = self.client.error
            for message in self.client.drain_game_messages():
                self._route_client_game_message(message)
        if notices:
            self.notices = (self.notices + notices)[-MAX_NOTICES:]
        return notices

    def _route_client_game_message(self, message):
        from .match import ClientMatch
        from .protocol import MessageType, message_type

        # 联机的对局从**开局流程的第一条消息**就开始了：身份分配先于牌局视图
        # 到达，所以这里不只认 GAME_SETUP。
        if self.match is None and message_type(message) in (
                MessageType.IDENTITY_ASSIGN,
                MessageType.GENERAL_CANDIDATES,
                MessageType.GAME_SETUP):
            self.match = ClientMatch(self)
        if self.match is not None:
            self.match.handle_client_message(message)

    def leave(self, notice=""):
        """关闭当前会话（返回菜单 / 换房间前必须调用，避免留下旧房主）。"""

        if self.match is not None:
            if self.host is not None and not self.match.aborted:
                self.match.abort("房主关闭了房间")
            self.match = None
        if self.host is not None:
            self.host.close()
            self.host = None
        if self.client is not None:
            self.client.leave()
            self.client = None
        self._exit_notice = notice or ""
        self.error = ""
        self.mode_label = ""

    def take_exit_notice(self):
        notice = self._exit_notice
        self._exit_notice = ""
        return notice

    # ==================================================
    # 辅助
    # ==================================================

    @staticmethod
    def wait_until(predicate, timeout=5.0, interval=0.02):
        """测试与工具用：在超时前反复推进，直到条件成立。

        真实游戏里由 Pygame 主循环每帧 ``poll`` 推进；这里只是给无窗口的
        脚本一个确定性的等待方式。
        """

        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return bool(predicate())
