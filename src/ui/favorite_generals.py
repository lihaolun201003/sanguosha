"""「我的武将池」页面（Favorite General Pool）。

这是**本机长期偏好**的管理界面：玩家在这里挑选"以后对局里希望出现的武将"，
每局选将时再从这份池子里随机抽候选（见 ``game.generals.draft``）。

四个概念在这里的落点（不要混）：

    GeneralRegistry      这一屏列出的全部武将（只读）
    FavoriteGeneralPool  ``self.draft``（编辑中）/ 磁盘上那一份（已保存）
    GeneralDraft         本局候选——**不在这屏**，开局时才生成
    SelectedGeneral      本局最终选择——**不在这屏**

保存语义（需求 §21/§22）：

* 页面上的一切改动都只落在 ``draft``（草稿），点「保存」才写盘；
* 「返回」时若有未保存改动 → 弹确认框，不会无声丢弃；
* 「恢复默认」= 全部当前可用武将，需要二次确认。

页面只读 ``GeneralDef`` / ``SkillDef``，不认识任何具体武将；武将数量变化
（25 → 40 → 100）只会让网格多几行，配合滚动条照常使用。
"""

import pygame

from src.game.generals import MIN_POOL

from . import general_cards, layout, theme
from .widgets import Button, draw_panel, draw_state_border, ellipsize_text, wrap_text

#: 设计尺寸下的网格参数。
CARD_WIDTH = 140
CARD_HEIGHT = 190
CARD_GAP = 14
#: 单张卡的最小宽度：宁可行数多一点（有滚动）也不要把武将牌压成小方块。
CARD_MIN_WIDTH = 104
#: 每张卡的固定高度分配：立绘 + 名字 + 势力/体力
PORTRAIT_RATIO = 0.62
SCROLL_STEP = 64
#: 网格右侧给滚动条留的缝（避免它压住最后一列卡片）。
SCROLLBAR_GUTTER = 26

HEADER_H = 128
FOOTER_H = 96
DETAIL_WIDTH = 400


class FavoriteGeneralsScreen:
    """一屏管理武将池（网格 + 详情 + 底部按钮）。"""

    def __init__(self, screen, preferences=None):
        self.screen = screen
        self.preferences = preferences
        self.metrics = None
        self.generals = ()
        self.draft = set()
        self.saved = set()
        self.detail_index = 0
        self.hover_index = None
        self.scroll = 0.0
        self.max_scroll = 0.0
        self.status = ""
        self.status_tone = "info"
        self.notice = ""
        self.confirm = None            # {"kind": "back"/"reset", "text": ...}
        self.card_rects = []
        self.card_hit_rects = []
        self.grid_rect = pygame.Rect(0, 0, 10, 10)
        self.detail_rect = pygame.Rect(0, 0, 10, 10)
        self.footer_rect = pygame.Rect(0, 0, 10, 10)
        self.all_button = Button(pygame.Rect(0, 0, 10, 10), "全选", kind="secondary", font="normal")
        self.clear_button = Button(pygame.Rect(0, 0, 10, 10), "清空", kind="ghost", font="normal")
        self.reset_button = Button(pygame.Rect(0, 0, 10, 10), "恢复默认", kind="secondary", font="normal")
        self.save_button = Button(pygame.Rect(0, 0, 10, 10), "保存", kind="primary", font="normal")
        self.back_button = Button(pygame.Rect(0, 0, 10, 10), "返回", kind="ghost", font="normal")
        self.confirm_yes = Button(pygame.Rect(0, 0, 10, 10), "确定", kind="primary", font="normal")
        self.confirm_no = Button(pygame.Rect(0, 0, 10, 10), "取消", kind="secondary", font="normal")
        self.sync_layout(None, ())

    # ==================================================
    # 生命周期
    # ==================================================

    def on_enter(self, game, preferences=None):
        """进入页面：把**已保存的**池子读进草稿（未保存改动不带进来）。"""

        if preferences is not None:
            self.preferences = preferences
        self.generals = tuple(game.playable_generals(for_random=True))
        self.saved = set(self._stored_ids(game))
        self.draft = set(self.saved)
        self.detail_index = 0
        self.hover_index = None
        self.scroll = 0.0
        self.confirm = None
        self.status = ""
        self.notice = ""
        self.sync_layout(self.metrics, self.generals)
        return self

    def _stored_ids(self, game):
        """磁盘上那份池子（过滤掉当前不可用的 id）。"""

        if self.preferences is None:
            return [general.id for general in self.generals]
        stored = self.preferences.favorite_general_ids()
        available = {general.id for general in self.generals}
        valid = [general_id for general_id in stored if general_id in available]
        if not valid:
            # 没配置过 / 配置里的武将全部已失效 → 默认"全部可用武将"，
            # 与候选池的回退口径一致（旧用户升级后直接能玩）。
            return [general.id for general in self.generals]
        return valid

    # ==================================================
    # 查询
    # ==================================================

    @property
    def dirty(self):
        return self.draft != self.saved

    def selected_count(self):
        return len(self.draft)

    def draft_ids(self):
        """草稿池（按注册顺序，稳定）——保存与"回写 Game"都用它。"""

        return [general.id for general in self.generals if general.id in self.draft]

    def can_save(self):
        return self.selected_count() >= MIN_POOL

    # ==================================================
    # 布局
    # ==================================================

    def sync_layout(self, metrics=None, generals=None):
        metrics = metrics or layout.LayoutMetrics(
            layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.metrics = metrics
        if generals is not None:
            self.generals = tuple(generals)

        pad = metrics.px(22)
        header_h = metrics.px(HEADER_H)
        footer_h = metrics.px(FOOTER_H)
        # 详情面板给"武将牌 + 技能说明"留够宽度，其余全部给网格（网格能滚动，
        # 详情面板不能——它挤了就没法读技能）。
        detail_w = min(metrics.px(DETAIL_WIDTH), int(metrics.screen_w * 0.32))

        body_top = header_h
        body_h = max(metrics.px(160), metrics.screen_h - header_h - footer_h - metrics.px(16))
        self.grid_rect = pygame.Rect(
            pad, body_top, max(metrics.px(200),
                               metrics.screen_w - pad * 3 - detail_w), body_h)
        self.detail_rect = pygame.Rect(
            self.grid_rect.right + pad, body_top, detail_w, body_h)
        self.footer_rect = pygame.Rect(
            pad, self.grid_rect.bottom + metrics.px(12),
            metrics.screen_w - pad * 2, footer_h - metrics.px(16))

        self._layout_grid(metrics)
        self._layout_buttons(metrics)
        return self

    def _layout_grid(self, metrics):
        """响应式网格：列数按可用宽度算，行数按可用高度算，超出就滚动。"""

        area = self.grid_rect
        gap = metrics.px(CARD_GAP)
        min_w = metrics.px(CARD_MIN_WIDTH)
        # 给滚动条留出右侧一条窄缝：不留的话它会压在最后一列卡片上。
        gutter = metrics.px(SCROLLBAR_GUTTER)
        usable_w = max(min_w, area.width - gutter)
        columns = max(1, (usable_w + gap) // (min_w + gap))
        card_w = min(metrics.px(CARD_WIDTH),
                     (usable_w - gap * (columns - 1)) // columns)
        card_w = max(min_w, card_w)
        card_h = max(metrics.px(96), min(metrics.px(CARD_HEIGHT), int(card_w * 1.42)))
        step_x = card_w + gap
        step_y = card_h + gap

        # 只显示"整行"：把网格高度收成行高的整数倍，避免最后一行被切一半
        # （名字还在、体力那行被裁掉，看起来像画错了）。多出来的空间留在
        # 网格下方，由详情面板与底部按钮之间的空隙吸收。
        inner = metrics.px(10)
        rows_visible = max(1, (area.height - inner * 2 + gap) // step_y)
        fit_h = rows_visible * step_y - gap + inner * 2
        if fit_h < area.height:
            area = pygame.Rect(area.x, area.y, area.width, fit_h)
            self.grid_rect = area

        total = len(self.generals)
        total_rows = max(1, (total + columns - 1) // columns) if total else 1
        content_h = total_rows * step_y - gap
        self.max_scroll = max(0.0, float(content_h - (area.height - inner * 2)))
        self.scroll = max(0.0, min(self.scroll, self.max_scroll))

        self.card_rects = []
        self.card_hit_rects = []
        offset = int(self.scroll)
        for index, _general in enumerate(self.generals):
            column, row = index % columns, index // columns
            rect = pygame.Rect(
                area.x + inner + column * step_x,
                area.y + inner + row * step_y - offset,
                card_w, card_h)
            self.card_rects.append(rect)
            self.card_hit_rects.append(rect)

    def _layout_buttons(self, metrics):
        bar = self.footer_rect
        gap = metrics.px(14)
        width = metrics.px(150)
        height = metrics.px(56)
        self.all_button.rect = pygame.Rect(bar.x, bar.y, width, height)
        self.clear_button.rect = pygame.Rect(
            self.all_button.rect.right + gap, bar.y, width, height)
        self.reset_button.rect = pygame.Rect(
            self.clear_button.rect.right + gap, bar.y, width, height)
        right_w = metrics.px(240)
        self.save_button.rect = pygame.Rect(
            bar.right - width - right_w - gap, bar.y, width, height)
        self.back_button.rect = pygame.Rect(
            bar.right - right_w, bar.y, right_w, height)

        modal = self._modal_rect(metrics)
        button_w = metrics.px(170)
        self.confirm_yes.rect = pygame.Rect(0, 0, button_w, height)
        self.confirm_yes.rect.center = (modal.centerx - button_w // 2 - gap // 2,
                                       modal.bottom - metrics.px(52))
        self.confirm_no.rect = pygame.Rect(0, 0, button_w, height)
        self.confirm_no.rect.center = (modal.centerx + button_w // 2 + gap // 2,
                                      modal.bottom - metrics.px(52))

    def _modal_rect(self, metrics):
        width = min(metrics.px(620), int(metrics.screen_w * 0.7))
        height = metrics.px(230)
        rect = pygame.Rect(0, 0, width, height)
        rect.center = (metrics.screen_w // 2, metrics.screen_h // 2)
        return rect

    # ==================================================
    # 交互
    # ==================================================

    def contains_grid(self, position):
        """点在网格区域里（含滚动裁切），而不是"卡片矩形里"。"""

        return self.grid_rect.collidepoint(position)

    def card_index_at(self, position):
        if not self.contains_grid(position):
            return None
        for index, rect in enumerate(self.card_hit_rects):
            if rect.collidepoint(position):
                return index
        return None

    def set_hover(self, position):
        index = self.card_index_at(position)
        self.hover_index = index
        if index is not None:
            self.detail_index = index
        return index

    def toggle(self, index):
        if index is None or index >= len(self.generals):
            return None
        general = self.generals[index]
        if general.id in self.draft:
            self.draft.discard(general.id)
            self._set_status("已移出：%s" % general.name, "info")
        else:
            self.draft.add(general.id)
            self._set_status("已加入：%s" % general.name, "good")
        return general.id

    def _set_status(self, text, tone="info"):
        self.status = text
        self.status_tone = tone

    def scroll_by(self, steps):
        self.scroll = max(0.0, min(self.scroll + steps * self.metrics.px(SCROLL_STEP),
                                   self.max_scroll))
        return self.scroll

    def handle_event(self, event, game):
        """返回 ``"saved"`` / ``"back"`` / ``"handled"`` / ``None``。"""

        if event.type == pygame.MOUSEWHEEL:
            self.scroll_by(-event.y)
            return "handled"
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            return self._request_back()
        if event.type == pygame.MOUSEMOTION:
            self.set_hover(event.pos)
            return "handled"
        if event.type != pygame.MOUSEBUTTONDOWN or getattr(event, "button", 1) != 1:
            return "handled" if event.type == pygame.MOUSEBUTTONUP else None
        return self.handle_click(event.pos, game)

    def handle_click(self, position, game):
        if self.confirm is not None:
            return self._handle_confirm_click(position)
        if self.all_button.contains(position):
            self.draft = {general.id for general in self.generals}
            self._set_status("已选择全部 %d 名武将" % len(self.generals), "good")
            return "handled"
        if self.clear_button.contains(position):
            self.draft = set()
            self._set_status("已清空，记得至少选 %d 名再保存" % MIN_POOL, "warn")
            return "handled"
        if self.reset_button.contains(position):
            self._open_confirm("reset", "恢复默认武将池？",
                               "将把你池子里的武将重置为当前全部可用武将（%d 名）。"
                               % len(self.generals))
            return "handled"
        if self.save_button.contains(position):
            return self._save()
        if self.back_button.contains(position):
            return self._request_back()
        index = self.card_index_at(position)
        if index is not None:
            self.toggle(index)
            return "select"
        return "handled"

    # ---- 保存 / 返回 ----

    def _save(self):
        if not self.can_save():
            self._set_status("武将池至少需要 %d 名武将，当前 %d 名"
                             % (MIN_POOL, self.selected_count()), "error")
            self.notice = "至少选择 %d 名武将才能保存。" % MIN_POOL
            return "blocked"
        if self.preferences is None:
            self._set_status("没有找到配置存储，无法保存", "error")
            return "blocked"
        order = self.draft_ids()
        self.preferences.set_favorite_general_ids(order)
        if self.preferences.save_error:
            self._set_status("保存失败：" + self.preferences.save_error, "error")
            return "blocked"
        self.saved = set(self.draft)
        self._set_status("已保存 %d 名武将" % self.selected_count(), "good")
        self.notice = ""
        return "saved"

    def _request_back(self):
        if self.dirty:
            self._open_confirm("back", "有未保存的修改",
                               "离开会丢弃这些改动，确定要返回吗？")
            return "handled"
        return "back"

    def _open_confirm(self, kind, title, body):
        self.confirm = {"kind": kind, "title": title, "body": body}

    def _handle_confirm_click(self, position):
        if self.confirm_yes.contains(position):
            kind = self.confirm["kind"]
            self.confirm = None
            if kind == "reset":
                self.draft = {general.id for general in self.generals}
                self._set_status("已恢复为全部 %d 名武将（还需点「保存」）"
                                 % len(self.generals), "good")
                return "handled"
            return "back"
        if self.confirm_no.contains(position):
            self.confirm = None
            return "handled"
        return "handled"

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, game, metrics=None):
        metrics = metrics or self.metrics
        if metrics is None or self.metrics is not metrics:
            self.sync_layout(metrics, self.generals)

        self.screen.blit(theme.menu_background(metrics.screen_w, metrics.screen_h), (0, 0))
        self._draw_header(metrics)
        self._draw_grid(metrics)
        self._draw_detail(game, metrics)
        self._draw_footer(metrics)
        if self.confirm is not None:
            self._draw_confirm(metrics)
        return self

    # ---- 头部 ----

    def _draw_header(self, metrics):
        fonts = metrics.fonts
        pad = metrics.px(28)
        title = fonts.get("title").render("我的武将池", True, theme.GOLD_BRIGHT)
        self.screen.blit(title, (pad, metrics.px(14)))
        subtitle = fonts.get("small").render(
            "选择你希望在对局中出现的武将", True, theme.TEXT_WARM_DIM)
        self.screen.blit(subtitle, (pad, metrics.px(14) + title.get_height() + metrics.px(2)))

        count_text = "已选择：%d / %d" % (self.selected_count(), len(self.generals))
        color = theme.JADE_BRIGHT if self.can_save() else theme.DANGER
        count = fonts.get("large").render(count_text, True, color)
        count_rect = count.get_rect(
            topright=(metrics.screen_w - pad - metrics.px(20), metrics.px(18)))
        # 计数放在一块浅色底上，数字在任何背景上都读得清
        plate = count_rect.inflate(metrics.px(28), metrics.px(12))
        draw_panel(self.screen, plate, fill=theme.PANEL_WARM_DEEP,
                   border=theme.BRONZE_DIM, border_width=2,
                   radius=metrics.px(12), shadow=False)
        self.screen.blit(count, count_rect)

        footer_note = self._pool_note()
        if footer_note:
            note = fonts.get("micro").render(footer_note, True, theme.TEXT_WARM_MUTED)
            self.screen.blit(note, note.get_rect(
                topright=(count_rect.right, count_rect.bottom + metrics.px(6))))

        pygame.draw.line(
            self.screen, theme.BRONZE_DIM,
            (pad, metrics.px(HEADER_H) - metrics.px(10)),
            (metrics.screen_w - pad, metrics.px(HEADER_H) - metrics.px(10)), 2)

    def _pool_note(self):
        if not self.generals:
            return ""
        if not self.can_save():
            return "至少选择 %d 名武将才能保存" % MIN_POOL
        if self.dirty:
            return "有未保存的修改"
        return "已保存"

    # ---- 网格 ----

    def _draw_grid(self, metrics):
        area = self.grid_rect
        draw_panel(self.screen, area, fill=theme.PANEL_WARM_DEEP,
                   border=theme.BRONZE_DIM, border_width=2,
                   radius=metrics.px(16), shadow=False)

        # 卡片画在离屏图层上：超出网格区域的部分自然被裁掉（滚动时不会
        # 画到头部 / 底部按钮上）。
        layer = pygame.Surface(area.size, pygame.SRCALPHA)
        for index, (general, rect) in enumerate(zip(self.generals, self.card_rects)):
            local = rect.move(-area.x, -area.y)
            if local.bottom < -metrics.px(40) or local.y > area.height + metrics.px(40):
                continue
            self._draw_card(layer, general, local, metrics,
                            selected=general.id in self.draft,
                            hovered=index == self.hover_index,
                            focused=index == self.detail_index)
        self.screen.blit(layer, area.topleft)

        self._draw_scrollbar(metrics)

    def _draw_scrollbar(self, metrics):
        if self.max_scroll <= 0:
            return
        area = self.grid_rect
        width = max(4, metrics.px(8))
        track = pygame.Rect(area.right - width - metrics.px(6), area.y + metrics.px(6),
                            width, area.height - metrics.px(12))
        pygame.draw.rect(self.screen, theme.PANEL_WARM_SUNKEN, track,
                         border_radius=width // 2)
        ratio = area.height / (area.height + self.max_scroll)
        bar_h = max(metrics.px(36), int(track.height * ratio))
        travel = track.height - bar_h
        offset = int(travel * (self.scroll / self.max_scroll)) if self.max_scroll else 0
        bar = pygame.Rect(track.x, track.y + offset, width, bar_h)
        pygame.draw.rect(self.screen, theme.BRONZE, bar, border_radius=width // 2)

    def _draw_card(self, surface, general, rect, metrics, *, selected, hovered, focused):
        tone = general_cards.kingdom_tone(general)
        fill = theme.PANEL_WARM if selected else theme.PANEL_WARM_DEEP
        if hovered and not selected:
            fill = theme.PANEL_WARM
        draw_panel(surface, rect, fill=fill, border=theme.BRONZE_DIM,
                   border_width=2, radius=metrics.px(12), shadow=False)

        # 势力色条（左侧）
        stripe = pygame.Rect(rect.x, rect.y, metrics.px(6), rect.height)
        pygame.draw.rect(surface, tone, stripe,
                         border_top_left_radius=metrics.px(12),
                         border_bottom_left_radius=metrics.px(12))

        pad = metrics.px(8)
        name_h = metrics.fonts.get("seat_name").get_height()
        meta_h = metrics.fonts.get("micro").get_height()
        portrait = pygame.Rect(
            rect.x + pad + metrics.px(4), rect.y + pad,
            rect.width - pad * 2 - metrics.px(4),
            max(metrics.px(24), rect.height - pad * 2 - name_h - meta_h - metrics.px(10)))
        general_cards.draw_portrait(surface, general, portrait, metrics, framed=False)

        text_y = portrait.bottom + metrics.px(4)
        name_font = metrics.fonts.get("seat_name")
        name = ellipsize_text(general.name, name_font, rect.width - pad * 2)
        rendered = name_font.render(name, True, theme.TEXT_WARM)
        surface.blit(rendered, rendered.get_rect(midtop=(rect.centerx, text_y)))

        meta_font = metrics.fonts.get("micro")
        meta = meta_font.render(
            "%s · 体力 %d" % (general_cards.kingdom_label(general),
                             int(general.max_hp or 0)),
            True, theme.TEXT_WARM_MUTED)
        meta = ellipsize_text(meta, meta_font, rect.width - pad * 2) \
            if False else meta
        surface.blit(meta, meta.get_rect(
            midtop=(rect.centerx, text_y + rendered.get_height())))

        state = theme.resolve_state(
            "pool_selected" if selected else None,
            "pool_hover" if hovered else None,
            "draft_focus" if (focused and not selected) else None,
        )
        draw_state_border(surface, rect, state, radius=metrics.px(12))

        if selected:
            badge_font = metrics.fonts.get("micro")
            label = badge_font.render("✓", True, theme.INK)
            badge = pygame.Rect(0, 0, label.get_width() + metrics.px(10),
                                label.get_height() + metrics.px(4))
            badge.topright = (rect.right - metrics.px(5), rect.y + metrics.px(5))
            pygame.draw.rect(surface, theme.JADE, badge, border_radius=metrics.px(8))
            surface.blit(label, label.get_rect(center=badge.center))

    # ---- 详情 ----

    def _draw_detail(self, game, metrics):
        rect = self.detail_rect
        draw_panel(self.screen, rect, fill=theme.PANEL_WARM_DEEP,
                   border=theme.BRONZE, border_width=2,
                   radius=metrics.px(16), shadow=False)
        general = self.generals[self.detail_index] if self.generals else None
        if general is None:
            return
        pad = metrics.px(18)
        fonts = metrics.fonts

        art_h = int(rect.height * 0.44)
        art_rect = pygame.Rect(rect.x + pad, rect.y + pad,
                               rect.width - pad * 2, art_h)
        general_cards.draw_portrait(self.screen, general, art_rect, metrics)

        cursor = art_rect.bottom + metrics.px(10)
        name_font = fonts.get("title")
        name = name_font.render(general.name, True, theme.TEXT_WARM)
        self.screen.blit(name, name.get_rect(midtop=(rect.centerx, cursor)))
        cursor += name.get_height() + metrics.px(2)

        kingdom = fonts.get("small").render(
            "%s · %s" % (general_cards.kingdom_label(general),
                         "男" if general.gender == "male" else "女"),
            True, theme.TEXT_WARM_DIM)
        self.screen.blit(kingdom, kingdom.get_rect(midtop=(rect.centerx, cursor)))
        cursor += kingdom.get_height() + metrics.px(8)

        general_cards.draw_hp_pips(
            self.screen, general,
            (rect.x + pad + metrics.px(4), cursor + metrics.px(6)), metrics)
        cursor += metrics.px(22)

        pygame.draw.line(self.screen, theme.BRONZE_DIM,
                         (rect.x + pad, cursor), (rect.right - pad, cursor), 1)
        cursor += metrics.px(8)

        skill_area = pygame.Rect(rect.x + pad, cursor,
                                 rect.width - pad * 2, rect.bottom - cursor - pad)
        general_cards.draw_skill_list(self.screen, general, game, skill_area, metrics)

    # ---- 底部 ----

    def _draw_footer(self, metrics):
        fonts = metrics.fonts
        mouse = pygame.mouse.get_pos()
        # 状态行：把"至少 5 名"说清楚，而不是等玩家点保存才报错。
        tone_color = {
            "good": theme.JADE_BRIGHT,
            "warn": theme.TARGET_YELLOW,
            "error": theme.DANGER,
            "info": theme.TEXT_WARM_DIM,
        }.get(self.status_tone, theme.TEXT_WARM_DIM)
        text = self.notice or self.status or self._pool_note()
        if text:
            rendered = fonts.get("small").render(text, True, tone_color)
            self.screen.blit(rendered, rendered.get_rect(
                midleft=(self.footer_rect.x, self.footer_rect.centery)))

        self.all_button.draw(self.screen, fonts, mouse)
        self.clear_button.draw(self.screen, fonts, mouse)
        self.reset_button.draw(self.screen, fonts, mouse)

        self.save_button.enabled = self.can_save()
        self.save_button.kind = "primary" if self.dirty else "secondary"
        self.save_button.draw(self.screen, fonts, mouse)
        self.back_button.draw(self.screen, fonts, mouse)

    # ---- 确认框 ----

    def _draw_confirm(self, metrics):
        veil = pygame.Surface((metrics.screen_w, metrics.screen_h), pygame.SRCALPHA)
        veil.fill((10, 8, 6, 170))
        self.screen.blit(veil, (0, 0))

        rect = self._modal_rect(metrics)
        draw_panel(self.screen, rect, fill=theme.PANEL_WARM,
                   border=theme.BRONZE_BRIGHT, border_width=3,
                   radius=metrics.px(18))
        fonts = metrics.fonts
        title = fonts.get("large").render(self.confirm["title"], True, theme.GOLD_BRIGHT)
        self.screen.blit(title, title.get_rect(
            midtop=(rect.centerx, rect.y + metrics.px(28))))
        body_font = fonts.get("small")
        for index, line in enumerate(wrap_text(
                self.confirm["body"], body_font, rect.width - metrics.px(60))):
            rendered = body_font.render(line, True, theme.TEXT_WARM)
            self.screen.blit(rendered, rendered.get_rect(
                midtop=(rect.centerx, rect.y + metrics.px(84) + index * (rendered.get_height() + 2))))

        mouse = pygame.mouse.get_pos()
        self.confirm_yes.draw(self.screen, fonts, mouse)
        self.confirm_no.draw(self.screen, fonts, mouse)
