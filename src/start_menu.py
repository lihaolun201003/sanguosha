import pygame

from src.constants import (
    EXIT_GAME_RECT,
    AI_MINUS_RECT,
    AI_PLUS_RECT,
    MULTIPLAYER_RECT,
    SINGLE_PLAYER_RECT,
)


class StartMenu:

    def __init__(
        self,
        screen,
        big_font,
        small_font,
        tiny_font
    ):

        self.screen = screen
        self.big_font = big_font
        self.small_font = small_font
        self.tiny_font = tiny_font


    def handle_click(
        self,
        position,
        game
    ):

        if pygame.Rect(
            *SINGLE_PLAYER_RECT
        ).collidepoint(position):

            game.start_single_player()
            return "start"

        if pygame.Rect(*AI_MINUS_RECT).collidepoint(position):
            game.ai_count = max(1, game.ai_count - 1)
            return "count"

        if pygame.Rect(*AI_PLUS_RECT).collidepoint(position):
            game.ai_count = min(7, game.ai_count + 1)
            return "count"

        if pygame.Rect(
            *EXIT_GAME_RECT
        ).collidepoint(position):

            return "exit"

        return None


    def draw_button(
        self,
        rect_data,
        label,
        color,
        enabled=True
    ):

        rect = pygame.Rect(*rect_data)
        draw_color = color

        if (
            enabled
            and rect.collidepoint(
                pygame.mouse.get_pos()
            )
        ):

            draw_color = tuple(
                min(255, channel + 22)
                for channel in color
            )

        pygame.draw.rect(
            self.screen,
            (25, 45, 31),
            rect.move(0, 5),
            border_radius=12
        )
        pygame.draw.rect(
            self.screen,
            draw_color,
            rect,
            border_radius=12
        )
        pygame.draw.rect(
            self.screen,
            (238, 210, 135)
            if enabled
            else (125, 125, 125),
            rect,
            2,
            border_radius=12
        )

        text = self.small_font.render(
            label,
            True,
            (35, 30, 24)
            if enabled
            else (205, 205, 205)
        )

        self.screen.blit(
            text,
            text.get_rect(center=rect.center)
        )


    def draw(self, game):

        self.screen.fill((27, 67, 43))

        pygame.draw.circle(
            self.screen,
            (38, 86, 53),
            (115, 110),
            190
        )
        pygame.draw.circle(
            self.screen,
            (34, 78, 49),
            (900, 610),
            245
        )

        panel = pygame.Rect(
            270,
            90,
            460,
            510
        )

        pygame.draw.rect(
            self.screen,
            (20, 48, 31),
            panel.move(0, 8),
            border_radius=22
        )
        pygame.draw.rect(
            self.screen,
            (47, 100, 63),
            panel,
            border_radius=22
        )
        pygame.draw.rect(
            self.screen,
            (218, 178, 82),
            panel,
            3,
            border_radius=22
        )

        title = self.big_font.render(
            "三国杀",
            True,
            (250, 221, 137)
        )
        subtitle = self.small_font.render(
            "单机原型",
            True,
            (225, 235, 220)
        )

        self.screen.blit(
            title,
            title.get_rect(center=(500, 160))
        )
        self.screen.blit(
            subtitle,
            subtitle.get_rect(center=(500, 220))
        )

        self.draw_button(
            SINGLE_PLAYER_RECT,
            "开始本地自由混战",
            (226, 184, 79)
        )
        count_label = self.small_font.render("AI 数量：" + str(game.ai_count) + "（总人数 " + str(game.ai_count + 1) + "）", True, (235, 235, 220))
        self.screen.blit(count_label, count_label.get_rect(center=(500, 417)))
        self.draw_button(AI_MINUS_RECT, "-", (160, 150, 105))
        self.draw_button(AI_PLUS_RECT, "+", (160, 150, 105))
        self.draw_button(
            EXIT_GAME_RECT,
            "退出游戏",
            (178, 91, 74)
        )

        notice = self.tiny_font.render(
            game.menu_message,
            True,
            (225, 230, 220)
        )

        self.screen.blit(
            notice,
            notice.get_rect(center=(500, 565))
        )
