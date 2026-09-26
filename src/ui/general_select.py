"""选将界面：本局候选（默认 5 个）+ 中央大卡详情。

布局参考"卡牌游戏选将"的常见做法：

    顶部   模式 / 一句说明（还差什么、怎么确认）
    中央   当前聚焦武将的大卡：立绘 + 姓名 + 势力 + 体力 + 全部技能
    底部   本局候选（横向 5 张）：点一下 = 聚焦并选中，双击 = 直接确认

**只显示本局候选**：候选来自 ``game.selectable_generals()``（单机走
``GeneralDraft``，联机走房主下发的候选），不再是整张武将表。随机发生在
业务层（``game.generals.draft``），这一屏一次都不抽——resize / hover /
重绘都不会让候选变化。

进入界面时默认聚焦第一张，但**不会**替你提交选择：必须自己点一张卡片
再按「确认出战」（或双击卡片）。这一屏不认识任何具体武将。
"""

import pygame

from . import general_cards, layout, theme
from .widgets import (
    Button,
    draw_panel,
    draw_state_border,
    ellipsize_text,
    wrap_text,
)

#: 底部候选卡的设计尺寸。
PICK_WIDTH = 168
PICK_HEIGHT = 226
PICK_FOCUS_LIFT = 10
PICK_HOVER_LIFT = 6

HEADER_H = 132
FOOTER_H = 116
DETAIL_GAP = 22


class GeneralSelectScreen:
    """一屏选择本局武将：候选横排 + 中央大卡。

    ``allow_random`` / ``allow_back`` 与之前一致：单机保留「随机」与「返回」
    两条退路；联机的候选由房主给定，两个按钮都关掉，只留「确认出战」。
    """

    def __init__(self, screen, *, allow_random=True, allow_back=True):
        self.screen = screen
        self.metrics = None
        self.generals = ()
        self.focus_index = 0
        self.hover_index = None
        self.allow_random = bool(allow_random)
        self.allow_back = bool(allow_back)
        #: 可选的一行提示（联机开局用它显示"等待其他玩家选将"）。
        self.notice = ""
        self.card_rects = []
        self.card_hit_rects = []
        self.detail_rect = pygame.Rect(0, 0, 10, 10)
        self.confirm_button = Button(pygame.Rect(0, 0, 10, 10), "确认出战", kind="primary", font="large")
        self.random_button = Button(pygame.Rect(0, 0, 10, 10), "随机选一个", kind="secondary", font="normal")
        self.back_button = Button(pygame.Rect(0, 0, 10, 10), "返回", kind="ghost", font="normal")
        self._last_click = (None, 0.0)
        self.sync_layout(None, ())

    # ==================================================
    # 布局
    # ==================================================

    def sync_layout(self, metrics=None, generals=()):
        metrics = metrics or layout.LayoutMetrics(
            layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.metrics = metrics
        self.generals = tuple(generals)
        #: 放不下、因此没有画出来的候选数量（真实开局只有 5 个，恒为 0）。
        self.hidden_count = 0

        pad = metrics.px(30)
        header = metrics.px(HEADER_H)
        footer = metrics.px(FOOTER_H)
        count = max(1, len(self.generals))

        # 底部候选：先按设计宽度排，排不下就等分（5 个在 4:3 屏上也放得下）。
        gap = metrics.px(18)
        available_w = metrics.screen_w - pad * 2
        card_w = min(metrics.px(PICK_WIDTH),
                     (available_w - gap * (count - 1)) // count)
        card_w = max(metrics.px(72), card_w)
        card_h = max(metrics.px(96), int(card_w * (PICK_HEIGHT / float(PICK_WIDTH))))
        card_h = min(card_h, metrics.px(PICK_HEIGHT + 16))
        strip_h = card_h + metrics.px(PICK_FOCUS_LIFT + 12)
        strip_y = metrics.screen_h - footer - strip_h
        total_w = card_w * count + gap * (count - 1)
        start_x = (metrics.screen_w - total_w) // 2
        top = max(header + metrics.px(8), strip_y)

        self.card_rects = []
        self.card_hit_rects = []
        lift = metrics.px(PICK_FOCUS_LIFT + PICK_HOVER_LIFT)
        for index in range(count):
            rect = pygame.Rect(start_x + index * (card_w + gap), top, card_w, card_h)
            self.card_rects.append(rect)
            # 聚焦 / 悬停时卡片上浮，点击区域必须跟着一起抬，否则"看得见点不到"。
            self.card_hit_rects.append(rect.union(rect.move(0, -lift)))

        # 中央大卡：顶部到候选条之间的全部空间。
        detail_top = metrics.px(HEADER_H)
        detail_h = max(metrics.px(180), top - detail_top - metrics.px(DETAIL_GAP))
        self.detail_rect = pygame.Rect(
            pad * 2, detail_top, metrics.screen_w - pad * 4, detail_h)

        button_h = metrics.px(58)
        button_y = metrics.screen_h - metrics.px(FOOTER_H) + metrics.px(34)
        self.confirm_button.rect = pygame.Rect(
            metrics.screen_w // 2 - metrics.px(150), button_y, metrics.px(300), button_h)
        self.random_button.rect = pygame.Rect(
            self.confirm_button.rect.left - metrics.px(230), button_y,
            metrics.px(210), button_h)
        self.back_button.rect = pygame.Rect(
            self.confirm_button.rect.right + metrics.px(20), button_y,
            metrics.px(210), button_h)
        return self

    # ==================================================
    # 交互
    # ==================================================

    def card_index_at(self, position):
        for index, rect in enumerate(self.card_hit_rects):
            if rect.collidepoint(position):
                return index
        return None

    def general_at(self, position):
        index = self.card_index_at(position)
        if index is None or index >= len(self.generals):
            return None
        return self.generals[index]

    def focused_general(self):
        if not self.generals:
            return None
        index = max(0, min(self.focus_index, len(self.generals) - 1))
        return self.generals[index]

    def selected_general(self, game):
        return game.generals.get(getattr(game, "selected_general", None))

    def set_hover(self, position):
        self.hover_index = self.card_index_at(position)
        return self.hover_index

    def handle_event(self, event, game):
        """返回 ``handle_click`` 的结果；只认左键与鼠标移动。"""

        if event.type == pygame.MOUSEMOTION:
            self.set_hover(event.pos)
            return "handled"
        if event.type != pygame.MOUSEBUTTONDOWN or getattr(event, "button", 1) != 1:
            return None
        return self.handle_click(event.pos, game)

    def handle_click(self, position, game):
        """返回 "select" / "confirm" / "random" / "back" / None。"""

        index = self.card_index_at(position)
        if index is not None and index < len(self.generals):
            general = self.generals[index]
            self.focus_index = index
            game.selected_general = general.id
            # 双击直接确认（第一次点已经把它选中了，第二次就是"就是它了"）。
            if self._is_double_click(index):
                self._last_click = (None, 0.0)
                return "confirm"
            return "select"

        if self.allow_random and self.random_button.contains(position):
            # 随机**只在候选里**抽——不会选出候选之外的武将。
            pool = [general.id for general in self.generals]
            if pool:
                game.selected_general = game.rng.choice(pool)
                self.focus_index = pool.index(game.selected_general)
            return "random"

        if self.confirm_button.contains(position) and game.selected_general:
            return "confirm"

        if self.allow_back and self.back_button.contains(position):
            return "back"

        return None

    def _is_double_click(self, index):
        import time

        now = time.monotonic()
        last_index, last_time = self._last_click
        self._last_click = (index, now)
        return last_index == index and (now - last_time) < 0.45

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, game, metrics=None):
        metrics = metrics or self.metrics
        # metrics 变了（全屏启动 / 缩放窗口）就重算布局；武将列表沿用当前这批，
        # 从未设置过时退回全量（只有工具 / 旧路径会走到）。
        if metrics is None or self.metrics is not metrics or not self.card_rects:
            self.sync_layout(metrics, self.generals or game.selectable_generals())
            metrics = self.metrics

        mouse = pygame.mouse.get_pos()
        self.set_hover(mouse)

        self.screen.blit(theme.menu_background(metrics.screen_w, metrics.screen_h), (0, 0))
        self._draw_header(game, metrics)
        self._draw_detail(self.focused_general(), game, metrics)
        self._draw_candidates(game, metrics, mouse)
        self._draw_buttons(game, metrics, mouse)

        if self.notice:
            font = metrics.fonts.get("small")
            note = font.render(self.notice, True, theme.TEXT_WARM_DIM)
            self.screen.blit(note, note.get_rect(
                center=(metrics.screen_w // 2, metrics.screen_h - metrics.px(16))))
        return self

    # ---- 顶部 ----

    def _draw_header(self, game, metrics):
        fonts = metrics.fonts
        mode = getattr(getattr(game, "mode", None), "name", "")
        title_text = "请选择武将" if not mode else "%s · 请选择武将" % mode
        title = fonts.get("title").render(title_text, True, theme.GOLD_BRIGHT)
        self.screen.blit(title, title.get_rect(
            center=(metrics.screen_w // 2, metrics.px(46))))

        selected = self.selected_general(game)
        if selected is not None:
            hint = "已选择：%s    ·    点「确认出战」开始对局（双击卡片也可以）" % selected.name
            color = theme.JADE_BRIGHT
        else:
            hint = "本局候选由你的武将池随机抽选 · 点一张卡片查看详情，再确认出战"
            color = theme.TEXT_WARM_DIM
        rendered = fonts.get("small").render(hint, True, color)
        self.screen.blit(rendered, rendered.get_rect(
            center=(metrics.screen_w // 2, metrics.px(92))))
        pygame.draw.line(
            self.screen, theme.BRONZE_DIM,
            (metrics.px(40), metrics.px(HEADER_H) - metrics.px(8)),
            (metrics.screen_w - metrics.px(40), metrics.px(HEADER_H) - metrics.px(8)), 2)

    # ---- 中央大卡 ----

    def _draw_detail(self, general, game, metrics):
        rect = self.detail_rect
        draw_panel(self.screen, rect, fill=theme.PANEL_WARM_DEEP,
                   border=theme.BRONZE, border_width=2,
                   radius=metrics.px(18), shadow=False)
        if general is None:
            hint = metrics.fonts.get("large").render(
                "没有可选的武将", True, theme.TEXT_WARM_DIM)
            self.screen.blit(hint, hint.get_rect(center=rect.center))
            return

        pad = metrics.px(22)
        # 立绘占左半，右侧放资料与技能：横屏下文字有足够宽度，不会被压缩。
        art_w = min(int(rect.width * 0.36), metrics.px(420))
        art_rect = pygame.Rect(rect.x + pad, rect.y + pad,
                               art_w, rect.height - pad * 2)
        general_cards.draw_portrait(self.screen, general, art_rect, metrics)

        info_x = art_rect.right + pad
        info_w = rect.right - pad - info_x
        fonts = metrics.fonts
        cursor = rect.y + pad

        name_font = fonts.get("hero")
        name = name_font.render(general.name, True, theme.TEXT_WARM)
        if name.get_width() > info_w:
            name_font = fonts.get("title")
            name = name_font.render(general.name, True, theme.TEXT_WARM)
        self.screen.blit(name, (info_x, cursor))
        cursor += name.get_height() + metrics.px(4)

        meta = fonts.get("small").render(
            "势力 %s · %s · 体力 %d" % (
                general_cards.kingdom_label(general),
                "男" if general.gender == "male" else "女",
                int(general.max_hp or 0)),
            True, theme.TEXT_WARM_DIM)
        self.screen.blit(meta, (info_x, cursor))
        cursor += meta.get_height() + metrics.px(10)

        general_cards.draw_hp_pips(
            self.screen, general, (info_x, cursor + metrics.px(6)), metrics)
        cursor += metrics.px(24)

        pygame.draw.line(self.screen, theme.BRONZE_DIM,
                         (info_x, cursor), (info_x + info_w, cursor), 1)
        cursor += metrics.px(10)

        skill_area = pygame.Rect(info_x, cursor, info_w,
                                 rect.bottom - pad - cursor)
        general_cards.draw_skill_list(self.screen, general, game, skill_area, metrics)

    # ---- 底部候选 ----

    def _draw_candidates(self, game, metrics, mouse):
        selected = getattr(game, "selected_general", None)
        for index, (general, rect) in enumerate(zip(self.generals, self.card_rects)):
            chosen = general.id == selected
            focused = index == self.focus_index
            hovered = index == self.hover_index
            self._draw_candidate_card(
                general, rect, metrics,
                chosen=chosen, focused=focused, hovered=hovered)
        del mouse

    def _draw_candidate_card(self, general, rect, metrics, *, chosen, focused, hovered):
        lift = 0
        if focused or chosen:
            lift = metrics.px(PICK_FOCUS_LIFT)
        elif hovered:
            lift = metrics.px(PICK_HOVER_LIFT)
        card = rect.move(0, -lift) if lift else pygame.Rect(rect)

        if lift:
            shadow = pygame.Surface((card.width, card.height), pygame.SRCALPHA)
            pygame.draw.rect(shadow, (0, 0, 0, 130), shadow.get_rect(),
                             border_radius=metrics.px(14))
            self.screen.blit(shadow, (card.x + metrics.px(3), card.y + metrics.px(9)))

        fill = theme.PANEL_WARM if (chosen or focused) else theme.PANEL_WARM_DEEP
        draw_panel(self.screen, card, fill=fill, border=theme.BRONZE_DIM,
                   border_width=2, radius=metrics.px(14), shadow=False)
        tone = general_cards.kingdom_tone(general)
        stripe = pygame.Rect(card.x, card.y, metrics.px(6), card.height)
        pygame.draw.rect(self.screen, tone, stripe,
                         border_top_left_radius=metrics.px(14),
                         border_bottom_left_radius=metrics.px(14))

        pad = metrics.px(8)
        name_font = metrics.fonts.get("seat_name")
        meta_font = metrics.fonts.get("micro")
        name_h = name_font.get_height()
        meta_h = meta_font.get_height()
        portrait = pygame.Rect(
            card.x + pad + metrics.px(4), card.y + pad,
            card.width - pad * 2 - metrics.px(4),
            max(metrics.px(30), card.height - pad * 2 - name_h - meta_h - metrics.px(8)))
        general_cards.draw_portrait(self.screen, general, portrait, metrics, framed=False)

        text_y = portrait.bottom + metrics.px(2)
        name = ellipsize_text(general.name, name_font, card.width - pad * 2)
        rendered = name_font.render(name, True, theme.TEXT_WARM)
        self.screen.blit(rendered, rendered.get_rect(midtop=(card.centerx, text_y)))
        meta = meta_font.render(
            "%s · 体力 %d" % (general_cards.kingdom_label(general),
                             int(general.max_hp or 0)),
            True, theme.TEXT_WARM_MUTED)
        self.screen.blit(meta, meta.get_rect(
            midtop=(card.centerx, text_y + rendered.get_height())))

        state = theme.resolve_state(
            "pool_selected" if chosen else None,
            "draft_focus" if (focused and not chosen) else None,
            "pool_hover" if hovered else None,
        )
        draw_state_border(self.screen, card, state, radius=metrics.px(14))

    # ---- 按钮 ----

    def _draw_buttons(self, game, metrics, mouse):
        fonts = metrics.fonts
        self.confirm_button.enabled = bool(getattr(game, "selected_general", None))
        self.confirm_button.draw(self.screen, fonts, mouse)
        if self.allow_random:
            self.random_button.draw(self.screen, fonts, mouse)
        if self.allow_back:
            self.back_button.draw(self.screen, fonts, mouse)

    # ---- 兼容旧调用点 ----

    def set_generals(self, generals):
        """外部（联机）显式指定候选列表。"""

        self.sync_layout(self.metrics, generals)
        return self

    def describe_selection(self, game):
        general = self.selected_general(game)
        if general is None:
            return ""
        kinds = []
        registry = getattr(game, "skill_registry", None)
        for skill_id in general.skill_ids:
            definition = registry.get(skill_id) if registry is not None else None
            if definition is None:
                continue
            kinds.append("【%s】%s" % (getattr(definition, "name", skill_id),
                                      general_cards.skill_kind_label(definition)))
        return "%s（%s %d 体力）：%s" % (
            general.name, general_cards.kingdom_label(general),
            int(general.max_hp or 0), " ".join(kinds))


def wrap(text, font, width):
    """兼容旧调用（历史上有模块从这里取折行辅助）。"""

    return wrap_text(text, font, width)
