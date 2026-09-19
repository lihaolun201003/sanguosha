"""Result overlay and modal affordances."""

import pygame

from . import layout
from . import theme
from .widgets import Button, draw_panel

WIN_TITLE = "胜 利"
LOSE_TITLE = "战 败"


class GameOverOverlay:
    """Formal result screen with restart / main menu."""

    def __init__(self):
        self.panel_rect = pygame.Rect(0, 0, 460, 300)
        self.panel_rect.center = (layout.WIDTH // 2, layout.HEIGHT // 2 - 10)
        self.restart_button = Button(
            pygame.Rect(0, 0, 180, 52), "重新开始", kind="primary"
        )
        self.restart_button.rect.topleft = (self.panel_rect.x + 34, self.panel_rect.bottom - 78)
        self.menu_button = Button(
            pygame.Rect(0, 0, 180, 52), "返回主菜单", kind="secondary"
        )
        self.menu_button.rect.topleft = (self.panel_rect.right - 214, self.panel_rect.bottom - 78)

    def restart_rect(self):
        return pygame.Rect(self.restart_button.rect)

    def main_menu_rect(self):
        return pygame.Rect(self.menu_button.rect)

    def result_texts(self, game):
        result = game.result
        winner = game.winner
        if winner is game.player:
            return WIN_TITLE, "最后存活者：玩家", theme.GOLD_BRIGHT
        if result is not None and result.reason == "HUMAN_ELIMINATED":
            return LOSE_TITLE, "你已阵亡", theme.DANGER
        if result is not None and result.reason == "NO_SURVIVOR":
            return LOSE_TITLE, "全场阵亡，无人获胜", theme.TEXT_DIM
        if winner is not None:
            return LOSE_TITLE, "最后存活者：" + winner.name, theme.DANGER
        return LOSE_TITLE, "对局结束", theme.TEXT_DIM

    def handle_click(self, position):
        if self.restart_button.contains(position):
            return "restart"
        if self.menu_button.contains(position):
            return "menu"
        return None

    def draw(self, surface, game, mouse_pos=None):
        veil = pygame.Surface((layout.WIDTH, layout.HEIGHT), pygame.SRCALPHA)
        veil.fill((6, 9, 13, 190))
        surface.blit(veil, (0, 0))

        rect = self.panel_rect
        draw_panel(surface, rect, fill=theme.PANEL, border=theme.GOLD, border_width=theme.BORDER_THICK)

        fonts = theme.fonts()
        title, subtitle, color = self.result_texts(game)

        title_surface = fonts.get("hero").render(title, True, color)
        surface.blit(title_surface, title_surface.get_rect(center=(rect.centerx, rect.y + 78)))

        line_y = rect.y + 132
        pygame.draw.line(surface, theme.GOLD_DIM, (rect.x + 60, line_y), (rect.right - 60, line_y), 2)

        subtitle_surface = fonts.get("large").render(subtitle, True, theme.TEXT)
        surface.blit(subtitle_surface, subtitle_surface.get_rect(center=(rect.centerx, rect.y + 168)))

        if game.game_log:
            last = fonts.get("small").render(game.game_log[-1][:28], True, theme.TEXT_DIM)
            surface.blit(last, last.get_rect(center=(rect.centerx, rect.y + 204)))

        self.restart_button.draw(surface, fonts, mouse_pos)
        self.menu_button.draw(surface, fonts, mouse_pos)
