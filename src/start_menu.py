"""Start menu: title, AI count stepper, start / exit.

Laid out in design space and centred on the real screen, so it stays a
comfortable古风 panel instead of stretching across a 4K display.
"""

import pygame

from src.ui import layout, theme
from src.ui.speed import SpeedControl
from src.ui.widgets import Button, draw_panel

# 设计坐标（相对菜单面板）
PANEL_DESIGN = (620, 800)
BUTTON_WIDTH = 380
BUTTON_HEIGHT = 74
# 多人对战（局域网）入口：与「开始游戏」「退出游戏」同级，夹在两者之间。
MULTIPLAYER_HEIGHT = 62
EXIT_HEIGHT = 62


def centered_text_origin(rendered, rect):
    """按**实际墨迹**居中，返回渲染结果的左上角坐标。

    数字没有下伸部，直接用 ``get_rect(center=...)``（按字号行高居中）会让
    数字视觉上整体偏上，并且 5/6/7/8 各有各的偏移量。按墨迹矩形居中后，
    不同数字的视觉位置完全一致。
    """

    ink = rendered.get_bounding_rect()
    if ink.width and ink.height:
        return (rect.centerx - ink.centerx, rect.centery - ink.centery)
    return rendered.get_rect(center=rect.center).topleft


class StartMenu:

    def __init__(self, screen, big_font=None, small_font=None, tiny_font=None):
        self.screen = screen
        self.start_button = Button(pygame.Rect(0, 0, 10, 10), "开始游戏", kind="primary", font="large")
        self.minus_button = Button(pygame.Rect(0, 0, 10, 10), "−", kind="secondary", font="large")
        self.plus_button = Button(pygame.Rect(0, 0, 10, 10), "＋", kind="secondary", font="large")
        self.multiplayer_button = Button(
            pygame.Rect(0, 0, 10, 10), "多人对战（局域网）",
            kind="secondary", font="normal")
        self.exit_button = Button(pygame.Rect(0, 0, 10, 10), "退出游戏", kind="ghost", font="normal")
        # 模式按钮：id → Button（文案由模式自己提供，菜单不认识模式业务）。
        self.mode_buttons = {}
        self.mode_order = ()
        self.mode_panel_rect = pygame.Rect(0, 0, 10, 10)
        self.panel_rect = pygame.Rect(0, 0, 10, 10)
        self.value_rect = pygame.Rect(0, 0, 10, 10)
        self.metrics = None
        # 节奏（对局推进速度）在主界面调好再开局，对局中不再占按钮区。
        self.speed_control = SpeedControl()
        self.sync_layout(None)

    def sync_modes(self, modes):
        """按模式注册表重建按钮；返回 [(mode_cls, Button)]。"""

        modes = tuple(modes)
        ids = tuple(mode.id for mode in modes)
        if ids != self.mode_order:
            self.mode_order = ids
            self.mode_buttons = {
                mode.id: Button(pygame.Rect(0, 0, 10, 10), mode.name, kind="secondary", font="normal")
                for mode in modes
            }
            self.sync_layout(self.metrics)
        return [(mode, self.mode_buttons[mode.id]) for mode in modes]

    def mode_button(self, mode_id):
        return self.mode_buttons.get(mode_id)

    def sync_layout(self, metrics=None):
        """Recompute every rect for the current screen."""

        metrics = metrics or layout.LayoutMetrics(layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.metrics = metrics

        panel_w = min(metrics.px(PANEL_DESIGN[0]), int(metrics.screen_w * 0.72))
        panel_h = min(metrics.px(PANEL_DESIGN[1]), int(metrics.screen_h * 0.92))
        self.panel_rect = pygame.Rect(0, 0, panel_w, panel_h)
        self.panel_rect.center = (metrics.screen_w // 2, metrics.screen_h // 2)

        center_x = self.panel_rect.centerx
        button_w = min(metrics.px(BUTTON_WIDTH), panel_w - metrics.px(120))
        button_h = metrics.px(BUTTON_HEIGHT)

        stepper_h = metrics.px(50)
        stepper_w = metrics.px(64)
        stepper_gap = metrics.px(180)
        stepper_y = self.panel_rect.y + metrics.px(426)

        self.minus_button.rect = pygame.Rect(
            center_x - stepper_gap // 2 - stepper_w, stepper_y, stepper_w, stepper_h
        )
        self.plus_button.rect = pygame.Rect(
            center_x + stepper_gap // 2, stepper_y, stepper_w, stepper_h
        )
        # 数值框位于两个步进按钮之间：只依赖按钮位置，与模式无关。
        self.value_rect = pygame.Rect(
            self.minus_button.rect.right + metrics.px(8),
            stepper_y,
            self.plus_button.rect.x - self.minus_button.rect.right - metrics.px(16),
            stepper_h,
        )

        # 模式按钮：在面板中部横排（数量随注册表变化）。
        mode_count = max(1, len(self.mode_order))
        mode_gap = metrics.px(16)
        mode_area = min(panel_w - metrics.px(120), metrics.px(520))
        mode_w = max(metrics.px(120), (mode_area - mode_gap * (mode_count - 1)) // mode_count)
        mode_h = metrics.px(54)
        mode_y = self.panel_rect.y + metrics.px(330)
        self.mode_panel_rect = pygame.Rect(
            center_x - (mode_w * mode_count + mode_gap * (mode_count - 1)) // 2,
            mode_y,
            mode_w * mode_count + mode_gap * (mode_count - 1),
            mode_h,
        )
        for index, mode_id in enumerate(self.mode_order):
            button = self.mode_buttons.get(mode_id)
            if button is None:
                continue
            button.rect = pygame.Rect(
                self.mode_panel_rect.x + index * (mode_w + mode_gap), mode_y, mode_w, mode_h
            )

        start_y = self.panel_rect.y + metrics.px(518)
        self.start_button.rect = pygame.Rect(
            center_x - button_w // 2, start_y, button_w, button_h
        )
        # 单人 / 联机 / 退出：三段式纵向排列，联机入口夹在中间。
        multiplayer_y = self.start_button.rect.bottom + metrics.px(16)
        multiplayer_h = metrics.px(MULTIPLAYER_HEIGHT)
        self.multiplayer_button.rect = pygame.Rect(
            center_x - button_w // 2, multiplayer_y, button_w, multiplayer_h
        )
        exit_h = metrics.px(EXIT_HEIGHT)
        self.exit_button.rect = pygame.Rect(
            center_x - button_w // 2,
            self.multiplayer_button.rect.bottom + metrics.px(14),
            button_w,
            exit_h,
        )
        self.speed_control.sync_layout(metrics)
        return self

    # ==================================================
    # 交互
    # ==================================================

    def handle_click(self, position, game):
        speed_action = self.speed_control.hit(position, game)
        if speed_action is not None:
            if speed_action == "slower":
                game.slower()
            else:
                game.faster()
            return "speed"

        if self.start_button.contains(position):
            # 先进入开局流程（身份模式先看身份，再选将）。
            game.begin_general_select()
            return "select_general"

        for mode_id, button in self.mode_buttons.items():
            if button.contains(position):
                game.set_mode(mode_id)
                return "mode"

        if self.minus_button.contains(position):
            game.adjust_player_count(-1)
            return "count"

        if self.plus_button.contains(position):
            game.adjust_player_count(1)
            return "count"

        if self.multiplayer_button.contains(position):
            return "multiplayer"

        if self.exit_button.contains(position):
            return "exit"

        return None

    def buttons(self):
        return (self.start_button, self.minus_button, self.plus_button,
                self.multiplayer_button, self.exit_button)

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, game, metrics=None):
        metrics = metrics or self.metrics
        # 窗口尺寸变化（含全屏启动）后必须先按新度量重算，否则第一帧会
        # 沿用启动时的设计尺寸布局，看起来"挤在中间"，直到某次点击才恢复。
        if (
            metrics is None
            or self.metrics is not metrics
            or self.panel_rect.width <= 10
        ):
            self.sync_layout(metrics)
            metrics = self.metrics

        mouse = pygame.mouse.get_pos()
        fonts = metrics.fonts

        self.screen.blit(theme.table_surface(metrics.screen_w, metrics.screen_h), (0, 0))

        # 装饰光晕
        glow_radius = max(metrics.px(190), metrics.screen_w // 8)
        for center, color in (
            ((metrics.screen_w // 6, metrics.screen_h // 5), (24, 40, 52)),
            ((metrics.screen_w - metrics.screen_w // 8, metrics.screen_h - metrics.screen_h // 6), (20, 34, 46)),
        ):
            halo = theme.radial_glow(glow_radius, color, alpha=120)
            self.screen.blit(halo, halo.get_rect(center=center))

        panel = self.panel_rect
        draw_panel(self.screen, panel, fill=theme.PANEL_DEEP, border=theme.GOLD,
                   border_width=3, radius=metrics.px(22))

        center_x = panel.centerx
        title = fonts.get("hero").render("三国杀", True, theme.GOLD_BRIGHT)
        self.screen.blit(title, title.get_rect(center=(center_x, panel.y + metrics.px(100))))

        subtitle = fonts.get("small").render("标 准 版 · 军 争 篇", True, theme.TEXT_DIM)
        self.screen.blit(subtitle, subtitle.get_rect(center=(center_x, panel.y + metrics.px(156))))

        pygame.draw.line(
            self.screen, theme.GOLD_DIM,
            (panel.x + metrics.px(90), panel.y + metrics.px(186)),
            (panel.right - metrics.px(90), panel.y + metrics.px(186)), 2,
        )

        mode = fonts.get("large").render(self.mode_name(game), True, theme.TEXT)
        self.screen.blit(mode, mode.get_rect(center=(center_x, panel.y + metrics.px(224))))

        hint = fonts.get("small").render(self.mode_description(game), True, theme.TEXT_DIM)
        self.screen.blit(hint, hint.get_rect(center=(center_x, panel.y + metrics.px(264))))

        mode_label = fonts.get("small").render("游戏模式", True, theme.TEXT_DIM)
        self.screen.blit(mode_label, mode_label.get_rect(center=(center_x, panel.y + metrics.px(306))))

        for mode_id, button in self.mode_buttons.items():
            button.kind = "primary" if mode_id == game.mode_id else "secondary"
            button.draw(self.screen, fonts, mouse)

        count_label = fonts.get("small").render("总人数", True, theme.TEXT_DIM)
        self.screen.blit(count_label, count_label.get_rect(center=(center_x, panel.y + metrics.px(402))))

        # 数值块（位于两个步进按钮之间）
        value_rect = self.value_rect
        pygame.draw.rect(self.screen, theme.PANEL_SUNKEN, value_rect, border_radius=metrics.px(9))
        pygame.draw.rect(self.screen, theme.GOLD_DIM, value_rect, 2, border_radius=metrics.px(9))
        # 数字是配置值，字号与模式按钮同级偏小（见 theme.FONT_SIZES["menu_count"]）。
        value = fonts.get("menu_count").render(str(game.total_players()), True, theme.GOLD_BRIGHT)
        self.screen.blit(value, centered_text_origin(value, value_rect))

        self.minus_button.enabled = game.can_adjust_player_count(-1)
        self.plus_button.enabled = game.can_adjust_player_count(1)
        self.minus_button.draw(self.screen, fonts, mouse)
        self.plus_button.draw(self.screen, fonts, mouse)

        allowed = game.allowed_player_counts()
        range_text = "可选人数：" + "～".join((str(allowed[0]), str(allowed[-1])))
        total = fonts.get("normal").render(
            "总人数：" + str(game.total_players()) + " 人（" + range_text + "）", True, theme.TEXT
        )
        self.screen.blit(total, total.get_rect(center=(center_x, panel.y + metrics.px(490))))

        self.start_button.draw(self.screen, fonts, mouse)
        self.multiplayer_button.draw(self.screen, fonts, mouse)
        self.exit_button.draw(self.screen, fonts, mouse)
        self.speed_control.draw(self.screen, game, mouse)

        notice = fonts.get("small").render(game.menu_message, True, theme.TEXT_DIM)
        self.screen.blit(notice, notice.get_rect(
            center=(center_x, self.exit_button.rect.bottom + metrics.px(26))))

    def mode_name(self, game):
        mode = game.modes.get(game.mode_id)
        return mode.name if mode is not None else ""

    def mode_description(self, game):
        mode = game.modes.get(game.mode_id)
        if mode is None:
            return ""
        return mode.description
