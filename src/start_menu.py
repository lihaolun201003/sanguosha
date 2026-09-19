"""Start menu: title, AI count stepper, start / exit."""

import pygame

from src.constants import (
    AI_MINUS_RECT,
    AI_PLUS_RECT,
    EXIT_GAME_RECT,
    SINGLE_PLAYER_RECT,
)
from src.ui import layout, theme
from src.ui.widgets import Button, draw_panel


class StartMenu:

    def __init__(self, screen, big_font, small_font, tiny_font):
        self.screen = screen
        self.big_font = big_font
        self.small_font = small_font
        self.tiny_font = tiny_font

        self.start_button = Button(pygame.Rect(*SINGLE_PLAYER_RECT), "开始游戏", kind="primary", font="large")
        self.minus_button = Button(pygame.Rect(*AI_MINUS_RECT), "−", kind="secondary", font="large")
        self.plus_button = Button(pygame.Rect(*AI_PLUS_RECT), "＋", kind="secondary", font="large")
        self.exit_button = Button(pygame.Rect(*EXIT_GAME_RECT), "退出游戏", kind="ghost", font="normal")

    # ==================================================
    # 交互
    # ==================================================

    def handle_click(self, position, game):
        if self.start_button.contains(position):
            game.start_single_player()
            return "start"

        if self.minus_button.contains(position):
            game.ai_count = max(1, game.ai_count - 1)
            return "count"

        if self.plus_button.contains(position):
            game.ai_count = min(7, game.ai_count + 1)
            return "count"

        if self.exit_button.contains(position):
            return "exit"

        return None

    def buttons(self):
        return (self.start_button, self.minus_button, self.plus_button, self.exit_button)

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, game):
        mouse = pygame.mouse.get_pos()
        fonts = theme.fonts()

        self.screen.blit(theme.table_surface(layout.WIDTH, layout.HEIGHT), (0, 0))

        # 装饰圆
        for center, radius, color in (
            ((150, 130), 190, (24, 40, 52)),
            ((880, 600), 240, (20, 34, 46)),
        ):
            halo = theme.radial_glow(radius, color, alpha=120)
            self.screen.blit(halo, halo.get_rect(center=center))

        panel = pygame.Rect(250, 84, 500, 522)
        draw_panel(self.screen, panel, fill=theme.PANEL_DEEP, border=theme.GOLD, border_width=3)

        title = fonts.get("hero").render("三国杀", True, theme.GOLD_BRIGHT)
        self.screen.blit(title, title.get_rect(center=(500, 158)))

        subtitle = fonts.get("small").render("标 准 版 · 军 争 篇", True, theme.TEXT_DIM)
        self.screen.blit(subtitle, subtitle.get_rect(center=(500, 208)))

        pygame.draw.line(self.screen, theme.GOLD_DIM, (322, 238), (678, 238), 2)

        mode = fonts.get("large").render("本地自由混战", True, theme.TEXT)
        self.screen.blit(mode, mode.get_rect(center=(500, 274)))

        hint = fonts.get("small").render("1 名真人 + 自定义 1～7 名 AI", True, theme.TEXT_DIM)
        self.screen.blit(hint, hint.get_rect(center=(500, 302)))

        # AI 数量
        count_label = fonts.get("small").render("AI 数量", True, theme.TEXT_DIM)
        self.screen.blit(count_label, count_label.get_rect(center=(500, 330)))

        value_font = fonts.get("hero")
        value = value_font.render(str(game.ai_count), True, theme.GOLD_BRIGHT)
        value_rect = pygame.Rect(AI_MINUS_RECT[0] + AI_MINUS_RECT[2], AI_MINUS_RECT[1],
                                 AI_PLUS_RECT[0] - AI_MINUS_RECT[0] - AI_MINUS_RECT[2], AI_MINUS_RECT[3])
        pygame.draw.rect(self.screen, theme.PANEL_SUNKEN, value_rect, border_radius=8)
        pygame.draw.rect(self.screen, theme.GOLD_DIM, value_rect, 2, border_radius=8)
        self.screen.blit(value, value.get_rect(center=value_rect.center))

        self.minus_button.enabled = game.ai_count > 1
        self.plus_button.enabled = game.ai_count < 7
        self.minus_button.draw(self.screen, fonts, mouse)
        self.plus_button.draw(self.screen, fonts, mouse)

        total = fonts.get("normal").render(
            "总人数：" + str(game.ai_count + 1) + " 人", True, theme.TEXT
        )
        self.screen.blit(total, total.get_rect(center=(500, 422)))

        self.start_button.draw(self.screen, fonts, mouse)
        self.exit_button.draw(self.screen, fonts, mouse)

        notice = fonts.get("small").render(game.menu_message, True, theme.TEXT_DIM)
        self.screen.blit(notice, notice.get_rect(center=(500, 592)))
