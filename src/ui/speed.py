"""In-game pacing control: a small [-] value [+] widget in the corner.

The widget only reads/writes ``Game.speed``; it never touches rules.
"""

import pygame

from . import layout
from . import theme
from .widgets import Button, draw_panel


class SpeedControl:
    """Compact speed stepper drawn in the top-left corner of the table."""

    def __init__(self, metrics=None):
        self.minus = Button(pygame.Rect(0, 0, 10, 10), "−", kind="secondary", font="small")
        self.plus = Button(pygame.Rect(0, 0, 10, 10), "＋", kind="secondary", font="small")
        self.rect = pygame.Rect(0, 0, 10, 10)
        self.sync_layout(metrics)

    def sync_layout(self, metrics=None):
        """Recompute for the current screen (called on resize / fullscreen)."""

        if metrics is None:
            metrics = layout.LayoutMetrics(layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.rect = metrics.speed_control
        pad = metrics.px(12)
        button_w = metrics.px(56)
        button_h = metrics.px(32)
        y = self.rect.bottom - button_h - metrics.px(10)
        self.minus.rect = pygame.Rect(self.rect.x + pad, y, button_w, button_h)
        self.plus.rect = pygame.Rect(self.rect.right - pad - button_w, y, button_w, button_h)
        self.metrics = metrics
        return self

    def value_rect(self):
        return pygame.Rect(
            self.minus.rect.right + self.metrics.px(6),
            self.minus.rect.y,
            self.plus.rect.x - self.minus.rect.right - self.metrics.px(12),
            self.minus.rect.height,
        )

    def sync(self, game):
        """Enable/disable the steppers at the ends of the range."""

        self.minus.enabled = game.speed_index() > 0
        self.plus.enabled = game.speed_index() < len(game.SPEED_STEPS) - 1

    def hit(self, position, game):
        self.sync(game)
        if self.minus.enabled and self.minus.contains(position):
            return "slower"
        if self.plus.enabled and self.plus.contains(position):
            return "faster"
        return None

    def draw(self, surface, game, mouse_pos=None):
        metrics = self.metrics
        fonts = metrics.fonts
        self.sync(game)

        draw_panel(
            surface,
            self.rect,
            fill=theme.PANEL_DEEP,
            border=theme.GOLD_DIM,
            border_width=2,
            radius=metrics.px(10),
        )

        label = fonts.get("small").render("节奏 − / ＋", True, theme.TEXT_DIM)
        surface.blit(label, label.get_rect(midtop=(self.rect.centerx, self.rect.y + metrics.px(6))))

        value_rect = self.value_rect()
        pygame.draw.rect(surface, theme.PANEL_SUNKEN, value_rect, border_radius=metrics.px(6))
        pygame.draw.rect(surface, theme.GOLD_DIM, value_rect, 1, border_radius=metrics.px(6))
        value = fonts.get("normal").render(game.speed_label, True, theme.GOLD_BRIGHT)
        surface.blit(value, value.get_rect(center=value_rect.center))

        self.minus.draw(surface, fonts, mouse_pos)
        self.plus.draw(surface, fonts, mouse_pos)
