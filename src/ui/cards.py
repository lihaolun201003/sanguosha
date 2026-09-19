"""Card faces, card backs, and small geometric equipment icons.

No external art is required: card backs and icons are drawn procedurally and
cached in the theme module.
"""

import pygame

from . import theme


SLOT_LABELS = {
    "weapon": "武器",
    "armor": "防具",
    "defensive_horse": "+1 马",
    "offensive_horse": "-1 马",
}

SLOT_SHORT = {
    "weapon": "武",
    "armor": "防",
    "defensive_horse": "+1",
    "offensive_horse": "-1",
}

CATEGORY_LABELS = {
    "basic": "基本",
    "trick": "锦囊",
    "equipment": "装备",
}


def suit_color(card):
    return theme.CARD_RED if card.card_color == "red" else theme.CARD_BLACK


def face_color(card):
    return theme.CARD_FACE.get(card.category, theme.CARD_FACE_FALLBACK)


def draw_card(
    surface,
    card,
    rect,
    font_set,
    *,
    selected=False,
    candidate=False,
    disabled=False,
    dimmed=False,
    compact=None,
    alpha=255,
):
    """Draw one card face.

    ``compact`` forces the compact (name-only) layout; by default it is chosen
    from the rect size so small seat-area cards stay readable.
    """

    rect = pygame.Rect(rect)
    if compact is None:
        compact = rect.width < 76 or rect.height < 104

    face = pygame.Surface(rect.size, pygame.SRCALPHA)
    body = pygame.Rect(0, 0, rect.width, rect.height)

    pygame.draw.rect(face, (8, 11, 15, 90), body.move(0, 3), border_radius=theme.RADIUS_CARD)
    pygame.draw.rect(face, face_color(card), body, border_radius=theme.RADIUS_CARD)

    # 内描边让牌面有印刷感
    inner = body.inflate(-8, -8)
    pygame.draw.rect(face, (255, 255, 255, 46), inner, 1, border_radius=6)
    pygame.draw.rect(face, theme.CARD_BORDER, body, 2, border_radius=theme.RADIUS_CARD)

    if dimmed or disabled:
        veil = pygame.Surface(rect.size, pygame.SRCALPHA)
        veil.fill((*theme.CARD_DISABLED, 150))
        face.blit(veil, (0, 0))

    _draw_face_content(face, card, body, font_set, compact=compact)

    if candidate and not selected:
        pygame.draw.rect(face, theme.TARGET_BLUE, body, 3, border_radius=theme.RADIUS_CARD)
    if selected:
        pygame.draw.rect(face, theme.TARGET_YELLOW, body, 3, border_radius=theme.RADIUS_CARD)

    if alpha < 255:
        face.set_alpha(alpha)

    surface.blit(face, rect.topleft)
    return rect


def _draw_face_content(face, card, body, font_set, *, compact):
    color = suit_color(card)

    if compact:
        if card.identity_label:
            label = font_set.get("card_meta").render(card.identity_label, True, color)
            face.blit(label, (5, 3))
        name_font = font_set.get("card_small" if len(card.display_name) <= 3 else "seat_meta")
        name = name_font.render(card.display_name, True, theme.INK)
        face.blit(name, name.get_rect(center=(body.centerx, body.centery + 2)))
        if card.category == "equipment" and card.subtype:
            tag = font_set.get("micro").render(SLOT_SHORT.get(card.subtype, "装"), True, theme.CARD_BORDER)
            face.blit(tag, tag.get_rect(bottomright=(body.right - 5, body.bottom - 3)))
        return

    # 左上：花色 + 点数
    if card.identity_label:
        suit_font = font_set.suit(20)
        suit = suit_font.render(card.suit_symbol, True, color)
        rank = font_set.get("card_small").render(str(card.rank), True, color)
        face.blit(suit, (8, 6))
        face.blit(rank, (8 + suit.get_width() + 3, 6))

    # 中央：牌名
    name = card.display_name
    if card.subtype == "weapon":
        name_font = font_set.get("card_small")
    else:
        name_font = font_set.get("card")
    rendered = name_font.render(name, True, theme.INK)
    max_width = body.width - 16
    if rendered.get_width() > max_width:
        rendered = font_set.get("card_small").render(name, True, theme.INK)
    face.blit(rendered, rendered.get_rect(center=(body.centerx, body.centery)))

    # 中央分隔线
    line_y = body.centery - rendered.get_height() // 2 - 8
    pygame.draw.line(face, (0, 0, 0, 40), (body.x + 12, line_y), (body.right - 12, line_y), 1)

    # 右下：类别
    category = CATEGORY_LABELS.get(card.category, "")
    if card.category == "equipment" and card.subtype:
        category = SLOT_LABELS.get(card.subtype, category)
    if category:
        tag = font_set.get("micro").render(category, True, theme.CARD_BORDER)
        face.blit(tag, tag.get_rect(bottomright=(body.right - 7, body.bottom - 5)))


def draw_card_back(surface, rect, *, selected=False, candidate=False, dimmed=False):
    rect = pygame.Rect(rect)
    back = theme.card_back_surface(rect.width, rect.height).copy()

    if dimmed:
        veil = pygame.Surface(rect.size, pygame.SRCALPHA)
        veil.fill((*theme.CARD_DISABLED, 140))
        back.blit(veil, (0, 0))

    if candidate and not selected:
        pygame.draw.rect(back, theme.TARGET_BLUE, pygame.Rect(0, 0, rect.width, rect.height), 3, border_radius=theme.RADIUS_CARD)
    if selected:
        pygame.draw.rect(back, theme.TARGET_YELLOW, pygame.Rect(0, 0, rect.width, rect.height), 3, border_radius=theme.RADIUS_CARD)

    surface.blit(back, rect.topleft)
    return rect


def draw_slot_icon(surface, center, slot, *, size=13, color=theme.GOLD_BRIGHT):
    """Small geometric icon for an equipment slot (no font dependency)."""

    cx, cy = center
    if slot == "weapon":
        # 斜置短剑
        pygame.draw.line(surface, color, (cx - size // 2, cy + size // 2), (cx + size // 2, cy - size // 2), 3)
        pygame.draw.line(surface, color, (cx - size // 2, cy - size // 2 + 2), (cx - size // 2, cy + size // 2 - 2), 2)
    elif slot == "armor":
        points = [
            (cx, cy - size // 2),
            (cx + size // 2, cy - size // 4),
            (cx + size // 3, cy + size // 2),
            (cx - size // 3, cy + size // 2),
            (cx - size // 2, cy - size // 4),
        ]
        pygame.draw.polygon(surface, color, points, 2)
    else:
        # 坐骑：圆环加方向符
        pygame.draw.circle(surface, color, (cx, cy), size // 2, 2)
        direction = 1 if slot == "offensive_horse" else -1
        pygame.draw.line(surface, color, (cx - 3 * direction, cy), (cx + 3 * direction, cy), 2)
    return pygame.Rect(cx - size, cy - size, size * 2, size * 2)
