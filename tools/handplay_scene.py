"""把真实窗口推进到"轮到本机真人出牌"，然后停住等真人鼠标操作。

用途：**真实手玩验收**的脚手架。菜单 → 选将 → 开局发牌这几步是重复劳动，
脚本点掉；从牌桌开始的一切交互（出牌 / 选目标 / 技能 / 结束回合）留给真人。

用法（在 tools/ 下，所以 ``SGS_RUNTIME_SCRIPT`` 直接写模块名）::

    set SGS_RUNTIME_SCRIPT=handplay_scene
    .venv\Scripts\python.exe main.py

就绪后会在 stdout 打印一行::

    handplay: 就绪 scene=game phase=play turn=玩家 hp=4/4

窗口一直开着（不返回 "quit"），由外部停止进程。配合 Computer Use 或人手操作。

注意：脚本用的是**事件注入**（``pygame.event.post``），走的是真实主循环 →
``handle_game_click`` → 引擎提交，不是直接调引擎 API。它只负责"把人送到牌桌
前"，不代替人做任何游戏决策。
"""

import os

import pygame


class Hook:
    def __init__(self):
        self.ctx = None
        self.frames = 0
        self.stage = 0
        self.timer = 0
        self.pending = None
        self.done = False

    def bind_environment(self, env):
        return self

    def bind(self, context):
        self.ctx = context
        return self

    def finish(self):
        print("handplay: 主循环结束", flush=True)

    def step(self, dt):
        if self.ctx is None or self.done:
            return False
        self.frames += 1
        if self.pending is not None:
            self.timer -= 1
            if self.timer <= 0:
                action, self.pending = self.pending, None
                action()
            return False
        if self.stage == 0:
            self._drive_menu()
        return False

    # ---- 脚本部分（只做"进入对局"，之后交给真人）----

    def _later(self, delay, action):
        self.pending = action
        self.timer = max(1, delay)

    SETTLE = 14

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
        metrics = self.ctx.renderer.metrics
        menu.sync_layout(metrics)
        menu.sync_modes(game.modes.list_modes())
        ffa = menu.mode_buttons.get("ffa")
        if ffa is None:
            return
        self.stage = 1
        self._click(ffa.rect.center)
        self._later(self.SETTLE, self._step2)

    def _step2(self):
        menu = self.ctx.start_menu
        self._click(menu.start_button.rect.center)
        self._later(self.SETTLE * 2, self._step3)

    def _step3(self):
        screen = self.ctx.general_select
        if only := getattr(screen, "card_rects", None):
            self._click(pygame.Rect(only[0]).center)
        self._later(self.SETTLE, self._step4)

    def _step4(self):
        screen = self.ctx.general_select
        self._click(screen.confirm_button.rect.center)
        self._later(self.SETTLE * 2, self._step5)

    def _step5(self):
        # 开局发牌要飞一会儿，等它落地再交给真人。
        self._later(self.SETTLE * 4, self._step6)

    def _step6(self):
        self.done = True
        game = self.ctx.game
        print("handplay: 就绪 scene=%s phase=%s turn=%s hp=%s/%s" % (
            game.scene, game.phase,
            getattr(game.current_turn_player, "name", "-"),
            getattr(game.player, "hp", "-"), getattr(game.player, "max_hp", "-")),
            flush=True)
        rect = self.ctx.screen.get_rect()
        zoom = os.environ.get("SGS_HANDPLAY_ZOOM")
        if zoom:
            # 让窗口只占屏幕一角，方便 Computer Use 在一个光栅里看全。
            pygame.display.set_mode((int(zoom.split("x")[0]),
                                     int(zoom.split("x")[1])))
        print("handplay: 窗口 %s" % (rect.size,), flush=True)
