"""打包版自检：``Sanguosha.exe --selftest`` 用的内置运行期脚本。

# 为什么需要它

发布的产物是**一个 EXE**，舍友的机器上没有 ``tools/``、没有源码目录。而
``tools/`` 下那些验收脚本（``present_acceptance`` / ``handplay_scene``）恰恰
是"真实主循环 + 事件注入"的验证手段——打包之后它们不可用，于是"exe 能不能
真的玩"就只能靠肉眼。

这个模块把最小的一套自检**放进包里**：主菜单 → 本地身份局 → 选将 → 进牌桌，
一路上断言真正会出问题的几件事，并按需截图：

* **素材读得到**（卡面 / 武将图 / 卡背都能从 bundle 里 load 出非空 surface）
  ——打包漏收 ``assets/`` 的典型表现就是这里先红；
* **字体解析得住**（每个档位都能 render 出字，不抛异常、不是空图）；
* **路径分类正确**（只读资源在 bundle 内、可写数据在用户目录，不在 _MEIPASS）；
* **能真的进对局**（菜单 → 选将 → 牌桌，且手牌/座位都画出来了）。

跑法（release 版没有控制台，所以结果同时写进 ``SGS_SELFTEST_REPORT``）::

    Sanguosha.exe --selftest                     # 默认 .\\selftest\\ 下输出
    Sanguosha.exe --selftest --shots D:\\out       # 指定截图目录

退出码：0 = 全部通过；3 = 有失败项。
"""

import os
import sys

import pygame


class Hook:
    """走真实主循环的打包自检（与 tools/ 下的验收脚本同一套挂钩协议）。"""

    SETTLE = 12

    def __init__(self):
        self.ctx = None
        self.frames = 0
        self.stage = 0
        self.timer = 0
        self.pending = None
        self.done_menu = False
        self.entries = []
        self.failures = []
        self.shots = 0
        self.out = os.environ.get("SGS_SELFTEST_OUT") or os.path.join(
            os.getcwd(), "selftest")
        self.shot_dir = os.environ.get("SGS_SELFTEST_SHOTS") or self.out
        self.reported = False

    # ---- 挂钩协议 ----

    def bind_environment(self, env):
        return self

    def bind(self, context):
        self.ctx = context
        return self

    def finish(self):
        self._report()

    def step(self, dt):
        if self.ctx is None:
            return False
        self.frames += 1
        if self.frames == 1:
            try:
                os.makedirs(self.shot_dir, exist_ok=True)
            except OSError:
                pass
            self._check_environment()
        if self.pending is not None:
            self.timer -= 1
            if self.timer <= 0:
                action, self.pending = self.pending, None
                try:
                    action()
                except Exception as error:             # noqa: BLE001
                    self._fail("动作异常", "%s: %s" % (type(error).__name__, error))
            return False
        if not self.done_menu:
            self._drive_menu()
            return False
        if self.stage < 3:
            self._run()
            return False
        self._report()
        return "quit"

    # ---- 断言 ----

    def check(self, name, ok, detail=""):
        line = "[%s] %s%s" % ("通过" if ok else "失败", name,
                              ("  —— " + str(detail)) if detail else "")
        print("selftest:", line, flush=True)
        self.entries.append(line)
        if not ok:
            self.failures.append(name)

    def _fail(self, name, detail):
        self.check(name, False, detail)

    def _check_environment(self):
        """资源 / 字体 / 路径：打包最常见出问题的三件事。"""

        from src import paths

        print("selftest: 运行环境 %s" % paths.describe(), flush=True)
        self.check("识别为打包运行（sys.frozen）", paths.is_frozen()
                   or os.environ.get("SGS_SELFTEST_ALLOW_SOURCE") == "1",
                   "frozen=%s" % paths.is_frozen())

        # 只读资源必须在 bundle 里
        assets = paths.assets_root()
        self.check("assets 目录存在", os.path.isdir(assets), assets)
        for name in ("cards", "generals", "identities"):
            self.check("assets/%s 存在" % name,
                       os.path.isdir(os.path.join(assets, name)))

        # 可写数据不能落在 _MEIPASS（否则关掉游戏就丢）
        user = paths.user_data_dir()
        meipass = getattr(sys, "_MEIPASS", "")
        inside_bundle = bool(meipass) and os.path.abspath(user).startswith(
            os.path.abspath(meipass))
        self.check("用户数据目录不在临时解压目录里", not inside_bundle, user)

        # 素材真能读出来（不是"目录在、文件缺"）
        from src import card_catalog
        from src.ui import assets as asset_module
        registry = asset_module.get_registry()
        card = card_catalog.create_normal_sha("spade", "7")
        surface = registry.surface(asset_module.card_asset_id(card))
        self.check("卡面素材能加载出非空图像",
                   surface is not None and surface.get_width() > 8,
                   None if surface is None else surface.get_size())
        back = asset_module.card_back_asset_id()
        back_surface = registry.surface(back) if back else None
        self.check("卡背素材能加载出非空图像",
                   back_surface is not None and back_surface.get_width() > 8,
                   None if back_surface is None else back_surface.get_size())

        # 字体：每个档位都能真的渲染出字
        from src.ui import theme
        fonts = theme.fonts()
        broken = []
        for name in ("title", "large", "normal", "small", "tiny", "micro"):
            try:
                rendered = fonts.get(name, 1.0).render("三国杀 8", True, (255, 255, 255))
                if rendered.get_width() <= 0 or rendered.get_height() <= 0:
                    broken.append(name)
            except Exception as error:                 # noqa: BLE001
                broken.append("%s(%s)" % (name, type(error).__name__))
        self.check("全部字号档位都能渲染中文", not broken, broken)
        self.check("字体路径解析成功（非 pygame 默认兜底）",
                   bool(theme._body_font_path() or theme._display_font_path()),
                   theme._body_font_path())

        # 版本标记：崩了之后拿到的 crash.log 要能对上构建
        from src import crash
        self.check("版本标记可读（crash.log 能对上构建）",
                   crash._version() not in ("", "dev"), crash._version())

        # 可写数据真的能持久化（"关掉再开设置还在"）。
        # **只在临时目录里试写**：默认路径指向的是玩家真实配置，自检不能动它
        # （SGS_PREFERENCES 就是给这种场合准备的覆盖开关）。
        from src.settings import preferences as pref_module

        default_path = pref_module.default_path()
        self.check("偏好文件的默认位置在可写用户目录（不在临时解压目录）",
                   not (bool(meipass) and os.path.abspath(default_path).startswith(
                       os.path.abspath(meipass))), default_path)

        import tempfile

        probe_dir = tempfile.mkdtemp(prefix="sgs-prefs-")
        probe_path = os.path.join(probe_dir, "user_preferences.json")
        prefs = pref_module.Preferences(path=probe_path)
        prefs.data["favorite_generals"] = ["zhaoyun", "guanyu"]
        saved = prefs.save()
        self.check("偏好能写入磁盘", bool(saved) and os.path.exists(probe_path),
                   prefs.save_error or probe_path)
        reread = pref_module.Preferences(path=probe_path)
        ids = list(reread.favorite_general_ids())
        self.check("重新打开游戏仍能读到刚保存的偏好（关掉不丢）",
                   ids[:2] == ["zhaoyun", "guanyu"], ids[:4])

        # 注册表规模：pack 里少收了某个武将 / 技能模块会在这里露出来
        # （每个武将包都是静态 import，漏收就是整包不见）。
        from src.game import Game
        game = Game()
        generals = len(game.generals.list_generals())
        skills = len(game.skill_registry.ids()) if hasattr(
            game.skill_registry, "ids") else len(game.skill_registry._definitions
                                                 if hasattr(game.skill_registry, "_definitions")
                                                 else ())
        self.check("武将注册表已加载（>=50）", generals >= 50, generals)
        self.check("技能注册表已加载（>=50）", skills >= 50, skills)

    # ---- 走进对局（事件注入，与真人点击同一条路由）----

    def _later(self, delay, action):
        self.pending = action
        self.timer = max(1, delay)

    def _click(self, position):
        rect = self.ctx.screen.get_rect()
        x = max(rect.left, min(int(position[0]), rect.right - 1))
        y = max(rect.top, min(int(position[1]), rect.bottom - 1))
        for event in (
            pygame.event.Event(pygame.MOUSEMOTION,
                               {"pos": (x, y), "rel": (0, 0), "buttons": (0, 0, 0)}),
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (x, y), "button": 1}),
            pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": (x, y), "button": 1}),
        ):
            pygame.event.post(event)

    def _drive_menu(self):
        menu = self.ctx.start_menu
        game = self.ctx.game
        renderer = self.ctx.renderer
        if self.stage == 0:
            if game.scene != "menu":
                return
            menu.sync_layout(renderer.metrics)
            menu.sync_modes(game.modes.list_modes())
            self._shot("01_menu.png")
            self.check("主菜单列出了游戏模式", bool(menu.mode_buttons),
                       sorted(menu.mode_buttons))
            button = menu.mode_buttons.get("identity") or next(
                iter(menu.mode_buttons.values()), None)
            if button is None:
                self._fail("菜单", "没有任何模式按钮")
                self._write_report()
                self.done_menu = True
                return
            self.stage = 1
            self._click(button.rect.center)
            self._later(self.SETTLE, self._start)
            return
        if game.scene == "game":
            self.done_menu = True
            self._later(self.SETTLE * 3, lambda: None)

    def _start(self):
        self._click(self.ctx.start_menu.start_button.rect.center)
        self._later(self.SETTLE * 2, self._after_start)

    def _after_start(self):
        game = self.ctx.game
        renderer = self.ctx.renderer
        if game.scene == "identity_reveal":
            screen = self.ctx.identity_reveal
            try:
                screen.sync_layout(renderer.metrics)
            except Exception:                          # noqa: BLE001
                pass
            button = getattr(screen, "continue_button", None)
            if button is not None:
                self._click(button.rect.center)
            self._later(self.SETTLE, self._after_start)
            return
        if game.scene == "general_select":
            screen = self.ctx.general_select
            try:
                screen.sync_layout(renderer.metrics, list(game.selectable_generals()))
            except Exception:                          # noqa: BLE001
                pass
            rects = getattr(screen, "card_rects", None) or ()
            self.check("选将页给出了候选", bool(rects), len(rects))
            self._shot("02_general_select.png")
            if rects:
                self._click(pygame.Rect(rects[0]).center)
            self._later(self.SETTLE, self._confirm)
            return
        if game.scene == "game":
            self.done_menu = True
            self._later(self.SETTLE * 3, lambda: None)
            return
        self._later(self.SETTLE, self._after_start)

    def _confirm(self):
        self._click(self.ctx.general_select.confirm_button.rect.center)
        self._later(self.SETTLE * 2, self._after_start)

    # ---- 牌桌断言 ----

    def _run(self):
        game = self.ctx.game
        renderer = self.ctx.renderer
        if game.scene != "game":
            return
        if self.stage == 0:
            self.stage = 1
            self.timer = 40
            return
        if self.stage == 1:
            self.check("进入牌桌（scene == game）", True,
                       "phase=%s" % game.phase)
            layout = renderer.table_layout
            self.check("牌桌布局已建立", layout is not None)
            if layout is not None:
                self.check("手牌区有可画的牌",
                           len(layout.hand_rects) == len(game.player.hand),
                           "%d 张" % len(layout.hand_rects))
                self.check("座位面板已布局", bool(layout.seat_rects),
                           len(layout.seat_rects))
            self._shot("03_table.png")
            self.stage = 2
            self.timer = 40
            return
        if self.stage == 2:
            # 真的用一张手牌走一遍点击路由（出牌 / 选目标都能被引擎接住）
            hand = list(game.player.hand)
            if hand:
                rects = renderer.get_card_rects(list(hand))
                if rects:
                    self._click(pygame.Rect(rects[0]).center)
            self.stage = 3
            self.timer = 40
            return
        if self.stage == 3:
            self.check("点击手牌后界面仍正常（无异常）", True,
                       "提示=%s" % str(getattr(game, "message", ""))[:40])
            self._shot("04_after_click.png")
            self.stage = 4
            return

    def _shot(self, name):
        try:
            path = os.path.join(self.shot_dir, name)
            pygame.image.save(self.ctx.screen, path)
            self.shots += 1
            print("selftest: 截图 %s" % path, flush=True)
        except Exception as error:                     # noqa: BLE001
            print("selftest: 截图失败 %s: %s" % (name, error), flush=True)

    # ---- 结果 ----

    def _report(self):
        if self.reported:
            return
        self.reported = True
        self._write_report()
        print("selftest: 检查 %d 项，失败 %d，截图 %d" % (
            len(self.entries), len(self.failures), self.shots), flush=True)

    def _write_report(self):
        lines = list(self.entries)
        lines.append("")
        lines.append("检查 %d 项，失败 %d，截图 %d" % (
            len(self.entries), len(self.failures), self.shots))
        text = "\n".join(lines)
        for path in (os.path.join(self.out, "selftest_report.txt"),
                     os.path.join(os.getcwd(), "selftest_report.txt")):
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
                print("selftest: 报告 %s" % path, flush=True)
                break
            except OSError:
                continue
        if self.failures:
            # 让外层（批处理 / 验证脚本）能凭退出码判断
            os.environ["SGS_SELFTEST_FAILED"] = "1"
