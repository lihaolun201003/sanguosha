"""SeatCard: one player panel with HP pips, equipment, judgement tags."""

import pygame

from . import cards as card_draw
from . import theme
from .widgets import draw_panel

JUDGE_SHORT = {
    "LEBU": "乐",
    "BINGLIANG": "粮",
    "SHANDIAN": "电",
}

STATUS_CHAIN = "横置"
STATUS_DEAD = "阵亡"


def _truncate(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _avatar(surface, rect, player, *, alive, current):
    radius = rect.height // 2
    center = rect.center
    ring = theme.GOLD_BRIGHT if current else (theme.TARGET_BLUE if alive else (92, 100, 110))
    pygame.draw.circle(surface, (30, 42, 54), center, radius)
    pygame.draw.circle(surface, ring, center, radius, 2)
    pygame.draw.circle(surface, (24, 34, 44), center, radius - 4)

    label = "玩家" if player.is_human else str(player.seat)
    font = theme.fonts().get("seat_name" if player.is_human else "normal")
    text = font.render(label, True, theme.TEXT if alive else theme.TEXT_MUTED)
    surface.blit(text, text.get_rect(center=center))


def draw_seat(
    surface,
    player,
    rect,
    *,
    is_current=False,
    candidate=False,
    selected=False,
    flash=None,
    flash_color=theme.DANGER,
    shake=0,
):
    """Draw one seat panel.  ``flash`` is a 0..1 presentation-only highlight."""

    fonts = theme.fonts()
    rect = pygame.Rect(rect)
    if shake:
        rect = rect.move(shake, 0)

    alive = player.alive
    fill = theme.PANEL if alive else theme.PANEL_DEEP
    border = theme.GOLD_DIM
    border_width = theme.BORDER
    glow = None

    if selected:
        border, border_width = theme.TARGET_YELLOW, theme.BORDER_THICK
    elif candidate:
        border, border_width = theme.TARGET_BLUE, theme.BORDER_THICK
    elif is_current:
        border, border_width, glow = theme.GOLD_BRIGHT, theme.BORDER_THICK, theme.GOLD
    elif not alive:
        border, border_width = (78, 86, 96), theme.BORDER_THIN

    draw_panel(surface, rect, fill=fill, border=border, border_width=border_width, glow=glow)

    if flash is not None and flash > 0:
        veil = pygame.Surface(rect.size, pygame.SRCALPHA)
        veil.fill((*flash_color, int(150 * flash)))
        surface.blit(veil, rect.topleft)

    if not alive:
        grey = pygame.Surface(rect.size, pygame.SRCALPHA)
        grey.fill((26, 30, 36, 130))
        surface.blit(grey, rect.topleft)

    pad = 8
    avatar_size = min(38, rect.height - 76)
    avatar_rect = pygame.Rect(rect.x + pad, rect.y + 7, avatar_size, avatar_size)
    _avatar(surface, avatar_rect, player, alive=alive, current=is_current)

    # 名字
    name_color = theme.TEXT if alive else theme.TEXT_MUTED
    name_font = fonts.get("seat_name")
    name_x = avatar_rect.right + 7
    name = name_font.render(_truncate(player.name, 7), True, name_color)
    surface.blit(name, (name_x, rect.y + 9))

    # 状态徽章
    badge_font = fonts.get("seat_small")
    badge = None
    if not alive:
        badge = (STATUS_DEAD, (96, 48, 44), (208, 132, 120))
    elif player.chained:
        badge = (STATUS_CHAIN, (52, 66, 96), theme.CHAIN)
    if badge:
        text, fill_color, text_color = badge
        rendered = badge_font.render(text, True, text_color)
        badge_rect = pygame.Rect(0, 0, rendered.get_width() + 10, rendered.get_height() + 4)
        badge_rect.topright = (rect.right - pad, rect.y + 9)
        pygame.draw.rect(surface, fill_color, badge_rect, border_radius=7)
        pygame.draw.rect(surface, text_color, badge_rect, 1, border_radius=7)
        surface.blit(rendered, rendered.get_rect(center=badge_rect.center))

    # 座次
    seat_font = fonts.get("seat_small")
    seat_text = seat_font.render("座次 " + str(player.seat), True, theme.TEXT_MUTED)
    surface.blit(seat_text, (name_x, rect.y + 31))

    # HP 血点 + 手牌数
    pip_y = rect.y + 56
    from .widgets import draw_hp_pips

    draw_hp_pips(surface, (rect.x + pad + 4, pip_y), max(0, player.hp), player.max_hp, spacing=13, radius=5)

    hp_font = fonts.get("seat_small")
    hp_text = hp_font.render(str(max(0, player.hp)) + "/" + str(player.max_hp), True, theme.TEXT_DIM)
    surface.blit(hp_text, (rect.x + pad + 6 + player.max_hp * 13 + 6, pip_y - 8))

    hand_text = seat_font.render("手牌 ×" + str(len(player.hand)), True, theme.TEXT_DIM)
    surface.blit(hand_text, hand_text.get_rect(topright=(rect.right - pad, pip_y - 8)))

    # 装备两行
    line_font = fonts.get("seat_small")
    equip_y = rect.y + 76
    weapon = player.get_equipment("weapon")
    armor = player.get_equipment("armor")
    _draw_equipment_line(surface, (rect.x + pad, equip_y), "weapon", weapon, line_font)
    _draw_equipment_line(surface, (rect.x + rect.width // 2 + 2, equip_y), "armor", armor, line_font)

    horse_y = equip_y + 17
    _draw_equipment_line(surface, (rect.x + pad, horse_y), "offensive_horse", player.get_equipment("offensive_horse"), line_font)
    _draw_equipment_line(surface, (rect.x + rect.width // 2 + 2, horse_y), "defensive_horse", player.get_equipment("defensive_horse"), line_font)

    # 判定区标签
    judge_y = horse_y + 17
    tag_x = rect.x + pad
    if player.judgement_zone:
        for card in player.judgement_zone:
            label = JUDGE_SHORT.get(card.name, card.display_name[:1])
            rect_tag = pygame.Rect(tag_x, judge_y, 20, 16)
            pygame.draw.rect(surface, (58, 44, 74), rect_tag, border_radius=5)
            pygame.draw.rect(surface, (176, 142, 214), rect_tag, 1, border_radius=5)
            text = fonts.get("micro").render(label, True, (222, 206, 244))
            surface.blit(text, text.get_rect(center=rect_tag.center))
            tag_x += 24
    else:
        text = fonts.get("micro").render("判定区空", True, theme.TEXT_MUTED)
        surface.blit(text, (tag_x, judge_y))

    return rect


def _draw_equipment_line(surface, position, slot, card, font):
    x, y = position
    icon_color = theme.GOLD_BRIGHT if card is not None else (86, 96, 108)
    card_draw.draw_slot_icon(surface, (x + 8, y + 8), slot, size=9, color=icon_color)
    label = card.display_name if card is not None else "空"
    text = font.render(_truncate(label, 5), True, theme.TEXT if card is not None else theme.TEXT_MUTED)
    surface.blit(text, (x + 20, y))
