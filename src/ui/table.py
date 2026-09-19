"""Central table: draw pile, discard pile, played cards, judge and turn banners."""

import pygame

from . import cards as card_draw
from . import layout
from . import theme
from .widgets import draw_panel


def draw_piles(surface, game, *, highlight_draw=False, highlight_discard=False):
    fonts = theme.fonts()

    draw_rect = layout.DRAW_PILE_RECT
    count = len(game.deck.draw_pile)
    if count:
        card_draw.draw_card_back(surface, draw_rect, candidate=highlight_draw)
    else:
        pygame.draw.rect(surface, theme.PANEL_SUNKEN, draw_rect, border_radius=theme.RADIUS_CARD)
        pygame.draw.rect(surface, theme.GOLD_DIM, draw_rect, 2, border_radius=theme.RADIUS_CARD)
    label = fonts.get("small").render("牌堆", True, theme.TEXT_DIM)
    surface.blit(label, label.get_rect(center=(draw_rect.centerx, draw_rect.bottom + 16)))
    number = fonts.get("normal").render(str(count), True, theme.TEXT)
    surface.blit(number, number.get_rect(center=(draw_rect.centerx, draw_rect.bottom + 40)))

    discard_rect = layout.DISCARD_PILE_RECT
    top = game.deck.discard_pile[-1] if game.deck.discard_pile else None
    if top is not None:
        card_draw.draw_card(surface, top, discard_rect, fonts, candidate=highlight_discard)
    else:
        pygame.draw.rect(surface, theme.PANEL_SUNKEN, discard_rect, border_radius=theme.RADIUS_CARD)
        pygame.draw.rect(surface, theme.GOLD_DIM, discard_rect, 2, border_radius=theme.RADIUS_CARD)
    label = fonts.get("small").render("弃牌", True, theme.TEXT_DIM)
    surface.blit(label, label.get_rect(center=(discard_rect.centerx, discard_rect.bottom + 16)))
    number = fonts.get("normal").render(str(len(game.deck.discard_pile)), True, theme.TEXT)
    surface.blit(number, number.get_rect(center=(discard_rect.centerx, discard_rect.bottom + 40)))


def draw_table_cards(surface, game):
    fonts = theme.fonts()
    for card, rect_data in game.table_cards:
        rect = pygame.Rect(*rect_data)
        card_draw.draw_card(surface, card, rect, fonts)


def draw_pool(surface, game, entries, rects, *, is_candidate, is_selected):
    """Public card area used by Wugu and 'pick a card from another player'.

    ``entries`` is a ``[(card, key)]`` list; the key carries the equipment slot
    so equipment candidates highlight and hit-test like hand cards.
    """

    if not entries or not rects:
        return
    fonts = theme.fonts()
    for (card, key), rect in zip(entries, rects):
        card_draw.draw_card(
            surface,
            card,
            rect,
            fonts,
            candidate=is_candidate(card, key),
            selected=is_selected(card, key),
        )


def draw_judge_banner(surface, info):
    """``info`` is a presentation dict supplied by the effects layer."""

    if not info:
        return
    card = info.get("card")
    result_text = info.get("result_text", "")
    owner_name = info.get("owner_name", "")
    reason = info.get("reason_text", "")
    alpha = info.get("alpha", 255)
    if alpha <= 0:
        return

    fonts = theme.fonts()
    panel = pygame.Surface((330, 132), pygame.SRCALPHA)
    pygame.draw.rect(panel, (*theme.PANEL_DEEP, 216), panel.get_rect(), border_radius=12)
    pygame.draw.rect(panel, (*theme.GOLD, 210), panel.get_rect(), 2, border_radius=12)

    header = fonts.get("small").render(owner_name + " 判定", True, theme.GOLD_BRIGHT)
    panel.blit(header, (14, 8))
    if reason:
        reason_text = fonts.get("micro").render(reason, True, theme.TEXT_DIM)
        panel.blit(reason_text, (14, 30))

    if card is not None:
        card_draw.draw_card(panel, card, pygame.Rect(14, 48, 52, 72), fonts)
    if result_text:
        text = fonts.get("normal").render(result_text, True, theme.TEXT)
        panel.blit(text, (78, 70))

    panel.set_alpha(alpha)
    surface.blit(panel, panel.get_rect(midtop=(layout.WIDTH // 2, layout.CENTRAL_RECT.y + 4)))


def draw_turn_banner(surface, info):
    if not info:
        return
    alpha = info.get("alpha", 255)
    if alpha <= 0:
        return
    fonts = theme.fonts()
    text = fonts.get("huge").render(info.get("text", ""), True, theme.GOLD_BRIGHT)
    shadow = fonts.get("huge").render(info.get("text", ""), True, (10, 12, 16))
    rect = text.get_rect(center=(layout.WIDTH // 2, layout.CENTRAL_RECT.y + 40))

    plate = pygame.Surface((rect.width + 60, rect.height + 22), pygame.SRCALPHA)
    pygame.draw.rect(plate, (*theme.INK, 168), plate.get_rect(), border_radius=14)
    pygame.draw.rect(plate, (*theme.GOLD, 190), plate.get_rect(), 2, border_radius=14)
    plate.set_alpha(alpha)

    surface.blit(plate, plate.get_rect(center=rect.center))
    text.set_alpha(alpha)
    shadow.set_alpha(alpha)
    surface.blit(shadow, shadow.get_rect(center=(rect.centerx + 2, rect.centery + 2)))
    surface.blit(text, rect)


def draw_phase_strip(surface, game, phase_label):
    fonts = theme.fonts()
    rect = pygame.Rect(layout.CENTRAL_RECT.right - 176, layout.CENTRAL_RECT.bottom - 30, 168, 24)
    draw_panel(surface, rect, fill=theme.PANEL_SUNKEN, border=theme.GOLD_DIM, border_width=2, shadow=False, radius=8)
    text = fonts.get("small").render(phase_label, True, theme.GOLD_BRIGHT)
    surface.blit(text, text.get_rect(center=rect.center))


def draw_log(surface, game, *, max_entries=5):
    """Low-priority combat log in the lower-left corner, clear of every seat."""

    entries = game.game_log[-max_entries:]
    if not entries:
        return
    fonts = theme.fonts()
    width = 170
    height = 19 * len(entries) + 20
    rect = pygame.Rect(10, layout.HEIGHT - 20 - height, width, height)

    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel, (10, 14, 19, 150), panel.get_rect(), border_radius=10)
    pygame.draw.rect(panel, (*theme.GOLD_DIM, 120), panel.get_rect(), 1, border_radius=10)
    surface.blit(panel, rect.topleft)

    title = fonts.get("micro").render("战报", True, theme.TEXT_MUTED)
    surface.blit(title, (rect.x + 9, rect.y + 3))
    for index, entry in enumerate(entries):
        text = fonts.get("micro").render(entry[:13], True, (206, 214, 222))
        surface.blit(text, (rect.x + 9, rect.y + 18 + index * 19))
