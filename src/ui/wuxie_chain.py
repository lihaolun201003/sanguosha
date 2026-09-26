"""无懈可击链的界面表达（第十六章）。

多层无懈的逻辑本来就在引擎里（``flows/wuxie.py`` 的 window / round），但界面
只给出一句"窗口 2 · 第 1 轮"——玩家看不懂现在到底在无懈哪张牌、谁在用、轮了几层。

这里把它画成**一条链**：

    【南蛮入侵】 张辽 使用
        ●───●───○   第 3 轮
        无懈可击

* 左边是被无懈的那张牌（桌上已经公开）+ 使用者；
* 中间是轮次指示：每一轮一个小格，已经过去的实心、当前的点亮、将来的空心
  ——**用图形表达"链有多深"，不写"链深度 3"**；
* 右边说明现在在等谁。

数据来源分两条路径，字段完全一致（``view_builder.build_response_window``
与本地 ``pending_request.context``）：被无懈的牌、使用者、目标数、轮次。
界面不自己判断"谁有【无懈可击】"——那会泄露手牌。
"""

import pygame

from . import cards as card_draw
from . import theme
from .widgets import ellipsize_text

#: 条带尺寸（设计像素）
PANEL_HEIGHT = 46
PANEL_PAD_X = 14
CARD_W = 30
CARD_H = 42
NODE_SPACING = 18
NODE_RADIUS = 5
MAX_ROUNDS_SHOWN = 6


def _context(game):
    """取当前无懈阶段的展示数据；不在无懈阶段返回 None。

    单机读引擎的待回答请求；联网客户端读房主下发的 ``response_window``
    （两条路径给的是同一组字段）。
    """

    window = getattr(game, "response_window", None)
    if window:
        return {
            "card": window.get("card"),
            "caster": window.get("caster_name") or "",
            "targets": int(window.get("target_count") or 0),
            "round_id": int(window.get("round_id") or 0),
            "status": str(window.get("status") or ""),
        }
    request = getattr(game, "pending_request", None)
    if request is None or not getattr(request, "is_group", False):
        return None
    context = dict(getattr(request, "context", None) or {})
    if str(context.get("reason") or "") != "wuxie_chain":
        return None
    card = context.get("card")
    # 使用者：无懈链的请求上下文里没有 ``caster``（那是火攻那种请求的字段），
    # 用的人就是这条请求的 source。
    caster = context.get("caster") or getattr(request, "source", None)
    local = getattr(game, "player", None)
    status = request.member_status(local) if local is not None else ""
    return {
        "card": card,
        "caster": str(getattr(caster, "name", "") or ""),
        "targets": len(context.get("targets") or ()),
        "round_id": int(context.get("round_id") or 0),
        "status": str(status or ""),
        # 单机侧直接拿实体牌，省一次查表。
        "card_object": card,
    }


def chain_active(game):
    """现在是不是正在无懈链里（界面据此决定要不要画）。"""

    return _context(game) is not None


def draw_chain(surface, game, metrics, *, resolve_card=None):
    """在中央战场上方画一条无懈链；没有链时什么都不做。

    ``resolve_card`` 把网络侧的牌载荷解析成展示用卡（客户端用卡缓存）；
    单机不需要它。
    """

    data = _context(game)
    if data is None:
        return None

    fonts = metrics.fonts
    pad = metrics.px(PANEL_PAD_X)
    label = "无懈可击"
    label_font = fonts.get(theme.FONT_BODY)
    meta_font = fonts.get(theme.FONT_SMALL)
    card_name = "这张牌"
    card = data.get("card_object")
    if card is None and data.get("card") is not None:
        card = resolve_card(data["card"]) if callable(resolve_card) else None
        card_name = str(getattr(card, "display_name", "") or "这张牌")
    elif card is not None:
        card_name = str(getattr(card, "display_name", "") or "这张牌")

    head = meta_font.render(
        "%s 使用" % (data["caster"] or "某角色"), True, theme.TEXT_SECONDARY)
    title = label_font.render("【" + card_name + "】", True, theme.GOLD_BRIGHT)
    rounds = max(1, min(MAX_ROUNDS_SHOWN, int(data["round_id"] or 1)))
    status_text = {
        "passed": "你已放弃本轮",
        "pending": "等你决定",
    }.get(str(data.get("status") or ""), "等待其他玩家响应")
    tail = meta_font.render(status_text, True, theme.TEXT_DIM)

    chain_w = rounds * metrics.px(NODE_SPACING)
    width = (pad * 2 + metrics.px(CARD_W) + metrics.px(10)
             + max(title.get_width(), head.get_width())
             + metrics.px(14) + chain_w + metrics.px(14) + tail.get_width())
    width = min(width, int(metrics.screen_w * 0.72))
    height = metrics.px(PANEL_HEIGHT)
    rect = pygame.Rect(0, 0, width, height)
    # 贴在中央战场上方：与技能横幅（0.16 屏高）错开，也不压判定面板。
    rect.midtop = (metrics.screen_w // 2,
                   int(metrics.screen_h * 0.048))

    layer = pygame.Surface(rect.size, pygame.SRCALPHA)
    local = layer.get_rect()
    pygame.draw.rect(layer, (*theme.PANEL_DEEP, 238), local,
                     border_radius=metrics.px(theme.RADIUS_SMALL))
    pygame.draw.rect(layer, theme.CHAIN, local, metrics.px(2),
                     border_radius=metrics.px(theme.RADIUS_SMALL))

    x = pad
    card_rect = pygame.Rect(x, (height - metrics.px(CARD_H)) // 2,
                            metrics.px(CARD_W), metrics.px(CARD_H))
    if card is not None:
        card_draw.draw_card(layer, card, card_rect, fonts, compact=True)
    else:
        pygame.draw.rect(layer, theme.PLATE, card_rect,
                         border_radius=metrics.px(theme.RADIUS_SMALL))
        pygame.draw.rect(layer, theme.GOLD_DIM, card_rect, 1,
                         border_radius=metrics.px(theme.RADIUS_SMALL))
    x = card_rect.right + metrics.px(10)

    layer.blit(title, title.get_rect(bottomleft=(x, height // 2 + 1)))
    layer.blit(head, head.get_rect(topleft=(x, height // 2 + 2)))
    x += max(title.get_width(), head.get_width()) + metrics.px(14)

    # 轮次：实心 = 已经过去的轮，亮点 = 当前轮，空心 = 还没到。
    center_y = height // 2
    for index in range(rounds):
        node_x = x + index * metrics.px(NODE_SPACING) + metrics.px(NODE_RADIUS)
        if index < rounds - 1:
            pygame.draw.line(layer, theme.CHAIN_DIM,
                             (node_x, center_y),
                             (node_x + metrics.px(NODE_SPACING), center_y), 2)
        if index < rounds - 1:
            color, radius = theme.CHAIN, metrics.px(NODE_RADIUS) - 1
        else:
            color, radius = theme.CHAIN_BRIGHT, metrics.px(NODE_RADIUS)
        pygame.draw.circle(layer, color, (node_x, center_y), radius)
    x += chain_w + metrics.px(14)

    # 状态文案右对齐贴边：宽度算不准时也不会挤掉轮次指示。
    layer.blit(tail, tail.get_rect(midright=(local.right - pad, center_y)))

    surface.blit(layer, rect.topleft)
    return rect
