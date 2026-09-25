"""联机场景总入口：多人菜单 + 房间大厅 + 客户端牌桌 + 每帧网络轮询。

Pygame 主线程与网络线程的边界就在这里：``update`` 每帧排空
``LanSession`` 的事件队列，把结果翻译成界面状态与场景切换；网络线程
永远不会碰到 pygame、Game 或 Renderer。
"""

from ..network.session import LanSession
from . import layout
from .human_control import LocalHumanController
from .lan_setup import LanSetupScreen
from .lobby import LobbyScreen
from .multiplayer_menu import MultiplayerMenuScreen, STATUS_ERROR
from .remote_table import RemoteTableScene

# 这几个场景都属于联机流程，由本模块统一接管事件、更新与绘制。
# 注意：房主正式开局后用的是**普通牌桌**（scene == "game"），不在这里——
# 房主的 Game 就是权威对局本身。
SCENE_MENU = "multiplayer_menu"
SCENE_LOBBY = "lobby"
SCENE_REMOTE = "remote_game"
#: 联机开局流程：看身份 → 选将（与单机同屏，数据来自房主）。
SCENE_SETUP = "lan_setup"
LAN_SCENES = (SCENE_MENU, SCENE_LOBBY, SCENE_REMOTE, SCENE_SETUP)

NOTICE_LEFT_ROOM = "已离开房间"
NOTICE_LEFT_LAN = "已退出多人对战"
NOTICE_LEFT_MATCH = "已离开联机对局"


def is_lan_scene(scene):
    return scene in LAN_SCENES


class HostLanController:
    """房主在联网对局里的控制器：规则动作照常，只有"重开"要过网络。

    单机的「重新开始」是 ``game.restart_setup()``——它在联网对局里是**错的**：
    远程玩家不会跟着换局，会停在上一局的只读视图上。联网时改为让网络桥把
    所有人带回大厅，由房主在大厅里重新开局（房主仍然是唯一权威）。
    """

    local_interaction = True

    def __init__(self, inner, scene, match):
        self.inner = inner
        self.scene = scene
        self.match = match

    def run_action(self, action, renderer):
        if action == "restart" and not getattr(self.match, "aborted", ""):
            self._restart(renderer)
            return action
        if action == "menu" and not getattr(self.match, "aborted", ""):
            # 房主「返回主菜单」= 关房：客户端会收到终止通知，各自回大厅/菜单。
            renderer.reset_effects()
            self.scene._leave_room(self.scene.game)
            return action
        return self.inner.run_action(action, renderer)

    def _restart(self, renderer):
        """房主点「重新开始」：带所有人回大厅（客户端收到 MATCH_RESTART）。"""

        renderer.reset_effects()
        game = self.scene.game
        self.match.return_to_lobby("房主重新开局")
        if game is not None:
            game.return_to_menu()
            # 联机的"大厅"就是这一局的出发点：直接回大厅，不在主菜单停一下。
            game.scene = SCENE_LOBBY
        self.scene.remote.reset()
        self.scene.menu.set_status("已结束本局，回到大厅即可重新开局", "info")
        return None

    # ---- 其余动作原样转发给本机权威 ----

    def __getattr__(self, name):
        return getattr(self.inner, name)


class LanScene:
    """联机流程的路由与状态机。"""

    def __init__(self, screen, game=None, renderer=None):
        self.screen = screen
        self.menu = MultiplayerMenuScreen(screen)
        self.lobby = LobbyScreen(screen)
        # 客户端牌桌复用主 Renderer：同一套牌桌 / 动画 / 判定面板。
        self.remote = RemoteTableScene(screen, renderer)
        # 联机开局屏（身份 / 选将）：复用单机的两个屏。
        self.setup = LanSetupScreen(screen)
        self.session = LanSession()
        self.game = game
        self._exit_notice = ""
        self.metrics = None
        if game is not None:
            self.session.set_game(game)

    def bind_game(self, game):
        """注入房主的权威 Game（联网对局要用它建立权威状态）。"""

        self.game = game
        self.session.set_game(game)
        return self

    def human_for(self, game):
        """这一帧该由哪个 HumanController 接收玩家动作。

        联网客户端没有权威 Game：它的动作由 ``RemoteHumanController`` 编码成
        决策响应发回房主；单机与房主本机就是权威，动作直接进 Game。
        点击路由（``ui.interaction``）对两者完全相同。

        房主在**联网对局**里多一层包装：结算页的「重新开始」不能直接重启本机
        Game（那会让远程玩家留在上一局），必须交给网络桥把所有人带回大厅。
        """

        if game.scene == SCENE_REMOTE:
            return self.remote.human
        match = self.session.match
        if self.session.active and self.session.is_host and match is not None:
            return HostLanController(LocalHumanController(game), self, match)
        return LocalHumanController(game)

    # ==================================================
    # 布局 / 屏幕
    # ==================================================

    def sync_layout(self, metrics=None):
        self.metrics = metrics or layout.LayoutMetrics(
            layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.menu.sync_layout(self.metrics)
        self.lobby.sync_layout(self.metrics)
        self.remote.sync_layout(self.metrics)
        self.setup.sync_layout(self.metrics)
        return self

    def set_screen(self, surface):
        self.screen = surface
        self.menu.screen = surface
        self.lobby.screen = surface
        self.remote.set_screen(surface)
        self.setup.set_screen(surface)
        return self

    # ==================================================
    # 进入 / 离开
    # ==================================================

    def enter(self, game):
        """从主菜单进入多人对战：清掉可能残留的旧会话再进菜单。"""

        self.close_session()
        self.menu.connecting = False
        self.menu.set_status("")
        self.menu.clear_fields_focus()
        game.open_multiplayer_menu()
        # 联机默认是标准身份局（房主可在菜单里改）：模式必须在建局前就落到
        # Game 上，否则"身份局 + 远程真人"会退化成"没有身份、没有武将的裸局"。
        self.menu.apply_match_mode(self.session, game)
        return SCENE_MENU

    def close_session(self):
        """关闭会话（重复调用安全：创建 → 返回 → 再创建不会留下旧房主）。"""

        if self.session.active:
            self.session.leave()
        self.session = LanSession()
        self.setup.reset()
        if self.game is not None:
            self.session.set_game(self.game)
        return self

    def take_exit_notice(self):
        notice = self._exit_notice
        self._exit_notice = ""
        return notice

    # ==================================================
    # 事件
    # ==================================================

    def handle_event(self, event, game):
        """返回 ``"back"``（回主菜单）/ ``"handled"`` / ``None``（交给主循环）。"""

        if game.scene == SCENE_SETUP:
            return self.setup.handle_event(event, self.session.match) or "handled"
        if game.scene == SCENE_REMOTE:
            action = self.remote.handle_event(event, self.session.match)
            if action is None and getattr(self.session.match, "restarted", False):
                return "match_restart"
        elif game.scene == SCENE_LOBBY:
            action = self.lobby.handle_event(event, self.session, game)
        else:
            action = self.menu.handle_event(event, self.session, game)

        if action == "back":
            if game.scene == SCENE_LOBBY:
                self._leave_room(game)
                return "handled"
            self.close_session()
            self._exit_notice = NOTICE_LEFT_LAN
            return "back"
        if action == "leave":
            # 对局中途离开（含结算页点「返回主菜单」）：房主关房 / 客户端走人。
            self._leave_room(game)
            return "handled"
        if action == "match_restart":
            # 房主重新开局：回大厅等下一局的 GAME_SETUP（不是错误路径）。
            self._back_to_lobby_after_restart(game)
            return "handled"
        return action

    def _back_to_lobby_after_restart(self, game):
        """房主重开：客户端丢掉这一局的只读对局，回大厅等下一局。"""

        self.remote.reset()
        if self.session.match is not None:
            self.session.match = None
        self.menu.set_status("房主重新开局，回到大厅", "info")
        game.scene = SCENE_LOBBY
        return SCENE_LOBBY

    def _leave_room(self, game):
        """离开房间：房主关房（客户端会立刻收到提示），然后回多人菜单。"""

        self.close_session()
        self.remote.reset()
        self.menu.connecting = False
        self.menu.set_status(NOTICE_LEFT_ROOM, "info")
        game.scene = SCENE_MENU
        return SCENE_MENU

    def is_host_setup(self):
        """房主正在走**联机的开局流程**（看身份 / 选将）？

        这些场景由 main.py 的常规路由显示（与单机同一套屏），但它们结束时
        要把结论回给网络桥，而不是直接开单机局。
        """

        if not self.session.active or not self.session.is_host:
            return False
        match = self.session.match
        return match is not None and getattr(match, "setup_stage", "") in (
            "identity", "identity_done", "choosing", "picking")

    def host_confirm_identity(self, game):
        """房主在身份屏点了「继续」：切到选将屏 **并且**通知网络桥。

        这两件事必须一起做——只切屏不通知，流程会一直停在"等身份确认"，
        对方永远收不到候选武将，两边看起来就是卡死。所以这里只留一份实现，
        UI 与验收脚本都调它。
        """

        game.scene = "general_select"
        return self.host_identity_confirmed()

    def host_identity_confirmed(self):
        """房主看完了自己的身份（本机身份屏点了「继续」）。"""

        match = self.session.match
        return bool(match is not None and match.host_identity_confirmed())

    def host_pick_general(self, game):
        """房主选好了武将；返回 False 表示还在等其他真人选完。"""

        match = self.session.match
        if match is None:
            return False
        return bool(match.host_general_picked(game.selected_general))

    def setup_notice(self, scene=""):
        """开局流程里给房主看的一行提示（等谁 / 该做什么）。

        房主可能停在两个屏上：身份屏，或选将屏。选将屏上"确认出战"可能点得
        比对方早——那时必须明确告诉他还在等谁，否则看起来就是整局卡住了。
        """

        match = self.session.match
        if match is None:
            return ""
        wait = match.wait_message()
        if match.setup_stage in ("identity", "identity_done"):
            if scene == "identity_reveal":
                return "看好了就点「继续」，接下来选武将"
            return wait or "已确认身份，正在等其他人…"
        if match.setup_stage in ("choosing", "picking"):
            if match.host_player_id in match.general_picks:
                return wait or "选好了，正在开局…"
            if wait:
                return "确认出战 → " + wait
            return "选定后点「确认出战」"
        return ""

    # ==================================================
    # 每帧更新（主线程）
    # ==================================================

    def update(self, dt, game):
        """轮询网络并推进场景；返回 ``"back"`` 表示要回主菜单。

        这一帧函数在**所有**场景下都会被调用（房主打对局时也要收发决策），
        没有联机会话时它什么都不做。
        """

        session = self.session
        if not session.active:
            return None
        session.poll()

        if session.error:
            return self._on_failure(session.error, game)

        match = session.match
        if match is not None and getattr(match, "aborted", ""):
            return self._on_match_aborted(match, game)

        if self._sync_host_setup(game, match):
            return None

        if game.scene == SCENE_MENU and session.connected:
            # 客户端握手完成（WELCOME 里带着权威大厅）→ 进大厅。
            self.menu.connecting = False
            self.menu.set_status("")
            game.scene = SCENE_LOBBY
        elif game.scene == SCENE_LOBBY and match is not None:
            if not session.is_host and (getattr(match, "setup_started", False)
                                        or getattr(match, "ready", False)):
                # 客户端：先看身份、再选武将（与单机同一套屏），最后进只读牌桌。
                game.scene = SCENE_SETUP
        elif game.scene == SCENE_SETUP:
            self.setup.sync_from_match(match)
            if match is not None and getattr(match, "ready", False):
                self.remote.reset()
                game.scene = SCENE_REMOTE
        elif game.scene == SCENE_REMOTE:
            # 客户端牌桌每帧消费房主发来的视图与表现事件。
            self.remote.update(dt, match)
            if match is not None and getattr(match, "restarted", False):
                self._back_to_lobby_after_restart(game)
        return None

    def _sync_host_setup(self, game, match):
        """房主的开局流程推进（看身份 → 选将 → 牌桌）；返回 True 表示已接管。

        房主用的是**单机的两个屏**（``identity_reveal`` / ``general_select``），
        由 main.py 的常规路由绘制与响应点击；这里只负责在正确的时刻把它们
        推上台、并在所有人选完之后切进权威牌桌。
        """

        if not self.session.is_host or match is None:
            return False
        if not getattr(match, "started", False):
            return False
        stage = str(getattr(match, "setup_stage", "") or "")
        if stage == "battle":
            if game.scene in (SCENE_LOBBY, "identity_reveal", "general_select"):
                # 所有真人都选完了：房主进权威牌桌（远程玩家由 GAME_SETUP 触发）。
                game.scene = "game"
            return False
        if game.scene == SCENE_LOBBY:
            # 开局流程第一步：先看自己的身份（与单机同一个屏）。
            game.scene = "identity_reveal"
        return False

    def _on_match_aborted(self, match, game):
        """联网对局被终止（掉线 / 出错）：回大厅并说明原因。"""

        reason = getattr(match, "aborted", "") or "联网对局已终止"
        self.session.match = None
        self.menu.set_status(reason, STATUS_ERROR)
        self._exit_notice = reason
        game.scene = SCENE_LOBBY if self.session.connected else SCENE_MENU
        return None

    def _on_failure(self, error, game):
        """连接失败 / 掉线：一律退回多人菜单并显示中文原因，绝不卡死。"""

        self.close_session()
        self.menu.connecting = False
        self.menu.set_status(error, STATUS_ERROR)
        self._exit_notice = error
        game.scene = SCENE_MENU
        return None

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, game, metrics=None):
        metrics = metrics or self.metrics
        if game.scene == SCENE_SETUP:
            self.setup.draw(self.session.match, metrics)
        elif game.scene == SCENE_REMOTE:
            self.remote.draw(self.session.match, metrics)
        elif game.scene == SCENE_LOBBY:
            self.lobby.draw(self.session, metrics, game)
        else:
            self.menu.draw(self.session, metrics, game)

    def update_client_view(self, dt=None, game=None):
        """兼容旧调用点：客户端牌桌的每帧推进（``update`` 里已包含）。"""

        self.remote.update(dt or 0.0, self.session.match)
