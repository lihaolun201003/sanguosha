"""鼠标悬停提示：把一名角色的武将技能摊开给玩家看。

纯只读展示，不参与任何规则判定：鼠标停在谁的面板上，就显示谁的武将、
体力上限与全部技能说明（含锁定 / 触发式等关键词）。
"""

import pygame

from . import theme
from .widgets import draw_panel

MAX_WIDTH_DESIGN = 380
PAD = 14
LINE_GAP = 6


def wrap_text(text, font, max_width):
    """中文没有空格，按字符宽度逐字换行。"""

    lines = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        probe = current + char
        if current and font.size(probe)[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = probe
    if current:
        lines.append(current)
    return lines or [""]


def skill_rows(game, player):
    """该角色当前的技能条目 [(技能名, 描述), ...]。"""

    general = game.generals.get(player.general_id)
    if general is None:
        return ()
    rows = []
    for skill_id in general.skill_ids:
        definition = game.skill_registry.get(skill_id)
        if definition is None:
            continue
        rows.append((definition.name, definition.description or ""))
    return tuple(rows)


def build_lines(game, player, font, max_width):
    """把标题与技能说明排成可绘制的行。

    返回 ``[(text, font, color, indent), ...]``。
    """

    general = game.generals.get(player.general_id)
    title_font = font
    lines = []
    if general is None:
        lines.append(("尚未选择武将", title_font, theme.TEXT_DIM, 0))
        return lines, ""

    header = "%s · %s · %d 体力" % (
        general.name, general.kingdom_name, general.max_hp)
    lines.append((header, title_font, theme.GOLD_BRIGHT, 0))

    rows = skill_rows(game, player)
    if not rows:
        lines.append(("无技能", font, theme.TEXT_DIM, 0))
        return lines, header

    for name, description in rows:
        lines.append(("【%s】" % name, font, theme.TEXT, 0))
        for piece in wrap_text(description, font, max_width):
            lines.append((piece, font, theme.TEXT_DIM, 1))
    return lines, header


def draw_general_tooltip(surface, game, player, position, metrics):
    """在鼠标附近画出该角色的武将与技能；没有武将时返回 False。"""

    screen_rect = surface.get_rect()
    width = min(metrics.px(MAX_WIDTH_DESIGN), int(screen_rect.width * 0.34))
    pad = metrics.px(PAD)
    text_width = width - pad * 2

    font = metrics.fonts.get("small")
    lines, _header = build_lines(game, player, font, text_width)
    if not lines:
        return False

    # 屏幕太矮时截断末尾的说明行，避免面板超出画面。
    gap = metrics.px(LINE_GAP)
    row_height = max(1, font.get_height() + gap)
    max_lines = max(3, (screen_rect.height - pad * 2 - metrics.px(24)) // row_height)
    if len(lines) > max_lines:
        lines = lines[:max_lines]

    line_heights = [font.get_height() for _text, _font, _color, _indent in lines]
    height = pad * 2 + sum(line_heights) + gap * (len(lines) - 1)

    x, y = position
    # 默认贴在鼠标右下；靠近边缘时翻到另一侧。
    left = x + metrics.px(18)
    if left + width > screen_rect.right - metrics.px(8):
        left = x - width - metrics.px(18)
    top = y + metrics.px(18)
    if top + height > screen_rect.bottom - metrics.px(8):
        top = y - height - metrics.px(18)
    left = max(screen_rect.left + metrics.px(8), left)
    top = max(screen_rect.top + metrics.px(8), top)

    rect = pygame.Rect(left, top, width, height)
    draw_panel(
        surface, rect, fill=theme.PANEL_DEEP, border=theme.GOLD,
        border_width=theme.BORDER, radius=metrics.px(12),
    )

    cursor_y = rect.y + pad
    for (text, line_font, color, indent), line_height in zip(lines, line_heights):
        rendered = line_font.render(text, True, color)
        surface.blit(
            rendered,
            (rect.x + pad + indent * metrics.px(12), cursor_y),
        )
        cursor_y += line_height + gap
    return True
