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
            theme.SHADOW,
            rect.move(shadow_offset, shadow_offset),
            border_radius=radius,
        )

    pygame.draw.rect(surface, fill, rect, border_radius=radius)

    # 顶部高光，让面板有厚度
    highlight = pygame.Surface((rect.width - 4, max(2, rect.height // 3)), pygame.SRCALPHA)
    highlight.fill(theme.PANEL_HIGHLIGHT)
    surface.blit(highlight, (rect.x + 2, rect.y + 2))

    if border_width > 0:
        pygame.draw.rect(surface, border, rect, border_width, border_radius=radius)


def draw_text(surface, text, position, font, color, *, anchor="topleft"):
    rendered = font.render(str(text), True, color)
    rect = rendered.get_rect(**{anchor: position})
    surface.blit(rendered, rect)
    return rect


def place_tooltip(anchor, size, viewport, *, avoid=(), gap=None, margin=None):
    """给提示框选一个位置：不越界、尽量不遮挡 anchor、尽量不压 avoid 区域。

    候选顺序：右 → 左 → 下 → 上 → 右下 → 左下 → 右上 → 左上，再补一轮
    "右 / 左但垂直居中或底对齐"的滑动变体——座位面板贴边时，只按顶端对齐
    找位置很容易整块撞到别的面板。

    取第一个"完整落在视口内且与 avoid 完全不相交"的位置；全都会相交时，
    取相交面积最小的那个（保证至少不越界）。

    所有提示框（卡牌 / 技能 / 座位 / 装备）都走这一个函数，不允许各自写死坐标。
    """

    anchor = pygame.Rect(anchor)
    viewport = pygame.Rect(viewport)
    width, height = int(size[0]), int(size[1])
    g = 14 if gap is None else int(gap)
    m = 8 if margin is None else int(margin)
    # anchor 自己也算"必须避开"：否则夹取回视口后可能正好压在它身上。
    avoid = [pygame.Rect(item) for item in avoid] + [anchor]

    candidates = (
        (anchor.right + g, anchor.y),
        (anchor.left - g - width, anchor.y),
        (anchor.centerx - width // 2, anchor.bottom + g),
        (anchor.centerx - width // 2, anchor.top - g - height),
        (anchor.right + g, anchor.bottom + g),
        (anchor.left - g - width, anchor.bottom + g),
        (anchor.right + g, anchor.top - g - height),
        (anchor.left - g - width, anchor.top - g - height),
        # 滑动变体：右侧 / 左侧，但纵向与 anchor 居中或底对齐。
        (anchor.right + g, anchor.centery - height // 2),
        (anchor.left - g - width, anchor.centery - height // 2),
        (anchor.right + g, anchor.bottom - height),
        (anchor.left - g - width, anchor.bottom - height),
    )

    best = None
    best_overlap = None
    for x, y in candidates:
        x = max(viewport.left + m, min(int(x), viewport.right - width - m))
        y = max(viewport.top + m, min(int(y), viewport.bottom - height - m))
        rect = pygame.Rect(x, y, width, height)
        if not viewport.contains(rect):
            continue
        overlap = 0
        for item in avoid:
            clipped = rect.clip(item)
            overlap += clipped.width * clipped.height
        if best is None or overlap < best_overlap:
            best, best_overlap = rect, overlap
            if overlap == 0:
                break

    if best is not None:
        return best
    # 视口比提示框还小：退回左上角并夹在视口内。
    return pygame.Rect(
        viewport.left + m, viewport.top + m,
        min(width, max(1, viewport.width - m * 2)),
        min(height, max(1, viewport.height - m * 2)),
    )


def draw_state_dim(surface, rect, state_name):
    """按视觉状态画"压暗层"（``dim`` 非 0 时才有内容）。

    **必须画在元素内容之上**：原来压暗与描边一起画在内容之前，于是座位变暗
    只暗了底板——头像、名字、血量、装备原封不动，"非法目标"与"阵亡"看起来
    只暗了一半。拆成两个函数之后顺序由调用方决定：

        底板 → 内容 → draw_state_dim → draw_state_border → 角标

    返回是否真的画了东西。
    """

    state = theme.visual_state(state_name)
    dim = int(state.get("dim") or 0)
    if dim <= 0:
        return False
    rect = pygame.Rect(rect)
    veil = pygame.Surface(rect.size, pygame.SRCALPHA)
    veil.fill((*theme.VEIL, dim))
    surface.blit(veil, rect.topleft)
    return True


def draw_state_border(surface, rect, state_name, *, radius=None, alpha=132,
                      hovered=False, label=False, metrics=None, inset=None):
    """按视觉状态画描边 + 外发光（唯一入口，组件不再自己配颜色与宽度）。

    ``rect`` 是元素的实际边界：发光向外扩散，但点击区域仍以 ``rect`` 为准，
    所以更亮更大的高亮不会改变真实的可点范围。

    * ``hovered`` —— 叠加**悬停层**。悬停与语义状态正交：鼠标停在"当前回合"
      角色上时金边与角标都保留、外面再多一圈柔光；停在"非法目标"上时压暗
      照旧。这修掉了原来 hover 把语义状态洗掉的问题。
    * ``label`` —— 是否画状态角标（座位要，小卡牌不要）。
    * ``inset`` —— 角标离右上角的距离（默认 ``metrics.px(7)``）。
    """

    state = theme.visual_state(state_name)
    rect = pygame.Rect(rect)
    if radius is None:
        radius = theme.RADIUS_PANEL

    # 悬停层先画：它更外圈、更柔，语义描边压在上面才清晰。
    if hovered:
        layer = theme.HOVER_LAYER
        halo = int(layer.get("glow_width") or 0)
        border = theme.glow_border(
            rect.size, layer["border"], int(layer["width"]), halo, radius, alpha)
        surface.blit(border, (rect.x - halo, rect.y - halo))

    stroke = int(state.get("width") or 0)
    if stroke > 0:
        halo = int(state.get("glow_width") or 0)
        border = theme.glow_border(
            rect.size, state["border"], stroke, halo, radius, alpha)
        surface.blit(border, (rect.x - halo, rect.y - halo))

    if label and metrics is not None:
        draw_state_label(surface, rect, state_name, metrics.fonts, metrics,
                         inset=inset)
    return rect


# ==================================================
# 模态框 / 演出面板的统一外观
#
# 原来 14 个弹层各写各的：遮罩 alpha 有 168/170/172/176/190 五档、填充有
# PANEL 与 PANEL_DEEP 两套、圆角有 10/12/16/18/22 五种。全部收到这里。
# ==================================================


def draw_modal_veil(surface, metrics, *, alpha=None):
    """全屏压暗遮罩（模态框专用）。``alpha`` 不给就用统一值。"""

    veil = pygame.Surface((metrics.screen_w, metrics.screen_h), pygame.SRCALPHA)
    veil.fill((*theme.OVERLAY_DIM, theme.MODAL_VEIL_ALPHA if alpha is None else int(alpha)))
    surface.blit(veil, (0, 0))
    return veil


def draw_modal_panel(surface, rect, metrics, *, radius=None, width=None,
                     fill=None, border=None, glow=None):
    """模态框 / picker 的面板外观（统一填充、描边、圆角、投影）。"""

    rect = pygame.Rect(rect)
    return draw_panel(
        surface, rect,
        fill=theme.MODAL_FILL if fill is None else fill,
        border=theme.MODAL_BORDER if border is None else border,
        radius=theme.MODAL_RADIUS if radius is None else radius,
        border_width=theme.MODAL_WIDTH if width is None else width,
        glow=glow,
    )


def draw_demo_panel(surface, rect, *, tone=None, radius=None, alpha=244,
                    metrics=None):
    """演出面板（判定 / 技能提示 / 火攻）的统一外观。

    与模态框的区别：**不铺全屏遮罩**（演出不该把牌桌整个压黑），面板自身
    带一点透明、金色描边、内圈语义色细线。``tone`` 给语义色（判定 tone /
    技能类型色）时画内圈。
    """

    rect = pygame.Rect(rect)
    layer = pygame.Surface(rect.size, pygame.SRCALPHA)
    local = layer.get_rect()
    radius = theme.RADIUS_LARGE if radius is None else radius
    fill = (*theme.PANEL_DEEP, alpha)
    pygame.draw.rect(layer, fill, local, border_radius=radius)
    if tone is not None:
        pygame.draw.rect(layer, (*tone, 150), local.inflate(-6, -6),
                         theme.BORDER_THIN, border_radius=max(2, radius - 4))
    pygame.draw.rect(layer, theme.MODAL_BORDER, local, theme.BORDER_MODAL,
                     border_radius=radius)
    surface.blit(layer, rect.topleft)
    return rect


def draw_state_label(surface, rect, state_name, font_set, metrics, *, inset=None,
                     right=None, top=None):
    """在元素右上角画状态角标（如「当前回合」「目标」「已选」）。

    ``right`` 给定时用它作为角标右边界、``top`` 作为上边界——座位顶部要并排
    挂两三个角标（角色状态 / 操作态 / 横置）时，调用方依次向左排。
    """

    state = theme.visual_state(state_name)
    label = state.get("label")
    if not label:
        return None
    font = font_set.get("micro")
    rendered = font.render(label, True, theme.INK)
    rect = pygame.Rect(rect)
    offset = metrics.px(6) if inset is None else inset
    badge = pygame.Rect(
        0, 0,
        rendered.get_width() + metrics.px(16),
        rendered.get_height() + metrics.px(8),
    )
    badge.topright = (rect.right - offset if right is None else right,
                      rect.y + offset if top is None else top)
    pygame.draw.rect(surface, state["border"], badge, border_radius=metrics.px(7))
    surface.blit(rendered, rendered.get_rect(center=badge.center))
    return badge


def ellipsize_text(text, font, max_width, *, suffix="…"):
    """按真实渲染宽度截断，不按字符数硬切。

    ``font.size()`` 才是中文字符串宽度的唯一可靠来源，所以这里逐字测量，
    放不下时补省略号；连省略号都放不下就返回空串。
    """

    text = str(text)
    if max_width <= 0:
        return ""
    if font.size(text)[0] <= max_width:
        return text

    ellipsis_width = font.size(suffix)[0]
    if ellipsis_width >= max_width:
        return ""

    result = ""
    for char in text:
        if font.size(result + char)[0] + ellipsis_width > max_width:
            break
        result += char
    return (result + suffix) if result else ""


def wrap_text(text, font, max_width, *, max_lines=None):
    """按真实渲染宽度折行（中文逐字、英文按词尽量不断开）。

    ``max_lines`` 给定时，最后一行用省略号收尾——详情面板高度固定，
    宁可截断也不能让文字压出面板外。
    """

    text = str(text or "")
    if max_width <= 0:
        return []
    lines = []
    current = ""
    for raw in (text.splitlines() or [""]):
        current = ""
        for char in raw:
            probe = current + char
            if current and font.size(probe)[0] > max_width:
                lines.append(current)
                current = char
                if max_lines is not None and len(lines) >= max_lines:
                    lines = lines[:max_lines]
                    lines[-1] = ellipsize_text(lines[-1] + current, font, max_width)
                    return lines
            else:
                current = probe
        lines.append(current)
        if max_lines is not None and len(lines) >= max_lines:
            lines = lines[:max_lines]
            lines[-1] = ellipsize_text(lines[-1], font, max_width)
            return lines
    while lines and not lines[-1] and len(lines) > 1:
        lines.pop()
    return lines


def fit_text(text, font_set, *, max_width, preferred, fallback="micro"):
    """先试首选字号，逐级降级；都放不下才省略。返回 (文本, 字体)。"""

    order = [
        preferred,
        "seat_meta", "small", "tiny", "micro",
    ]
    seen = []
    for name in order:
        if name in seen or name not in theme.FONT_SIZES:
            continue
        seen.append(name)
        font = font_set.get(name)
        if font.size(str(text))[0] <= max_width:
            return str(text), font
        if name == fallback:
            return ellipsize_text(text, font, max_width), font
    font = font_set.get(fallback)
    return ellipsize_text(text, font, max_width), font


def draw_center_text(surface, text, center, font, color):
    return draw_text(surface, text, center, font, color, anchor="center")


#: 按钮配色。四档的"重量"靠金色的多少区分：primary 是实心金，
#: secondary 是暖棕面板 + 金边，ghost 只有暗金细边，danger 用暗红。
#: 全部取自 theme 的暖色板，不再有蓝灰档。
BUTTON_STYLES = {
    "primary": {
        "fill": (150, 116, 60),
        "fill_hover": (180, 142, 74),
        "fill_pressed": (118, 90, 46),
        "border": theme.GOLD_BRIGHT,
        "text": theme.TEXT,
    },
    "danger": {
        "fill": (128, 48, 42),
        "fill_hover": (158, 62, 54),
        "fill_pressed": (100, 38, 34),
        "border": (226, 140, 120),
        "text": (252, 238, 234),
    },
    "secondary": {
        "fill": theme.PANEL_ALT,
        "fill_hover": (92, 80, 66),
        "fill_pressed": theme.PANEL_ALT_PRESSED,
        "border": theme.GOLD,
        "text": theme.TEXT,
    },
    "ghost": {
        "fill": theme.PANEL_DEEP,
        "fill_hover": theme.PANEL,
        "fill_pressed": theme.PANEL_SUNKEN,
        "border": theme.GOLD_DIM,
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
            border = theme.CARD_EMPTY_BORDER
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

        pygame.draw.rect(surface, theme.SHADOW, self.rect.move(0, 3), border_radius=self.radius)
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


def draw_badge(surface, center, text, font, *, fill=theme.TAG_NEUTRAL_BG, border=theme.GOLD_DIM, text_color=theme.TEXT, radius=8, padding=(8, 3)):
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
        full_color = theme.HEAL
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
            pygame.draw.circle(surface, theme.SEAT_AVATAR, center, radius, 1)
        else:
            pygame.draw.circle(surface, theme.PANEL_SUNKEN, center, radius)
            pygame.draw.circle(surface, theme.CARD_EMPTY_BORDER, center, radius, 1)

    return origin[0] + max_hp * spacing if not vertical else origin[0]
