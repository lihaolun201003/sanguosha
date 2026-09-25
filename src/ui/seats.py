"""SeatCard: one player panel with avatar, HP pips, equipment, judgement tags.

Every measurement is derived from the current ``LayoutMetrics``, and text is
measured with ``font.size()`` before it is drawn — icons and labels never share
a rect, so a long weapon name can no longer collide with the armour name.
"""

import pygame

from . import assets as assets_module
from . import cards as card_draw
from . import theme
from .widgets import (
    draw_hp_pips,
    draw_panel,
    draw_state_border,
    draw_state_label,
    ellipsize_text,
)

JUDGE_SHORT = {
    "LEBU": "乐",
    "BINGLIANG": "粮",
    "SHANDIAN": "电",
}

STATUS_CHAIN = "横置"
STATUS_DEAD = "阵亡"
STATUS_RESPONDING = "响应中"

PAD = 14

# 座位面板里装备四格：**横排一行**（与真人状态栏同一套观感）。
# 数值要保证"装备行 + 判定区"不重叠、也不顶出面板底边：
#   装备行 98..122，判定区 130..148。
EQUIPMENT_ROW_ONE = 98
EQUIPMENT_LINE_HEIGHT = 24
EQUIPMENT_COLUMN_GAP = 6
JUDGE_ROW = 130

# 武将牌素材比例（420:572）：头像位改成竖版，整张武将牌按比例缩进来。
AVATAR_ASPECT = 420.0 / 572.0


def general_art(general, rect, registry=None):
    """武将牌缩略图 ``(Surface, Rect)``；没有素材返回 None。"""

    if general is None:
        return None
    registry = registry or assets_module.get_registry()
    asset_id = assets_module.general_asset_id(general.id)
    source = registry.surface(asset_id)
    if source is None:
        return None
    target = assets_module.fit_contain(rect, source.get_size())
    scaled = registry.scaled(asset_id, target.size)
    if scaled is None:
        return None
    return scaled, target


def _avatar(surface, rect, player, *, alive, current, metrics, general=None):
    """头像位：能拿到武将牌就画缩略图，否则退回圆形占位。"""

    ring = theme.GOLD_BRIGHT if current else (theme.TARGET_BLUE if alive else (92, 100, 110))

    art = general_art(general, rect)
    if art is not None:
        scaled, target = art
        frame = target.inflate(metrics.px(4), metrics.px(4))
        pygame.draw.rect(surface, (30, 42, 54), frame, border_radius=metrics.px(5))
        pygame.draw.rect(surface, ring, frame, max(1, metrics.px(2)), border_radius=metrics.px(5))
        surface.blit(scaled, target.topleft)
        if not alive:
            veil = pygame.Surface(target.size, pygame.SRCALPHA)
            veil.fill((26, 30, 36, 150))
            surface.blit(veil, target.topleft)
        return

    radius = min(rect.width, rect.height) // 2
    center = (rect.x + radius, rect.y + radius)
    pygame.draw.circle(surface, (30, 42, 54), center, radius)
    pygame.draw.circle(surface, ring, center, radius, max(2, metrics.px(2)))
    pygame.draw.circle(surface, (24, 34, 44), center, max(1, radius - metrics.px(4)))

    label = "玩家" if player.is_human else str(player.seat)
    font = metrics.fonts.get("seat_name" if player.is_human else "normal")
    text = font.render(label, True, theme.TEXT if alive else theme.TEXT_MUTED)
    surface.blit(text, text.get_rect(center=center))


def draw_seat(
    surface,
    player,
    rect,
    *,
    metrics,
    general=None,
    identity="",
    is_current=False,
    is_responding=False,
    candidate=False,
    selected=False,
    hovered=False,
    in_target_mode=False,
    distance_hint=None,
    flash=None,
    flash_color=theme.DANGER,
    shake=0,
):
    """Draw one seat panel.

    高亮统一走 ``theme.resolve_state``：整个 seat 面板（武将缩略 + 名字 +
    血量 + 身份 + 手牌数 + 装备 + 判定）作为一个完整的可选目标区域一起高亮，
    描边与发光由 ``draw_state_border`` 一次画完。
    """

    fonts = metrics.fonts
    rect = pygame.Rect(rect)
    if shake:
        rect = rect.move(shake, 0)

    alive = player.alive
    fill = theme.PANEL if alive else theme.PANEL_DEEP

    state = theme.resolve_state(
        None if alive else "dead",
        "selected_target" if selected else None,
        "valid_target_hover" if (candidate and hovered) else None,
        "valid_target" if candidate else None,
        "pending_response" if is_responding else None,
        "current_turn" if is_current else None,
        "hover" if hovered else None,
        "invalid_target" if (in_target_mode and alive and not candidate) else None,
    )

    # 面板本体：底色 + 阴影，边框与发光交给状态层统一画。
    draw_panel(
        surface, rect, fill=fill, border=theme.GOLD_DIM, border_width=0,
        radius=theme.RADIUS_PANEL,
    )

    if flash is not None and flash > 0:
        veil = pygame.Surface(rect.size, pygame.SRCALPHA)
        veil.fill((*flash_color, int(150 * flash)))
        surface.blit(veil, rect.topleft)

    draw_state_border(surface, rect, state, radius=metrics.px(theme.RADIUS_PANEL))

    pad = metrics.px(PAD)

    # ---- 头像 / 武将牌缩略 ----
    avatar_box = avatar_rect(rect, metrics)
    _avatar(surface, avatar_box, player, alive=alive, current=is_current,
            metrics=metrics, general=general)

    # ---- 名字 / 座次（按实际宽度省略）----
    info_x = avatar_box.right + metrics.px(10)
    right_limit = rect.right - pad - metrics.px(66)   # 给状态徽章留位置
    name_color = theme.TEXT if alive else theme.TEXT_MUTED
    name_font = fonts.get("seat_name")
    name = ellipsize_text(player.name, name_font, max(1, right_limit - info_x))
    surface.blit(name_font.render(name, True, name_color), (info_x, rect.y + metrics.px(13)))

    seat_font = fonts.get("micro")
    subtitle = "座次 " + str(player.seat)
    if identity:
        # 身份模式：只显示已经公开的身份（调用方已按可见性过滤）。
        subtitle += " · " + identity
    if general is not None:
        subtitle += " · " + general.name + " · " + general.kingdom_name
    subtitle_text = ellipsize_text(subtitle, seat_font, max(1, right_limit - info_x + metrics.px(60)))
    surface.blit(seat_font.render(subtitle_text, True, theme.TEXT_MUTED), (info_x, rect.y + metrics.px(41)))

    # ---- 状态角标：当前回合 / 目标 / 可选 / 响应中 / 阵亡 ----
    badge_font = fonts.get("seat_small")
    marker = draw_state_label(surface, rect, state, fonts, metrics, inset=metrics.px(7))
    anchor_right = (marker.left - metrics.px(6)) if marker is not None \
        else (rect.right - metrics.px(7))
    anchor_y = marker.centery if marker is not None else rect.y + metrics.px(16)
    if player.chained:
        text = badge_font.render(STATUS_CHAIN, True, theme.CHAIN)
        badge_rect = pygame.Rect(
            0, 0, text.get_width() + metrics.px(12), text.get_height() + metrics.px(6))
        badge_rect.midright = (anchor_right, anchor_y)
        pygame.draw.rect(surface, (52, 66, 96), badge_rect, border_radius=metrics.px(7))
        pygame.draw.rect(surface, theme.CHAIN, badge_rect, 1, border_radius=metrics.px(7))
        surface.blit(text, text.get_rect(center=badge_rect.center))

    # ---- 距离提示：只在目标选择 / 悬停时出现，不常驻 ----
    if distance_hint is not None:
        hint_font = fonts.get("seat_small")
        text = hint_font.render("距离 " + str(distance_hint), True, theme.GOLD_BRIGHT)
        hint_rect = pygame.Rect(
            0, 0, text.get_width() + metrics.px(14), text.get_height() + metrics.px(7))
        hint_rect.topright = (rect.right - metrics.px(7), anchor_y + metrics.px(14))
        pygame.draw.rect(surface, (14, 19, 26), hint_rect, border_radius=metrics.px(7))
        pygame.draw.rect(surface, theme.GOLD_DIM, hint_rect, 1, border_radius=metrics.px(7))
        surface.blit(text, text.get_rect(center=hint_rect.center))

    # ---- HP 血点 + 手牌数 ----
    pip_y = rect.y + metrics.px(72)
    pip_spacing = metrics.px(16)
    pip_radius = max(3, metrics.px(7))
    draw_hp_pips(
        surface, (rect.x + pad + pip_radius, pip_y),
        max(0, player.hp), player.max_hp,
        spacing=pip_spacing, radius=pip_radius,
    )
    hp_font = fonts.get("seat_small")
    hp_x = rect.x + pad + pip_radius + pip_spacing * player.max_hp + metrics.px(6)
    hp_text = hp_font.render(str(max(0, player.hp)) + "/" + str(player.max_hp), True, theme.TEXT_DIM)
    surface.blit(hp_text, (hp_x, pip_y - hp_text.get_height() // 2))

    hand_text = hp_font.render("手牌 ×" + str(len(player.hand)), True, theme.TEXT_DIM)
    surface.blit(hand_text, hand_text.get_rect(topright=(rect.right - pad, pip_y - hand_text.get_height() // 2)))

    # ---- 装备四格（横排一行，与真人状态栏同一套观感）----
    line_font = fonts.get("micro")
    for slot, slot_rect in equipment_slot_rects(rect, metrics).items():
        _draw_equipment_slot(
            surface, slot_rect, slot, player.get_equipment(slot), line_font, metrics)

    # ---- 判定区标签（真实卡面缩略 + 名称，两者都不省略）----
    judge_y = rect.y + metrics.px(JUDGE_ROW)
    tag_height = metrics.px(18)
    tag_x = rect.x + pad
    tag_font = fonts.get("micro")
    if player.judgement_zone:
        thumb_width = max(9, int(tag_height * AVATAR_ASPECT))
        for card in player.judgement_zone:
            label = JUDGE_SHORT.get(card.name, card.display_name[:1])
            text = tag_font.render(label, True, (222, 206, 244))
            tag_width = thumb_width + text.get_width() + metrics.px(11)
            tag_rect = pygame.Rect(tag_x, judge_y, tag_width, tag_height)
            pygame.draw.rect(surface, (58, 44, 74), tag_rect, border_radius=metrics.px(6))
            pygame.draw.rect(surface, (176, 142, 214), tag_rect, 1, border_radius=metrics.px(6))

            thumb = pygame.Rect(0, 0, thumb_width, tag_height)
            thumb.midleft = (tag_rect.x + metrics.px(3), tag_rect.centery)
            art = card_draw.card_art(card, thumb)
            if art is not None:
                surface.blit(art[0], art[1].topleft)
            else:
                pygame.draw.rect(surface, (86, 70, 104), thumb.inflate(0, -4),
                                 border_radius=metrics.px(3))
            surface.blit(text, text.get_rect(
                midleft=(tag_rect.x + metrics.px(7) + thumb_width, tag_rect.centery)))
            tag_x += tag_width + metrics.px(6)
    else:
        rendered = tag_font.render("判定区空", True, theme.TEXT_MUTED)
        surface.blit(rendered, rendered.get_rect(midleft=(tag_x, judge_y + tag_height // 2)))

    return rect


def avatar_rect(rect, metrics, *, shift=0):
    """座位面板里武将牌缩略的位置（绘制与命中检测共用同一套坐标）。"""

    rect = pygame.Rect(rect)
    if shift:
        rect = rect.move(shift, 0)
    pad = metrics.px(PAD)
    height = min(metrics.px(48), rect.height - metrics.px(80))
    width = max(metrics.px(20), int(height * AVATAR_ASPECT))
    return pygame.Rect(rect.x + pad, rect.y + metrics.px(11), width, height)


def equipment_slot_rects(rect, metrics, *, shift=0):
    """座位面板里四个装备格（横排一行）的矩形。

    绘制与命中检测共用同一套坐标；``shift`` 是震屏横移的像素
    （命中检测要与绘制对齐）。
    """

    rect = pygame.Rect(rect)
    if shift:
        rect = rect.move(shift, 0)
    pad = metrics.px(PAD)
    gap = metrics.px(EQUIPMENT_COLUMN_GAP)
    slots = ("weapon", "armor", "offensive_horse", "defensive_horse")
    width = max(1, (rect.width - pad * 2 - gap * (len(slots) - 1)) // len(slots))
    height = metrics.px(EQUIPMENT_LINE_HEIGHT)
    y = rect.y + metrics.px(EQUIPMENT_ROW_ONE)
    return {
        slot: pygame.Rect(rect.x + pad + index * (width + gap), y, width, height)
        for index, slot in enumerate(slots)
    }


def _draw_equipment_slot(surface, rect, slot, card, font, metrics):
    """一个装备槽：小方块底 + 槽位图标 + 卡名（与真人状态栏同一套视觉）。"""

    rect = pygame.Rect(rect)
    pygame.draw.rect(surface, theme.PANEL_SUNKEN, rect,
                     border_radius=metrics.px(5))
    border = theme.GOLD_DIM if card is not None else (74, 84, 96)
    pygame.draw.rect(surface, border, rect, 1, border_radius=metrics.px(5))

    icon_size = metrics.px(13)
    icon_center = (rect.x + metrics.px(6) + icon_size // 2, rect.centery)
    card_draw.draw_slot_icon(
        surface,
        icon_center,
        slot,
        size=max(5, metrics.px(7)),
        color=theme.GOLD_BRIGHT if card is not None else (86, 96, 108),
    )

    text_x = icon_center[0] + icon_size // 2 + metrics.px(4)
    available = max(1, rect.right - text_x - metrics.px(4))
    label = card.display_name if card is not None else "空"
    text = ellipsize_text(label, font, available)
    if card is not None and not text:
        # 格子只有五十来像素宽：再窄也要留下第一个字，否则看不出穿了什么。
        text = label[:1]
    color = theme.TEXT if card is not None else theme.TEXT_MUTED
    rendered = font.render(text, True, color)
    surface.blit(rendered, rendered.get_rect(midleft=(text_x, rect.centery)))
