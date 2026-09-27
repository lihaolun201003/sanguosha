"""鼠标悬停提示：把一名角色的武将技能摊开给玩家看。

纯只读展示，不参与任何规则判定：鼠标停在谁的面板上，就显示谁的武将、
体力上限与全部技能说明（含锁定 / 触发式等关键词）。

# 统一入口：``TooltipManager``

所有提示（技能 / 装备 / 角色状态 / 卡牌 / 按钮）都经过同一条延时通道：
**鼠标停稳 200ms 才出现**。在此之前每一帧都直接画，于是"鼠标划过去一下"
就闪一堆提示框，而且不同来源的提示会互相抢屏（同时画两份）。管理器只认
一个"当前悬停目标"，目标一变就重新计时——所以划过不会闪，停住才出。
"""

import pygame

from . import anim_config
from . import theme
from .widgets import draw_panel, place_tooltip

MAX_WIDTH_DESIGN = 380
#: 正文档位：与卡牌提示框、技能提示框保持同一档字号
FONT_BODY_NAME = "small"
PAD = 14
LINE_GAP = 6

#: 悬停多久才弹提示（秒）。太快会闪、太慢会让人以为提示没了。
HOVER_DELAY = 0.20


class TooltipManager:
    """唯一的提示通道：延时出现 + 同时只显示一份。"""

    def __init__(self, delay=HOVER_DELAY):
        self.delay = float(delay)
        self.key = None
        self.elapsed = 0.0

    # ---- 每帧 ----

    def observe(self, key):
        """告知"这一帧鼠标停在什么上面"（``None`` = 没停在可提示的东西上）。

        ``key`` 要能唯一标识目标（``("card", id(card))`` / ``("skill", …)``）：
        换目标就重新计时。
        """

        if key != self.key:
            self.key = key
            self.elapsed = 0.0
        return key

    def update(self, dt):
        if self.key is None:
            self.elapsed = 0.0
            return
        self.elapsed += max(0.0, float(dt))

    def clear(self):
        self.key = None
        self.elapsed = 0.0

    # ---- 查询 ----

    @property
    def ready(self):
        """现在该不该把提示画出来。"""

        return self.key is not None and self.elapsed >= self.delay

    @property
    def alpha(self):
        """淡入用的不透明度（0..255）：出现时不硬切。"""

        if self.key is None or self.delay <= 0:
            return 255 if self.key is not None else 0
        fade = max(0.01, anim_config.duration("hover"))
        ratio = min(1.0, max(0.0, (self.elapsed - self.delay) / fade))
        return int(255 * ratio)


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
        lines.append((name, font, theme.TEXT, 0))
        for piece in wrap_text(description, font, max_width):
            lines.append((piece, font, theme.TEXT_DIM, 1))
    return lines, header


def draw_general_tooltip(surface, game, player, position, metrics, *, avoid=()):
    """在鼠标附近画出该角色的武将与技能；没有武将时返回 False。

    # 位置算法统一到 ``widgets.place_tooltip``

    这里原来自己算位置（贴鼠标右下、越界就翻面、夹进屏幕），**没有任何避让
    名单**：武将提示框会盖住座位面板、装备区、公共牌池，而且内容高于屏幕时
    直接画到画面外。现在与卡牌 / 技能提示框走同一个函数：右侧 → 左侧 → 下方
    → 上方 → 四个角，取第一个"完整在视口内且与 avoid 不相交"的位置。

    ``avoid`` 由调用方给（座位 / 手牌 / 提示条 / 判定面板……），与卡牌提示框
    用的是同一份名单，见 ``Renderer._tooltip_avoid_rects``。
    """

    screen_rect = surface.get_rect()
    width = min(metrics.px(MAX_WIDTH_DESIGN), int(screen_rect.width * 0.34))
    pad = metrics.px(PAD)
    text_width = width - pad * 2

    font = metrics.fonts.get(FONT_BODY_NAME)
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

    anchor = pygame.Rect(position[0], position[1], 1, 1)
    rect = place_tooltip(anchor, (width, height), screen_rect, avoid=avoid)
    draw_panel(
        surface, rect, fill=theme.PANEL_DEEP, border=theme.GOLD,
        border_width=theme.BORDER, radius=metrics.px(theme.RADIUS_MEDIUM),
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
