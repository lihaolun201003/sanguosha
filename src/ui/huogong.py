"""火攻专用交互界面（Huogong Panel）。

火攻有两次"有上下文"的选牌：

1. **展示**：被火攻的目标选一张手牌展示（``huogong_reveal``）；
2. **弃置**：火攻的使用者弃置一张与之同花色的手牌（``huogong_discard``）。

通用 SELECT_CARDS 只把候选牌摆出来，玩家看不到"对方翻出来的到底是什么牌"，
也不知道自己手上哪几张才是能弃的。这个面板把两侧放在一起：

    左侧：对方（或"即将展示"）的真实牌面 + 花色点数
    右侧：自己的手牌（合法候选高亮，非法候选变暗且点不动）
    底部：这一轮到底要做什么（"你需要弃置一张 ♥ 手牌"）

**规则真相仍然只在引擎 / 房主手里**：面板显示的候选集就来自引擎给出的
``candidates``，它不自己算花色、不自己判断合法与否，也不改任何状态。
客户端与单机读的是同一份结构（``pending_selection``），所以只有一个实现。
"""

import pygame

from . import cards as card_draw
from . import theme
from .widgets import draw_panel, ellipsize_text

#: 面板的设计尺寸（实际按屏幕缩放，并限制在屏幕内）。
PANEL_WIDTH = 960
PANEL_HEIGHT = 452
PANEL_PAD = 26
COLUMN_GAP = 28
LEFT_WIDTH = 320

#: 左列展示牌 / 右列候选牌的尺寸。
SHOW_CARD_SIZE = (168, 234)
PICK_CARD_SIZE = (96, 134)
PICK_CARD_MIN_WIDTH = 44

#: 认得出的火攻选择（引擎在请求上下文里给出的 reason）。
HUOGONG_REASONS = ("huogong_reveal", "huogong_discard")

TITLE = "火攻"


def resolve_player(game, value):
    """把"某个角色"规范成角色对象。

    单机侧给的是角色对象，客户端决策载荷里给的是 player_id 字符串——面板
    两种都要认，否则名字会显示成空。
    """

    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return value
    for player in getattr(game, "players", ()) or ():
        if str(getattr(player, "player_id", "") or "") == value:
            return player
    return None


def panel_data(game):
    """把当前选牌界面整理成火攻面板要的数据；不是火攻选择时返回 None。"""

    selection = getattr(game, "pending_selection", None)
    if not selection:
        return None
    reason = str(selection.get("reason") or "")
    if reason not in HUOGONG_REASONS:
        return None
    owner = selection.get("owner") or getattr(game, "player", None)
    hand = [card for card in list(getattr(owner, "hand", ()) or ()) if card is not None]
    candidates = [card for card, _key in selection.get("candidates", ())]
    candidate_ids = {id(card) for card in candidates}
    selected_ids = {id(card) for item in selection.get("selected", ())
                    for card in (item[0],)}
    return {
        "reason": reason,
        "reveal": reason == "huogong_reveal",
        "owner": owner,
        "hand": hand,
        "candidates": candidates,
        "candidate_ids": candidate_ids,
        "selected_ids": selected_ids,
        "revealed": selection.get("revealed") or None,
        "revealed_player": resolve_player(game, selection.get("revealed_player")),
        "caster": resolve_player(game, selection.get("caster")),
        "cancellable": bool(selection.get("cancellable")),
        "prompt": str(selection.get("prompt") or ""),
    }


class HuogongPanel:
    """火攻的两阶段选择界面（只画 + 命中，不改任何状态）。

    坐标约定：``rect`` / ``_card_rects`` / ``_cancel_rect`` 都是**屏幕坐标**
    （命中测试要用），绘制时统一换算到 layer 局部坐标（见 ``_local``）。
    """

    def __init__(self):
        self.metrics = None
        self.data = None
        #: 当前面板矩形（屏幕坐标）。
        self._rect = None
        self._card_rects = []
        self._cancel_rect = None
        #: 绘制期间的鼠标位置（只影响悬停高亮）。
        self._mouse = None

    # ==================================================
    # 布局
    # ==================================================

    def sync(self, game, metrics):
        """按当前屏幕算出面板与牌位矩形（绘制与命中测试共用同一份）。"""

        self.metrics = metrics
        self.data = panel_data(game)
        if self.data is None:
            self._rect = None
            self._card_rects = []
            self._cancel_rect = None
            return None
        panel = self.rect(metrics)
        self._rect = panel
        self._card_rects = self._layout_cards(panel, metrics, self.data)
        self._cancel_rect = self._layout_cancel(panel, metrics, self.data)
        return panel

    def rect(self, metrics):
        """面板矩形（**屏幕坐标**：命中测试与绘制共用同一份布局）。"""

        width = min(metrics.px(PANEL_WIDTH), int(metrics.screen_w * 0.9))
        height = min(metrics.px(PANEL_HEIGHT), int(metrics.screen_h * 0.6))
        rect = pygame.Rect(0, 0, width, height)
        # 压在中央战场那一带上：下边界不越过真人状态条（自己的体力要看得见），
        # 上边界不越过顶部座位。
        rect.center = (metrics.screen_w // 2, metrics.central.centery)
        return rect

    def _column_rects(self, panel, metrics):
        """两块内容区 + 底部说明带的高度（**屏幕坐标**）。"""

        pad = metrics.px(PANEL_PAD)
        gap = metrics.px(COLUMN_GAP)
        header = metrics.px(78)
        footer = metrics.px(58)
        body_h = max(metrics.px(80), panel.height - header - footer)
        left = pygame.Rect(panel.x + pad, panel.y + header,
                           min(metrics.px(LEFT_WIDTH), panel.width // 3), body_h)
        right = pygame.Rect(left.right + gap, panel.y + header,
                            panel.right - pad - (left.right + gap), body_h)
        return left, right, footer

    @staticmethod
    def _local(rect, panel):
        """屏幕矩形 → 面板局部的矩形。

        绘制在 panel 大小的离线图层上，图层坐标是"以面板左上角为原点"。
        命中测试用屏幕坐标，两者混用会让面板里的东西整体偏移（曾把标题画到
        右列上）。所有绘制入口都经过这里换算。
        """

        if rect is None:
            return None
        return pygame.Rect(rect).move(-panel.x, -panel.y)

    def _layout_cards(self, panel, metrics, data):
        _left, right, _footer = self._column_rects(panel, metrics)
        hand = data["hand"]
        if not hand:
            return []
        width, height = self._card_size(metrics)
        count = len(hand)
        step = width + metrics.px(8)
        span = step * count - metrics.px(8)
        if span > right.width:
            # 手牌很多：重叠排布也要保证每一张都能点到（步长有下限）。
            step = max(metrics.px(PICK_CARD_MIN_WIDTH),
                       (right.width - width) // max(1, count - 1))
            span = step * (count - 1) + width
        start_x = right.centerx - span // 2
        top = right.centery - height // 2
        return [pygame.Rect(int(start_x + step * index), int(top), width, height)
                for index in range(count)]

    def _card_size(self, metrics):
        return (max(1, int(metrics.px(PICK_CARD_SIZE[0]))),
                max(1, int(metrics.px(PICK_CARD_SIZE[1]))))

    def _layout_cancel(self, panel, metrics, data):
        if not data["cancellable"]:
            return None
        pad = metrics.px(PANEL_PAD)
        width = metrics.px(148)
        height = metrics.px(44)
        return pygame.Rect(panel.right - pad - width, panel.bottom - pad - height,
                           width, height)

    # ==================================================
    # 命中测试
    # ==================================================

    def contains(self, position):
        return bool(self._rect is not None and self._rect.collidepoint(position))

    def hit(self, position, game, metrics):
        """点在这块面板上：返回 ``("card", card, rect)`` / ``("cancel",)`` / ``None``。

        返回 ``None`` 表示"落在这块面板里但没有意义"（吞掉即可，不外泄给底下
        的牌桌路由）。
        """

        self.sync(game, metrics)
        data = self.data
        if data is None or self._rect is None:
            return None
        if self._cancel_rect is not None and self._cancel_rect.collidepoint(position):
            return ("cancel",)
        for rect, card in zip(self._card_rects, data["hand"]):
            if rect.collidepoint(position):
                # 合法性由引擎给的候选集决定：非候选点不动（面板不自己算规则）。
                if id(card) in data["candidate_ids"]:
                    return ("card", card, tuple(rect))
                return None
        return None

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, surface, game, metrics, mouse_pos=None):
        self.sync(game, metrics)
        # 悬停高亮只在绘制时用；命中测试不依赖鼠标位置。
        self._mouse = mouse_pos
        data = self.data
        if data is None or self._rect is None:
            return None
        panel = self._rect
        layer = pygame.Surface(panel.size, pygame.SRCALPHA)
        local = layer.get_rect()
        draw_panel(layer, local, fill=theme.PANEL_DEEP, border=theme.GOLD,
                   border_width=metrics.px(3), radius=metrics.px(18))
        accent = theme.DANGER if not data["reveal"] else theme.TARGET_YELLOW
        pygame.draw.rect(layer, (*accent, 150),
                         local.inflate(-metrics.px(8), -metrics.px(8)),
                         metrics.px(2), border_radius=metrics.px(14))

        self._draw_header(layer, local, metrics, data)
        left, right, _footer = self._column_rects(panel, metrics)
        self._draw_left(layer, self._local(left, panel), metrics, data)
        self._draw_right(layer, self._local(right, panel), panel, metrics, data)
        self._draw_footer(layer, local, metrics, data)

        surface.blit(layer, panel.topleft)
        return panel

    # ---- 头部 ----

    def _draw_header(self, layer, panel, metrics, data):
        """``panel`` 是**局部坐标**的整块面板矩形。"""

        title_font = metrics.fonts.get("large")
        title = title_font.render(TITLE, True, theme.GOLD_BRIGHT)
        layer.blit(title, title.get_rect(midtop=(panel.centerx, metrics.px(10))))
        subtitle = ("对方要你展示一张手牌" if data["reveal"]
                    else "对方已展示一张手牌")
        who = self._name(data["caster"] if data["reveal"] else data["revealed_player"])
        if who:
            subtitle = who + " · " + subtitle
        small = metrics.fonts.get("small")
        text = small.render(subtitle, True, theme.TEXT_DIM)
        layer.blit(text, text.get_rect(
            midtop=(panel.centerx,
                    metrics.px(10) + title.get_height() + metrics.px(2))))

    # ---- 左列：展示牌 ----

    def _draw_left(self, layer, rect, metrics, data):
        """``rect`` 是**局部坐标**的左列内容区。"""

        caption = metrics.fonts.get("small")
        label = caption.render("对方展示的牌", True, theme.TEXT_DIM)
        layer.blit(label, (rect.x, rect.y))

        card = data["revealed"]
        size = self._fit(metrics, SHOW_CARD_SIZE, rect)
        target = pygame.Rect(0, 0, size[0], size[1])
        target.midtop = (rect.centerx, rect.y + label.get_height() + metrics.px(8))
        if card is None:
            pygame.draw.rect(layer, theme.PANEL_SUNKEN, target,
                             border_radius=metrics.px(10))
            pygame.draw.rect(layer, theme.GOLD_DIM, target, 2,
                             border_radius=metrics.px(10))
            hint = metrics.fonts.get("normal").render("尚未展示", True, theme.TEXT_DIM)
            layer.blit(hint, hint.get_rect(center=target.center))
        else:
            frame = target.inflate(metrics.px(8), metrics.px(8))
            pygame.draw.rect(layer, theme.PANEL_SUNKEN, frame,
                             border_radius=metrics.px(10))
            pygame.draw.rect(layer, theme.GOLD, frame, 2,
                             border_radius=metrics.px(10))
            card_draw.draw_card(layer, card, target, metrics.fonts)

        cursor = target.bottom + metrics.px(8)
        if card is not None:
            name_line = self._name(data["revealed_player"])
            if name_line:
                text = metrics.fonts.get("small").render(
                    name_line + " 展示", True, theme.TEXT)
                layer.blit(text, text.get_rect(midtop=(rect.centerx, cursor)))
                cursor += text.get_height() + metrics.px(4)
            mark = self._suit_mark(card, metrics)
            if mark is not None:
                layer.blit(mark, mark.get_rect(midtop=(rect.centerx, cursor)))
                cursor += mark.get_height() + metrics.px(6)
            detail = self._suit_requirement(metrics, card)
            if detail is not None:
                layer.blit(detail, detail.get_rect(midtop=(rect.centerx, cursor)))

    # ---- 右列：自己的手牌 ----

    def _draw_right(self, layer, rect, panel, metrics, data):
        """``rect`` 是**局部坐标**的右列内容区；牌位是屏幕坐标（这里换算）。"""

        caption = metrics.fonts.get("small")
        label_text = "请选择你要展示的牌" if data["reveal"] else "请选择你要弃置的牌"
        label = caption.render(label_text, True, theme.TEXT_DIM)
        layer.blit(label, (rect.x, rect.y))

        hand = data["hand"]
        if not hand:
            hint = metrics.fonts.get("normal").render(
                "手牌里没有可选的牌", True, theme.TEXT_DIM)
            layer.blit(hint, hint.get_rect(center=rect.center))
            return
        for card_rect, card in zip(self._card_rects, hand):
            legal = id(card) in data["candidate_ids"]
            selected = id(card) in data["selected_ids"]
            # 悬停用**屏幕坐标**判断（鼠标位置是屏幕坐标），画在局部坐标上。
            hovered = bool(self._mouse is not None
                           and card_rect.collidepoint(self._mouse))
            card_draw.draw_card(
                layer, card, self._local(card_rect, panel), metrics.fonts,
                selected=selected,
                candidate=legal and not selected,
                dimmed=not legal,
                hovered=hovered and legal,
            )

    # ---- 底部说明 ----

    def _draw_footer(self, layer, panel, metrics, data):
        """``panel`` 是**局部坐标**的整块面板矩形。"""

        pad = metrics.px(PANEL_PAD)
        card = data["revealed"]
        font = metrics.fonts.get("normal")
        if data["reveal"]:
            text = "选择一张手牌展示给对方；对方若能弃置同花色的牌，你将受到 1 点火焰伤害。"
            color = theme.TEXT
        elif not data["candidates"]:
            text = "你手上没有与展示牌同花色的手牌，无需弃置。"
            color = theme.TEXT_DIM
        elif card is not None:
            text = "你需要弃置一张 %s 手牌。" % (getattr(card, "suit_name", "") or "同花色")
            color = theme.TEXT
        else:
            text = data["prompt"] or "请选择要弃置的牌。"
            color = theme.TEXT
        room = panel.width - pad * 2 - metrics.px(170)
        rendered = font.render(ellipsize_text(text, font, max(40, room)), True, color)
        layer.blit(rendered, rendered.get_rect(
            midleft=(pad, panel.bottom - pad - metrics.px(14))))

        if self._cancel_rect is not None:
            button = self._local(self._cancel_rect, panel)
            pygame.draw.rect(layer, theme.PANEL_ALT, button,
                             border_radius=metrics.px(8))
            pygame.draw.rect(layer, theme.GOLD_DIM, button, 2,
                             border_radius=metrics.px(8))
            text = metrics.fonts.get("normal").render("放弃选择", True, theme.TEXT)
            layer.blit(text, text.get_rect(center=button.center))

    # ---- 小工具 ----

    @staticmethod
    def _fit(metrics, size, area):
        """左列展示牌：给下面三行文字（"X 展示" / 花色点数 / 花色要求）留位置。

        不留位置的话牌会一路顶到底部说明带上（实测：♥7 会压住
        "你需要弃置一张…"那一行）。
        """

        width = min(metrics.px(size[0]), area.width)
        height = min(metrics.px(size[1]),
                     max(metrics.px(60), area.height - metrics.px(150)))
        return max(1, int(width)), max(1, int(height))

    @staticmethod
    def _name(player):
        return str(getattr(player, "name", "") or "")

    @staticmethod
    def _suit_mark(card, metrics):
        symbol = getattr(card, "suit_symbol", "") or ""
        rank = str(getattr(card, "rank", "") or "")
        if not symbol or not rank:
            return None
        font = metrics.fonts.suit(max(16, int(metrics.px(30))))
        color = (238, 122, 108) if getattr(card, "card_color", "") == "red" \
            else (228, 234, 242)
        return font.render("%s %s" % (symbol, rank), True, color)

    @staticmethod
    def _suit_requirement(metrics, card):
        suit = getattr(card, "suit_name", "") or ""
        if not suit:
            return None
        font = metrics.fonts.get("small")
        return font.render("展示牌花色：" + suit, True, theme.TEXT_DIM)
