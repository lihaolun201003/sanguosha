"""1v1 测试的**对局内控制条**：当前操作提示 + 重开 / 换将 / 回主菜单。

它只在对局场景里、且当前模式是 1v1 测试时显示（``main.py`` 按模式挂载），
画在牌桌之上、常规按钮之外的空位（左上角），因此：

* 不碰 ``Renderer`` 的既有按钮与点击优先级（原有模式一行都不受影响）；
* 结算浮层出现时它仍然画在最上层，所以"原配置重开 / 换将 / 主菜单"在结算后
  照样点得到（浮层的"重新开始"也会走模式的"原配置重开"语义）。
"""

import pygame

from src.game.modes.duel import config_of

from . import layout, theme
from .widgets import Button, draw_panel, ellipsize_text

PANEL = (16, 16, 600, 110)

PHASE_LABELS = {
    "prepare": "准备阶段",
    "judge": "判定阶段",
    "draw": "摸牌阶段",
    "play": "出牌阶段",
    "discard": "弃牌阶段",
    "finish": "结束阶段",
    "over": "已结束",
}


class DuelHud:
    """左上角那一条：告诉玩家"现在谁在操作"，并给出三条退路。"""

    def __init__(self, screen):
        self.screen = screen
        self.metrics = None
        self.panel_rect = pygame.Rect(0, 0, 10, 10)
        self.restart_button = Button(pygame.Rect(0, 0, 10, 10), "原配置重开", kind="secondary", font="small")
        self.setup_button = Button(pygame.Rect(0, 0, 10, 10), "换将", kind="secondary", font="small")
        self.menu_button = Button(pygame.Rect(0, 0, 10, 10), "主菜单", kind="ghost", font="small")
        self.sync_layout(None)

    def sync_layout(self, metrics=None):
        metrics = metrics or layout.LayoutMetrics(layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.metrics = metrics
        self.panel_rect = pygame.Rect(
            metrics.px(PANEL[0]), metrics.px(PANEL[1]),
            metrics.px(PANEL[2]), metrics.px(PANEL[3]))
        gap = metrics.px(8)
        button_w = (self.panel_rect.width - metrics.px(20) - gap * 2) // 3
        button_h = metrics.px(36)
        y = self.panel_rect.bottom - button_h - metrics.px(10)
        for index, button in enumerate(
                (self.restart_button, self.setup_button, self.menu_button)):
            button.rect = pygame.Rect(
                self.panel_rect.x + metrics.px(10) + index * (button_w + gap),
                y, button_w, button_h)
        return self

    # ==================================================
    # 交互
    # ==================================================

    def active(self, game):
        """只在 1v1 测试的牌桌上接管这三个按钮（设置页有自己的按钮）。"""

        return (getattr(game, "mode_id", "") == "duel_test"
                and getattr(game, "scene", "") == "game")

    def handle_click(self, position, game):
        if not self.active(game):
            return None
        if self.restart_button.contains(position):
            return "restart"
        if self.setup_button.contains(position):
            return "setup"
        if self.menu_button.contains(position):
            return "menu"
        return None

    def run_action(self, action, game):
        """执行一个控制条动作（真正的规则调用都在这里，界面只管画）。"""

        if action == "restart":
            # 原配置重开：双方武将 / 先手 / 操作方式 / 种子全部保持不变。
            game.restart_battle()
            return True
        if action == "setup":
            # 回设置页换将：走通用开局流程（reset + 模式给的场景）。
            game.begin_general_select()
            return True
        if action == "menu":
            game.return_to_menu()
            return True
        return False

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, game, metrics=None):
        if not self.active(game):
            return None
        metrics = metrics or self.metrics
        if metrics is None or self.metrics is not metrics:
            self.sync_layout(metrics)
            metrics = self.metrics

        fonts = metrics.fonts
        mouse = pygame.mouse.get_pos()
        draw_panel(self.screen, self.panel_rect, fill=theme.PANEL_DEEP,
                   border=theme.GOLD_DIM, border_width=theme.BORDER,
                   radius=metrics.px(10), shadow=False)

        config = config_of(game)
        control = "双边手动测试" if config.manual else "玩家对AI"
        title = "1v1 测试 · %s · 种子 %s" % (control, config.seed_text or "随机")
        self._blit(fonts.get("micro"), title, theme.TEXT_DIM,
                   self.panel_rect.x + metrics.px(10), self.panel_rect.y + metrics.px(6))

        operator = game.local_operator_label or game.player.name
        current = game.current_turn_player
        suffix = ""
        if game.game_over:
            suffix = "　（对局结束）"
        elif current is not None:
            turn = "本方回合" if current is game.player else "对方回合"
            phase = PHASE_LABELS.get(str(game.phase), str(game.phase))
            suffix = "　（" + turn + " · " + phase + "）"
        headline = "当前操作：" + operator + suffix
        self._blit(fonts.get("normal"), ellipsize_text(
            headline, fonts.get("normal"), self.panel_rect.width - metrics.px(20)),
            theme.GOLD_BRIGHT, self.panel_rect.x + metrics.px(10),
            self.panel_rect.y + metrics.px(26))

        for button in (self.restart_button, self.setup_button, self.menu_button):
            button.draw(self.screen, fonts, mouse)
        return self.panel_rect

    def _blit(self, font, text, color, x, y):
        self.screen.blit(font.render(text, True, color), (x, y))
