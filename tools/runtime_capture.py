"""Phase 11.4.2：真实运行路径上的 UI 截图 + 几何导出。

这个模块**不是**一个独立的渲染工具。它由 ``main.py`` 在主循环里加载
（见 ``src/ui/runtime_hook.py``）：屏幕、Game、Renderer、各场景全部是玩家
正常启动时正在用的那一个对象，脚本只负责"替玩家点几下鼠标"和"在合适的
时刻按快门"。

用法（三个角色分别在不同进程里启动）：

    SGS_RUNTIME_SCRIPT=runtime_capture SGS_CAPTURE_ROLE=local \
        python main.py

    SGS_RUNTIME_SCRIPT=runtime_capture SGS_CAPTURE_ROLE=host \
        SGS_CAPTURE_PORT=19627 SGS_CAPTURE_OUT=tools/ui_snapshots/x_host.png \
        python main.py

环境变量：

* ``SGS_CAPTURE_ROLE``     local | host | client
* ``SGS_CAPTURE_OUT``      截图输出路径（PNG）
* ``SGS_CAPTURE_JSON``     几何导出路径（JSON）
* ``SGS_CAPTURE_PORT``     host / client 使用的端口
* ``SGS_CAPTURE_PLAYERS``  身份局人数（local 用它决定 AI 数，host 用它作为房间上限）
* ``SGS_CAPTURE_NAME``     client 的昵称
* ``SGS_CAPTURE_HANDOVER`` host：截完自己的图之后把回合交给哪个 player_id
* ``SGS_CAPTURE_LINGER``   截图之后继续运行多少秒再退出（等别的进程也截完）
* ``SGS_CAPTURE_TIMEOUT``  整体超时（到点即使没达成条件也截图并退出）
* ``SGS_CAPTURE_TABLE_WAIT`` client：进入牌桌后最多等多少秒自己的回合（超时就
  截当前牌桌——对局可能已经结束，等不到就永远等不到）
"""

import json
import os
import time

import pygame

from src.ui import layout as layout_module
from src.ui import prompt as prompt_module
from src.ui.lan_scene import SCENE_LOBBY, SCENE_REMOTE, SCENE_SETUP

ROLE_LOCAL = "local"
ROLE_HOST = "host"
ROLE_CLIENT = "client"

#: 条件满足之后还要连续稳定多少帧才截图（等布局与动画落定）。
STABLE_FRAMES = 36


def _flag(name, default=""):
    return os.environ.get(name, default)


def _number(name, default, cast=int):
    raw = _flag(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        return default


def _normalize(path):
    """把相对路径固定到仓库根：截图进程的 cwd 可能不是仓库根。"""

    if not path:
        return ""
    if os.path.isabs(path):
        return path
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, path)


class Hook:
    """主循环每帧调一次：驱动操作 → 判定条件 → 截图 → 收工。"""

    def __init__(self):
        self.role = ROLE_LOCAL
        self.out = ""
        self.json_path = ""
        self.port = 19627
        self.players = 5
        self.nickname = "远程玩家"
        self.handover = ""
        self.linger = 8.0
        self.timeout = 150.0
        self.ctx = None
        self.notes = []
        self._started_at = time.monotonic()
        self._stable = 0
        self._shot = False
        self._shot_at = 0.0
        self._finished = False
        self._boot = False
        self._handover_done = False
        self._handover_at = 0.0
        self._handover_turn = 0
        self._handover_rounds = 1
        self.handover_step = 6.0
        self._ready_sent = 0.0
        self._table_seen = 0.0
        self._setup_shots_done = set()
        self.join_wait = 60.0
        self.mode_id = "identity"
        self.size = None
        self.trace = []
        self._last_trace = None
        self._size_done = False
        self._forced = False
        self.force_after = 25.0
        self._last_error = ""

    # ==================================================
    # 绑定
    # ==================================================

    def bind_environment(self, env):
        self.role = (env.get("SGS_CAPTURE_ROLE") or ROLE_LOCAL).strip() or ROLE_LOCAL
        self.out = _normalize(env.get("SGS_CAPTURE_OUT", ""))
        self.json_path = _normalize(env.get("SGS_CAPTURE_JSON", ""))
        self.port = _number("SGS_CAPTURE_PORT", 19627)
        self.players = max(2, min(8, _number("SGS_CAPTURE_PLAYERS", 5)))
        self.nickname = env.get("SGS_CAPTURE_NAME", "远程玩家")
        self.handover = (env.get("SGS_CAPTURE_HANDOVER") or "").strip()
        self.handover_step = max(2.0, _number("SGS_CAPTURE_HANDOVER_STEP", 6, float))
        self._handover_rounds = (9 if self.handover == "all" else 1)
        self.linger = max(0.0, _number("SGS_CAPTURE_LINGER", 8, float))
        self.timeout = max(10.0, _number("SGS_CAPTURE_TIMEOUT", 150, float))
        self.join_wait = max(5.0, _number("SGS_CAPTURE_JOIN_WAIT", 60, float))
        self.force_after = max(5.0, _number("SGS_CAPTURE_FORCE_AFTER", 25, float))
        self.table_wait = max(5.0, _number("SGS_CAPTURE_TABLE_WAIT", 25, float))
        self.setup_shots = str(env.get("SGS_CAPTURE_SETUP_SHOTS", "1")).strip() not in (
            "0", "off", "false", "no")
        self.mode_id = (env.get("SGS_CAPTURE_MODE") or "identity").strip() or "identity"
        self.size = self._parse_size(env.get("SGS_CAPTURE_SIZE", ""))
        return self

    @staticmethod
    def _parse_size(text):
        """``"1920x1080"`` → ``(1920, 1080)``；没写就沿用启动分辨率。"""

        raw = (text or "").strip().lower().replace("*", "x")
        if "x" not in raw:
            return None
        head, _, tail = raw.partition("x")
        try:
            return (max(640, int(head)), max(480, int(tail)))
        except ValueError:
            return None

    def _apply_size(self):
        """按验收分辨率重设显示模式（与 F11 全屏切换走同一条 resync 路径）。"""

        if self._size_done:
            return
        self._size_done = True
        if self.size is None:
            return
        try:
            # 两步切换：从 FULLSCREEN 出来时第一次 set_mode 只负责"退出全屏"，
            # 尺寸要到下一次调用才生效（SDL 在 dummy 驱动下的行为）。
            pygame.display.set_mode((4, 4))
            screen = pygame.display.set_mode(self.size)
        except pygame.error as error:                     # pragma: no cover
            self.note("切换分辨率失败：" + repr(error))
            return
        self.ctx.resync(screen)
        self.ctx.screen = screen
        self.note("分辨率=%s（实际 %s）" % (self.size, screen.get_size()))

    def note(self, text, key=None):
        """记一条诊断：同一个状态指纹只记一次，避免把 notes 灌满。"""

        fingerprint = text if key is None else key
        if fingerprint == self._last_trace:
            return
        self._last_trace = fingerprint
        self.notes.append(text)
        del self.notes[:-80]

    def bind(self, context):
        self.ctx = context
        return self

    # ==================================================
    # 每帧
    # ==================================================

    def step(self, dt):
        if self.ctx is None:
            return True
        self._apply_size()
        try:
            if self.role == ROLE_HOST:
                done = self._step_host()
            elif self.role == ROLE_CLIENT:
                done = self._step_client()
            else:
                done = self._step_local()
        except Exception as error:                       # pragma: no cover
            self.notes.append("异常：" + repr(error))
            self._save("error")
            return True

        if done:
            return True
        if self._shot and time.monotonic() - self._shot_at >= self.linger:
            return True
        if time.monotonic() - self._started_at >= self.timeout:
            self.notes.append("超时：未达成截图条件")
            self._save("timeout")
            return True
        return False

    def finish(self):
        if self._finished:
            return
        self._finished = True
        if not self._shot:
            self.notes.append("主循环退出时仍未截图")
            self._save("unreached")
        else:
            self._flush_notes()

    def _flush_notes(self):
        """把最新诊断写回 JSON：截图那一刻的 notes 快照可能已经过时。"""

        if not self.json_path or not os.path.exists(self.json_path):
            return
        try:
            with open(self.json_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):                     # pragma: no cover
            return
        data["notes"] = list(self.notes)
        try:
            with open(self.json_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        except OSError:                                   # pragma: no cover
            pass

    # ==================================================
    # 三个角色的驱动
    # ==================================================

    def _step_local(self):
        game = self.ctx.game
        scene = game.scene
        if scene == "menu":
            self._boot_local_game(game)
            return False
        if scene == "identity_reveal":
            game.confirm_identity()
            return False
        if scene == "general_select":
            candidates = game.selectable_generals()
            if candidates:
                game.confirm_general(candidates[0].id)
            return False
        if scene == "game":
            self._auto_play_through(game)
            ready = self._capture_when(lambda: self._my_play_phase(game))
            # 兜底：AI 连续行动 / 一直被响应打断时，直接把回合交给真人，
            # 保证三边都在"我的出牌阶段"这一同一状态下截图。
            if not self._shot and time.monotonic() - self._started_at > self.force_after:
                self._force_my_turn(game)
            return ready
        return False

    def _force_my_turn(self, game):
        if self._forced or game.scene != "game" or game.game_over:
            return
        if game.current_turn_player is game.player and game.phase == "play":
            return
        self._forced = True
        game.actions.clear()
        try:
            game.start_turn(game.player)
        except Exception as error:                        # pragma: no cover
            self.notes.append("强制回合失败：" + repr(error))
            return
        self.note("t=%.1f 直接把回合交给真人（兜底）" % (
            time.monotonic() - self._started_at))

    def _auto_play_through(self, game):
        """等自己的出牌阶段期间，替玩家处理掉必须的响应（真人也会这么做）。

        这不是"跳过流程"：AI 打了你，你要么打出【闪】，要么点「不出」，两种
        都是玩家真实会做的事；不处理的话这一局就停在这里，永远截不到出牌阶段。
        """

        if self._shot or game.game_over or game.busy:
            return
        # 技能确认（【裸衣】一类"是否发动"）：真人在牌桌上会点「不发动」，
        # 验收里同样点掉，否则这一局永远停在那个弹窗上。
        choice = getattr(game, "choice", None)
        if getattr(choice, "active", False):
            choice.choose_no()
            return
        if not getattr(getattr(game, "response", None), "active", False):
            return
        hand = list(getattr(game.player, "hand", ()) or ())
        index = next((position for position, card in enumerate(hand)
                      if getattr(card, "name", "") == "SHAN"), None)
        if index is not None:
            game.respond_with_card(index, (0, 0, 10, 10))
        else:
            game.pass_response()

    def _boot_local_game(self, game):
        """单机身份局：与开始菜单点击等价的几步（模式 → 人数 → 开局流程）。"""

        if self._boot:
            return
        self._boot = True
        game.set_mode(self.mode_id)
        game.ai_count = max(1, min(7, self.players - 1))
        game.begin_general_select()

    def _step_host(self):
        lan = self.ctx.lan_scene
        game = self.ctx.game
        if not self._boot:
            self._boot = True
            game.set_mode(self.mode_id)
            lan.enter(game)
            session = lan.session
            session.set_max_players(self.players)
            ok, message = session.create_room(
                "房主", self.players, str(self.port), game_mode=self.mode_id)
            if not ok:
                self.notes.append("创建房间失败：" + message)
                return True
            return False

        session = lan.session
        if not session.active:
            self.notes.append("会话意外关闭")
            self._save("session_closed")
            return True

        if session.match is None:
            lobby = session.lobby
            waiting = time.monotonic() - self._started_at
            self.note("t=%.1f 大厅 %s/%d 人 can_start=%s" % (
                waiting, "?" if lobby is None else lobby.count, self.players,
                "?" if lobby is None else lobby.can_start()),
                key=(None if lobby is None else lobby.count,
                     None if lobby is None else lobby.can_start()))
            # 等"人满 + 全员已准备"再开。少等一步都不行：人没到齐就开会把
            # 还没启动完的客户端挡在门外；人到了但没准备会得到"等待 xxx 准备"
            # 的失败重试，而那会把刚进牌桌的客户端又掀掉一次。
            if lobby is not None and lobby.can_start() and (
                    lobby.count >= self.players or waiting > self.join_wait):
                ok, reason = session.start_match()
                if not ok:
                    self.notes.append("开始游戏失败：" + reason)
            return False

        if not self._shot:
            self._advance_lan_setup(lan, game, session.match)
            self._auto_play_through(game)
            if game.scene == "game" and not self._table_seen:
                self._table_seen = time.monotonic()
            ready = self._capture_when(lambda: self._my_play_phase(game))
            if not self._shot and time.monotonic() - self._started_at > self.force_after:
                self._force_my_turn(game)
            if (not self._shot and self._table_seen
                    and time.monotonic() - self._table_seen >= self.table_wait):
                # 兜底：牌桌早就开了却没轮到自己出牌（对面连打 / 自己一直被打断）。
                # 这时截当前牌桌——画面同样是真实的身份局牌桌，五个座位、武将、
                # 身份、手牌都在。
                self.note("t=%.1f 等待自己的出牌阶段超时，改截当前牌桌" % (
                    time.monotonic() - self._started_at))
                self._save("table")
                return True
            return ready
        return self._hand_over_turn(game)

    def _advance_lan_setup(self, lan, game, match):
        """替房主点完联机的开局流程（看身份 → 选将），等价于人点「继续 / 确认」。

        联机的开局与单机一致：先看自己的身份，再从候选里挑一个武将。验收脚本
        要走到牌桌，就得把这两步也点掉（候选都取第一个）。
        """

        if match is None or not getattr(match, "started", False):
            return False
        if str(getattr(match, "setup_stage", "")) == "battle":
            return False
        if game.scene == "identity_reveal":
            if "identity" not in self._setup_shots_done:
                self._setup_shots_done.add("identity")
                self._save_setup_shot("identity")
            # 走与真人点「继续」**同一个入口**（切屏 + 通知网络桥），这样脚本
            # 不会绕过 UI 才有的那几步。
            lan.host_confirm_identity(game)
            select = getattr(self.ctx, "general_select", None)
            if select is not None:
                metrics = getattr(self.ctx.renderer, "metrics", None)
                if metrics is not None:
                    select.sync_layout(metrics, game.selectable_generals())
            self.note("t=%.1f 房主确认身份 → 进入选将" % (
                time.monotonic() - self._started_at))
            return True
        if game.scene == "general_select":
            if not game.general_candidates:
                return False
            if "generals" not in self._setup_shots_done:
                self._setup_shots_done.add("generals")
                self._save_setup_shot("generals")
            game.selected_general = game.general_candidates[0]
            match.host_general_picked(game.selected_general)
            self.note("t=%.1f 房主选定武将 %s" % (
                time.monotonic() - self._started_at, game.selected_general))
            return True
        if str(getattr(match, "setup_stage", "")) in ("identity", "identity_done"):
            match.host_identity_confirmed()
            return True
        return False

    def _hand_over_turn(self, game):
        """房主截完自己的画面后，把回合交给一名远程玩家（让客户端也能截到自己的回合）。"""

        if not self.handover or self._handover_rounds <= self._handover_turn:
            return False
        now = time.monotonic()
        if self._handover_at == 0.0:
            self._handover_at = now + 2.0
            return False
        if now < self._handover_at:
            return False
        self._handover_at = now + self.handover_step
        # 只交给**远程真人**座位：身份局里还有 AI 补位，把回合交给 AI 座位
        # 对"让客户端截到自己的出牌阶段"毫无帮助。
        from src.player import ControllerType

        remote = [
            player for player in sorted(game.players, key=lambda item: item.seat)
            if getattr(player, "controller_type", None) is ControllerType.REMOTE_HUMAN
        ]
        if not remote:
            return False
        if self.handover == "all":
            # 座位是按"谁先连上"分配的，与进程启动顺序不一致：轮流交给每一
            # 个远程真人的座位，每个客户端才有机会截到"我自己的出牌阶段"。
            target = remote[self._handover_turn % len(remote)]
        elif self.handover == "auto":
            target = remote[0]
        else:
            target = game.get_player(self.handover)
        self._handover_turn += 1
        if target is None or not target.alive:
            self.notes.append("交接回合失败：找不到 " + self.handover)
            return False
        game.actions.clear()
        game.start_turn(target)
        self.note("t=%.1f 已交接回合给 %s" % (
            time.monotonic() - self._started_at, target.player_id))
        self._flush_notes()
        return False

    def _step_client(self):
        lan = self.ctx.lan_scene
        game = self.ctx.game
        if not self._boot:
            self._boot = True
            lan.enter(game)
            ok, message = lan.session.join_room(
                self.nickname, "127.0.0.1", str(self.port))
            if not ok:
                self.notes.append("加入房间失败：" + message)
                return True
            return False

        session = lan.session
        match = session.match
        error = str(session.error or "")
        if error and error != self._last_error:
            # 记录**第一次**报错：会话被关掉之后 error 会被清空，只记最后
            # 看到的状态就永远看不到"为什么掉线"。
            self._last_error = error
            self.note("t=%.1f 掉线原因=%s" % (
                time.monotonic() - self._started_at, error))
        self.note("t=%.1f scene=%s error=%r match=%s ready=%s decision=%s stable=%d shot=%s" % (
            time.monotonic() - self._started_at, game.scene, session.error,
            "无" if match is None else "有",
            getattr(match, "ready", "-") if match is not None else "-",
            "无" if match is None or not match.decision
            else str(match.decision.get("kind")), self._stable, self._shot),
            key=(game.scene, bool(getattr(match, "ready", False)),
                 str(match.decision.get("kind")) if match is not None and match.decision
                 else "", self._shot, self._stable >= STABLE_FRAMES,
                 session.active))
        if not session.active:
            self.note("会话已关闭")
            self._save("session_closed")
            return True

        if game.scene == SCENE_LOBBY:
            if time.monotonic() - self._ready_sent > 0.5:
                self._ready_sent = time.monotonic()
                session.set_ready(True)
            return False
        if game.scene == SCENE_SETUP:
            self._advance_client_setup(match)
            return False
        if game.scene != SCENE_REMOTE:
            return False
        if match is None:
            return False
        ready = self._capture_when(lambda: self._client_play_phase(match))
        if not self._shot and self._table_seen and \
                time.monotonic() - self._table_seen >= self.table_wait:
            # 兜底：牌桌早就就绪，却一直没轮到自己（对局可能已经结束，或者
            # 房主没来得及交棒）。这时截当前牌桌——它同样是真实的身份局画面：
            # 五个座位、武将、身份、手牌都在。
            #
            # 这里**直接截图**，不再走 ``_capture_when``：主判定每帧会把它的
            # 稳定计数清零，兜底判定永远攒不够连续帧。
            self.note("t=%.1f 等待自己的回合超时，改截当前牌桌" % (
                time.monotonic() - self._started_at))
            self._save("table")
            return True
        return ready

    def _save_setup_shot(self, tag):
        """开局流程的中间截图（身份屏 / 选将屏）。

        联机的"先看身份、再选将"是这一阶段的重点，光有牌桌截图证明不了它
        真的发生过——这里在点「继续 / 确认」之前各存一张。
        """

        if not self.out or not self.setup_shots:
            return ""
        path = os.path.splitext(self.out)[0] + "_" + tag + ".png"
        try:
            pygame.image.save(self.ctx.screen, path)
        except (pygame.error, OSError):                    # pragma: no cover - 兜底
            return ""
        self.note("已存开局截图：" + os.path.basename(path))
        return path

    def _advance_client_setup(self, match):
        """替客户端点完开局流程（看身份 → 选将），与真人点的完全一样。"""

        if match is None:
            return False
        # 先把房主发来的数据同步到屏上、并真的画一帧：main.py 这一帧的绘制
        # 发生在运行期脚本之前，刚切进开局屏时屏幕还是上一帧（大厅）的样子，
        # 那样截图会存下一张"没有身份的大厅"。
        setup = getattr(self.ctx.lan_scene, "setup", None)
        if setup is not None:
            setup.sync_from_match(match)
            try:
                setup.draw(match, getattr(self.ctx.renderer, "metrics", None))
            except Exception as error:                     # pragma: no cover - 兜底
                self.notes.append("开局屏绘制失败：" + repr(error))
        if match.my_identity and not match.identity_seen:
            if "identity" not in self._setup_shots_done:
                self._setup_shots_done.add("identity")
                self._save_setup_shot("identity")
            match.confirm_identity()
            self.note("t=%.1f 客户端确认身份 %s" % (
                time.monotonic() - self._started_at, match.my_identity_name))
            return True
        if match.candidates and not match.picked_general:
            if "generals" not in self._setup_shots_done:
                self._setup_shots_done.add("generals")
                self._save_setup_shot("generals")
            match.pick_general(match.candidates[0]["general_id"])
            self.note("t=%.1f 客户端选定武将 %s" % (
                time.monotonic() - self._started_at,
                match.candidates[0].get("name")))
            return True
        return False

    def _client_play_phase(self, match):
        """客户端：房主把这个座位派上了出牌阶段（我自己的回合）。"""

        if not getattr(match, "ready", False):
            return False
        # 牌桌视图第一次就绪的时刻：兜底截图从这一刻开始计时。
        if not self._table_seen:
            self._table_seen = time.monotonic()
        request = match.decision
        if not request:
            return False
        return str(request.get("kind") or "") == "play_phase"

    # ==================================================
    # 触发
    # ==================================================

    def _my_play_phase(self, game):
        """轮到我、出牌阶段、没有动画在放。

        不要求"没有任何待决请求"：身份局里摸牌阶段的技能确认（【裸衣】一类）
        也发生在出牌阶段之前，它同样属于"轮到我出牌"的真实画面。
        """

        return (game.scene == "game"
                and not game.game_over
                and game.current_turn_player is game.player
                and game.phase == "play"
                and not game.busy)

    def _capture_when(self, predicate):
        try:
            ready = bool(predicate())
        except Exception as error:                        # pragma: no cover
            self.notes.append("判定异常：" + repr(error))
            ready = False
        self._stable = self._stable + 1 if ready else 0
        if self._shot or self._stable < STABLE_FRAMES:
            return False
        self._save("scene")
        return False

    # ==================================================
    # 截图 + 几何
    # ==================================================

    def _view(self):
        """当前这一帧真正画的那份"牌局"（单机是 Game，客户端是只读视图）。"""

        lan = self.ctx.lan_scene
        if self.role == ROLE_CLIENT and lan.session.match is not None:
            return lan.remote.view
        return self.ctx.game

    def _save(self, tag):
        if self._shot:
            # 第一次按快门才是"玩家看到的那一帧"；之后的掉线 / 超时不许覆盖它。
            return
        self._shot = True
        self._shot_at = time.monotonic()
        try:
            data = self.geometry(tag)
        except Exception as error:                        # pragma: no cover
            self.notes.append("几何导出失败：" + repr(error))
            data = {"role": self.role, "tag": tag, "notes": list(self.notes)}
        if self.out:
            os.makedirs(os.path.dirname(self.out), exist_ok=True)
            pygame.image.save(self.ctx.screen, self.out)
        if self.json_path:
            os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
            with open(self.json_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        pygame.display.set_caption("captured " + self.role)

    def geometry(self, tag):
        """导出**这一帧真实绘制时用的**几何（与 Renderer 内部同一批对象）。"""

        renderer = self.ctx.renderer
        metrics = renderer.metrics
        table = renderer.table_layout
        view = self._view()

        def rect(value):
            return None if value is None else [int(value.x), int(value.y),
                                               int(value.width), int(value.height)]

        data = {
            "role": self.role,
            "tag": tag,
            "screen": [int(self.ctx.screen.get_width()), int(self.ctx.screen.get_height())],
            "scale": round(float(metrics.scale), 6),
            "scene": self.ctx.game.scene,
            "notes": list(self.notes),
            "viewer": str(getattr(view.player, "player_id", "")),
            "local_interaction": bool(renderer.local_interaction(view)),
            "interaction_layers": bool(renderer.interaction_layers(view)),
            "phase": str(getattr(view, "phase", "")),
            "regions": {
                "central": rect(metrics.central),
                "prompt": rect(metrics.prompt),
                "player_status": rect(metrics.player_status),
                "hand_area": rect(metrics.hand_area),
                "log": rect(metrics.log_rect),
                "speed_control": rect(metrics.speed_control),
                "action_card": rect(metrics.action_card_rect),
                "response_card": rect(metrics.response_card_rect),
                "draw_pile": rect(metrics.to_screen(layout_module.DRAW_PILE_RECT)),
                "discard_pile": rect(metrics.to_screen(layout_module.DISCARD_PILE_RECT)),
            },
            "buttons": {
                "primary": self._button(renderer, "primary"),
                "secondary": self._button(renderer, "secondary"),
                "surrender": rect(renderer.surrender_button.rect),
            },
            "prompt_text": self._prompt_text(view),
            "seats": self._seats(table, view),
            "hand": [rect(item) for item in (table.hand_rects if table else [])],
            "equipment": (
                {slot: rect(item) for slot, item in table.player_equipment_rects().items()}
                if table is not None else {}
            ),
            "skill_bar": self._skill_bar(renderer, view),
            "public_pool": [rect(item) for item in renderer.get_public_card_rects(
                [card for card, _key in renderer.get_pool_entries(view)
                 if card is not None])],
        }
        return data

    @staticmethod
    def _button(renderer, name):
        button = renderer.primary_button if name == "primary" else renderer.secondary_button
        rect = button.rect
        return {
            "rect": [int(rect.x), int(rect.y), int(rect.width), int(rect.height)],
            "label": str(getattr(button, "label", "")),
            "enabled": bool(getattr(button, "enabled", False)),
        }

    @staticmethod
    def _prompt_text(view):
        try:
            info = prompt_module.describe(view)
        except Exception as error:                        # pragma: no cover
            return {"title": "<异常>", "body": repr(error), "kind": ""}
        return {"title": str(info.title), "body": str(info.body),
                "progress": str(getattr(info, "progress", "")), "kind": str(info.kind)}

    def _seats(self, table, view):
        """按"离自己多远"导出座位几何：offset 就是 viewer-relative 座次环位移。"""

        if table is None:
            return []
        ordered = sorted(view.players, key=lambda player: player.seat)
        me = view.player
        try:
            my_index = ordered.index(me)
        except ValueError:
            my_index = 0
        total = len(ordered)
        rows = []
        for player in ordered:
            if player is me:
                continue
            rect = table.seat_rect(player)
            if rect is None:
                continue
            offset = (ordered.index(player) - my_index) % total
            rows.append({
                "offset": int(offset),
                "side": ("left" if rect.centerx < table.metrics.central.centerx
                         else "right" if rect.centerx > table.metrics.central.centerx
                         else "top"),
                "rect": [int(rect.x), int(rect.y), int(rect.width), int(rect.height)],
                "hand_count": int(getattr(player, "hand_count", 0) or 0),
            })
        rows.sort(key=lambda item: item["offset"])
        return rows

    @staticmethod
    def _skill_bar(renderer, view):
        bar = renderer.skill_bar
        rows = []
        try:
            for index, rect in enumerate(bar.rects):
                skill_id = ""
                if index < len(bar.skills):
                    skill_id = str(getattr(bar.skills[index], "id", ""))
                rows.append({
                    "skill_id": skill_id,
                    "rect": [int(rect.x), int(rect.y),
                             int(rect.width), int(rect.height)],
                })
        except Exception as error:                        # pragma: no cover
            return [{"error": repr(error)}]
        return rows
