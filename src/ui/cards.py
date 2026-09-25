"""Card faces, card backs, and small geometric equipment icons.

卡面优先使用 ``assets/`` 里的真实扫描图；素材缺失时自动退回原来的程序绘制。
无论走哪条路径，**真实 Card 数据（花色 / 点数）始终画在最上层**：扫描图上
印刷的固定花色点数不会成为玩家唯一能看到的信息，Card.suit / Card.rank 才是
权威。

牌背走 ``assets/cards/back/`` 的素材（牌堆封面），素材缺失时退回程序绘制；
装备缩略图小到看不清卡面时也用图标代替（悬停时仍会显示真实卡面）。
"""

import pygame

from . import assets as assets_module
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

# 小于这个尺寸就退回程序绘制：缩到这个大小的卡面已经看不清，不如用
# 名字 + 花色点数的紧凑排版。悬停预览仍然显示真实卡面。
ART_MIN_WIDTH = 76
ART_MIN_HEIGHT = 104


def suit_color(card):
    return theme.CARD_RED if card.card_color == "red" else theme.CARD_BLACK


def face_color(card):
    return theme.CARD_FACE.get(card.category, theme.CARD_FACE_FALLBACK)


def category_label(card):
    """卡面右下角要显示的类别文字。"""

    label = CATEGORY_LABELS.get(card.category, "")
    if card.category == "equipment" and card.subtype:
        label = SLOT_LABELS.get(card.subtype, label)
    return label


def card_art(card, body, registry=None):
    """按比例放进 ``body`` 的真实卡面；横版素材自动竖版化。

    返回 ``(Surface, Rect)``。竖版素材走缩放缓存；横版素材走竖版容器
    合成缓存（同一尺寸跨帧复用，不每帧合成）。
    """

    registry = registry or assets_module.get_registry()
    asset_id = assets_module.card_asset_id(card)
    if asset_id is None:
        return None
    source = registry.surface(asset_id)
    if source is None:
        return None

    if source.get_width() > source.get_height():
        # 横版素材（延时锦囊横置形态等）：整块卡位用竖版容器合成，
        # 直接铺满而不是缩成一条横带。
        oriented = registry.oriented(
            asset_id, body.size,
            title=card.display_name,
            category=category_label(card),
        )
        if oriented is None:
            return None
        return oriented, pygame.Rect(body)

    target = assets_module.fit_contain(body, source.get_size())
    scaled = registry.scaled(asset_id, target.size)
    if scaled is None:
        return None
    return scaled, target


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
    hovered=False,
    compact=None,
    alpha=255,
    registry=None,
):
    """Draw one card face.

    ``compact`` forces the compact (name-only) layout; by default it is chosen
    from the rect size so small seat-area cards stay readable.

    状态描边与发光统一走 ``theme`` 的视觉状态表，且画在**目标 surface** 上，
    所以发光可以溢出卡片边界而不会被卡面裁掉。
    """

    rect = pygame.Rect(rect)
    if compact is None:
        compact = rect.width < ART_MIN_WIDTH or rect.height < ART_MIN_HEIGHT

    face = pygame.Surface(rect.size, pygame.SRCALPHA)
    body = pygame.Rect(0, 0, rect.width, rect.height)

    pygame.draw.rect(face, (8, 11, 15, 90), body.move(0, 3), border_radius=theme.RADIUS_CARD)
    pygame.draw.rect(face, face_color(card), body, border_radius=theme.RADIUS_CARD)

    art = None if compact else card_art(card, body, registry)
    if art is not None:
        scaled, target = art
        face.blit(scaled, target.topleft)
    else:
        # 内描边让牌面有印刷感
        inner = body.inflate(-8, -8)
        pygame.draw.rect(face, (255, 255, 255, 46), inner, 1, border_radius=6)
        _draw_face_content(face, card, body, font_set, compact=compact)

    pygame.draw.rect(face, theme.CARD_BORDER, body, 2, border_radius=theme.RADIUS_CARD)

    if dimmed or disabled:
        veil = pygame.Surface(rect.size, pygame.SRCALPHA)
        veil.fill((*theme.CARD_DISABLED, 150))
        face.blit(veil, (0, 0))

    # 真实花色 / 点数画在卡面之上：素材上的印刷信息不会冒充游戏数据。
    if art is not None:
        _draw_suit_rank_badge(face, card, body, font_set, muted=dimmed or disabled)

    if alpha < 255:
        face.set_alpha(alpha)

    surface.blit(face, rect.topleft)

    state_name = theme.resolve_state(
        "disabled" if (disabled or dimmed) else None,
        "selected" if selected else None,
        "view_as_candidate" if candidate else None,
        "hover" if hovered else None,
    )
    _draw_state_border(surface, rect, state_name)
    return rect


def _draw_state_border(surface, rect, state_name):
    """按视觉状态在卡牌外沿画描边 + 外发光（画在卡面之上，发光可溢出）。"""

    stroke = int(theme.visual_state(state_name).get("width") or 0)
    if stroke <= 0:
        return
    state = theme.visual_state(state_name)
    halo = int(state.get("glow_width") or 0)
    border = theme.glow_border(
        rect.size, state["border"], stroke, halo, theme.RADIUS_CARD, 150)
    surface.blit(border, (rect.x - halo, rect.y - halo))


def _draw_suit_rank_badge(face, card, body, font_set, *, muted=False):
    """在卡面左上角画真实的 suit / rank，底衬保证任何卡面上都能读清。"""

    if not card.identity_label:
        # 技能生成的虚拟牌没有花色点数，不画。
        return

    color = (150, 154, 160) if muted else suit_color(card)
    suit_font = font_set.suit(22)
    rank_font = font_set.get("card_meta")
    suit = suit_font.render(card.suit_symbol, True, color)
    rank = rank_font.render(str(card.rank), True, color)

    gap = max(2, body.width // 40)
    pad_x = max(3, body.width // 22)
    pad_y = max(2, body.height // 44)
    content_w = suit.get_width() + gap + rank.get_width()
    content_h = max(suit.get_height(), rank.get_height())
    badge = pygame.Rect(0, 0, content_w + pad_x * 2, content_h + pad_y * 2)
    badge.topleft = (body.x + max(2, body.width // 26), body.y + max(2, body.height // 40))

    radius = max(3, badge.height // 3)
    plate = pygame.Surface(badge.size, pygame.SRCALPHA)
    pygame.draw.rect(plate, (12, 16, 22, 196), plate.get_rect(), border_radius=radius)
    pygame.draw.rect(plate, (*theme.GOLD_DIM, 170), plate.get_rect(), 1, border_radius=radius)
    face.blit(plate, badge.topleft)

    face.blit(suit, (badge.x + pad_x, badge.centery - suit.get_height() // 2))
    face.blit(rank, (badge.x + pad_x + suit.get_width() + gap,
                     badge.centery - rank.get_height() // 2))


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
    category = category_label(card)
    if category:
        tag = font_set.get("micro").render(category, True, theme.CARD_BORDER)
        face.blit(tag, tag.get_rect(bottomright=(body.right - 7, body.bottom - 5)))


def card_back_surface(width, height):
    """牌背底图：优先用素材卡背（牌堆封面），素材缺失时退回程序绘制。

    素材走 ``assets/cards/back/`` 的稳定 ID（与 general_back 同一套机制），
    返回的是**缓存 Surface**——调用方要改内容请先 ``.copy()``。
    """

    registry = assets_module.get_registry()
    surface = registry.scaled(
        assets_module.card_back_asset_id(),
        (max(1, int(width)), max(1, int(height))),
    )
    if surface is not None:
        return surface
    return theme.card_back_surface(width, height)


def draw_card_back(surface, rect, *, selected=False, candidate=False,
                   dimmed=False, hovered=False):
    rect = pygame.Rect(rect)
    back = card_back_surface(rect.width, rect.height).copy()

    if dimmed:
        veil = pygame.Surface(rect.size, pygame.SRCALPHA)
        veil.fill((*theme.CARD_DISABLED, 140))
        back.blit(veil, (0, 0))

    surface.blit(back, rect.topleft)

    state_name = theme.resolve_state(
        "disabled" if dimmed else None,
        "selected" if selected else None,
        "view_as_candidate" if candidate else None,
        "hover" if hovered else None,
    )
    _draw_state_border(surface, rect, state_name)
    return rect


def draw_equipment_thumb(surface, card, rect, *, size=None, registry=None):
    """装备区的小缩略图：有卡面就按比例放一张，没有就什么都不画。

    返回是否真的画了卡面；调用方（装备槽）在返回 False 时继续用图标 + 文字。
    """

    rect = pygame.Rect(rect)
    if card is None or rect.width < 24 or rect.height < 24:
        return False
    art = card_art(card, rect, registry)
    if art is None:
        return False
    scaled, target = art
    surface.blit(scaled, target.topleft)
    return True


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
