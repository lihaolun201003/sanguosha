"""Reusable panels, buttons, and text helpers."""

import pygame

from . import theme


def draw_panel(
    surface,
    rect,
    *,
    fill=theme.PANEL,
    border=theme.GOLD_DIM,
    radius=theme.RADIUS_PANEL,
    border_width=theme.BORDER,
    shadow=True,
    shadow_offset=4,
    glow=None,
    glow_width=3,
):
    """One consistent panel primitive used by every component."""

    rect = pygame.Rect(rect)

    if glow is not None:
        halo = theme.radial_glow(max(rect.width, rect.height) // 2 + 18, glow)
        surface.blit(
            halo,
            (
                rect.centerx - halo.get_width() // 2,
                rect.centery - halo.get_height() // 2,
            ),
        )

    if shadow:
        pygame.draw.rect(
            surface,
            (8, 11, 15),
            rect.move(shadow_offset, shadow_offset),
            border_radius=radius,
        )

    pygame.draw.rect(surface, fill, rect, border_radius=radius)

    # 顶部高光，让面板有厚度
    highlight = pygame.Surface((rect.width - 4, max(2, rect.height // 3)), pygame.SRCALPHA)
    highlight.fill((255, 255, 255, 14))
    surface.blit(highlight, (rect.x + 2, rect.y + 2))

    if border_width > 0:
        pygame.draw.rect(surface, border, rect, border_width, border_radius=radius)


def draw_text(surface, text, position, font, color, *, anchor="topleft"):
    rendered = font.render(str(text), True, color)
    rect = rendered.get_rect(**{anchor: position})
    surface.blit(rendered, rect)
    return rect


def draw_center_text(surface, text, center, font, color):
    return draw_text(surface, text, center, font, color, anchor="center")


BUTTON_STYLES = {
    "primary": {
        "fill": (150, 116, 46),
        "fill_hover": (178, 140, 58),
        "fill_pressed": (120, 92, 36),
        "border": theme.GOLD_BRIGHT,
        "text": (252, 244, 222),
    },
    "danger": {
        "fill": (128, 48, 42),
        "fill_hover": (158, 62, 54),
        "fill_pressed": (100, 38, 34),
        "border": (226, 140, 120),
        "text": (252, 238, 234),
    },
    "secondary": {
        "fill": (52, 74, 94),
        "fill_hover": (66, 92, 116),
        "fill_pressed": (42, 60, 78),
        "border": (120, 158, 190),
        "text": theme.TEXT,
    },
    "ghost": {
        "fill": (34, 48, 62),
        "fill_hover": (46, 64, 82),
        "fill_pressed": (28, 40, 52),
        "border": (96, 116, 136),
        "text": theme.TEXT_DIM,
    },
}


class Button:
    """A themed button with hover / pressed / disabled states."""

    def __init__(self, rect, label, *, kind="secondary", enabled=True, font="normal", radius=None):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.kind = kind
        self.enabled = enabled
        self.font_name = font
        self.radius = theme.RADIUS_BUTTON if radius is None else radius
        self._press_origin = None

    def set_rect(self, rect):
        self.rect = pygame.Rect(rect)

    def set_label(self, label):
        self.label = label

    def contains(self, position):
        return self.rect.collidepoint(position)

    def hovered(self, mouse_pos):
        return mouse_pos is not None and self.enabled and self.contains(mouse_pos)

    def draw(self, surface, font_set, mouse_pos=None, pressed=False):
        if not self.label:
            return
        style = BUTTON_STYLES[self.kind]
        rect = self.rect

        if not self.enabled:
            fill = theme.DISABLED_FILL
            border = (96, 104, 112)
            text_color = theme.DISABLED_TEXT
        else:
            hover = self.hovered(mouse_pos)
            if pressed and hover:
                fill = style["fill_pressed"]
                rect = rect.move(0, 2)
            elif hover:
                fill = style["fill_hover"]
            else:
                fill = style["fill"]
            border = style["border"]
            text_color = style["text"]

        pygame.draw.rect(surface, (8, 11, 15), self.rect.move(0, 3), border_radius=self.radius)
        pygame.draw.rect(surface, fill, rect, border_radius=self.radius)
        pygame.draw.rect(surface, border, rect, 2, border_radius=self.radius)

        font = font_set.get(self.font_name)
        label = font.render(self.label, True, text_color)
        surface.blit(label, label.get_rect(center=rect.center))


class ButtonBar:
    """A fixed row/column of buttons with shared hit testing."""

    def __init__(self, buttons=()):
        self.buttons = list(buttons)

    def add(self, button):
        self.buttons.append(button)
        return button

    def hit(self, position):
        for button in self.buttons:
            if button.enabled and button.contains(position):
                return button
        return None

    def draw(self, surface, font_set, mouse_pos=None, pressed_button=None):
        for button in self.buttons:
            button.draw(surface, font_set, mouse_pos, pressed=button is pressed_button)


def draw_badge(surface, center, text, font, *, fill=(44, 60, 78), border=theme.GOLD_DIM, text_color=theme.TEXT, radius=8, padding=(8, 3)):
    rendered = font.render(str(text), True, text_color)
    rect = pygame.Rect(0, 0, rendered.get_width() + padding[0] * 2, rendered.get_height() + padding[1] * 2)
    rect.center = center
    pygame.draw.rect(surface, fill, rect, border_radius=radius)
    if border is not None:
        pygame.draw.rect(surface, border, rect, 2, border_radius=radius)
    surface.blit(rendered, rendered.get_rect(center=rect.center))
    return rect


def draw_hp_pips(surface, origin, hp, max_hp, *, spacing=15, radius=6, vertical=False):
    """Pygame-drawn health pips: green / orange / red by remaining ratio."""

    ratio = hp / max_hp if max_hp else 0
    if ratio > 0.6:
        full_color = (104, 196, 118)
    elif ratio > 0.3:
        full_color = (226, 166, 74)
    else:
        full_color = (214, 84, 68)

    for index in range(max_hp):
        if vertical:
            center = (origin[0], origin[1] + index * spacing)
        else:
            center = (origin[0] + index * spacing, origin[1])
        if index < hp:
            pygame.draw.circle(surface, full_color, center, radius)
            pygame.draw.circle(surface, (26, 32, 40), center, radius, 1)
        else:
            pygame.draw.circle(surface, (38, 48, 58), center, radius)
            pygame.draw.circle(surface, (78, 92, 104), center, radius, 1)

    return origin[0] + max_hp * spacing if not vertical else origin[0]
