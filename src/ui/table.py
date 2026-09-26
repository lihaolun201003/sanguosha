"""Central table: draw pile, discard pile, played cards, judge and turn banners."""

import math

import pygame

from . import cards as card_draw
from . import layout
from . import theme
from .widgets import draw_panel, ellipsize_text


# ==================================================
# 指向箭头（谁对谁做了什么）
# ==================================================
#
# 纯表现：箭头数据来自引擎事件的 targets，这里只负责画。箭头两端裁到
# 座位面板的边缘，所以不会遮住面板里的血量 / 身份 / 手牌数。
#
# 走线规则（本轮玩家反馈："太乱、有些太短、上下两个应该是先向右再向下再向左"）：
#
# * 上下两个面板（同一列，例如顶端座位 ↔ 底部真人条、左侧栏里上下相邻的两个
#   面板）→ 从侧面的走廊绕过去："先横向出去 → 纵向走 → 再横向进入目标"；
#   向下走右侧走廊、向上走左侧走廊，两条方向的箭头不会重叠。
# * 左右两个面板（同一行）→ 从上下走廊绕过去。
# * 其余（斜对角）保持直线。
#
# 不这样走的话，"上下相邻的两个面板"之间只有 40 来像素的直连线（又短又看不出
# 指向），顶端座位 → 真人条则会走一条穿过中央出牌位的直线。

ARROW_WIDTH_DESIGN = 7
ARROW_HEAD_SCALE = 3.0
#: 小于这个设计距离的两个面板算"紧挨着"（近距离折线兜底：斜对角但贴在一起）。
ARROW_CLOSE_DISTANCE = 200
#: 折线绕出去的距离（设计像素）。
ARROW_DETOUR_GAP = 46
#: 中心线对齐的判定阈值（设计像素）：两面板中心在这个范围内才算"同一列/行"。
#: 取得比较紧是有意的——只有真的上下/左右对齐时才走走廊，斜对角保持直线，
#: 免得为了折线反而横穿中央出牌位。
ARROW_AXIS_TOLERANCE = 60
#: 一对面板与桌面中心的横向距离小于它时算"居中的一对"（顶端座位 ↔ 真人条）。
ARROW_CENTER_BAND = 150
#: 来/回两条箭头在**同一条走廊**上错开的车道距离（设计像素）：
#: 上下相邻的两个面板一上一下都用同一侧走廊，不错开就会完全重叠。
ARROW_LANE_GAP = 28
#: 走廊离桌面边缘的内缩量（设计像素）：贴着桌面边缘走，只压到提示条 / 手牌区
#: 的边框，不压到里面的文字。
ARROW_CORRIDOR_INSET = 8


def draw_action_arrows(surface, arrows, table_layout, metrics):
    """把当前生效的指向箭头画到桌面上。"""

    if not arrows:
        return 0
    drawn = 0
    for arrow in arrows:
        alpha = arrow.alpha
        if alpha <= 0:
            continue
        source_rect = table_layout.any_seat_rect(arrow.source)
        target_rect = table_layout.any_seat_rect(arrow.target)
        if source_rect is None or target_rect is None:
            continue
        path = arrow_path(source_rect, target_rect, metrics)
        if path is None:
            start, end = arrow.endpoints(source_rect, target_rect)
            path = [start, end]
        # 入场阶段沿路径"长出来"：只画到 progress 对应的位置，箭头随尖端前进。
        points = truncate_path(path, arrow.progress)
        if len(points) < 2:
            continue
        _draw_arrow_path(surface, points, arrow.color, alpha, metrics)
        drawn += 1
    return drawn


def arrow_path(source_rect, target_rect, metrics):
    """折线箭头路径；返回 None 表示"用两端裁边的直线"。

    * 同一列（上下两个面板）→ 走侧面走廊：向右/向左出去，纵向走，再横向进入；
    * 同一行（左右两个面板）→ 走桌面上/下走廊：出去、横向走、再纵向进入；
    * 斜对角 → 直线（两端裁到面板边缘）；贴在一起的斜对角仍走老的侧面绕行。
    """

    sx, sy = source_rect.center
    tx, ty = target_rect.center
    horizontal = abs(tx - sx)
    vertical = abs(ty - sy)
    tolerance = metrics.px(ARROW_AXIS_TOLERANCE)

    if horizontal <= tolerance and vertical > tolerance:
        corridor = _vertical_corridor(source_rect, target_rect, metrics, down=ty > sy)
        return _elbow_vertical(source_rect, target_rect, corridor)
    if vertical <= tolerance and horizontal > tolerance:
        return _elbow_horizontal(
            source_rect, target_rect,
            _horizontal_corridor(metrics, right=tx > sx))
    if max(horizontal, vertical) < metrics.px(ARROW_CLOSE_DISTANCE):
        return _side_detour(source_rect, target_rect, metrics)
    return None


def _vertical_corridor(source_rect, target_rect, metrics, *, down):
    """上下两面板绕出去的**纵向走廊** x 坐标。"""

    central = metrics.central
    gap = metrics.px(ARROW_DETOUR_GAP)
    band = metrics.px(ARROW_CENTER_BAND)
    center = (source_rect.centerx + target_rect.centerx) / 2.0
    lane = metrics.px(ARROW_LANE_GAP)
    if center < central.centerx - band:
        # 偏左的一对（左侧栏上下两个、左上与顶端）：从它们的**右侧**绕，
        # 于是走线是"向右 → 向下 → 向左"；向上那条换一条车道，两条不重叠。
        base = max(source_rect.right, target_rect.right) + gap
        return base if down else base + lane
    if center > central.centerx + band:
        # 偏右的一对：镜像，从左侧绕（向左 → 向下 → 向右）。
        base = min(source_rect.left, target_rect.left) - gap
        return base if down else base - lane
    # 居中（顶端座位 ↔ 底部真人条）：走桌面内侧走廊。向下走右侧、向上走左侧，
    # 一上一下两条箭头各占一边，不会叠在一起。
    if down:
        return central.right - metrics.px(ARROW_CORRIDOR_INSET)
    return central.left + metrics.px(ARROW_CORRIDOR_INSET)


def _horizontal_corridor(metrics, *, right):
    """左右两面板绕出去的**横向走廊** y 坐标。

    走桌面内侧（中央出牌位上方那一条带子）：座位环的上下缝隙只有几像素，
    把箭头塞进去等于看不见。向右的一对走上边、向左的一对稍低一点，两条
    方向的箭头不会叠在一条线上。
    """

    inset = metrics.px(ARROW_CORRIDOR_INSET)
    base = metrics.central.top + metrics.px(44)
    return base if right else base + metrics.px(30)


def _side_detour(source_rect, target_rect, metrics):
    """贴在一起的两个面板：从侧面绕出去再折回来（原有的近距离兜底）。"""

    gap = metrics.px(ARROW_DETOUR_GAP)
    sx, sy = source_rect.center
    tx, ty = target_rect.center
    if (sx + tx) / 2.0 < metrics.screen_w / 2.0:
        x_out = max(source_rect.right, target_rect.right) + gap
        start = (source_rect.right, sy)
        end = (target_rect.right, ty)
    else:
        x_out = min(source_rect.left, target_rect.left) - gap
        start = (source_rect.left, sy)
        end = (target_rect.left, ty)
    return [start, (x_out, sy), (x_out, ty), end]


def _elbow_vertical(source_rect, target_rect, corridor):
    """先横向出去 → 纵向走 → 再横向进入目标（或直接从上下方进入宽面板）。

    宽面板（真人状态条 / 顶端座位）如果横跨走廊，就直接从它的上/下边缘进出：
    这样不会有"先往反方向挪一点点"的短残段。
    """

    sx, sy = source_rect.center
    tx, ty = target_rect.center
    if source_rect.left <= corridor <= source_rect.right:
        points = [(corridor, source_rect.bottom if ty > sy else source_rect.top)]
    else:
        points = [
            (source_rect.right if corridor >= sx else source_rect.left, sy),
            (corridor, sy),
        ]
    if target_rect.left <= corridor <= target_rect.right:
        points.append((corridor, target_rect.top if ty > sy else target_rect.bottom))
        return points
    points.append((corridor, ty))
    points.append((target_rect.right if corridor >= tx else target_rect.left, ty))
    return points


def _elbow_horizontal(source_rect, target_rect, corridor):
    """先纵向出去 → 横向走 → 再纵向进入目标。"""

    sx, sy = source_rect.center
    tx, ty = target_rect.center
    if source_rect.top <= corridor <= source_rect.bottom:
        points = [(source_rect.right if tx > sx else source_rect.left, corridor)]
    else:
        points = [
            (sx, source_rect.bottom if corridor >= sy else source_rect.top),
            (sx, corridor),
        ]
    if target_rect.top <= corridor <= target_rect.bottom:
        points.append((target_rect.left if tx > sx else target_rect.right, corridor))
        return points
    points.append((tx, corridor))
    points.append((tx, target_rect.bottom if corridor >= ty else target_rect.top))
    return points


def truncate_path(points, ratio):
    """把折线截到总长的前 ``ratio`` 段（箭头"长出来"的动画用）。"""

    ratio = max(0.0, min(1.0, float(ratio)))
    if ratio >= 1.0 or len(points) < 2:
        return list(points)
    if ratio <= 0.0:
        return []
    total = 0.0
    lengths = []
    for index in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[index], points[index + 1]
        length = math.hypot(x2 - x1, y2 - y1)
        lengths.append(length)
        total += length
    if total <= 0:
        return list(points)
    remaining = total * ratio
    result = [points[0]]
    for index, length in enumerate(lengths):
        if remaining <= length:
            start, end = points[index], points[index + 1]
            if length > 0:
                t = remaining / length
                result.append((start[0] + (end[0] - start[0]) * t,
                               start[1] + (end[1] - start[1]) * t))
            return result
        remaining -= length
        result.append(points[index + 1])
    return result


def _draw_arrow_path(surface, points, color, alpha, metrics):
    """折线箭头：与外光 / 内光 / 主线三层直线保持同一套视觉。"""

    width = max(3, metrics.px(ARROW_WIDTH_DESIGN))
    ratio = max(0.0, min(1.0, alpha / 255.0))
    outer = _blend(color, theme.BG_TABLE, 0.28 * ratio)
    inner = _blend(color, theme.BG_TABLE, 0.55 * ratio)
    for layer, layer_width in (
        (outer, width + metrics.px(9)),
        (inner, width + metrics.px(4)),
        (color, width),
    ):
        pygame.draw.lines(surface, layer, False, points, layer_width)
    _draw_arrow_head(surface, points[-2], points[-1], color, width, metrics)


def _draw_arrow_head(surface, start, end, color, width, metrics):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1:
        return
    ux, uy = dx / length, dy / length
    size = max(metrics.px(12), width * ARROW_HEAD_SCALE)
    base = (end[0] - ux * size, end[1] - uy * size)
    nx, ny = -uy, ux
    half = size * 0.55
    pygame.draw.polygon(surface, color, [
        end,
        (base[0] + nx * half, base[1] + ny * half),
        (base[0] - nx * half, base[1] - ny * half),
    ])


def _blend(color, background, ratio):
    ratio = max(0.0, min(1.0, ratio))
    return tuple(int(channel * ratio + back * (1 - ratio))
                 for channel, back in zip(color, background))


def draw_action_banner(surface, metrics, info):
    """动作横幅：谁对谁使用了什么（与卡牌动画同步停留）。

    画在中央牌桌上方、回合横幅的上方，处于 FX overlay 层，
    因此不会被座位面板或按钮盖住。
    """

    if not info:
        return
    alpha = info.get("alpha", 255)
    if alpha <= 0:
        return
    fonts = metrics.fonts
    text = info.get("text", "")
    if not text:
        return
    rendered = fonts.get("large").render(text, True, (250, 236, 206))
    plate = pygame.Surface(
        (rendered.get_width() + metrics.px(44), rendered.get_height() + metrics.px(16)),
        pygame.SRCALPHA,
    )
    pygame.draw.rect(plate, (*theme.INK, 190), plate.get_rect(),
                     border_radius=metrics.px(12))
    pygame.draw.rect(plate, (*theme.GOLD, 200), plate.get_rect(), 2,
                     border_radius=metrics.px(12))
    plate.blit(rendered, rendered.get_rect(center=plate.get_rect().center))
    plate.set_alpha(alpha)
    rect = plate.get_rect(midtop=(metrics.screen_w // 2, metrics.central.y + metrics.px(6)))
    surface.blit(plate, rect)
    return rect


def draw_story_banner(surface, metrics, info):
    """结算提示条：判定结果 / 延时锦囊生效 / 阶段跳过。

    与动作横幅同一层、同一种观感，但多一行"结论"（跳过出牌阶段 / 受到 3 点
    雷电伤害），并且按语义上色（有利 / 不利 / 中性）。它是**结论必须被看到**
    的那一条，所以由演出队列按顺序播放，不与人抢画面。
    """

    if not info:
        return
    alpha = info.get("alpha", 255)
    if alpha <= 0:
        return
    fonts = metrics.fonts
    title = info.get("text", "")
    if not title:
        return
    detail = info.get("detail", "")
    tone = info.get("tone", "")
    tone_color = {
        "positive": theme.JUDGE_POSITIVE,
        "negative": theme.JUDGE_NEGATIVE,
        "phase": theme.TARGET_YELLOW,
        "turn": theme.GOLD_BRIGHT,
    }.get(tone, theme.GOLD)

    title_font = fonts.get("large")
    rendered = title_font.render(title, True, (250, 236, 206))
    detail_rendered = None
    if detail:
        detail_font = fonts.get("small")
        detail_rendered = detail_font.render(detail, True, tone_color)

    width = max(rendered.get_width(), detail_rendered.get_width() if detail_rendered else 0)
    height = rendered.get_height() + metrics.px(16)
    if detail_rendered is not None:
        height += detail_rendered.get_height() + metrics.px(6)
    plate = pygame.Surface(
        (width + metrics.px(48), height + metrics.px(16)), pygame.SRCALPHA)
    bounds = plate.get_rect()
    pygame.draw.rect(plate, (*theme.INK, 205), bounds, border_radius=metrics.px(12))
    pygame.draw.rect(plate, (*tone_color, 220), bounds, 2, border_radius=metrics.px(12))
    plate.blit(rendered, rendered.get_rect(
        midtop=(bounds.centerx, metrics.px(8))))
    if detail_rendered is not None:
        plate.blit(detail_rendered, detail_rendered.get_rect(
            midtop=(bounds.centerx, metrics.px(8) + rendered.get_height() + metrics.px(6))))
    plate.set_alpha(alpha)
    rect = plate.get_rect(midtop=(metrics.screen_w // 2, metrics.central.y + metrics.px(6)))
    surface.blit(plate, rect)
    return rect


def draw_deal_flights(surface, flights, metrics):
    """开局发牌：还在飞行中的牌用牌背绘制，飞到位置后由手牌区接手。"""

    if not flights:
        return 0
    drawn = 0
    size = metrics.hand_card_size()
    for flight in flights:
        if flight.done:
            continue
        x, y = flight.position
        rect = pygame.Rect(int(x), int(y), size[0], size[1])
        card_draw.draw_card_back(surface, rect)
        drawn += 1
    return drawn


def draw_piles(surface, game, metrics, *, highlight_draw=False, highlight_discard=False,
               hover=None, hidden_ids=()):
    """牌堆 / 弃牌堆：默认只占一个小图标 + 数量，把中央让给结算与箭头。

    **弃牌堆永远用卡背当封面**（Phase 11.5 §12）：牌桌上常驻的那一份是"一摞
    牌"的视觉，不是"最近一张弃牌"的展示位——随手暴露最顶弃牌会把别人刚弃掉
    的牌摊在桌面上。想要看具体弃了什么，悬停 / 详情里有放大预览（保留原逻辑）；
    数量仍然由下方标签给出。

    单机、房主、游客共用这一个函数，因此三个视角的封面样式天然一致。

    ``hover`` 是 ``"draw"`` / ``"discard"`` / ``None``：悬停时弹出放大预览。
    ``hidden_ids`` 是在飞行中的牌（``effects.animating_card_ids()``）：弃牌堆
    顶牌若还在飞向弃牌堆的路上，就先显示它下面的一张——避免同一张牌同时
    出现在弃牌堆与飞行副本两处。
    """

    fonts = metrics.fonts
    small = metrics.px(58)
    hidden = hidden_ids or ()

    draw_rect = metrics.to_screen(layout.DRAW_PILE_RECT)
    count = pile_count(game, "draw")
    if count:
        card_draw.draw_card_back(surface, draw_rect, candidate=highlight_draw)
    else:
        pygame.draw.rect(surface, theme.PANEL_SUNKEN, draw_rect,
                         border_radius=theme.RADIUS_CARD)
        pygame.draw.rect(surface, theme.GOLD_DIM, draw_rect, 2,
                         border_radius=theme.RADIUS_CARD)
    label = fonts.get("micro").render("牌堆 " + str(count), True, theme.TEXT_DIM)
    surface.blit(label, label.get_rect(
        midtop=(draw_rect.centerx, draw_rect.bottom + metrics.px(4))))

    discard_rect = metrics.to_screen(layout.DISCARD_PILE_RECT)
    top = discard_top_card(game.deck.discard_pile, hidden)
    # 封面统一走卡背：有牌时用正常亮度（表示"这摞牌有东西"），空堆压暗。
    card_draw.draw_card_back(surface, discard_rect,
                             candidate=highlight_discard, dimmed=top is None)
    label = fonts.get("small").render(
        "弃牌 " + str(pile_count(game, "discard")), True, theme.TEXT_DIM)
    surface.blit(label, label.get_rect(
        midtop=(discard_rect.centerx, discard_rect.bottom + metrics.px(4))))

    # 悬停预览：只在鼠标停在图标上时占用更大的画面（这里才展示最近弃牌）。
    if hover == "draw" and count:
        preview = pygame.Rect(0, 0, metrics.px(120), metrics.px(168))
        preview.midbottom = (draw_rect.centerx, draw_rect.top - metrics.px(8))
        draw_panel(surface, preview.inflate(metrics.px(10), metrics.px(10)),
                   fill=theme.PANEL_DEEP, border=theme.GOLD, border_width=2,
                   radius=metrics.px(10), shadow=False)
        card_draw.draw_card_back(surface, preview)
    elif hover == "discard" and top is not None:
        preview = pygame.Rect(0, 0, metrics.px(120), metrics.px(168))
        preview.midbottom = (discard_rect.centerx, discard_rect.top - metrics.px(8))
        draw_panel(surface, preview.inflate(metrics.px(10), metrics.px(10)),
                   fill=theme.PANEL_DEEP, border=theme.GOLD, border_width=2,
                   radius=metrics.px(10), shadow=False)
        card_draw.draw_card(surface, top, preview, fonts)
    return draw_rect, discard_rect


def discard_top_card(pile, hidden_ids=()):
    """弃牌堆最上面一张**不在飞行中**的牌（正被动画表现的牌先不算）。"""

    hidden = hidden_ids or ()
    for card in reversed(pile):
        if id(card) not in hidden:
            return card
    return None


def pile_count(game, kind):
    """牌堆 / 弃牌堆的**权威张数**。

    联网客户端的 ``deck`` 只是只读替身：它带回来的弃牌堆只有末尾若干张
    （够画顶牌），真正的总数由 ``discard_count`` / ``draw_count`` 给出。
    """

    deck = getattr(game, "deck", None)
    if kind == "draw":
        return int(getattr(deck, "draw_count", None)
                   or len(getattr(deck, "draw_pile", ()) or ()))
    return int(getattr(deck, "discard_count", None)
               or len(getattr(deck, "discard_pile", ()) or ()))


def pile_at_position(position, metrics):
    """鼠标是否停在牌堆 / 弃牌堆图标上（用于悬停预览）。"""

    if metrics.to_screen(layout.DRAW_PILE_RECT).collidepoint(position):
        return "draw"
    if metrics.to_screen(layout.DISCARD_PILE_RECT).collidepoint(position):
        return "discard"
    return None


def draw_table_cards(surface, game, metrics, hidden_ids=()):
    """桌面主体卡（Action Display）：当前动作 / 响应牌停在中央。

    落位来自 ``game.table_cards``：命名落位（"table_card" / "response_card"）
    每帧按当前布局解析，所以 resize / F11 之后主体卡自动落到新的中央 anchor；
    ``hidden_ids`` 是正在被移动动画表现的牌，静态这一份不再重复画。
    """

    fonts = metrics.fonts
    hidden = hidden_ids or ()
    for card, rect_data in game.table_cards:
        if id(card) in hidden:
            continue
        card_draw.draw_card(
            surface, card, layout.resolve_card_rect(metrics, rect_data), fonts)


def pool_hover_index(rects, mouse_pos):
    """鼠标所在的公共牌下标（顶层优先）；不在任何牌上时返回 None。"""

    if mouse_pos is None:
        return None
    for index in range(len(rects) - 1, -1, -1):
        if rects[index].collidepoint(mouse_pos):
            return index
    return None


def pool_hover_rect(rect, metrics=None):
    """悬停时的公共牌矩形：向上放大 + 上浮，底边中心保持不动。

    锚点固定在底边，所以放大只向上展开——不会盖住下方的提示区，鼠标
    停留在原位置时仍然指着同一张牌（命中判定始终用未放大的布局矩形）。
    """

    scale = layout.PUBLIC_POOL_HOVER_SCALE
    lift = metrics.px(layout.PUBLIC_POOL_HOVER_LIFT) if metrics is not None else 0
    lifted = pygame.Rect(0, 0, max(1, int(round(rect.width * scale))),
                         max(1, int(round(rect.height * scale))))
    lifted.midbottom = (rect.centerx, rect.bottom - lift)
    return lifted


def draw_pool(surface, game, entries, rects, *, is_candidate, is_selected, metrics=None,
              mouse_pos=None, hidden_ids=(), face_down_ids=()):
    """Public card area used by Wugu and 'pick a card from another player'.

    ``entries`` is a ``[(card, key)]`` list; the key carries the equipment slot
    so equipment candidates highlight and hit-test like hand cards.

    公共牌是公开信息：一律走统一的 ``cards.draw_card`` 真实卡面（花色点数仍由
    真实 Card 数据画在最上层），并提供与手牌一致的悬停反馈——当前牌放大上浮
    并画在最后。

    正在被移动动画表现的牌（``hidden_ids``，例如"公共池 → 手牌"的取牌）
    在这一帧不画：格子留着，牌已经由动画独占表现，不会出现两个副本。

    ``face_down_ids`` 是**内容未知**的牌（顺手牵羊 / 过河拆桥看到的对方手牌）：
    这些格子只画牌背与高亮边框，不画卡面——否则等于把对方手牌摊开给玩家看。
    """

    if not entries or not rects:
        return
    fonts = metrics.fonts if metrics is not None else theme.fonts()
    count = min(len(entries), len(rects))
    hidden = hidden_ids or ()
    face_down = face_down_ids or ()
    hover_index = pool_hover_index(rects[:count], mouse_pos)

    def paint(index):
        card, key = entries[index]
        if id(card) in hidden:
            return
        candidate = is_candidate(card, key)
        hovered = index == hover_index and candidate
        rect = pool_hover_rect(pygame.Rect(rects[index]), metrics) if hovered else pygame.Rect(rects[index])
        if id(card) in face_down:
            card_draw.draw_card_back(
                surface, rect,
                selected=is_selected(card, key),
                candidate=candidate,
                hovered=hovered,
            )
            return
        card_draw.draw_card(
            surface,
            card,
            rect,
            fonts,
            candidate=candidate,
            selected=is_selected(card, key),
            hovered=hovered,
            # 公开牌面：不因为格子偏小就退回纯文字卡，有素材就一定画真实卡面。
            compact=False,
        )

    for index in range(count):
        if index != hover_index:
            paint(index)
    if hover_index is not None:
        paint(hover_index)


def draw_turn_banner(surface, metrics, info):
    if not info:
        return
    alpha = info.get("alpha", 255)
    if alpha <= 0:
        return
    fonts = metrics.fonts
    text = fonts.get("huge").render(info.get("text", ""), True, theme.GOLD_BRIGHT)
    shadow = fonts.get("huge").render(info.get("text", ""), True, (10, 12, 16))
    rect = text.get_rect(center=(metrics.screen_w // 2, metrics.central.y + metrics.px(52)))

    plate = pygame.Surface((rect.width + metrics.px(72), rect.height + metrics.px(26)), pygame.SRCALPHA)
    pygame.draw.rect(plate, (*theme.INK, 168), plate.get_rect(), border_radius=metrics.px(16))
    pygame.draw.rect(plate, (*theme.GOLD, 190), plate.get_rect(), 2, border_radius=metrics.px(16))
    plate.set_alpha(alpha)

    surface.blit(plate, plate.get_rect(center=rect.center))
    text.set_alpha(alpha)
    shadow.set_alpha(alpha)
    surface.blit(shadow, shadow.get_rect(center=(rect.centerx + metrics.px(2), rect.centery + metrics.px(2))))
    surface.blit(text, rect)


def draw_phase_strip(surface, game, metrics, phase_label):
    fonts = metrics.fonts
    height = metrics.px(30)
    # 贴在中央战场**左下角**：右下角会压住放大后的弃牌堆，居中又会与当前
    # 动作卡撞在一起；左下这一块是唯一始终空着的位置。
    rect = pygame.Rect(
        metrics.central.x + metrics.px(40),
        metrics.central.bottom - height - metrics.px(10),
        metrics.px(190),
        height,
    )
    draw_panel(surface, rect, fill=theme.PANEL_SUNKEN, border=theme.GOLD_DIM,
               border_width=2, shadow=False, radius=metrics.px(8))
    text = fonts.get("small").render(phase_label, True, theme.GOLD_BRIGHT)
    surface.blit(text, text.get_rect(center=rect.center))


def draw_log(surface, game, metrics, *, max_entries=5):
    """Low-priority combat log in the lower-left corner, clear of every seat."""

    entries = game.game_log[-max_entries:]
    if not entries:
        return
    fonts = metrics.fonts
    rect = metrics.log_rect

    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel, (10, 14, 19, 150), panel.get_rect(), border_radius=metrics.px(10))
    pygame.draw.rect(panel, (*theme.GOLD_DIM, 120), panel.get_rect(), 1, border_radius=metrics.px(10))
    surface.blit(panel, rect.topleft)

    pad = metrics.px(10)
    title = fonts.get("micro").render("战报", True, theme.TEXT_MUTED)
    surface.blit(title, (rect.x + pad, rect.y + metrics.px(4)))

    line_font = fonts.get("micro")
    line_height = metrics.px(21)
    available = rect.width - pad * 2
    for index, entry in enumerate(entries):
        text = ellipsize_text(entry, line_font, available)
        surface.blit(
            line_font.render(text, True, (206, 214, 222)),
            (rect.x + pad, rect.y + metrics.px(22) + index * line_height),
        )
